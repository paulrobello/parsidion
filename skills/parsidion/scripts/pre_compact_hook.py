#!/usr/bin/env python3
"""Claude Code PreCompact hook that snapshots working state before compaction.

Reads JSON from stdin with session info, analyzes the last N lines of the
transcript to identify the current task and files being worked on, then
writes a pre-compact snapshot section to today's daily note.
"""

import argparse
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

import vault_common

# Tool names and the input fields that contain file paths.
# Keys are lowercase to support both Claude Code (Read/Write/Edit) and
# pi tool names (read/write/edit/grep/find/ls).
_FILE_TOOLS: dict[str, list[str]] = {
    "read": ["file_path", "path"],
    "write": ["file_path", "path"],
    "edit": ["file_path", "path"],
    "notebookedit": ["notebook_path", "path"],
    "grep": ["path"],
    "find": ["path"],
    "ls": ["path"],
}


# Utility functions imported from vault_common (canonical implementation)
extract_text_from_content = vault_common.extract_text_from_content
read_last_n_lines = vault_common.read_last_n_lines


def extract_user_task(lines: list[str]) -> str:
    """Extract the current task from the most recent user text message.

    Scans lines in reverse to find the latest ``type: "user"`` entry that
    contains a plain text block (not a tool_result). In active sessions the
    tail of a transcript is dominated by tool_result/tool_use pairs; the
    actual typed user message is further back.

    Args:
        lines: Raw JSONL lines from the transcript.

    Returns:
        First line of the most recent user text message (max 200 chars),
        or ``"Unknown task"`` if none is found.
    """
    for raw_line in reversed(lines):
        line = raw_line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        message = entry.get("message")
        content: object = None
        role: str | None = None

        if isinstance(message, dict):
            role_raw = message.get("role")
            if isinstance(role_raw, str):
                role = role_raw
            content = message.get("content")

        if role is None:
            msg_type = entry.get("type")
            if isinstance(msg_type, str) and msg_type in {"user", "assistant"}:
                role = msg_type
                content = entry.get("content")

        if role != "user":
            continue

        # Plain string content
        if isinstance(content, str):
            text = content.strip()
            if len(text) > 10:
                return text.split("\n")[0][:200].strip()
            continue

        # List of content blocks — look for text blocks, skip tool_result
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text = block.get("text", "")
                    if isinstance(text, str) and len(text.strip()) > 10:
                        return text.strip().split("\n")[0][:200].strip()

    return "Unknown task"


def extract_file_paths(lines: list[str], cwd: str | None = None) -> list[str]:
    """Extract file paths from assistant tool call blocks.

    Supports both Claude Code ``tool_use`` blocks and pi ``toolCall`` blocks.

    Args:
        lines: Raw JSONL lines from the transcript.
        cwd: Optional working directory used to resolve relative paths.

    Returns:
        A deduplicated list of file paths found (max 15).
    """
    seen: set[str] = set()
    paths: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        message = entry.get("message")
        content: object = None
        role: str | None = None

        if isinstance(message, dict):
            role_raw = message.get("role")
            if isinstance(role_raw, str):
                role = role_raw
            content = message.get("content")

        if role is None:
            msg_type = entry.get("type")
            if isinstance(msg_type, str) and msg_type in {"user", "assistant"}:
                role = msg_type
                top_level_content = entry.get("content")
                if top_level_content is not None:
                    content = top_level_content

        if role != "assistant" or not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue

            block_type = block.get("type")
            tool_name = ""
            input_data: object = {}

            if block_type == "tool_use":
                name_raw = block.get("name", "")
                if isinstance(name_raw, str):
                    tool_name = name_raw.lower()
                input_data = block.get("input", {})
            elif block_type == "toolCall":
                name_raw = block.get("name", "")
                if isinstance(name_raw, str):
                    tool_name = name_raw.lower()
                input_data = block.get("arguments", {})
            else:
                continue

            if not isinstance(input_data, dict):
                continue

            for field in _FILE_TOOLS.get(tool_name, []):
                path_val = input_data.get(field, "")
                if not isinstance(path_val, str) or not path_val.strip():
                    continue

                normalized = path_val.strip()
                if not os.path.isabs(normalized):
                    if not cwd:
                        continue
                    normalized = str((Path(cwd) / normalized).resolve())

                if normalized not in seen:
                    seen.add(normalized)
                    paths.append(normalized)
                    if len(paths) >= 15:
                        return paths

    return paths


