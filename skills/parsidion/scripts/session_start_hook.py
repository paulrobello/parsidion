#!/usr/bin/env python3
"""Claude Code SessionStart hook that loads relevant vault context.

Reads JSON from stdin with session info, searches the vault for project-specific
and recent notes, and outputs additionalContext as JSON to stdout.

Optional --ai flag uses the configured AI backend to intelligently select the
most relevant notes rather than relying on recency and project tags alone.
Note: when --ai is used, increase the hook timeout in settings.json to at
least 30000ms to allow time for the AI call to complete.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import traceback
from datetime import date, datetime
from io import TextIOWrapper
from pathlib import Path

import ai_backend
import vault_common

_DEFAULT_AI_MODEL: str = vault_common.get_config(
    "defaults", "haiku_model", "claude-haiku-4-5-20251001"
)
_DEFAULT_AI_TIMEOUT = 25  # seconds; hook timeout in settings.json should be >= 30000ms
_BACKEND_DEFAULT_AI_MODEL = "__parsidion_backend_default__"
_DEFAULT_MAX_CHARS = 4000
_DEBUG_FILE = vault_common.secure_log_dir() / "parsidion-session-start-debug.log"
_VAULT_SEARCH_SCRIPT_NAME: str = "vault_search.py"
_SEMANTIC_TOP_N: int = 5
_SEMANTIC_TIMEOUT: int = 10  # seconds
_DEFAULT_AI_SINGLE_FLIGHT = True
_DEFAULT_AI_COOLDOWN_SECONDS = 30
_AI_LOCK_FILENAME = ".session_start_ai.lock"
_AI_STAMP_FILENAME = ".session_start_ai.last_run"
# Characters reserved for the vault-context header injected before the AI-selected
# note content.  Ensures the final output never slightly exceeds max_chars.
_AI_CONTEXT_HEADER_RESERVE: int = 500

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


def _build_candidates(project_name: str, vault_path: Path) -> list[Path]:
    """Collect candidate vault notes for AI selection.

    Returns project-specific notes first, then all other notes sorted by
    most recently modified.

    ARC-011: Uses ``query_note_index()`` (SQLite) first for fast project
    matching without reading every file.  Falls back to the full filesystem
    walk when the database is absent or the table is missing.

    Args:
        project_name: The current project name (used to prioritize notes).
        vault_path: The vault root path.

    Returns:
        Ordered list of note paths; project notes first, then others by mtime.
    """
    # ARC-011: Try SQLite first for project notes (O(1) index lookup)
    db_project_notes = vault_common.query_note_index(project=project_name, limit=500)
    db_recent_notes = vault_common.query_note_index(recent_days=30, limit=500)

    if db_project_notes is not None and db_recent_notes is not None:
        # SQLite path: fast, no file reads needed for candidate list
        project_set = set(str(p) for p in db_project_notes)
        other_notes = [p for p in db_recent_notes if str(p) not in project_set]
        return db_project_notes + other_notes

    # Fallback: full filesystem walk (when embeddings.db is absent)
    all_notes = vault_common.all_vault_notes(vault=vault_path)
    project_lower = project_name.lower()

    project_notes: list[Path] = []
    other_notes_with_mtime: list[tuple[float, Path]] = []

    for note_path in all_notes:
        try:
            content = note_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = vault_common.parse_frontmatter(content)
        proj_val = fm.get("project")
        if isinstance(proj_val, str) and proj_val.lower() == project_lower:
            project_notes.append(note_path)
        else:
            try:
                mtime = note_path.stat().st_mtime
            except OSError:
                mtime = 0.0
            other_notes_with_mtime.append((mtime, note_path))

    other_notes_with_mtime.sort(key=lambda x: x[0], reverse=True)
    return project_notes + [p for _, p in other_notes_with_mtime]


def _run_semantic_search(
    query: str,
    top: int,
    vault_search_script: Path,
    vault_path: Path,
) -> list[Path]:
    """Run vault_search.py as a subprocess and return matching note paths.

    Returns an empty list if the script doesn't exist, the DB is missing,
    the subprocess times out, or any other error occurs.

    Args:
        query: Search query string.
        top: Number of results to request.
        vault_search_script: Path to vault_search.py.
        vault_path: The vault root path.

    Returns:
        List of note Paths from the semantic search results.
    """
    import json as _json

    if not vault_search_script.exists():
        return []

    db_path = vault_common.get_embeddings_db_path(vault=vault_path)
    if not db_path.exists():
        return []

    try:
        # Use Popen + start_new_session so the entire process group (uv + its
        # Python child) can be killed together on timeout.  subprocess.run with
        # timeout only kills the direct child (uv), leaving the grandchild
        # (vault_search.py Python) holding the stdout pipe open, which causes
        # communicate() to block indefinitely and turns session_start_hook.py
        # into a zombie process.
        proc = subprocess.Popen(
            [
                "uv",
                "run",
                "--no-project",
                str(vault_search_script),
                query,
                "--top",
                str(top),
                "--json",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # new process group — enables killpg
            env=vault_common.env_without_claudecode(),
        )
        try:
            stdout, _ = proc.communicate(timeout=_SEMANTIC_TIMEOUT)
        except subprocess.TimeoutExpired:
            # Kill the entire process group (uv + vault_search.py Python child)
            # so the stdout pipe is closed and communicate() returns immediately.
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                proc.kill()
            proc.wait()
            return []
        if proc.returncode != 0:
            return []
        items: list[dict[str, object]] = _json.loads(stdout)
        return [Path(str(item["path"])) for item in items]
    except (
        FileNotFoundError,
        OSError,
        _json.JSONDecodeError,
        KeyError,
        ValueError,
    ):
        return []


def _select_context_with_ai(
    project_name: str,
    cwd: str,
    candidate_notes: list[Path],
    model: str | None,
    max_chars: int = _DEFAULT_MAX_CHARS,
    vault_path: Path | None = None,
) -> str:
    """Use the configured AI backend to select relevant notes for session context.

    Backend execution is delegated to ai_backend.run_ai_prompt so Claude and
    Codex model defaults are resolved consistently.

    Args:
        project_name: The current project name.
        cwd: The current working directory.
        candidate_notes: Ordered list of candidate note paths (project-first).
        model: Explicit model ID to use, or None for the backend default.
        max_chars: Maximum characters for the output context block.
        vault_path: The vault root path.

    Returns:
        Formatted context string chosen by the AI, or empty string on failure.
    """
    if vault_path is None:
        vault_path = vault_common.resolve_vault(cwd=cwd)

    lock_handle: TextIOWrapper | None = None
    if vault_common.get_config(
        "session_start_hook",
        "ai_single_flight",
        _DEFAULT_AI_SINGLE_FLIGHT,
    ):
        lock_handle = _try_acquire_ai_lock(vault_path)
        if lock_handle is None:
            return ""

    if _is_ai_cooldown_active(vault_path):
        _release_ai_lock(lock_handle)
        return ""

    try:
        # Build the candidate block, capped so the prompt stays manageable.
        candidate_parts: list[str] = []
        char_budget = 8000

        for note_path in candidate_notes:
            try:
                rel = note_path.relative_to(vault_path)
            except ValueError:
                rel = Path(note_path.parent.name) / note_path.name

            summary = vault_common.read_note_summary(note_path, max_lines=6)
            if not summary:
                continue

            entry = f"### {rel}\n{summary}\n\n"
            if sum(len(p) for p in candidate_parts) + len(entry) > char_budget:
                break
            candidate_parts.append(entry)

        if not candidate_parts:
            return ""

        candidates_text = "".join(candidate_parts)
        output_limit = (
            max_chars - _AI_CONTEXT_HEADER_RESERVE
        )  # reserve headroom for the header

        prompt = (
            "You are building context for a Claude Code session.\n\n"
            f"Project: {project_name}\n"
            f"Working directory: {cwd}\n\n"
            "Below are vault notes with titles and summaries. Select and format the most "
            f"relevant ones as session context. Keep total output under {output_limit} characters.\n\n"
            "Prioritize notes that are:\n"
            f"- Specific to the '{project_name}' project\n"
            "- Recent patterns, debugging insights, or architectural decisions\n"
            "- Likely useful at the start of a work session\n\n"
            f"Candidate notes:\n{candidates_text}\n"
            "Format selected notes exactly as:\n"
            "### Note Title (path/to/note.md)\n"
            "Key point 1\n"
            "Key point 2\n\n"
            "Only include genuinely relevant notes. Output nothing but the formatted context blocks."
        )

        output = ai_backend.run_ai_prompt(
            prompt,
            model=model,
            model_tier="small",
            timeout=vault_common.get_config(
                "session_start_hook", "ai_timeout", _DEFAULT_AI_TIMEOUT
            ),
            cwd=cwd,
            purpose="session-start-selection",
            vault=vault_path,
        )
        if output:
            _write_ai_cooldown_stamp(vault_path)
            return output.strip()
    except (FileNotFoundError, OSError):
        pass
    finally:
        _release_ai_lock(lock_handle)

    return ""


def _ai_lock_path(vault_path: Path) -> Path:
    """Return the per-vault lock file path for AI SessionStart selection."""
    return vault_path / _AI_LOCK_FILENAME


def _try_acquire_ai_lock(vault_path: Path) -> TextIOWrapper | None:
    """Acquire the per-vault SessionStart AI lock, or return None if busy."""
    lock_path = _ai_lock_path(vault_path)
    lock_file = open(lock_path, "a+", encoding="utf-8")
    if fcntl is None:  # pragma: no cover - Windows fallback
        return lock_file
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(f"{os.getpid()}\n")
    lock_file.flush()
    return lock_file


def _ai_stamp_path(vault_path: Path) -> Path:
    """Return the per-vault cooldown stamp path for SessionStart AI."""
    return vault_path / _AI_STAMP_FILENAME


def _is_ai_cooldown_active(vault_path: Path) -> bool:
    """Return True when AI SessionStart ran too recently for this vault."""
    cooldown_seconds = vault_common.get_config(
        "session_start_hook",
        "ai_cooldown_seconds",
        _DEFAULT_AI_COOLDOWN_SECONDS,
    )
    if cooldown_seconds <= 0:
        return False
    stamp_path = _ai_stamp_path(vault_path)
    try:
        age_seconds = datetime.now().timestamp() - stamp_path.stat().st_mtime
    except OSError:
        return False
    return age_seconds < cooldown_seconds


def _write_ai_cooldown_stamp(vault_path: Path) -> None:
    """Update the per-vault cooldown stamp after a successful AI selection."""
    stamp_path = _ai_stamp_path(vault_path)
    try:
        stamp_path.write_text(f"{datetime.now().isoformat()}\n", encoding="utf-8")
    except OSError:
        pass


def _release_ai_lock(lock_file: TextIOWrapper | None) -> None:
    """Release and close a previously-acquired SessionStart AI lock."""
    if lock_file is None:
        return
    try:
        if fcntl is not None:  # pragma: no branch
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    """Terminate a process group and wait for it to fully exit."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        proc.kill()
    proc.wait()


