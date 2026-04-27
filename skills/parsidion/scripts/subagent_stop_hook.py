#!/usr/bin/env python3
"""Claude Code SubagentStop hook — captures learnings from subagent transcripts.

Registered under the SubagentStop hook with ``async: true`` so it runs in the
background and never blocks the subagent. Reads the subagent's own transcript
(``agent_transcript_path``), detects learnable content using keyword heuristics,
and queues the transcript to ``<resolved vault>/pending_summaries.jsonl`` for
AI-powered summarisation by ``summarize_sessions.py``.

Differences from session_stop_hook.py:
- Uses ``agent_transcript_path`` (the subagent's transcript) not ``transcript_path``
- Reads ALL lines of the transcript (subagents are short; no 200-line cap)
- Uses ``agent_id`` as the deduplication key when available
- Skips daily-note update (too noisy for frequent subagent calls)
- Does NOT launch the summarizer (deferred to the next SessionEnd)
- Respects ``subagent_stop_hook.enabled`` config (default: true)
- Respects ``subagent_stop_hook.min_messages`` config (default: 3) to filter
  trivial subagents with only one or two assistant turns
- Uses a pi-friendly default floor of 1 assistant message for transcripts
  under ``~/.pi`` / ``<cwd>/.pi`` (override via ``min_messages`` config)
- Respects ``subagent_stop_hook.excluded_agents`` config — comma-separated list
  of agent types to skip (default: "vault-explorer,research-agent")
"""

import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

import vault_common

_LOG_PREFIX = "[subagent_stop_hook]"
_DEFAULT_EXCLUDED_AGENTS = {"vault-explorer", "research-agent"}
_DEFAULT_MIN_MESSAGES = 3
_DEFAULT_MIN_MESSAGES_PI = 1


def _get_excluded_agents() -> set[str]:
    """Return the set of agent types to skip, from config or defaults.

    Reads ``subagent_stop_hook.excluded_agents`` from config.yaml as a
    comma-separated string. Falls back to the default exclusion set when
    the key is absent.

    Returns:
        A set of lowercase agent type strings to exclude.
    """
    raw = vault_common.get_config("subagent_stop_hook", "excluded_agents")
    if raw is None:
        return _DEFAULT_EXCLUDED_AGENTS
    return {s.strip().lower() for s in str(raw).split(",") if s.strip()}


_HOOK_ERROR_LOG = vault_common.secure_log_dir() / "parsidion-hook-errors.log"


def _log_hook_error(hook_name: str) -> None:
    """Append a timestamped traceback entry to the hook error log.

    Called only from the outermost ``except Exception`` handler so that
    unexpected programming errors (regressions, NameErrors, etc.) are
    written to a persistent file rather than disappearing into stderr.
    Best-effort — never raises.

    Args:
        hook_name: Short identifier for the hook (e.g. ``"subagent_stop_hook"``).
    """
    try:
        ts = datetime.now().isoformat(timespec="seconds")
        tb = traceback.format_exc()
        entry = f"[{ts}] {hook_name}\n{tb}\n"
        vault_common.rotate_log_file(_HOOK_ERROR_LOG)
        with open(_HOOK_ERROR_LOG, "a", encoding="utf-8") as fh:
            fh.write(entry)
    except Exception:  # noqa: BLE001 — logging must never raise
        pass