def get_git_context(cwd: str) -> tuple[str | None, list[str]]:
    """Get current git branch and list of uncommitted files.

    Args:
        cwd: The working directory to check.

    Returns:
        Tuple of (branch_name, uncommitted_file_list). Both may be empty/None
        when not in a git repo or git is unavailable.
    """
    import subprocess as _sp

    branch: str | None = None
    uncommitted: list[str] = []

    if not cwd:
        return branch, uncommitted

    try:
        result = _sp.run(
            ["git", "branch", "--show-current"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            branch = result.stdout.strip() or None
    except (OSError, _sp.TimeoutExpired):
        pass

    try:
        result = _sp.run(
            ["git", "status", "--short"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if stripped:
                    uncommitted.append(stripped[:80])
    except (OSError, _sp.TimeoutExpired):
        pass

    return branch, uncommitted


def append_snapshot_to_daily(
    project: str,
    task_summary: str,
    recent_files: list[str],
    cwd: str = "",
    vault_path: Path | None = None,
) -> None:
    """Append a pre-compact snapshot section to today's daily note.

    Args:
        project: The project name.
        task_summary: Brief description of the current task.
        recent_files: List of file paths being worked on.
        cwd: Working directory for git context extraction.
        vault_path: The vault root path.
    """
    if vault_path is None:
        vault_path = vault_common.resolve_vault(cwd=cwd)

    daily_path = vault_common.today_daily_path(vault=vault_path)
    # Ensure the daily note exists
    if not daily_path.exists():
        vault_common.ensure_vault_dirs(vault=vault_path)
        from datetime import date as _date

        _month = f"{_date.today().year:04d}-{_date.today().month:02d}"
        daily_dir = vault_path / "Daily" / _month
        daily_dir.mkdir(parents=True, exist_ok=True)
        daily_path.touch()

    now_time = datetime.now().strftime("%H:%M")

    files_str = ", ".join(recent_files[:10]) if recent_files else "None detected"

    # Git context (#2)
    branch, uncommitted = get_git_context(cwd)
    branch_line = f"- **Branch**: {branch}\n" if branch else ""
    if uncommitted:
        uncommitted_str = ", ".join(uncommitted[:10])
        uncommitted_line = f"- **Uncommitted files**: {uncommitted_str}\n"
    else:
        uncommitted_line = ""

    section = (
        f"\n## Pre-Compact Snapshot ({now_time})\n"
        f"- **Project**: {project}\n"
        f"{branch_line}"
        f"- **Working on**: {task_summary}\n"
        f"{uncommitted_line}"
        f"- **Recent files**: {files_str}\n"
    )

    try:
        existing = daily_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        existing = ""

    updated = existing + section
    daily_path.write_text(updated, encoding="utf-8")


_DEFAULT_LINES = 200

_HOOK_ERROR_LOG = vault_common.secure_log_dir() / "parsidion-hook-errors.log"


def _log_hook_error(hook_name: str) -> None:
    """Append a timestamped traceback entry to the hook error log.

    Called only from the outermost ``except Exception`` handler so that
    unexpected programming errors (regressions, NameErrors, etc.) are
    written to a persistent file rather than disappearing into stderr.
    Best-effort — never raises.

    Args:
        hook_name: Short identifier for the hook (e.g. ``"pre_compact_hook"``).
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
    """Entry point: read session JSON from stdin, snapshot state to daily note."""
    if os.environ.get("PARSIDION_INTERNAL"):
        sys.stdout.write("{}")
        return

    parser = argparse.ArgumentParser(
        description="Claude Code PreCompact hook — snapshots working state to daily note.",
    )
    parser.add_argument(
        "--lines",
        type=int,
        default=None,
        metavar="N",
        help=f"Number of transcript lines to analyse (default: {_DEFAULT_LINES})",
    )
    args = parser.parse_args()

    try:
        input_data: dict = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        sys.stdout.write("{}")
        return

    try:
        cwd: str = input_data.get("cwd", "")
        transcript_path_str: str = input_data.get("transcript_path", "")

        # Resolve vault path from cwd (supports multi-vault)
        vault_path: Path = vault_common.resolve_vault(cwd=cwd)

        # Ensure vault directories exist
        vault_common.ensure_vault_dirs(vault=vault_path)

        project: str = vault_common.get_project_name(cwd) if cwd else "unknown"

        # Resolve options: defaults → config → CLI args
        lines: int = (
            args.lines
            if args.lines is not None
            else vault_common.get_config("pre_compact_hook", "lines", _DEFAULT_LINES)
        )

        task_summary: str = "Unknown task"
        recent_files: list[str] = []

        if transcript_path_str:
            transcript_path = Path(transcript_path_str)
            if transcript_path.is_file():
                raw_lines: list[str] = read_last_n_lines(transcript_path, lines)
                task_summary = extract_user_task(raw_lines)
                recent_files = extract_file_paths(raw_lines, cwd=cwd)

        append_snapshot_to_daily(
            project, task_summary, recent_files, cwd=cwd, vault_path=vault_path
        )
        # SEC-002: sanitize project name to prevent embedded newlines in commit messages
        safe_project = project.replace("\n", " ").replace("\r", "").strip()
        vault_common.git_commit_vault(
            f"chore(vault): pre-compact snapshot [{safe_project}]",
            vault=vault_path,
        )

        sys.stdout.write("{}")

    except Exception:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        # Log unexpected programming errors to a persistent file so regressions
        # are visible without requiring manual stderr inspection.
        _log_hook_error("pre_compact_hook")
        # On any error, output empty JSON and exit cleanly
        sys.stdout.write("{}")


if __name__ == "__main__":
    main()