# Canonical implementation lives in vault_common; re-export for backwards compatibility.
build_compact_index = vault_common.build_compact_index


def _rank_by_usefulness(notes: list[Path]) -> list[Path]:
    """Re-rank *notes* by usefulness score (adaptive context #17).

    Notes with a positive hit/miss ratio float to the top; notes that were
    repeatedly injected but never referenced sink toward the bottom.  Notes
    with no recorded stats keep their original relative order (stable sort).

    Args:
        notes: Candidate note paths in their current order.

    Returns:
        Re-ranked list of the same paths.
    """
    scores = vault_common.load_usefulness_scores()

    def _score(path: Path) -> float:
        entry = scores.get(path.stem)
        if not entry:
            return 0.5  # Neutral score for new notes
        hits: int = entry.get("hits", 0)
        misses: int = entry.get("misses", 0)
        total = hits + misses
        if total == 0:
            return 0.5
        # Simple Laplace-smoothed ratio: (hits+1) / (total+2)
        return (hits + 1) / (total + 2)

    return sorted(notes, key=_score, reverse=True)


def _build_pending_notice(vault_path: Path) -> str:
    """Return a one-line warning if pending_summaries.jsonl has entries.

    Args:
        vault_path: The vault root path.

    Returns:
        Warning string like ``⚠ 7 sessions pending summarization (run summarize_sessions.py)``
        or empty string if queue is empty or file is absent.
    """
    pending_path = vault_path / "pending_summaries.jsonl"
    if not pending_path.exists():
        return ""
    try:
        with open(pending_path, encoding="utf-8") as f:
            count = sum(1 for line in f if line.strip())
    except OSError:
        return ""
    if count == 0:
        return ""
    return f"⚠ {count} session{'s' if count != 1 else ''} pending summarization (run summarize_sessions.py)"


