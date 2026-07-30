#!/usr/bin/env python3
"""Codex SessionStart hook shim — delegates to the shared agent adapter.

QA-008 / ARC-020: the five codex/gemini hook scripts collapsed to thin shims
over ``agent_adapter.run_session_start``. The previous 77-line copy is now
three lines + the dispatch table; behaviour is identical and the shared
entrypoint now writes the hook_events.log entry the previous wrapper omitted
(``vault-stats --hooks`` was blind to every Codex session start).
"""

from agent_adapter import get, run_session_start


def main() -> None:
    adapter = get("codex")
    assert adapter is not None, "codex adapter not registered"
    run_session_start(adapter)


if __name__ == "__main__":
    main()
