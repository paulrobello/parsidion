#!/usr/bin/env python3
"""Codex SessionStart hook wrapper for Parsidion vault context.

Reads a Codex hook payload from stdin, builds non-AI session context using the
existing Parsidion SessionStart implementation, and emits valid JSON for Codex.
All errors are reported to stderr while stdout remains valid JSON so the hook
never blocks Codex startup.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

import vault_common
from session_start_hook import _DEFAULT_MAX_CHARS, build_session_context


def _read_payload() -> dict[str, object]:
    """Read a JSON object from stdin, returning an empty payload on bad input."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> None:
    """Build Codex additional context and write a JSON hook response."""
    try:
        if os.environ.get("PARSIDION_INTERNAL"):
            sys.stdout.write("{}")
            return

        payload = _read_payload()
        cwd_value = payload.get("cwd")
        cwd = str(cwd_value) if cwd_value else str(Path.cwd())

        max_chars = int(
            vault_common.get_config(
                "session_start_hook", "max_chars", _DEFAULT_MAX_CHARS
            )
        )
        old_runtime = os.environ.get("PARSIDION_RUNTIME")
        os.environ["PARSIDION_RUNTIME"] = "codex"
        try:
            context, _notes_injected = build_session_context(
                cwd,
                ai_model=None,
                max_chars=max_chars,
                verbose_mode=False,
            )
        finally:
            if old_runtime is None:
                os.environ.pop("PARSIDION_RUNTIME", None)
            else:
                os.environ["PARSIDION_RUNTIME"] = old_runtime
        sys.stdout.write(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": context,
                    }
                }
            )
        )
    except Exception:  # noqa: BLE001 - hooks must not fail closed
        traceback.print_exc(file=sys.stderr)
        sys.stdout.write("{}")


if __name__ == "__main__":
    main()
