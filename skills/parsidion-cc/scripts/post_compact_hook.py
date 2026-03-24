#!/usr/bin/env python3
"""Claude Code PostCompact hook that restores working context after compaction.

Reads JSON from stdin with session info (extracts cwd for vault resolution),
scans today's daily note for the most recent Pre-Compact Snapshot section, and
returns it as ``additionalContext`` so Claude can resume where it left off.
"""

import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

# These scripts are not a proper package — sys.path.insert is intentional so
# each script can run standalone via ``uv run`` or ``python`` without requiring
# pip install or editable installs.  See ARC-009 in AUDIT.md.
# SEC-011: SHADOWING RISK — a ``vault_common.py`` in the process cwd at hook
# invocation time would shadow the real module.  Accepted risk under the
# stdlib-only constraint; proper packaging would eliminate it.
sys.path.insert(0, str(Path(__file__).parent))

import vault_common  # noqa: E402

_HOOK_ERROR_LOG = "/tmp/parsidion-cc-hook-errors.log"
_SNAPSHOT_HEADING = "## Pre-Compact Snapshot"


def _log_hook_error(hook_name: str) -> None:
    """Append a timestamped traceback entry to the hook error log.

    Called only from the outermost ``except Exception`` handler so that
    unexpected programming errors (regressions, NameErrors, etc.) are
    written to a persistent file rather than disappearing into stderr.
    Best-effort — never raises.

    Args:
        hook_name: Short identifier for the hook (e.g. ``"post_compact_hook"``).
    """
    try:
        ts = datetime.now().isoformat(timespec="seconds")
        tb = traceback.format_exc()
        entry = f"[{ts}] {hook_name}\n{tb}\n"
        with open(_HOOK_ERROR_LOG, "a", encoding="utf-8") as fh:
            fh.write(entry)
    except Exception:  # noqa: BLE001 — logging must never raise
        pass


def extract_latest_snapshot(daily_content: str) -> str | None:
    """Extract the most recent Pre-Compact Snapshot section from a daily note.

    Scans backwards through the note to find the last occurrence of
    ``## Pre-Compact Snapshot``, then collects all lines belonging to that
    section (until the next ``##``-level heading or end-of-file).

    Args:
        daily_content: Full text of a daily vault note.

    Returns:
        The snapshot section text (including heading), or ``None`` if not found.
    """
    lines = daily_content.splitlines()

    # Find the last occurrence of the snapshot heading
    last_idx: int | None = None
    for i, line in enumerate(lines):
        if line.startswith(_SNAPSHOT_HEADING):
            last_idx = i

    if last_idx is None:
        return None

    # Collect lines from that heading until the next same-level heading or EOF
    section_lines: list[str] = [lines[last_idx]]
    for line in lines[last_idx + 1 :]:
        if line.startswith("## ") and not line.startswith(_SNAPSHOT_HEADING):
            break
        section_lines.append(line)

    return "\n".join(section_lines).strip()


def main() -> None:
    """Entry point: read daily note and inject latest snapshot as additionalContext."""
    try:
        # Consume stdin (Claude Code always sends JSON; ignore contents here)
        raw_stdin = sys.stdin.read()
        # Try to parse as JSON to extract cwd for vault resolution
        try:
            input_data = json.loads(raw_stdin)
            cwd = input_data.get("cwd", "")
        except (json.JSONDecodeError, ValueError):
            cwd = ""
    except Exception:  # noqa: BLE001
        cwd = ""

    try:
        # Resolve vault path from cwd (supports multi-vault)
        vault_path: Path = vault_common.resolve_vault(cwd=cwd)

        daily_path = vault_common.today_daily_path(vault=vault_path)

        if not daily_path.is_file():
            # Fallback: legacy un-namespaced path (pre-migration vault)
            from datetime import date as _date

            _today = _date.today()
            _month = f"{_today.year:04d}-{_today.month:02d}"
            _legacy = vault_path / "Daily" / _month / f"{_today.day:02d}.md"
            if _legacy.is_file():
                daily_path = _legacy
            else:
                sys.stdout.write("{}")
                return

        try:
            content = daily_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            sys.stdout.write("{}")
            return

        snapshot = extract_latest_snapshot(content)
        if not snapshot:
            sys.stdout.write("{}")
            return

        context = (
            "**Context restored from pre-compact snapshot:**\n\n"
            + snapshot
            + "\n\n*(Resume from where you left off above.)*"
        )
        sys.stdout.write(json.dumps({"additionalContext": context}))

    except Exception:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        _log_hook_error("post_compact_hook")
        sys.stdout.write("{}")


if __name__ == "__main__":
    main()