def main() -> None:
    """Entry point: read SubagentStop JSON from stdin, analyse transcript, queue learnings."""
    try:
        raw_stdin = sys.stdin.read()
        input_data: dict[str, object] = json.loads(raw_stdin)
    except (json.JSONDecodeError, ValueError):
        print(f"{_LOG_PREFIX} ERROR: failed to parse stdin JSON", file=sys.stderr)
        sys.stdout.write("{}")
        return

    try:
        # Guard against recursive invocation
        if os.environ.get("CLAUDE_VAULT_STOP_ACTIVE"):
            print(
                f"{_LOG_PREFIX} skipping: recursive invocation detected",
                file=sys.stderr,
            )
            sys.stdout.write("{}")
            return

        # Skip sessions launched internally by parsidion tools
        if os.environ.get("PARSIDION_INTERNAL"):
            print(
                f"{_LOG_PREFIX} skipping: internal parsidion session",
                file=sys.stderr,
            )
            sys.stdout.write("{}")
            return

        # Respect enabled config (default: true)
        if not vault_common.get_config("subagent_stop_hook", "enabled", True):
            print(f"{_LOG_PREFIX} disabled via config", file=sys.stderr)
            sys.stdout.write("{}")
            return

        _hook_start = time.monotonic()
        agent_type = str(input_data.get("agent_type", "unknown"))

        # Skip excluded agent types (vault-explorer, research-agent, etc.)
        excluded = _get_excluded_agents()
        if agent_type.lower() in excluded:
            print(
                f"{_LOG_PREFIX} skipping excluded agent type: {agent_type}",
                file=sys.stderr,
            )
            sys.stdout.write("{}")
            return

        agent_transcript_str = str(input_data.get("agent_transcript_path", ""))
        agent_id = str(input_data.get("agent_id", ""))
        cwd = str(input_data.get("cwd", ""))

        if not agent_transcript_str:
            print(
                f"{_LOG_PREFIX} skipping: no agent_transcript_path in input",
                file=sys.stderr,
            )
            sys.stdout.write("{}")
            return

        agent_transcript = Path(agent_transcript_str)
        if not agent_transcript.is_file():
            print(
                f"{_LOG_PREFIX} skipping: agent transcript not found: {agent_transcript}",
                file=sys.stderr,
            )
            sys.stdout.write("{}")
            return

        # SEC-004: Validate transcript path is under an allowed root
        # (Claude Code ~/.claude, pi ~/.pi, or cwd/.pi).
        if not vault_common.is_allowed_transcript_path(agent_transcript, cwd=cwd):
            roots = ", ".join(
                str(p) for p in vault_common.allowed_transcript_roots(cwd=cwd)
            )
            print(
                f"{_LOG_PREFIX} skipping: transcript outside allowed roots "
                f"({roots}): {agent_transcript}",
                file=sys.stderr,
            )
            sys.stdout.write("{}")
            return

        # Resolve vault path from cwd (supports multi-vault)
        vault_path: Path = vault_common.resolve_vault(cwd=cwd)

        vault_common.ensure_vault_dirs(vault=vault_path)

        project: str = vault_common.get_project_name(cwd) if cwd else "unknown"
        print(
            f"{_LOG_PREFIX} agent_type={agent_type} project={project} "
            f"transcript={agent_transcript.name}",
            file=sys.stderr,
        )

        # Read ALL lines (subagent sessions are short)
        all_lines: list[str] = []
        try:
            with open(agent_transcript, encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
        except OSError as exc:
            print(f"{_LOG_PREFIX} ERROR reading transcript: {exc}", file=sys.stderr)
            sys.stdout.write("{}")
            return

        assistant_texts = vault_common.parse_transcript_lines(all_lines)

        min_messages_default = (
            _DEFAULT_MIN_MESSAGES_PI
            if vault_common.is_pi_transcript_path(agent_transcript, cwd=cwd)
            else _DEFAULT_MIN_MESSAGES
        )
        min_messages: int = int(
            vault_common.get_config(
                "subagent_stop_hook", "min_messages", min_messages_default
            )
        )
        if len(assistant_texts) < min_messages:
            print(
                f"{_LOG_PREFIX} skipping: only {len(assistant_texts)} assistant message(s) "
                f"(min_messages={min_messages})",
                file=sys.stderr,
            )
            sys.stdout.write("{}")
            return

        print(
            f"{_LOG_PREFIX} parsed {len(assistant_texts)} assistant message(s)",
            file=sys.stderr,
        )

        categories = vault_common.detect_categories(assistant_texts)
        cats_str = ", ".join(categories.keys()) or "none"
        print(f"{_LOG_PREFIX} detected categories: [{cats_str}]", file=sys.stderr)

        # Pass the real transcript path so vault-review can read it.
        # Use agent_id as the explicit dedup key when available so that
        # restarted subagents with the same agent_id are not queued twice.
        vault_common.append_to_pending(
            transcript_path=agent_transcript,
            project=project,
            categories=categories,
            source="subagent",
            agent_type=agent_type,
            session_id=agent_id if agent_id else None,
            vault=vault_path,
        )

        significant = {"error_fix", "research", "pattern"}
        if significant & set(categories.keys()):
            print(f"{_LOG_PREFIX} subagent queued for summarization", file=sys.stderr)
        else:
            print(
                f"{_LOG_PREFIX} subagent not queued (no significant categories)",
                file=sys.stderr,
            )

        # Hook event log (#1)
        vault_common.write_hook_event(
            hook="SubagentStop",
            project=project,
            duration_ms=(time.monotonic() - _hook_start) * 1000,
            agent_type=agent_type,
            categories={k: len(v) for k, v in categories.items()},
            vault=vault_path,
        )

        sys.stdout.write("{}")

    except Exception:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        # Log unexpected programming errors to a persistent file so regressions
        # are visible without requiring manual stderr inspection.
        _log_hook_error("subagent_stop_hook")
        sys.stdout.write("{}")


if __name__ == "__main__":
    main()
