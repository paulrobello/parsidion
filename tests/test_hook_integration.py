"""Integration tests for hook scripts invoked as subprocesses.

Each test:
- Spawns the hook as a real subprocess
- Feeds minimal valid JSON on stdin
- Asserts exit code 0 and valid JSON on stdout

CLAUDE_VAULT is pointed at tmp_path to avoid touching the real vault.
CLAUDE_VAULT_STOP_ACTIVE is unset to allow the hooks to run (otherwise
session_stop_hook and subagent_stop_hook skip themselves).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)


def _run_hook(
    script_name: str,
    payload: dict,
    tmp_vault: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run a hook script as a subprocess and return the result.

    Args:
        script_name: Filename of the script under _SCRIPTS_DIR.
        payload: Dict to serialize as JSON and pass on stdin.
        tmp_vault: Temporary directory to use as CLAUDE_VAULT.
        extra_env: Additional environment variables to set.

    Returns:
        CompletedProcess instance with stdout/stderr captured.
    """
    script_path = _SCRIPTS_DIR / script_name
    env = {
        **os.environ,
        "CLAUDE_VAULT": str(tmp_vault),
        # Unset recursion guard so hooks actually run
        "CLAUDE_VAULT_STOP_ACTIVE": "",
    }
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        [sys.executable, str(script_path)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )


# ---------------------------------------------------------------------------
# session_stop_hook
# ---------------------------------------------------------------------------