def _build_delta_section(
    project_name: str, last_seen_ts: str | None, vault_path: Path
) -> str:
    """Build a 'Since last time' section from notes newer than *last_seen_ts*.

    Args:
        project_name: Current project name (used to label the section).
        last_seen_ts: ISO 8601 timestamp of the last session, or None.
        vault_path: The vault root path.

    Returns:
        A formatted section string, or empty string if nothing new.
    """
    if last_seen_ts is None:
        return ""
    try:
        last_seen_dt = datetime.fromisoformat(last_seen_ts)
    except ValueError:
        return ""

    cutoff_ts = last_seen_dt.timestamp()
    new_notes: list[tuple[float, str, str]] = []  # (mtime, stem, folder)

    for note_path in vault_common.all_vault_notes(vault=vault_path):
        try:
            mtime = note_path.stat().st_mtime
        except OSError:
            continue
        if mtime > cutoff_ts:
            try:
                rel = note_path.relative_to(vault_path)
                folder = str(rel.parent) if str(rel.parent) != "." else "root"
            except ValueError:
                folder = note_path.parent.name
            new_notes.append((mtime, note_path.stem, folder))

    if not new_notes:
        return ""

    # Sort by mtime descending, keep top 10
    new_notes.sort(key=lambda x: -x[0])
    new_notes = new_notes[:10]

    # Calculate human-readable age
    now = datetime.now()
    age_seconds = (now - last_seen_dt).total_seconds()
    if age_seconds < 3600:
        age_str = f"{int(age_seconds / 60)} minutes ago"
    elif age_seconds < 86400:
        age_str = f"{int(age_seconds / 3600)} hours ago"
    else:
        age_str = f"{int(age_seconds / 86400)} days ago"

    lines = [f"Since last session in {project_name} ({age_str}):"]
    for _, stem, folder in new_notes:
        lines.append(f"  NEW/UPDATED: {stem} ({folder})")

    return "\n".join(lines)


