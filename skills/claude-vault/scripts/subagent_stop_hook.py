#!/usr/bin/env python3
"""Claude Code SubagentStop hook — captures learnings from subagent transcripts.

Registered under the SubagentStop hook with ``async: true`` so it runs in the
background and never blocks the subagent. Reads the subagent's own transcript
(``agent_transcript_path``), detects learnable content using keyword heuristics,
and queues the transcript to ``~/ClaudeVault/pending_summaries.jsonl`` for
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
- Respects ``subagent_stop_hook.excluded_agents`` config — comma-separated list
  of agent types to skip (default: "vault-explorer,research-documentation-agent")
"""

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

# These scripts are not a proper package — sys.path.insert is intentional so
# each script can run standalone via ``uv run`` or ``python`` without requiring
# pip install or editable installs.  See ARC-009 in AUDIT.md.
# SEC-011: SHADOWING RISK — if a file named ``vault_common.py`` exists in the
# process's cwd at hook invocation time, it would be imported instead of the
# real module from this scripts directory.  This is an accepted risk given the
# stdlib-only constraint; the real fix is to package the scripts properly
# (eliminating the sys.path hack).  Do NOT remove this sys.path.insert without
# first packaging vault_common as an installable module.
sys.path.insert(0, str(Path(__file__).parent))

import vault_common  # noqa: E402

_LOG_PREFIX = "[subagent_stop_hook]"
_DEFAULT_EXCLUDED_AGENTS = {"vault-explorer", "research-documentation-agent"}


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


_HOOK_ERROR_LOG = "/tmp/claude-vault-hook-errors.log"


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

        # Respect enabled config (default: true)
        if not vault_common.get_config("subagent_stop_hook", "enabled", True):
            print(f"{_LOG_PREFIX} disabled via config", file=sys.stderr)
            sys.stdout.write("{}")
            return

        agent_type = str(input_data.get("agent_type", "unknown"))

        # Skip excluded agent types (vault-explorer, research-documentation-agent, etc.)
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

        vault_common.ensure_vault_dirs()

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

        min_messages: int = int(
            vault_common.get_config("subagent_stop_hook", "min_messages", 3)
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

        # Use agent_id as the deduplication key when available; fall back to transcript stem
        dedup_path = agent_transcript
        if agent_id:
            # Synthetic path whose stem is the agent_id — used only for deduplication
            dedup_path = agent_transcript.parent / f"{agent_id}.jsonl"

        vault_common.append_to_pending(
            transcript_path=dedup_path,
            project=project,
            categories=categories,
            source="subagent",
            agent_type=agent_type,
        )

        significant = {"error_fix", "research", "pattern"}
        if significant & set(categories.keys()):
            print(f"{_LOG_PREFIX} subagent queued for summarization", file=sys.stderr)
        else:
            print(
                f"{_LOG_PREFIX} subagent not queued (no significant categories)",
                file=sys.stderr,
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