@pytest.mark.timeout(15)
class TestSessionStopHookIntegration:
    """Integration tests for session_stop_hook.py."""

    def test_missing_transcript_exits_cleanly(self, tmp_path: Path) -> None:
        result = _run_hook(
            "session_stop_hook.py",
            {"cwd": "/tmp", "transcript_path": "/nonexistent/transcript.jsonl"},
            tmp_path,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

    def test_empty_transcript_path_exits_cleanly(self, tmp_path: Path) -> None:
        result = _run_hook(
            "session_stop_hook.py",
            {"cwd": "/tmp"},
            tmp_path,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

    def test_invalid_json_stdin_exits_cleanly(self, tmp_path: Path) -> None:
        script_path = _SCRIPTS_DIR / "session_stop_hook.py"
        env = {
            **os.environ,
            "CLAUDE_VAULT": str(tmp_path),
            "CLAUDE_VAULT_STOP_ACTIVE": "",
        }
        result = subprocess.run(
            [sys.executable, str(script_path)],
            input="not valid json",
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

    def test_stdout_is_valid_json(self, tmp_path: Path) -> None:
        result = _run_hook(
            "session_stop_hook.py",
            {"cwd": "/tmp", "transcript_path": "/nonexistent/t.jsonl"},
            tmp_path,
        )
        # Must not raise
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, dict)

    def test_with_real_transcript(self, tmp_path: Path) -> None:
        """Hook with a real transcript file should still exit 0 and return {}."""
        transcript = tmp_path / "session-abc.jsonl"
        # Write a minimal transcript with one assistant message containing an error fix keyword
        assistant_msg = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Root cause was a missing import."}
                    ],
                },
            }
        )
        transcript.write_text(assistant_msg + "\n", encoding="utf-8")

        result = _run_hook(
            "session_stop_hook.py",
            {"cwd": str(tmp_path), "transcript_path": str(transcript)},
            tmp_path,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

    def test_pi_transcript_under_home_dot_pi_is_processed(self, tmp_path: Path) -> None:
        """pi session transcripts under ~/.pi should be accepted and queued."""
        transcript = (
            tmp_path / ".pi" / "agent" / "sessions" / "proj" / "pi-session.jsonl"
        )
        transcript.parent.mkdir(parents=True, exist_ok=True)
        assistant_msg = json.dumps(
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": "Root cause was a missing environment variable.",
                        }
                    ],
                },
            }
        )
        transcript.write_text(assistant_msg + "\n", encoding="utf-8")

        result = _run_hook(
            "session_stop_hook.py",
            {"cwd": str(tmp_path), "transcript_path": str(transcript)},
            tmp_path,
            extra_env={"HOME": str(tmp_path)},
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

        pending = tmp_path / "pending_summaries.jsonl"
        assert pending.exists()
        entry = json.loads(pending.read_text(encoding="utf-8").strip())
        assert entry["transcript_path"] == str(transcript)
        assert "error_fix" in entry["categories"]

    def test_pi_transcript_uses_deeper_tail_fallback(self, tmp_path: Path) -> None:
        """When default tail misses assistant text, pi fallback tail should recover it."""
        transcript = (
            tmp_path / ".pi" / "agent" / "sessions" / "proj" / "pi-deep-tail.jsonl"
        )
        transcript.parent.mkdir(parents=True, exist_ok=True)

        lines = [
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": "Root cause was a bad cache key in metadata.",
                            }
                        ],
                    },
                }
            )
        ]
        # Add enough trailing user noise so the default 200-line tail misses the
        # assistant text above, requiring the pi fallback tail.
        for i in range(260):
            lines.append(
                json.dumps(
                    {
                        "type": "message",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": f"noise-{i}"}],
                        },
                    }
                )
            )
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = _run_hook(
            "session_stop_hook.py",
            {"cwd": str(tmp_path), "transcript_path": str(transcript)},
            tmp_path,
            extra_env={"HOME": str(tmp_path)},
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

        pending = tmp_path / "pending_summaries.jsonl"
        assert pending.exists()
        entry = json.loads(pending.read_text(encoding="utf-8").strip())
        assert entry["transcript_path"] == str(transcript)
        assert "error_fix" in entry["categories"]


# ---------------------------------------------------------------------------
# codex hooks
# ---------------------------------------------------------------------------


@pytest.mark.timeout(15)
class TestCodexHookIntegration:
    """Integration tests for Codex hook wrapper scripts."""

    def test_codex_session_start_stdout_is_valid_json(self, tmp_path: Path) -> None:
        result = _run_hook(
            "codex_session_start_hook.py",
            {
                "cwd": str(tmp_path),
                "hook_event_name": "SessionStart",
                "transcript_path": None,
            },
            tmp_path,
        )

        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        hook_output = parsed["hookSpecificOutput"]
        assert hook_output["hookEventName"] == "SessionStart"
        assert "additionalContext" in hook_output

    def test_codex_stop_missing_transcript_exits_cleanly(self, tmp_path: Path) -> None:
        result = _run_hook(
            "codex_stop_hook.py",
            {"cwd": str(tmp_path), "hook_event_name": "Stop", "transcript_path": None},
            tmp_path,
        )

        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

    def test_codex_stop_with_real_transcript_queues_pending(
        self, tmp_path: Path
    ) -> None:
        codex_home = tmp_path / ".codex"
        transcript = (
            codex_home / "sessions" / "2026" / "04" / "27" / "rollout-test.jsonl"
        )
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            '{"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Fixed a pytest failure by updating the parser test."}]}}\n',
            encoding="utf-8",
        )

        result = _run_hook(
            "codex_stop_hook.py",
            {
                "cwd": str(tmp_path),
                "hook_event_name": "Stop",
                "transcript_path": str(transcript),
            },
            tmp_path,
            extra_env={"CODEX_HOME": str(codex_home)},
        )

        assert result.returncode == 0
        assert json.loads(result.stdout) == {}
        pending = tmp_path / "pending_summaries.jsonl"
        assert pending.exists()
        assert "rollout-test" in pending.read_text(encoding="utf-8")

    def test_codex_stop_config_setup_only_updates_daily_without_pending(
        self, tmp_path: Path
    ) -> None:
        codex_home = tmp_path / ".codex"
        transcript = (
            codex_home / "sessions" / "2026" / "04" / "27" / "config-only.jsonl"
        )
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "configured the setting",
                            }
                        ],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = _run_hook(
            "codex_stop_hook.py",
            {
                "cwd": str(tmp_path),
                "hook_event_name": "Stop",
                "transcript_path": str(transcript),
            },
            tmp_path,
            extra_env={"CODEX_HOME": str(codex_home)},
        )

        assert result.returncode == 0
        assert json.loads(result.stdout) == {}
        daily_notes = list((tmp_path / "Daily").glob("**/*.md"))
        assert len(daily_notes) == 1
        daily_text = daily_notes[0].read_text(encoding="utf-8")
        assert f"Session: {tmp_path.name}" in daily_text
        assert "configured the setting" in daily_text
        assert not (tmp_path / "pending_summaries.jsonl").exists()