def build_session_context(
    cwd: str,
    ai_model: str | None = None,
    max_chars: int = _DEFAULT_MAX_CHARS,
    verbose_mode: bool = False,
    ai_enabled: bool = False,
) -> tuple[str, int]:
    """Build a context string from vault notes relevant to the current session.

    Args:
        cwd: The current working directory from the session info.
        ai_model: Explicit model override for AI note selection. When None and
            ai_enabled is true, the configured backend resolves its default.
            Falls back to standard behaviour on failure.
        max_chars: Maximum total characters for the context output (default: 4000).
        verbose_mode: When True, inject full note summaries instead of the default
            compact one-line-per-note index. Ignored when AI mode is enabled (AI
            mode always uses full summaries). Defaults to False.
        ai_enabled: Enables AI selection even when ai_model is None, allowing the
            backend to resolve its tier default model.

    Returns:
        Tuple of (formatted context string, number of notes injected).
    """
    project_name: str = vault_common.get_project_name(cwd)
    today_str: str = date.today().isoformat()

    # Resolve vault path from cwd (supports multi-vault)
    vault_path: Path = vault_common.resolve_vault(cwd=cwd)

    # Ensure vault directories exist and create today's daily note
    vault_common.ensure_vault_dirs(vault=vault_path)

    header: str = f"# Vault Context for {project_name}\n**Date:** {today_str}\n\n"

    # --- Pending queue warning (#3) ---
    pending_notice = _build_pending_notice(vault_path)

    # --- Cross-session delta (#10) ---
    delta_section = ""
    if vault_common.get_config("session_start_hook", "track_delta", True):
        last_seen_map = vault_common.load_last_seen(vault=vault_path)
        last_seen_ts = last_seen_map.get(project_name)
        delta_section = _build_delta_section(project_name, last_seen_ts, vault_path)
    # Update last-seen timestamp for this project
    vault_common.save_last_seen(project_name, vault=vault_path)

    notes_injected = 0

    if ai_enabled or ai_model is not None:
        candidates = _build_candidates(project_name, vault_path)
        ai_context = _select_context_with_ai(
            project_name, cwd, candidates, ai_model, max_chars, vault_path=vault_path
        )
        if ai_context:
            notes_injected = ai_context.count("\n### ") + (
                1 if ai_context.startswith("### ") else 0
            )
            context = _assemble_context(
                header, ai_context, pending_notice, delta_section
            )
            return context, notes_injected
        # AI failed — fall through to standard behaviour

    # Standard behaviour: project notes + recent notes + today's daily note
    daily_path: Path = vault_common.today_daily_path(vault=vault_path)
    if not daily_path.exists():
        # Create daily note if missing
        vault_common.ensure_vault_dirs(vault=vault_path)
        from datetime import date as _date

        _month = f"{_date.today().year:04d}-{_date.today().month:02d}"
        daily_dir = vault_path / "Daily" / _month
        daily_dir.mkdir(parents=True, exist_ok=True)
        daily_path.touch()

    project_notes: list[Path] = vault_common.find_notes_by_project(project_name)
    recent_days: int = vault_common.get_config("session_start_hook", "recent_days", 3)
    recent_notes: list[Path] = vault_common.find_recent_notes(days=recent_days)

    # Deduplicate: merge project and recent notes, preserving order
    seen: set[Path] = set()
    all_notes: list[Path] = []

    for note in project_notes:
        resolved: Path = note.resolve()
        if resolved not in seen:
            seen.add(resolved)
            all_notes.append(note)

    for note in recent_notes:
        resolved = note.resolve()
        if resolved not in seen:
            seen.add(resolved)
            all_notes.append(note)

    # Blend semantic search results when embeddings.db is available
    use_embeddings: bool = vault_common.get_config(
        "session_start_hook", "use_embeddings", True
    )
    if use_embeddings:
        db_path = vault_common.get_embeddings_db_path(vault=vault_path)
        if db_path.exists():
            vault_search_script = Path(__file__).parent / _VAULT_SEARCH_SCRIPT_NAME
            semantic_notes = _run_semantic_search(
                project_name, _SEMANTIC_TOP_N, vault_search_script, vault_path
            )
            for note in semantic_notes:
                resolved = note.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    all_notes.append(note)

    # Ensure today's daily note is included
    daily_resolved: Path = daily_path.resolve()
    if daily_resolved not in seen:
        all_notes.append(daily_path)

    # Adaptive context (#17): re-rank notes by usefulness when enabled
    adaptive_enabled: bool = vault_common.get_config(
        "adaptive_context", "enabled", False
    )
    if adaptive_enabled and all_notes:
        all_notes = _rank_by_usefulness(all_notes)

    notes_injected = len(all_notes)

    if not all_notes:
        context = _assemble_context(
            header, "_No relevant vault notes found._", pending_notice, delta_section
        )
        return context, 0

    # Build context block from collected notes, reserving space for the header
    max_body_chars: int = max_chars - len(header)
    if not verbose_mode:
        context_body: str = build_compact_index(all_notes, max_chars=max_body_chars)
    else:
        context_body = vault_common.build_context_block(
            all_notes, max_chars=max_body_chars
        )

    if not context_body:
        context = _assemble_context(
            header, "_No relevant vault notes found._", pending_notice, delta_section
        )
        return context, 0

    # Save injected stems for usefulness tracking
    if adaptive_enabled:
        injected_stems = [p.stem for p in all_notes]
        vault_common.save_injected_notes(project_name, injected_stems)

    context = _assemble_context(header, context_body, pending_notice, delta_section)
    return context, notes_injected


