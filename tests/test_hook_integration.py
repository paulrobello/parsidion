"""Integration tests for hook scripts invoked as subprocesses.

Each test:
- Spawns the hook as a real subprocess
- Feeds minimal valid JSON on stdin
- Asserts exit code 0 and valid JSON on stdout

VAULT_ROOT is pointed at tmp_path to avoid touching the real vault.
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
    Path(__file__).resolve().parent.parent / "skills" / "parsidion-cc" / "scripts"
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
        tmp_vault: Temporary directory to use as VAULT_ROOT.
        extra_env: Additional environment variables to set.

    Returns:
        CompletedProcess instance with stdout/stderr captured.
    """
    script_path = _SCRIPTS_DIR / script_name
    env = {
        **os.environ,
        "VAULT_ROOT": str(tmp_vault),
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
        env = {**os.environ, "VAULT_ROOT": str(tmp_path), "CLAUDE_VAULT_STOP_ACTIVE": ""}
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
        assistant_msg = json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Root cause was a missing import."}],
            },
        })
        transcript.write_text(assistant_msg + "\n", encoding="utf-8")

        result = _run_hook(
            "session_stop_hook.py",
            {"cwd": str(tmp_path), "transcript_path": str(transcript)},
            tmp_path,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}


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
        env = {**os.environ, "VAULT_ROOT": str(tmp_path)}
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
        user_msg = json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": "Implement the new feature for the project",
            },
        })
        assistant_msg = json.dumps({
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
        })
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
        # post_compact_hook reads VAULT_ROOT from the module constant (not env var),
        # so we cannot redirect it to tmp_path via the environment.  We can only
        # guarantee the process exits with code 0 and returns valid JSON; the
        # specific payload depends on whether today's daily note exists in the
        # real vault.
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
        env = {**os.environ, "VAULT_ROOT": str(tmp_path)}
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
        env = {**os.environ, "VAULT_ROOT": str(tmp_path), "CLAUDE_VAULT_STOP_ACTIVE": ""}
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
