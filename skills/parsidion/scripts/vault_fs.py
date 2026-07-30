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
import stat
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
    "atomic_write_text",
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
    # SEC-013: Windows fallback — fcntl is not available.
    # Use msvcrt.locking() which is stdlib on Windows (CPython only).
    # msvcrt.locking() locks byte ranges; we lock the first byte of the file
    # as a mutex token.  Both the exclusive and shared locks use LK_LOCK
    # (blocking exclusive) because msvcrt has no shared-lock mode.
    # This is intentionally conservative: it may serialise readers but it
    # prevents interleaved writes from parallel Claude instances.
    try:
        import msvcrt as _msvcrt

        # msvcrt.locking / LK_LOCK / LK_UNLCK exist on Windows CPython but
        # pyright's cross-platform stubs do not declare them.
        # Use getattr to silence the pyright attribute errors while keeping
        # runtime correctness on Windows.
        _msvcrt_locking = getattr(_msvcrt, "locking")  # type: ignore[attr-defined]  # noqa: B009
        _LK_LOCK = getattr(_msvcrt, "LK_LOCK")  # type: ignore[attr-defined]  # noqa: B009
        _LK_UNLCK = getattr(_msvcrt, "LK_UNLCK")  # type: ignore[attr-defined]  # noqa: B009

        _LOCK_BYTES = 1  # Lock one sentinel byte at offset 0

        def flock_exclusive(f: IO[Any]) -> None:
            """Acquire an exclusive lock on the file (Windows: msvcrt.locking)."""
            f.flush()
            f.seek(0)
            _msvcrt_locking(f.fileno(), _LK_LOCK, _LOCK_BYTES)

        def flock_shared(f: IO[Any]) -> None:
            """Acquire a shared lock on the file (Windows: msvcrt exclusive, no shared mode)."""
            f.flush()
            f.seek(0)
            _msvcrt_locking(f.fileno(), _LK_LOCK, _LOCK_BYTES)

        def funlock(f: IO[Any]) -> None:
            """Release a lock on the file (Windows: msvcrt.LK_UNLCK)."""
            try:
                f.seek(0)
                _msvcrt_locking(f.fileno(), _LK_UNLCK, _LOCK_BYTES)
            except OSError:
                pass  # Already unlocked or file closed

    except ImportError:
        # Neither fcntl nor msvcrt available (non-CPython on Windows?).
        # File operations proceed without locking — acceptably rare scenario.
        def flock_exclusive(f: IO[Any]) -> None:
            """Acquire an exclusive lock (no-op: no locking primitives available)."""
            pass

        def flock_shared(f: IO[Any]) -> None:
            """Acquire a shared lock (no-op: no locking primitives available)."""
            pass

        def funlock(f: IO[Any]) -> None:
            """Release a lock (no-op: no locking primitives available)."""
            pass


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------


