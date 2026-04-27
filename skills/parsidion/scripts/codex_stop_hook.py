#!/usr/bin/env python3
"""Codex Stop hook wrapper for Parsidion transcript queueing.

Reads a Codex Stop payload from stdin, validates Codex transcript paths, parses
assistant text from Codex rollout JSONL, updates the vault daily note, and queues
pending summarization when useful categories are detected. The hook always emits
valid JSON on stdout and falls back to ``{}`` on errors.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

import vault_common

_DEFAULT_TRANSCRIPT_TAIL_LINES = 200


def _read_payload() -> dict[str, object]:
    """Read a JSON object from stdin, returning an empty payload on bad input."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _first_summary(texts: list[str]) -> str:
    """Return a compact summary candidate from parsed assistant text."""
    for text in texts:
        if len(text.strip()) > 50:
            return text[:500]
    return texts[0][:500] if texts else ""


def main() -> None:
    """Process a Codex transcript and queue useful session summaries."""
    try:
        payload = _read_payload()

        if os.environ.get("PARSIDION_INTERNAL"):
            sys.stdout.write("{}")
            return

        cwd_value = payload.get("cwd")
        cwd = str(cwd_value) if cwd_value else str(Path.cwd())
        transcript_value = payload.get("transcript_path")
        if not transcript_value:
            sys.stdout.write("{}")
            return

        transcript_path = Path(str(transcript_value))
        if not transcript_path.is_file():
            sys.stdout.write("{}")
            return

        if not vault_common.is_allowed_transcript_path(transcript_path, cwd=cwd):
            sys.stdout.write("{}")
            return
        if not vault_common.is_codex_transcript_path(transcript_path):
            sys.stdout.write("{}")
            return

        vault_path = vault_common.resolve_vault(cwd=cwd)
        vault_common.ensure_vault_dirs(vault=vault_path)

        tail_lines = int(
            vault_common.get_config(
                "session_stop_hook",
                "transcript_tail_lines",
                _DEFAULT_TRANSCRIPT_TAIL_LINES,
            )
        )
        raw_lines = vault_common.read_last_n_lines(transcript_path, tail_lines)
        assistant_texts = vault_common.parse_codex_transcript_lines(raw_lines)
        if not assistant_texts:
            sys.stdout.write("{}")
            return

        categories = vault_common.detect_categories(assistant_texts)
        if categories:
            project = vault_common.get_project_name(cwd)
            vault_common.append_session_to_daily(
                project,
                categories,
                _first_summary(assistant_texts),
                vault_path,
            )
            vault_common.append_to_pending(
                transcript_path,
                project,
                categories,
                vault=vault_path,
            )

        sys.stdout.write("{}")
    except Exception:  # noqa: BLE001 - hooks must not fail closed
        traceback.print_exc(file=sys.stderr)
        sys.stdout.write("{}")


if __name__ == "__main__":
    main()
