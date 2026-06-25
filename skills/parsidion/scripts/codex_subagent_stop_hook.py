#!/usr/bin/env python3
"""Codex SubagentStop hook wrapper for Parsidion transcript queueing.

Reads a Codex SubagentStop payload from stdin, validates the agent's rollout
transcript, parses assistant text from the Codex rollout JSONL, and queues the
subagent session for summarization when useful categories are detected. The hook
always emits valid JSON on stdout and falls back to ``{}`` on errors.

Mirrors ``codex_stop_hook.py`` but targets the subagent's own transcript
(``agent_transcript_path``) and tags the queued entry with ``source="subagent"``
plus ``agent_type``/``session_id`` metadata. Codex's SubagentStop output cannot
return ``additionalContext`` (only ``decision``/``reason``), so this hook is a
pure side-effect queue: it always emits ``{}``.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

import vault_common


def _read_payload() -> dict[str, object]:
    """Read a JSON object from stdin, returning an empty payload on bad input."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> None:
    """Process a Codex subagent transcript and queue useful session summaries."""
    try:
        payload = _read_payload()

        if os.environ.get("PARSIDION_INTERNAL"):
            sys.stdout.write("{}")
            return

        agent_transcript_value = payload.get("agent_transcript_path")
        if not agent_transcript_value:
            sys.stdout.write("{}")
            return

        agent_transcript = Path(str(agent_transcript_value))
        if not agent_transcript.is_file():
            sys.stdout.write("{}")
            return

        cwd_value = payload.get("cwd")
        cwd = str(cwd_value) if cwd_value else str(Path.cwd())

        if not vault_common.is_allowed_transcript_path(agent_transcript, cwd=cwd):
            sys.stdout.write("{}")
            return
        if not vault_common.is_codex_transcript_path(agent_transcript):
            sys.stdout.write("{}")
            return

        vault_path = vault_common.resolve_vault(cwd=cwd)
        vault_common.ensure_vault_dirs(vault=vault_path)

        # Read ALL lines: subagent transcripts are short, unlike main sessions.
        with open(agent_transcript, encoding="utf-8", errors="replace") as handle:
            raw_lines = handle.readlines()
        assistant_texts = vault_common.parse_codex_transcript_lines(raw_lines)
        if not assistant_texts:
            sys.stdout.write("{}")
            return

        categories = vault_common.detect_categories(assistant_texts)
        if categories:
            project = vault_common.get_project_name(cwd)
            agent_id = str(payload.get("agent_id") or "") or None
            agent_type = str(payload.get("agent_type") or "") or None
            vault_common.append_to_pending(
                transcript_path=agent_transcript,
                project=project,
                categories=categories,
                source="subagent",
                agent_type=agent_type,
                # Use agent_id as the dedup key so a restarted subagent with the
                # same id is not queued twice.
                session_id=agent_id,
                vault=vault_path,
            )

        sys.stdout.write("{}")
    except Exception:  # noqa: BLE001 - hooks must not fail closed
        traceback.print_exc(file=sys.stderr)
        sys.stdout.write("{}")


if __name__ == "__main__":
    main()