# ---------------------------------------------------------------------------
# pre_compact_hook
# ---------------------------------------------------------------------------


@pytest.mark.timeout(15)
class TestPreCompactHookIntegration:
    """Integration tests for pre_compact_hook.py."""

    def test_missing_transcript_exits_cleanly(self, tmp_path: Path) -> None:
        result = _run_hook(
            "pre_compact_hook.py",
            {"cwd": "/tmp", "transcript_path": "/nonexistent/transcript.jsonl"},
            tmp_path,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

    def test_empty_payload_exits_cleanly(self, tmp_path: Path) -> None:
        result = _run_hook(
            "pre_compact_hook.py",
            {"cwd": "/tmp"},
            tmp_path,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

    def test_stdout_is_valid_json(self, tmp_path: Path) -> None:
        result = _run_hook(
            "pre_compact_hook.py",
            {"cwd": "/tmp", "transcript_path": "/nonexistent/t.jsonl"},
            tmp_path,
        )
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, dict)

    def test_invalid_json_stdin_exits_cleanly(self, tmp_path: Path) -> None:
        script_path = _SCRIPTS_DIR / "pre_compact_hook.py"
        env = {**os.environ, "CLAUDE_VAULT": str(tmp_path)}
        result = subprocess.run(
            [sys.executable, str(script_path)],
            input="{ bad json !!",
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

    def test_with_real_transcript(self, tmp_path: Path) -> None:
        """Hook with a real transcript should write a snapshot and return {}."""
        transcript = tmp_path / "compact-session.jsonl"
        user_msg = json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": "Implement the new feature for the project",
                },
            }
        )
        assistant_msg = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {"file_path": str(tmp_path / "file.py")},
                        }
                    ],
                },
            }
        )
        transcript.write_text(user_msg + "\n" + assistant_msg + "\n", encoding="utf-8")

        result = _run_hook(
            "pre_compact_hook.py",
            {"cwd": str(tmp_path), "transcript_path": str(transcript)},
            tmp_path,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}


# ---------------------------------------------------------------------------
# post_compact_hook
# ---------------------------------------------------------------------------


@pytest.mark.timeout(15)
class TestPostCompactHookIntegration:
    """Integration tests for post_compact_hook.py."""

    def test_no_daily_note_exits_cleanly(self, tmp_path: Path) -> None:
        # post_compact_hook resolves the vault via CLAUDE_VAULT env var (set by
        # _run_hook), so the hook will look in tmp_path for the daily note.
        # Since the daily note won't exist there, the hook returns {}.
        result = _run_hook(
            "post_compact_hook.py",
            {},
            tmp_path,
        )
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, dict)

    def test_empty_stdin_exits_cleanly(self, tmp_path: Path) -> None:
        script_path = _SCRIPTS_DIR / "post_compact_hook.py"
        env = {**os.environ, "CLAUDE_VAULT": str(tmp_path)}
        result = subprocess.run(
            [sys.executable, str(script_path)],
            input="",
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, dict)

    def test_stdout_is_valid_json(self, tmp_path: Path) -> None:
        result = _run_hook("post_compact_hook.py", {}, tmp_path)
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, dict)

    def test_with_snapshot_in_daily_note(self, tmp_path: Path) -> None:
        """When daily note has a snapshot, hook returns additionalContext."""
        from datetime import datetime

        year_month = datetime.now().strftime("%Y-%m")
        day = datetime.now().strftime("%d")
        daily_dir = tmp_path / "Daily" / year_month
        daily_dir.mkdir(parents=True, exist_ok=True)
        daily_note = daily_dir / f"{day}.md"
        daily_note.write_text(
            "## Pre-Compact Snapshot (12:00)\n"
            "- **Project**: myproject\n"
            "- **Working on**: Implement feature X\n"
            "- **Recent files**: /tmp/a.py\n",
            encoding="utf-8",
        )

        result = _run_hook("post_compact_hook.py", {}, tmp_path)
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        # Should have additionalContext with the snapshot content
        assert "additionalContext" in parsed
        assert "Pre-Compact Snapshot" in parsed["additionalContext"]


# ---------------------------------------------------------------------------
# subagent_stop_hook
# ---------------------------------------------------------------------------


