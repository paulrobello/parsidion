#!/usr/bin/env python3
"""Codex SubagentStop hook shim — delegates to the shared agent adapter.

QA-008 / ARC-020: the previous 100-line copy is now three lines + dispatch.
Mirrors ``codex_stop_hook`` but passes ``subagent=True`` so the shared
entrypoint reads ``agent_transcript_path``, queues with
``source='subagent'`` + ``agent_type``/``session_id`` metadata, and skips
the daily-note update (subagents fire too frequently for daily entries).
"""

from agent_adapter import get, run_session_end


def main() -> None:
    """Run the session-end pipeline in subagent mode for the Codex adapter."""
    adapter = get("codex")
    assert adapter is not None, "codex adapter not registered"
    run_session_end(adapter, subagent=True)


if __name__ == "__main__":
    main()
