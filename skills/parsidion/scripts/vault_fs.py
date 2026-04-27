"""Filesystem I/O, file locking, pending queue, git, and daily note management.

Provides cross-platform file locking, atomic writes, pending summary queue
management, git commit helpers, and daily note lifecycle functions.

This module is part of the vault_common split (ARC-005).  All public symbols
are re-exported from ``vault_common`` for backward compatibility.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import IO, Any

from vault_config import get_config
from vault_path import resolve_vault, resolve_templates_dir, VAULT_DIRS

__all__: list[str] = [
    # File locking
    "flock_exclusive",
    "flock_shared",
    "funlock",
    # File I/O
    "read_last_n_lines",
    # Pending queue
    "append_to_pending",
    "migrate_pending_paths",
    # Git
    "git_commit_vault",
    # Daily notes
    "get_vault_username",
    "today_daily_path",
    "create_daily_note_if_missing",
    "append_session_to_daily",
    # Vault directory management
    "ensure_vault_dirs",
]

# ---------------------------------------------------------------------------
# Cross-platform file locking
# ---------------------------------------------------------------------------

try:
    import fcntl as _fcntl

    def flock_exclusive(f: IO[Any]) -> None:
        """Acquire an exclusive (write) lock on an open file descriptor."""
        _fcntl.flock(f, _fcntl.LOCK_EX)

    def flock_shared(f: IO[Any]) -> None:
        """Acquire a shared (read) lock on an open file descriptor."""
        _fcntl.flock(f, _fcntl.LOCK_SH)

    def funlock(f: IO[Any]) -> None:
        """Release a lock on an open file descriptor."""
        _fcntl.flock(f, _fcntl.LOCK_UN)

except ImportError:
    # Windows: fcntl is not available. File operations proceed without locking.
    # Race conditions between simultaneous Claude instances are acceptably rare
    # on Windows.
    def flock_exclusive(f: IO[Any]) -> None:
        """Acquire an exclusive (write) lock on an open file descriptor (no-op on Windows)."""
        pass

    def flock_shared(f: IO[Any]) -> None:
        """Acquire a shared (read) lock on an open file descriptor (no-op on Windows)."""
        pass

    def funlock(f: IO[Any]) -> None:
        """Release a lock on an open file descriptor (no-op on Windows)."""
        pass


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------


def read_last_n_lines(filepath: Path, n: int) -> list[str]:
    """Read the last *n* lines of a file.

    Uses ``collections.deque(maxlen=n)`` to avoid loading the entire file
    into memory -- only the last *n* lines are retained.  See ARC-014.

    Args:
        filepath: Path to the file.
        n: Number of trailing lines to return.

    Returns:
        A list of the last n lines (or fewer if the file is shorter).
    """
    from collections import deque

    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            tail = deque(f, maxlen=n)
        return list(tail)
    except (OSError, UnicodeDecodeError):
        return []


# ---------------------------------------------------------------------------
# Pending summary queue
# ---------------------------------------------------------------------------


def append_to_pending(
    transcript_path: Path,
    project: str,
    categories: dict[str, list[str]],
    force: bool = False,
    source: str = "session",
    agent_type: str | None = None,
    session_id: str | None = None,
    vault: Path | None = None,
) -> None:
    """Append a session entry to the pending summaries queue.

    Only appends when at least one significant category is detected,
    unless *force* is True (used when the AI gate has already decided).
    Guards against duplicates by session ID (transcript filename stem).

    Args:
        transcript_path: Path to the transcript JSONL file (must be readable).
        project: The project name.
        categories: Detected categories mapping keys to excerpt lists.
        force: Skip the significance filter; queue unconditionally.
        source: Origin of the transcript -- ``"session"`` or ``"subagent"``.
        agent_type: Subagent type (e.g. ``"Explore"``); only meaningful when
            *source* is ``"subagent"``.
        session_id: Explicit deduplication key.  Defaults to
            ``transcript_path.stem`` when omitted.  Pass the ``agent_id``
            here when the transcript path is the real agent transcript so
            that the stored path remains readable while dedup uses the ID.
        vault: Optional vault path. Defaults to resolve_vault().
    """
    vault = vault or resolve_vault()
    all_keys = set(categories.keys())
    if not force:
        significant = {"error_fix", "research", "pattern"}
        if not (significant & all_keys):
            return

    pending_path = vault / "pending_summaries.jsonl"
    session_id = session_id if session_id is not None else transcript_path.stem

    entry: dict[str, object] = {
        "session_id": session_id,
        "transcript_path": str(transcript_path),
        "project": project,
        "categories": sorted(all_keys),
        "timestamp": datetime.now().isoformat(),
        "source": source,
    }
    if agent_type is not None:
        entry["agent_type"] = agent_type

    # SEC-010: On Windows, fcntl is unavailable so flock_exclusive() is a no-op
    # (see the ImportError fallback in the locking section above).  Concurrent writes
    # from multiple Claude instances on Windows may therefore produce duplicate entries
    # or interleaved JSON lines.  The deduplication check below provides a best-effort
    # guard, but is not race-free without OS-level locking.
    # If Windows atomic locking becomes critical, add a lock-file sidecar using:
    #   import msvcrt; msvcrt.locking(lf.fileno(), msvcrt.LK_LOCK, 1)
    try:
        with open(pending_path, "a+", encoding="utf-8") as f:
            flock_exclusive(f)
            try:
                f.seek(0)
                # ARC-012: Build a set of existing session IDs for O(1) dedup
                # instead of comparing each line individually during iteration.
                existing_ids: set[str] = set()
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        existing = json.loads(line)
                        eid = (
                            existing.get("session_id")
                            or Path(existing.get("transcript_path", "")).stem
                        )
                        existing_ids.add(eid)
                    except (json.JSONDecodeError, ValueError):
                        continue
                if session_id in existing_ids:
                    return  # Already queued
                f.seek(0, 2)
                f.write(json.dumps(entry) + "\n")
            finally:
                funlock(f)
    except OSError:
        pass


def migrate_pending_paths(dry_run: bool = False, vault: Path | None = None) -> int:
    """Fix broken transcript paths in pending_summaries.jsonl.

    Older versions of subagent_stop_hook stored paths without the ``agent-``
    prefix used by Claude Code (e.g. ``<id>.jsonl`` instead of
    ``agent-<id>.jsonl``).  This scans every entry, resolves the real path,
    and rewrites the file with corrected paths.

    Args:
        dry_run: If True, report what would change without writing.
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        Number of entries whose paths were fixed.
    """
    vault = vault or resolve_vault()
    pending_path = vault / "pending_summaries.jsonl"
    if not pending_path.exists():
        return 0
    entries: list[dict] = []
    with open(pending_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    fixed = 0
    for entry in entries:
        stored = entry.get("transcript_path", "")
        if not stored:
            continue
        stored_path = Path(stored)
        if stored_path.exists():
            continue

        candidates: list[Path] = []

        # Claude Code fallback: old entries lacked the "agent-" prefix.
        candidates.append(stored_path.parent / f"agent-{stored_path.stem}.jsonl")

        # pi fallback: support both historical location spellings.
        stored_str = str(stored_path)
        if "/.pi/agent/sessions/" in stored_str:
            candidates.append(
                Path(stored_str.replace("/.pi/agent/sessions/", "/.pi/agent-sessions/"))
            )
        if "/.pi/agent-sessions/" in stored_str:
            candidates.append(
                Path(stored_str.replace("/.pi/agent-sessions/", "/.pi/agent/sessions/"))
            )

        repaired = next(
            (candidate for candidate in candidates if candidate.exists()), None
        )
        if repaired is not None:
            if not dry_run:
                entry["transcript_path"] = str(repaired)
            fixed += 1
    if fixed and not dry_run:
        tmp = pending_path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            flock_exclusive(fh)
            for entry in entries:
                fh.write(json.dumps(entry) + "\n")
        tmp.replace(pending_path)
    return fixed


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def git_commit_vault(
    message: str, vault: Path | None = None, paths: list[Path] | None = None
) -> bool:
    """Stage and commit changes to the vault git repository.

    Does nothing and returns False if the vault is not a git repository,
    if git is not available, or if ``git.auto_commit`` is ``false`` in config.
    Never raises exceptions.

    Args:
        message: Commit message.
        vault: Optional vault path. Defaults to resolve_vault().
        paths: Specific paths to stage. If None, stages all changes (``git add -A``).

    Returns:
        True if the commit succeeded, False otherwise.
    """
    vault = vault or resolve_vault()
    if not get_config("git", "auto_commit", True):
        return False

    git_marker = vault / ".git"
    if not (git_marker.is_dir() or git_marker.is_file()):
        return False

    try:
        # Stage files
        if paths:
            add_args = ["git", "add"] + [str(p) for p in paths]
        else:
            add_args = ["git", "add", "-A"]

        result = subprocess.run(
            add_args,
            cwd=str(vault),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False

        # Commit -- exit code 1 with "nothing to commit" is not an error
        # SEC-002: message is caller-controlled but project names embedded in it
        # are sanitized by callers using safe_project (see git_commit_vault usages).
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(vault),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# ---------------------------------------------------------------------------
# Daily note management
# ---------------------------------------------------------------------------


def get_vault_username() -> str:
    """Return the configured vault username for daily note naming.

    Reads ``vault.username`` from config.yaml first, then falls back to the
    ``USER`` / ``USERNAME`` environment variable.  Returns ``"unknown"`` if
    neither source yields a non-empty value.

    Used to produce per-user daily note filenames (``DD-{username}.md``) so
    multiple team members can share a vault via git without daily-note conflicts.
    """
    username = get_config("vault", "username", "")
    if not username:
        username = os.environ.get("USER", os.environ.get("USERNAME", ""))
    return username.strip() or "unknown"


def today_daily_path(vault: Path | None = None) -> Path:
    """Return the path to today's daily note: ``Daily/YYYY-MM/DD-{username}.md``.

    The username suffix prevents merge conflicts when a team shares a vault via
    git -- each member writes to their own file on the same day.  The username is
    resolved by :func:`get_vault_username`.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().
    """
    vault = vault or resolve_vault()
    today = date.today()
    month_dir = f"{today.year:04d}-{today.month:02d}"
    day_file = f"{today.day:02d}-{get_vault_username()}.md"
    return vault / "Daily" / month_dir / day_file


def create_daily_note_if_missing(vault: Path | None = None) -> Path:
    """Create today's daily note from the template if it doesn't exist.

    Replaces ``{{date}}`` in the template with today's date. Returns the
    path to the daily note (whether newly created or already existing).

    Args:
        vault: Optional vault path. Defaults to resolve_vault().
    """
    daily_path = today_daily_path(vault=vault)

    if daily_path.exists():
        return daily_path

    # Ensure the Daily directory exists
    daily_path.parent.mkdir(parents=True, exist_ok=True)

    template_path = resolve_templates_dir() / "daily.md"
    today_str = date.today().isoformat()

    if template_path.is_file():
        template_content = template_path.read_text(encoding="utf-8")
        content = template_content.replace("{{date}}", today_str)
    else:
        # Minimal fallback if template is missing
        content = (
            f"---\ndate: {today_str}\ntype: daily\ntags: [daily]\n---\n\n"
            f"## Sessions\n\n## Key Decisions\n\n## Problems Solved\n\n## Open Questions\n"
        )

    daily_path.write_text(content, encoding="utf-8")
    return daily_path


def append_session_to_daily(
    project: str,
    categories: dict[str, list[str]],
    first_summary: str,
    vault_path: Path,
) -> None:
    """Append a session summary section to today's daily note.

    QA-010: Moved from ``session_stop_hook.py`` to ``vault_common.py`` so
    other scripts that need to write daily entries can access it.

    Args:
        project: The project name.
        categories: Detected category keys mapped to excerpts.
        first_summary: The first significant assistant message summary.
        vault_path: The vault root path.
    """
    # Import here to avoid circular dependency at module level
    from vault_hooks import TRANSCRIPT_CATEGORY_LABELS

    # Ensure the daily note exists with proper frontmatter from the template.
    # Previously used daily_path.touch(), which created an empty file and left
    # the note without frontmatter if this hook was the first writer of the day.
    daily_path = create_daily_note_if_missing(vault=vault_path)

    now_time = datetime.now().strftime("%H:%M")

    topic_labels = [TRANSCRIPT_CATEGORY_LABELS.get(cat, cat) for cat in categories]
    topics_str = ", ".join(topic_labels) if topic_labels else "General"

    # Truncate the summary for the daily note
    summary_text = first_summary[:300].replace("\n", " ").strip()
    if not summary_text:
        summary_text = "Session completed"

    section = (
        f"\n### Session: {project} ({now_time})\n"
        f"- **Topics**: {topics_str}\n"
        f"- **Summary**: {summary_text}\n"
    )

    try:
        existing = daily_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        existing = ""

    # Append under the ## Sessions heading if it exists, else append at end
    if "## Sessions" in existing:
        sessions_idx = existing.index("## Sessions")
        rest = existing[sessions_idx + len("## Sessions") :]

        # Find the next ## heading after Sessions
        next_heading_match = re.search(r"\n## ", rest)
        if next_heading_match:
            insert_pos = sessions_idx + len("## Sessions") + next_heading_match.start()
            updated = existing[:insert_pos] + section + existing[insert_pos:]
        else:
            updated = existing + section
    else:
        updated = existing + "\n## Sessions\n" + section

    daily_path.write_text(updated, encoding="utf-8")


# ---------------------------------------------------------------------------
# Vault directory management
# ---------------------------------------------------------------------------


def ensure_vault_dirs(vault: Path | None = None) -> None:
    """Create any missing vault directories.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().
    """
    vault = vault or resolve_vault()
    vault.mkdir(parents=True, exist_ok=True)
    for dirname in VAULT_DIRS:
        (vault / dirname).mkdir(exist_ok=True)

    # Ensure Templates symlink points to the skill templates
    templates_dir = resolve_templates_dir()
    templates_link = vault / "Templates"
    if templates_link.is_dir() and not templates_link.is_symlink():
        # Only create symlink if the directory is empty (freshly created by us)
        try:
            if not any(templates_link.iterdir()):
                templates_link.rmdir()
                templates_link.symlink_to(templates_dir)
        except OSError:
            pass
    elif not templates_link.exists():
        try:
            templates_link.symlink_to(templates_dir)
        except OSError:
            # Fall back to a plain directory if symlink fails
            templates_link.mkdir(exist_ok=True)
