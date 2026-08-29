#!/usr/bin/env python3
"""Antigravity PreInvocation hook shim — vault context at session start.

Antigravity (successor to the discontinued Gemini CLI) fires PreInvocation
before every model call; parsidion injects vault context only on the first
invocation of a conversation (``invocationNum == 0``), which is the
session-start analog. Output uses Antigravity's ``injectSteps`` envelope:
the context rides an ``ephemeralMessage`` step, so it reaches the model
without polluting the persistent transcript.

Wire contract (antigravity.google/docs/hooks, camelCase on stdin):
``conversationId``, ``workspacePaths``, ``transcriptPath``,
``artifactDirectoryPath``, ``modelName``, plus event fields
``invocationNum`` and ``initialNumSteps``.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from agent_adapter import get, run_session_start


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
    """Run the session-start context builder for the Antigravity adapter."""
    adapter = get("antigravity")
    assert adapter is not None, "antigravity adapter not registered"

    payload = _read_payload()
    # Only the literal first invocation (exact integer 0) is a session start.
    # Missing/malformed invocationNum means an off-contract payload — a
    # quiet no-op, never an injection. (bool is excluded because in Python
    # False == 0.)
    invocation_num = payload.get("invocationNum")
    is_first = (
        isinstance(invocation_num, int)
        and not isinstance(invocation_num, bool)
        and invocation_num == 0
    )
    if not is_first:
        sys.stdout.write("{}")
        return

    run_session_start(
        adapter,
        payload=_to_shared_payload(payload),
        wrap_output=lambda context: {"injectSteps": [{"ephemeralMessage": context}]},
    )


if __name__ == "__main__":
    main()
