#!/usr/bin/env python3
"""Gemini SessionEnd hook shim — delegates to the shared agent adapter.

QA-008 / ARC-020: see codex_stop_hook.py for the collapse rationale.
The previous 107-line copy is now three lines + dispatch.
"""

from agent_adapter import get, run_session_end


def main() -> None:
    """Run the generic session-end pipeline for the Gemini adapter."""
    adapter = get("gemini")
    assert adapter is not None, "gemini adapter not registered"
    run_session_end(adapter)


if __name__ == "__main__":
    main()
