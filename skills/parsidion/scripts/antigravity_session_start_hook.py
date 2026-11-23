#!/usr/bin/env python3
"""Antigravity SessionStart hook shim — vault context at session start.

Antigravity (successor to the discontinued Gemini CLI) fires SessionStart
once when a conversation starts, before the first model call (native event
since agy 1.5.x). The shim still accepts legacy PreInvocation payloads
gated on ``invocationNum == 0`` for older CLIs. Output uses Antigravity's
``injectSteps`` envelope: the context rides an ``ephemeralMessage`` step,
so it reaches the model without polluting the persistent transcript.

Wire contract (antigravity.google/docs/hooks, camelCase on stdin):
``conversationId``, ``workspacePaths``, ``transcriptPath``,
``artifactDirectoryPath``, ``modelName``, ``hookEventName``; SessionStart
carries none of the invocation fields, legacy PreInvocation adds
``invocationNum`` and ``initialNumSteps``.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from parsidion.agent_adapter import _to_shared_payload, get, run_session_start


def _read_payload() -> dict[str, Any]:
    """Parse one JSON object from stdin; any malformed input becomes ``{}``."""
    try:
        raw = sys.stdin.read() or "{}"
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def main() -> None:
    """Run the session-start context builder for the Antigravity adapter."""
    adapter = get("antigravity")
    assert adapter is not None, "antigravity adapter not registered"

    payload = _read_payload()
    # Native SessionStart payloads (agy 1.5.x+) carry hookEventName and no
    # invocationNum; legacy PreInvocation payloads gate on the literal first
    # invocation (exact integer 0). Missing/malformed fields mean an
    # off-contract payload — a quiet no-op, never an injection. (bool is
    # excluded because in Python False == 0.)
    invocation_num = payload.get("invocationNum")
    is_first = payload.get("hookEventName") == "SessionStart" or (
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
