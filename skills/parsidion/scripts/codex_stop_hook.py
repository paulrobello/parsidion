#!/usr/bin/env python3
"""Codex Stop hook shim — delegates to the shared agent adapter.

QA-008 / ARC-020: the previous 107-line copy is now three lines + dispatch.
Behaviour is identical, AND the shared entrypoint now emits the hook_events.log
entry + commits the daily note change the previous wrapper omitted — so
``vault-stats --hooks`` surfaces Codex sessions and a Codex-only user's vault
no longer accumulates uncommitted daily-note changes.
"""

from agent_adapter import get, run_session_end


def main() -> None:
    adapter = get("codex")
    assert adapter is not None, "codex adapter not registered"
    run_session_end(adapter)


if __name__ == "__main__":
    main()
