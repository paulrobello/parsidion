#!/usr/bin/env python3
"""Claude Code PostCompact hook: snapshot observability only — no context injection.

Doc-verified 2026-11-10 against the Claude Code hooks reference and hooks
guide (code.claude.com/docs/en/hooks): PostCompact hook stdout is NOT
injected into model context (only UserPromptSubmit, UserPromptExpansion,
SessionStart, and PostModelSwitch output reaches Claude; a PostCompact hook
cannot block and only its exit-2 stderr is surfaced). The historical
``additionalContext`` emission here was therefore a silent no-op for the
model.

Snapshot re-injection now lives in ``session_start_hook.py``: Claude Code
fires SessionStart with ``source == "compact"`` right after a compaction,
and SessionStart stdout IS added to context. That script appends the latest
``## Pre-Compact Snapshot`` section (shared extraction in
``parsidion.core.vault_fs.extract_latest_snapshot``) under the SEC-108 untrusted-data
framing.

This script stays registered for its side effect: it resolves the vault
from cwd, reads today's daily note (with the legacy un-namespaced
fallback), and logs a PostCompact event recording whether a snapshot was
found — visible via ``vault-stats --hooks``. It always writes ``{}``.
"""

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

# ARC-001: imported directly from parsidion.core.* instead of the vault_common facade.
from parsidion.core.vault_fs import existing_daily_path, extract_latest_snapshot
from parsidion.core.vault_hooks import (
    get_project_name,
    log_hook_error,
    write_hook_event,
)
from parsidion.core.vault_path import resolve_vault


def main() -> None:
    """Log snapshot availability to hook_events.log; always emit ``{}``."""
    if os.environ.get("PARSIDION_INTERNAL"):
        sys.stdout.write("{}")
        return

    started = datetime.now()

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
        vault_path: Path = resolve_vault(cwd=cwd)

        snapshot_found = False
        daily_path = existing_daily_path(vault=vault_path)
        if daily_path is not None:
            try:
                content = daily_path.read_text(encoding="utf-8")
                snapshot_found = bool(extract_latest_snapshot(content))
            except (OSError, UnicodeDecodeError):
                snapshot_found = False

        write_hook_event(
            hook="PostCompact",
            project=get_project_name(cwd),
            duration_ms=(datetime.now() - started).total_seconds() * 1000,
            vault=vault_path,
            snapshot_found=snapshot_found,
        )

    except Exception:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        log_hook_error("post_compact_hook")

    # PostCompact stdout is not injected into model context (doc-verified
    # 2026-11-10); always emit the minimal valid JSON response.
    sys.stdout.write("{}")


if __name__ == "__main__":
    main()
