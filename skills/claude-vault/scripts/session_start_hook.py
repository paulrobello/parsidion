#!/usr/bin/env python3
"""Claude Code SessionStart hook that loads relevant vault context.

Reads JSON from stdin with session info, searches the vault for project-specific
and recent notes, and outputs additionalContext as JSON to stdout.

Optional --ai flag uses claude haiku to intelligently select the most
relevant notes rather than relying on recency and project tags alone.
Note: when --ai is used, increase the hook timeout in settings.json to at
least 30000ms to allow time for the AI call to complete.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import traceback
from datetime import date, datetime
from pathlib import Path

# These scripts are not a proper package — sys.path.insert is intentional so
# each script can run standalone via ``uv run`` or ``python`` without requiring
# pip install or editable installs.  See ARC-009 in AUDIT.md.
sys.path.insert(0, str(Path(__file__).parent))

import vault_common  # noqa: E402

_DEFAULT_AI_MODEL = "claude-haiku-4-5-20251001"
_DEFAULT_AI_TIMEOUT = 25  # seconds; hook timeout in settings.json should be >= 30000ms
_DEFAULT_MAX_CHARS = 4000
_DEBUG_FILE = Path(tempfile.gettempdir()) / "claude-vault-session-start-debug.log"
_VAULT_SEARCH_SCRIPT_NAME: str = "vault_search.py"
_SEMANTIC_TOP_N: int = 5
_SEMANTIC_TIMEOUT: int = 10  # seconds


def _build_candidates(project_name: str) -> list[Path]:
    """Collect candidate vault notes for AI selection.

    Returns project-specific notes first, then all other notes sorted by
    most recently modified.

    Args:
        project_name: The current project name (used to prioritize notes).

    Returns:
        Ordered list of note paths; project notes first, then others by mtime.
    """
    all_notes = vault_common.all_vault_notes()
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
) -> list[Path]:
    """Run vault_search.py as a subprocess and return matching note paths.

    Returns an empty list if the script doesn't exist, the DB is missing,
    the subprocess times out, or any other error occurs.

    Args:
        query: Search query string.
        top: Number of results to request.
        vault_search_script: Path to vault_search.py.

    Returns:
        List of note Paths from the semantic search results.
    """
    import json as _json

    if not vault_search_script.exists():
        return []

    db_path = vault_common.get_embeddings_db_path()
    if not db_path.exists():
        return []

    try:
        result = subprocess.run(
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
            capture_output=True,
            text=True,
            timeout=_SEMANTIC_TIMEOUT,
            env=vault_common.env_without_claudecode(),
        )
        if result.returncode != 0:
            return []
        items: list[dict[str, object]] = _json.loads(result.stdout)
        return [Path(str(item["path"])) for item in items]
    except (
        subprocess.TimeoutExpired,
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
    model: str,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> str:
    """Use claude haiku to select the most relevant notes for session context.

    Runs ``claude -p`` with CLAUDECODE unset so it can be called from within
    an active Claude Code session without triggering the nesting guard.

    Args:
        project_name: The current project name.
        cwd: The current working directory.
        candidate_notes: Ordered list of candidate note paths (project-first).
        model: The claude model ID to use.
        max_chars: Maximum characters for the output context block.

    Returns:
        Formatted context string chosen by the AI, or empty string on failure.
    """
    # Build the candidate block, capped so the prompt stays manageable
    candidate_parts: list[str] = []
    char_budget = 8000

    for note_path in candidate_notes:
        try:
            rel = note_path.relative_to(vault_common.VAULT_ROOT)
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
    output_limit = max_chars - 500  # reserve headroom for the header

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

    try:
        result = subprocess.run(
            [
                "claude",
                "-p",
                prompt,
                "--model",
                model,
                "--no-session-persistence",
            ],
            capture_output=True,
            text=True,
            timeout=vault_common.get_config(
                "session_start_hook", "ai_timeout", _DEFAULT_AI_TIMEOUT
            ),
            env=vault_common.env_without_claudecode(),
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            if output:
                return output
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return ""


def build_compact_index(notes: list[Path], max_chars: int = 2000) -> str:
    """Build a compact one-line-per-note index: title [tags] (folder).

    Much smaller than build_context_block — use when vault is large or
    token budget is tight. Full note content is available via the claude-vault skill.

    Args:
        notes: List of note paths to include.
        max_chars: Maximum total characters before truncating with a count line.

    Returns:
        A compact index string, or empty string if notes is empty.
    """
    lines: list[str] = []
    total = 0
    for path in notes:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = vault_common.parse_frontmatter(content)
        # Extract title from first heading or filename
        title = path.stem.replace("-", " ").title()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("# ") and not stripped.startswith("## "):
                title = stripped[2:].strip()
                break
        tags = fm.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        tag_str = " ".join(f"`{t}`" for t in tags) if tags else ""
        folder = (
            path.parent.name
            if path.parent != vault_common.VAULT_ROOT
            else "root"
        )
        entry = (
            f"- [[{path.stem}]] ({folder})"
            + (" — " + tag_str if tag_str else "")
        )
        total += len(entry) + 1
        if total > max_chars:
            lines.append(
                f"- ... ({len(notes) - len(lines)} more notes, "
                "use claude-vault skill to browse)"
            )
            break
        lines.append(entry)

    if not lines:
        return ""

    header = (
        "**Available vault notes** (compact index — "
        "use `claude-vault` skill to load full content):\n"
    )
    return header + "\n".join(lines)


def build_session_context(
    cwd: str,
    ai_model: str | None = None,
    max_chars: int = _DEFAULT_MAX_CHARS,
    verbose_mode: bool = False,
) -> str:
    """Build a context string from vault notes relevant to the current session.

    Args:
        cwd: The current working directory from the session info.
        ai_model: When set, use this claude model to select the most relevant
            notes. Falls back to standard behaviour on failure.
        max_chars: Maximum total characters for the context output (default: 4000).
        verbose_mode: When True, inject full note summaries instead of the default
            compact one-line-per-note index. Ignored when *ai_model* is set (AI
            mode always uses full summaries). Defaults to False.

    Returns:
        A formatted context string capped at *max_chars* with project and recent notes.
    """
    project_name: str = vault_common.get_project_name(cwd)
    today_str: str = date.today().isoformat()

    # Ensure vault directories exist and create today's daily note
    vault_common.ensure_vault_dirs()
    vault_common.create_daily_note_if_missing()

    header: str = f"# Vault Context for {project_name}\n**Date:** {today_str}\n\n"

    if ai_model:
        candidates = _build_candidates(project_name)
        ai_context = _select_context_with_ai(
            project_name, cwd, candidates, ai_model, max_chars
        )
        if ai_context:
            return header + ai_context
        # AI failed — fall through to standard behaviour

    # Standard behaviour: project notes + recent notes + today's daily note
    daily_path: Path = vault_common.create_daily_note_if_missing()
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
        db_path = vault_common.get_embeddings_db_path()
        if db_path.exists():
            vault_search_script = Path(__file__).parent / _VAULT_SEARCH_SCRIPT_NAME
            semantic_notes = _run_semantic_search(
                project_name, _SEMANTIC_TOP_N, vault_search_script
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

    if not all_notes:
        return header + "_No relevant vault notes found._"

    # Build context block from collected notes, reserving space for the header
    max_body_chars: int = max_chars - len(header)
    if not verbose_mode:
        context_body: str = build_compact_index(all_notes, max_chars=max_body_chars)
    else:
        context_body = vault_common.build_context_block(
            all_notes, max_chars=max_body_chars
        )

    if not context_body:
        return header + "_No relevant vault notes found._"

    return header + context_body


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
        # Use os.open with 0o600 permissions so the debug log is not world-readable
        fd = os.open(
            _DEBUG_FILE,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            with open(fd, "a", encoding="utf-8", closefd=True) as f:
                f.write(entry)
        except Exception:
            # fd ownership transferred to open(); only close manually on open() failure
            pass
    except OSError:
        pass  # debug logging is best-effort


def main() -> None:
    """Entry point: read session JSON from stdin, output context JSON to stdout."""
    parser = argparse.ArgumentParser(
        description="Claude Code SessionStart hook — loads relevant vault context.",
    )
    parser.add_argument(
        "--ai",
        metavar="MODEL",
        nargs="?",
        const=_DEFAULT_AI_MODEL,
        default=None,
        help=(
            "Use the specified claude model to intelligently select the most relevant "
            f"vault notes (default model: {_DEFAULT_AI_MODEL}). "
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
        action="store_true",
        default=None,
        help=(
            f"Append injected context and metadata to {_DEBUG_FILE} "
            "for quality evaluation."
        ),
    )
    args = parser.parse_args()

    try:
        input_data: dict = json.loads(sys.stdin.read())
        cwd: str = input_data.get("cwd", "")

        if not cwd:
            cwd = str(Path.cwd())

        # Resolve options: defaults → config → CLI args
        ai_model: str | None = args.ai
        if ai_model is None:
            ai_model = vault_common.get_config("session_start_hook", "ai_model")
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
        debug: bool = (
            args.debug
            if args.debug is not None
            else vault_common.get_config("session_start_hook", "debug", False)
        )

        start_time = datetime.now()
        context: str = build_session_context(
            cwd, ai_model=ai_model, max_chars=max_chars, verbose_mode=verbose_mode
        )
        elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000

        if debug:
            project_name = vault_common.get_project_name(cwd)
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

    except Exception:
        traceback.print_exc(file=sys.stderr)
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
