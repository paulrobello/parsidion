#!/usr/bin/env python3
"""Gemini SessionStart hook shim — delegates to the shared agent adapter.

QA-008 / ARC-020: see codex_session_start_hook.py for the collapse rationale.
The previous 78-line copy is now three lines + the dispatch table.
"""

from agent_adapter import get, run_session_start


def main() -> None:
    """Run the generic session-start context builder for the Gemini adapter."""
    adapter = get("gemini")
    assert adapter is not None, "gemini adapter not registered"
    run_session_start(adapter)


if __name__ == "__main__":
    main()