def read_last_n_lines(
    filepath: Path, n: int, max_bytes: int | None = None
) -> list[str]:
    """Read the last *n* lines of a file, optionally bounded by total bytes.

    Uses ``collections.deque(maxlen=n)`` to avoid loading the entire file
    into memory -- only the last *n* lines are retained.  See ARC-014.

    When *max_bytes* is given, oldest lines are dropped until the retained
    tail totals at most *max_bytes* (the most recent line is always kept).
    This bounds transcripts whose few lines are individually huge -- e.g.
    codex subagent rollouts whose 300-ish lines can total megabytes because
    each line carries large tool outputs -- so downstream cleaning/chunking
    does not explode.  See the ``transcript_tail_bytes`` summarizer config.

    SEC-111: when *max_bytes* is set and the file is larger than it, the
    reader reads the trailing *max_bytes* window via a bounded binary
    ``read()`` (not a full-file ``deque``). A single newline-free multi-MB
    line otherwise drags the whole file into memory before the byte cap is
    enforced; reading only the last ``max_bytes`` bytes bounds both the
    iteration window and the return size. The partial first line of the
    window (likely cut mid-character) is dropped; if the window contains
    no newline, the whole window is one logical "line" and is kept as the
    most-recent line.

    Args:
        filepath: Path to the file.
        n: Number of trailing lines to return.
        max_bytes: Optional ceiling on total bytes of returned lines.

    Returns:
        A list of the last n lines (or fewer if the file is shorter, or if
        truncated to fit *max_bytes*).
    """
    from collections import deque

    # SEC-111: bounded binary read of the trailing max_bytes when the file
    # is larger than the budget. Avoids loading a single newline-free
    # multi-MB line into the deque.
    if max_bytes and max_bytes > 0:
        try:
            file_size = filepath.stat().st_size
        except OSError:
            return []
        if file_size > max_bytes:
            try:
                with open(filepath, "rb") as bf:
                    bf.seek(file_size - max_bytes)
                    window = bf.read(max_bytes)
            except OSError:
                return []
            text = window.decode("utf-8", errors="replace")
            # Drop the partial first line (the seek landed mid-line). If the
            # window has no newline, keep the whole window as one line so
            # the most-recent line is preserved. If dropping the partial
            # first line leaves nothing (the window landed such that the
            # only newline is at the very end), fall back to the partial
            # first line as a truncated most-recent line — never return
            # empty when the file has content.
            nl = text.find("\n")
            if nl >= 0:
                rest = text[nl + 1 :]
                if rest:
                    text = rest
                # else: keep the partial first line as the truncated tail.
            tail_lines = text.splitlines(keepends=True)
            # Apply the line-count cap.
            if len(tail_lines) > n:
                tail_lines = tail_lines[-n:]
            # Drop oldest until we fit the byte budget; keep most-recent.
            sizes = [len(ln.encode("utf-8", "replace")) for ln in tail_lines]
            total = sum(sizes)
            start = 0
            while len(tail_lines) - start > 1 and total > max_bytes:
                total -= sizes[start]
                start += 1
            return tail_lines[start:]

    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            tail = deque(f, maxlen=n)
    except (OSError, UnicodeDecodeError):
        return []

    lines = list(tail)
    return lines


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write *content* to *path* atomically via a sibling tmp file + ``Path.replace``.

    The tmp file lives in *path*'s own directory so the final ``Path.replace``
    is a same-filesystem rename -- atomic on POSIX and safe against readers
    observing a half-written file.  When *path* already exists, its
    permission bits are copied onto the tmp file before the replace so an
    existing ``chmod`` is not silently reset.  The tmp file is removed if
    either the write or the replace fails.

    Args:
        path: Destination file path.
        content: Text content to write.
        encoding: Text encoding (default: utf-8).
    """
    existing_mode: int | None = None
    try:
        existing_mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        pass

    tmp = path.parent / (path.name + ".tmp")
    try:
        tmp.write_text(content, encoding=encoding)
        if existing_mode is not None:
            os.chmod(tmp, existing_mode)
        tmp.replace(path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


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

    # SEC-008: Create pending_summaries.jsonl with mode 0o600 (owner read/write
    # only) so session metadata is not world-readable.  os.open sets the mode
    # atomically on first creation; existing files retain their current mode.
    # SEC-013 / SEC-010 (Windows): fcntl is unavailable on Windows so
    # flock_exclusive() is a no-op; concurrent writes may race.  The dedup
    # check below provides best-effort guard.  msvcrt.locking() is not
    # used here because it locks byte ranges (not the whole file) and would
    # require careful size tracking — too complex for stdlib-only code.
    dropped_after_retries = True  # set False on a successful write/dedup hit
    try:
        # migrate_pending_paths() rewrites the queue via tmp + atomic replace
        # while holding the same flock. If that replace happens while we are
        # blocked on the lock, our handle points at the unlinked old inode and
        # the write would be silently lost — re-open until the locked handle
        # matches the path on disk.
        for _attempt in range(5):
            fd = os.open(str(pending_path), os.O_CREAT | os.O_RDWR, 0o600)
            with open(fd, "r+", encoding="utf-8") as f:
                flock_exclusive(f)
                try:
                    try:
                        if os.fstat(f.fileno()).st_ino != os.stat(pending_path).st_ino:
                            continue  # File was replaced; retry on the new inode
                    except OSError:
                        continue
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
                        dropped_after_retries = False
                        return  # Already queued
                    f.seek(0, 2)
                    f.write(json.dumps(entry) + "\n")
                    dropped_after_retries = False
                    return
                finally:
                    funlock(f)
    except OSError:
        pass
    # ARC-048(b): the entry is silently lost when 5 inode-retry attempts all
    # see a replaced queue (or the OSError path swallowed the write). Surface
    # it via write_hook_event so vault-stats --hooks N can show the drop and
    # the user can tell a missing session from a transient bug — without this
    # the queue can shed entries with zero observable signal anywhere.
    if dropped_after_retries:
        try:
            from vault_hooks import write_hook_event  # noqa: PLC0415

            write_hook_event(
                hook="PendingQueueDrop",
                project=str(project),
                duration_ms=0.0,
                vault=vault,
                action="append_to_pending_dropped",
                detail=(
                    f"session {session_id} dropped after 5 inode-retry attempts "
                    f"(concurrent rewrite race); queue entry lost"
                ),
            )
        except Exception:  # noqa: BLE001 — never raise on a best-effort log
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

    # Hold the same exclusive lock append_to_pending() takes — on the REAL
    # pending file — for the entire read → transform → replace sequence, so a
    # concurrently-queued session cannot be dropped by the rewrite.
    try:
        fd = os.open(str(pending_path), os.O_RDWR)
    except OSError:
        return 0
    fixed = 0
    with open(fd, "r+", encoding="utf-8") as fh:
        flock_exclusive(fh)
        try:
            entries: list[dict] = []
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
            for entry in entries:
                stored = entry.get("transcript_path", "")
                if not stored:
                    continue
                stored_path = Path(stored)
                if stored_path.exists():
                    continue

                candidates: list[Path] = []

                # Claude Code fallback: old entries lacked the "agent-" prefix.
                candidates.append(
                    stored_path.parent / f"agent-{stored_path.stem}.jsonl"
                )

                # pi fallback: support both historical location spellings.
                stored_str = str(stored_path)
                if "/.pi/agent/sessions/" in stored_str:
                    candidates.append(
                        Path(
                            stored_str.replace(
                                "/.pi/agent/sessions/", "/.pi/agent-sessions/"
                            )
                        )
                    )
                if "/.pi/agent-sessions/" in stored_str:
                    candidates.append(
                        Path(
                            stored_str.replace(
                                "/.pi/agent-sessions/", "/.pi/agent/sessions/"
                            )
                        )
                    )

                repaired = next(
                    (candidate for candidate in candidates if candidate.exists()), None
                )
                if repaired is not None:
                    if not dry_run:
                        entry["transcript_path"] = str(repaired)
                    fixed += 1
            if fixed and not dry_run:
                # Write via tmp + atomic replace (mode 0o600 like the queue
                # itself) while still holding the lock on the original file.
                tmp = pending_path.with_suffix(".jsonl.tmp")
                tmp_fd = os.open(str(tmp), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
                with open(tmp_fd, "w", encoding="utf-8") as out:
                    for entry in entries:
                        out.write(json.dumps(entry) + "\n")
                tmp.replace(pending_path)
        finally:
            funlock(fh)
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
        paths: Specific paths to stage and commit. If None, stages and commits all
            changes (``git add -A``).

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
            # The vault-root config.yaml may contain secrets (e.g.
            # ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN under anthropic_env),
            # so auto-commits must never stage it — it would end up on
            # remotes via the documented multi-machine git sync.  Callers
            # that pass explicit paths are honored unchanged.
            add_args = ["git", "add", "-A", "--", ".", ":(exclude)config.yaml"]

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
        if paths:
            # Scope the commit as well as the preceding add. A vault can already
            # contain staged work from another session, which must remain staged.
            commit_args = [
                "git",
                "commit",
                "--only",
                "-m",
                message,
                "--",
                *[str(p) for p in paths],
            ]
        else:
            commit_args = ["git", "commit", "-m", message]

        result = subprocess.run(
            commit_args,
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

    # SEC-127: atomic write so a half-written daily note is never observable
    # by session_start_hook at the next session start.
    atomic_write_text(daily_path, content)
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

    # SEC-127: atomic write so a concurrent session_start_hook read never sees
    # a half-appended Sessions section.
    atomic_write_text(daily_path, updated)


# ---------------------------------------------------------------------------
# Vault directory management
# ---------------------------------------------------------------------------


def ensure_vault_dirs(vault: Path | None = None) -> None:
    """Create any missing vault directories.

    SEC-114: the vault root holds ``embeddings.db`` (37 MB+ of indexed note
    text) plus ``pending_summaries.jsonl``, ``dead_letters.jsonl``,
    ``hook_events.log``, and ``config.yaml`` — all 0644 by default and
    world-readable under a default ``umask 022``. ``mkdir(mode=...)`` is
    ignored when the dir exists, so a one-time ``mkdir -m 700`` on the root
    is far cheaper than chmod-ing thousands of notes, and closes the same
    confidentiality class as SEC-007/SEC-110 by restricting the whole root.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().
    """
    vault = vault or resolve_vault()
    vault.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(vault, 0o700)
    except OSError:
        pass
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