def _assemble_context(
    header: str,
    body: str,
    pending_notice: str,
    delta_section: str,
) -> str:
    """Combine context parts into the final injected string.

    Args:
        header: The vault context header line.
        body: Main note content block.
        pending_notice: Optional pending queue warning.
        delta_section: Optional cross-session delta block.

    Returns:
        Assembled context string.
    """
    parts: list[str] = [header]
    if pending_notice:
        parts.append(pending_notice + "\n\n")
    if delta_section:
        parts.append(delta_section + "\n\n")
    parts.append(body)
    return "".join(parts)


def _write_debug_log(
    context: str,
    cwd: str,
    project_name: str,
    ai_model: str | None,
    max_chars: int,
    elapsed_ms: float,
    verbose_mode: bool = False,
) -> None:
    """Append injection details to the debug log file for quality evaluation.

    Args:
        context: The full context string that was injected.
        cwd: The working directory for this session.
        project_name: The resolved project name.
        ai_model: The AI model used for note selection, or None if standard mode.
        max_chars: The max_chars budget that was configured.
        elapsed_ms: Wall-clock time in milliseconds to build the context.
        verbose_mode: Whether verbose (full summaries) mode was used.
    """
    timestamp = datetime.now().isoformat(timespec="seconds")
    context_chars = len(context)
    context_lines = context.count("\n") + 1 if context else 0
    # Count note sections (### headings) as a proxy for number of notes included
    note_count = context.count("\n### ") + (1 if context.startswith("### ") else 0)
    budget_pct = (context_chars / max_chars * 100) if max_chars > 0 else 0.0
    if ai_model:
        mode = f"ai ({ai_model})"
    elif verbose_mode:
        mode = "verbose"
    else:
        mode = "compact"

    separator = "=" * 80
    entry = (
        f"\n{separator}\n"
        f"Timestamp:    {timestamp}\n"
        f"Project:      {project_name}\n"
        f"CWD:          {cwd}\n"
        f"Mode:         {mode}\n"
        f"Max chars:    {max_chars}\n"
        f"Context size: {context_chars} chars / {context_lines} lines\n"
        f"Budget used:  {budget_pct:.1f}%\n"
        f"Notes found:  {note_count}\n"
        f"Elapsed:      {elapsed_ms:.0f}ms\n"
        f"{separator}\n"
        f"{context}\n"
    )

    try:
        # SEC-008: Use O_NOFOLLOW to prevent a symlink-substitution attack — if an
        # adversary replaced _DEBUG_FILE with a symlink to a sensitive file, O_NOFOLLOW
        # causes the open to fail with ELOOP rather than following the symlink.
        # O_NOFOLLOW is POSIX and available on Linux/macOS; on Windows it is absent
        # so we fall back gracefully (Windows does not support symlinks by default).
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(
            _DEBUG_FILE,
            flags,
            0o600,
        )
        try:
            with open(fd, "a", encoding="utf-8", closefd=True) as f:
                f.write(entry)
        except Exception:  # noqa: BLE001
            # fd ownership transferred to open(); only close manually on open() failure
            pass
    except OSError:
        pass  # debug logging is best-effort