@pytest.mark.timeout(15)
class TestSubagentStopHookIntegration:
    """Integration tests for subagent_stop_hook.py."""

    def test_missing_transcript_exits_cleanly(self, tmp_path: Path) -> None:
        result = _run_hook(
            "subagent_stop_hook.py",
            {
                "cwd": "/tmp",
                "agent_transcript_path": "/nonexistent/agent.jsonl",
                "agent_id": "test-123",
                "agent_type": "Explore",
            },
            tmp_path,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

    def test_excluded_agent_type_skips(self, tmp_path: Path) -> None:
        """vault-explorer is in the default exclusion list."""
        result = _run_hook(
            "subagent_stop_hook.py",
            {
                "cwd": "/tmp",
                "agent_transcript_path": "/nonexistent/agent.jsonl",
                "agent_id": "test-456",
                "agent_type": "vault-explorer",
            },
            tmp_path,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

    def test_invalid_json_stdin_exits_cleanly(self, tmp_path: Path) -> None:
        script_path = _SCRIPTS_DIR / "subagent_stop_hook.py"
        env = {
            **os.environ,
            "CLAUDE_VAULT": str(tmp_path),
            "CLAUDE_VAULT_STOP_ACTIVE": "",
        }
        result = subprocess.run(
            [sys.executable, str(script_path)],
            input="{ bad json",
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

    def test_stdout_is_valid_json(self, tmp_path: Path) -> None:
        result = _run_hook(
            "subagent_stop_hook.py",
            {
                "cwd": "/tmp",
                "agent_transcript_path": "/nonexistent/agent.jsonl",
                "agent_id": "test-789",
                "agent_type": "Explore",
            },
            tmp_path,
        )
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, dict)

    def test_no_transcript_path_exits_cleanly(self, tmp_path: Path) -> None:
        result = _run_hook(
            "subagent_stop_hook.py",
            {"cwd": "/tmp", "agent_id": "test-000", "agent_type": "Explore"},
            tmp_path,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

    def test_pi_subagent_transcript_under_home_dot_pi_is_processed(
        self, tmp_path: Path
    ) -> None:
        """pi subagent transcripts under ~/.pi should be accepted and queued."""
        transcript = (
            tmp_path
            / ".pi"
            / "agent"
            / "sessions"
            / "proj"
            / "subagent-vault-explorer-1.jsonl"
        )
        transcript.parent.mkdir(parents=True, exist_ok=True)

        lines = [
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": "Root cause was a stale cache key.",
                            }
                        ],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": "According to docs, invalidation is required.",
                            }
                        ],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": "This pattern prevents repeat failures.",
                            }
                        ],
                    },
                }
            ),
        ]
        transcript.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = _run_hook(
            "subagent_stop_hook.py",
            {
                "cwd": str(tmp_path),
                "agent_transcript_path": str(transcript),
                "agent_id": "pi-agent-123",
                "agent_type": "Explore",
            },
            tmp_path,
            extra_env={"HOME": str(tmp_path)},
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

        pending = tmp_path / "pending_summaries.jsonl"
        assert pending.exists()
        entry = json.loads(pending.read_text(encoding="utf-8").strip())
        assert entry["source"] == "subagent"
        assert entry["session_id"] == "pi-agent-123"
        assert entry["transcript_path"] == str(transcript)

    def test_pi_subagent_single_message_is_allowed_by_default(
        self, tmp_path: Path
    ) -> None:
        """pi subagents usually emit one final assistant message; that should queue."""
        transcript = (
            tmp_path
            / ".pi"
            / "agent"
            / "sessions"
            / "proj"
            / "subagent-single-message.jsonl"
        )
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": "Root cause was a missing lock around state updates.",
                            }
                        ],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = _run_hook(
            "subagent_stop_hook.py",
            {
                "cwd": str(tmp_path),
                "agent_transcript_path": str(transcript),
                "agent_id": "pi-agent-single",
                "agent_type": "Explore",
            },
            tmp_path,
            extra_env={"HOME": str(tmp_path)},
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

        pending = tmp_path / "pending_summaries.jsonl"
        assert pending.exists()
        entry = json.loads(pending.read_text(encoding="utf-8").strip())
        assert entry["session_id"] == "pi-agent-single"
        assert entry["transcript_path"] == str(transcript)
