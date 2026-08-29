#!/usr/bin/env python3
"""Antigravity Stop hook shim — session end for the vault pipeline.

Antigravity (successor to the discontinued Gemini CLI) fires Stop when an
execution loop terminates; parsidion queues the conversation transcript for
summarization only when the agent is fully idle (``fullyIdle == true``), so
background-task pauses do not enqueue partial sessions. The hook always
allows the stop (``{"decision": ""}``); it never forces a continue.

Wire contract (antigravity.google/docs/hooks, camelCase on stdin):
``conversationId``, ``workspacePaths``, ``transcriptPath``,
``artifactDirectoryPath``, ``modelName``, plus event fields
``executionNum``, ``terminationReason``, ``error``, ``fullyIdle``. The
transcript lives at
``~/.gemini/antigravity-cli/brain/<conversationId>/.system_generated/logs/transcript.jsonl``.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from agent_adapter import get, run_session_end

_ALLOW_STOP = '{"decision": ""}'


def _read_payload() -> dict[str, Any]:
    """Parse one JSON object from stdin; any malformed input becomes ``{}``."""
    try:
        raw = sys.stdin.read() or "{}"
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def _to_shared_payload(payload: dict[str, Any]) -> dict[str, object]:
    """Map Antigravity's camelCase wire fields onto the shared pipeline's."""
    workspace_paths = payload.get("workspacePaths")
    cwd = ""
    if isinstance(workspace_paths, list) and workspace_paths:
        cwd = str(workspace_paths[0])
    mapped: dict[str, object] = {}
    if cwd:
        mapped["cwd"] = cwd
    if payload.get("conversationId"):
        mapped["session_id"] = str(payload["conversationId"])
    if payload.get("transcriptPath"):
        mapped["transcript_path"] = str(payload["transcriptPath"])
    return mapped


def main() -> None:
    """Run the session-end pipeline for the Antigravity adapter."""
    adapter = get("antigravity")
    assert adapter is not None, "antigravity adapter not registered"

    payload = _read_payload()
    # Only queue when the agent is completely finished; a Stop with
    # background tasks still running is not a session end.
    if payload.get("fullyIdle") is not True:
        sys.stdout.write(_ALLOW_STOP)
        return

    run_session_end(
        adapter, payload=_to_shared_payload(payload), final_output=_ALLOW_STOP
    )


if __name__ == "__main__":
    main()
