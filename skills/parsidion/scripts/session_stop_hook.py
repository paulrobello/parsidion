#!/usr/bin/env python3
"""Claude Code SessionEnd hook — thin shim over the shared session-end pipeline.

Registered under the SessionEnd hook — fires once when the session terminates,
not on every turn. Since ARC-002 the classify/persist pipeline itself lives in
``agent_adapter.run_session_end`` and is shared by every runtime (Claude,
Codex, Gemini); this shim keeps only Claude's invocation concerns:

- stdin JSON parsing and the ``--ai [MODEL]`` flag,
- the ``CLAUDE_VAULT_STOP_ACTIVE`` recursion guard,
- releasing the parsight watch hold taken at SessionStart (before the
  transcript early-returns so the hold is released even for sessions with
  nothing to summarize),
- the verbose ``_should_skip`` guard chain (input validation + SEC-004
  transcript-path allowlist),
- the persistent hook-error log wrapper.

The shared pipeline handles transcript reading (byte-bounded), optional AI
classification via the configured backend (``--ai`` or
``session_stop_hook.ai_model``), daily-note update, pending-queue append,
vault git commit, auto-summarizer launch, and the hook-events entry.

Note: when --ai is used, increase the hook timeout in settings.json to at
least 30000ms to allow time for the AI call to complete.
"""

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

from core import parsight_backend
from core.vault_hooks import (
    allowed_transcript_roots,
    is_allowed_transcript_path,
    log_hook_error,
)
from core.vault_path import resolve_vault

import agent_adapter


def _should_skip(input_data: dict[str, object], cwd: str) -> str | None:
    """Return a skip reason if the shared pipeline should bail before doing work.

    QA-004: lifts the input-validation + SEC-004 transcript-path security
    guard chain out of the straight-line pipeline so each guard reads as an
    early-return. Each guard's skip-reason string is returned without the
    ``"[session_stop_hook] skipping: "`` prefix; the caller adds it so the
    format stays consistent.

    The cheaper presence/existence checks come first because they don't
    need the SEC-004 root-allowlist work; the allowlist check runs last so
    a missing transcript never pays for the roots computation.

    Args:
        input_data: Parsed stdin JSON dict.
        cwd: Session cwd (used to compute the allowed transcript roots).

    Returns:
        Skip-reason string, or ``None`` when the input passes every guard
        and the pipeline may proceed.
    """
    transcript_path_str = str(input_data.get("transcript_path", ""))
    if not transcript_path_str:
        return "no transcript_path in input"

    transcript_path = Path(transcript_path_str)
    if not transcript_path.is_file():
        return f"transcript not found: {transcript_path}"

    # SEC-004: Validate transcript path is under an allowed root (Claude
    # Code ~/.claude, pi ~/.pi, or cwd/.pi). Do not collapse or weaken this
    # guard — it is the only check preventing an attacker-controlled cwd
    # from pointing the hook at an arbitrary filesystem location.
    if not is_allowed_transcript_path(transcript_path, cwd=cwd):
        roots = ", ".join(str(p) for p in allowed_transcript_roots(cwd=cwd))
        return f"transcript outside allowed roots ({roots}): {transcript_path}"

    return None


def main() -> None:
    """Entry point: read session JSON from stdin, run the shared session-end pipeline."""
    parser = argparse.ArgumentParser(
        description="Claude Code SessionEnd hook — captures learnings from the session transcript.",
    )
    parser.add_argument(
        "--ai",
        metavar="MODEL",
        nargs="?",
        const=agent_adapter._BACKEND_DEFAULT_AI_MODEL,
        default=None,
        help=(
            "Use the specified model to intelligently classify session content "
            "(no MODEL = configured backend default). Falls back to keyword heuristics on failure. "
            "Requires increasing the hook timeout in settings.json to >= 30000ms."
        ),
    )
    args = parser.parse_args()

    try:
        raw_stdin = sys.stdin.read()
        input_data: dict[str, object] = json.loads(raw_stdin)
    except (json.JSONDecodeError, ValueError):
        print("[session_stop_hook] ERROR: failed to parse stdin JSON", file=sys.stderr)
        sys.stdout.write("{}")
        return

    try:
        # Prevent recursive hook invocation
        if os.environ.get("CLAUDE_VAULT_STOP_ACTIVE"):
            print(
                "[session_stop_hook] skipping: recursive invocation detected",
                file=sys.stderr,
            )
            sys.stdout.write("{}")
            return
        os.environ["CLAUDE_VAULT_STOP_ACTIVE"] = "1"

        cwd = str(input_data.get("cwd", ""))

        # Release the parsight watch hold taken at SessionStart. Runs before
        # the transcript early-returns so the hold is released even for
        # sessions with nothing to summarize; server-side TTL covers crashed
        # sessions. Fire-and-forget — failures land in
        # ~/.claude/logs/parsidion-parsight.log, never in the hook.
        session_id = str(input_data.get("session_id", "") or "")
        if session_id:
            parsight_backend.spawn_unwatch(resolve_vault(cwd=cwd), session_id)

        # QA-004: input-validation + SEC-004 transcript-path security
        # guards lifted to _should_skip so the pipeline below reads as a
        # straight line. The SEC-004 guard is preserved verbatim — see
        # _should_skip for why it must not be weakened.
        skip_reason = _should_skip(input_data, cwd)
        if skip_reason is not None:
            print(
                f"[session_stop_hook] skipping: {skip_reason}",
                file=sys.stderr,
            )
            sys.stdout.write("{}")
            return

        adapter = agent_adapter.get("claude")
        if adapter is None:
            print(
                "[session_stop_hook] ERROR: claude adapter not registered",
                file=sys.stderr,
            )
            sys.stdout.write("{}")
            return

        # run_session_end emits "{}" on every path, including its own
        # broad except (documented BLE001 contract — the hook must never
        # fail closed).
        agent_adapter.run_session_end(adapter, payload=input_data, ai_cli_arg=args.ai)

    except Exception:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        # Log unexpected programming errors to a persistent file so regressions
        # are visible without requiring manual stderr inspection.
        log_hook_error("session_stop_hook")
        # On any error, output empty JSON and exit cleanly
        sys.stdout.write("{}")


if __name__ == "__main__":
    main()