_HOOK_ERROR_LOG = vault_common.secure_log_dir() / "parsidion-hook-errors.log"


def _log_hook_error(hook_name: str) -> None:
    """Append a timestamped traceback entry to the hook error log.

    Called only from the outermost ``except Exception`` handler so that
    unexpected programming errors (regressions, NameErrors, etc.) are
    written to a persistent file rather than disappearing into stderr.
    Best-effort — never raises.

    Args:
        hook_name: Short identifier for the hook (e.g. ``"session_start_hook"``).
    """
    try:
        ts = datetime.now().isoformat(timespec="seconds")
        tb = traceback.format_exc()
        entry = f"[{ts}] {hook_name}\n{tb}\n"
        vault_common.rotate_log_file(_HOOK_ERROR_LOG)
        with open(_HOOK_ERROR_LOG, "a", encoding="utf-8") as fh:
            fh.write(entry)
    except Exception:  # noqa: BLE001 — logging must never raise
        pass


def main() -> None:
    """Entry point: read session JSON from stdin, output context JSON to stdout."""
    if os.environ.get("PARSIDION_INTERNAL"):
        sys.stdout.write("{}")
        return

    parser = argparse.ArgumentParser(
        description="Claude Code SessionStart hook — loads relevant vault context.",
    )
    parser.add_argument(
        "--ai",
        metavar="MODEL",
        nargs="?",
        const=_BACKEND_DEFAULT_AI_MODEL,
        default=None,
        help=(
            "Use the specified model to intelligently select the most relevant "
            "vault notes (no MODEL = configured backend default). "
            "Requires increasing the hook timeout in settings.json to >= 30000ms."
        ),
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=None,
        metavar="N",
        help=f"Maximum characters for injected context (default: {_DEFAULT_MAX_CHARS})",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help=(
            "Inject full note summaries instead of the default compact one-line-per-note "
            "index. Uses significantly more tokens."
        ),
    )
    parser.add_argument(
        "--debug",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            f"Append injected context and metadata to {_DEBUG_FILE} "
            "for quality evaluation. Use --no-debug to force off even if "
            "config.yaml enables it."
        ),
    )
    args = parser.parse_args()

    try:
        input_data: dict = json.loads(sys.stdin.read())
        cwd: str = input_data.get("cwd", "")

        if not cwd:
            cwd = str(Path.cwd())

        # Resolve vault path from cwd (supports multi-vault)
        vault_path: Path = vault_common.resolve_vault(cwd=cwd)

        # Resolve options: defaults → config → CLI args
        ai_model: str | None
        ai_enabled: bool
        if args.ai == _BACKEND_DEFAULT_AI_MODEL:
            ai_model = None
            ai_enabled = True
        elif args.ai is not None:
            ai_model = args.ai
            ai_enabled = True
        else:
            ai_model = vault_common.get_config("session_start_hook", "ai_model")
            ai_enabled = ai_model is not None
        max_chars: int = (
            args.max_chars
            if args.max_chars is not None
            else vault_common.get_config(
                "session_start_hook", "max_chars", _DEFAULT_MAX_CHARS
            )
        )
        verbose_mode: bool = args.verbose or vault_common.get_config(
            "session_start_hook", "verbose_mode", False
        )
        # args.debug is always a bool (BooleanOptionalAction); OR with config so
        # either --debug CLI flag or config.yaml debug:true enables it, while
        # --no-debug explicitly overrides config.
        debug: bool = args.debug or bool(
            vault_common.get_config("session_start_hook", "debug", False)
        )

        # Config validation (#5) — warn on startup for typos
        config_warnings = vault_common.validate_config()
        for warning in config_warnings:
            print(f"[session_start_hook] {warning}", file=sys.stderr)

        start_time = datetime.now()
        context, notes_injected = build_session_context(
            cwd,
            ai_model=ai_model,
            max_chars=max_chars,
            verbose_mode=verbose_mode,
            ai_enabled=ai_enabled,
        )
        elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000

        project_name = vault_common.get_project_name(cwd)

        # Hook event log (#1)
        vault_common.write_hook_event(
            hook="SessionStart",
            project=project_name,
            duration_ms=elapsed_ms,
            notes_injected=notes_injected,
            chars=len(context),
            vault=vault_path,
        )

        if debug:
            _write_debug_log(
                context=context,
                cwd=cwd,
                project_name=project_name,
                ai_model=ai_model,
                max_chars=max_chars,
                elapsed_ms=elapsed_ms,
                verbose_mode=verbose_mode,
            )

        output: dict = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        }

        sys.stdout.write(json.dumps(output))

    except Exception:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        # Log unexpected programming errors to a persistent file so regressions
        # are visible without requiring manual stderr inspection.
        _log_hook_error("session_start_hook")
        # On any error, output valid JSON with empty context so the hook doesn't crash
        fallback: dict = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "",
            }
        }
        sys.stdout.write(json.dumps(fallback))


if __name__ == "__main__":
    main()
