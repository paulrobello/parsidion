"""QA-001: stdin/stdout contract tests for ``subagent_stop_hook.py``.

Mirrors the integration pattern in ``tests/test_hook_integration.py``: each test
spawns the hook as a real subprocess, feeds minimal JSON on stdin, and asserts
exit 0 + valid JSON on stdout. The hook is registered under ``SubagentStop``
with ``async: true`` and runs on every subagent termination — these tests pin
the contract so regressions surface as test failures rather than silent memory
loss (the failure mode of swallowed exceptions on this hook).

The hook deliberately swallows unexpected exceptions (``except Exception:  #
noqa: BLE001``) so a regression cannot break the user's session. The contract
we pin here is therefore *not* "failures are loud" — it is "failures are
recorded". The hook writes a traceback to ``parsidion-hook-errors.log`` on the
swallowed-failure path (distinct from the success-path ``hook_events.log``
entry, which is what makes ``vault-stats --hooks N`` observable for healthy
runs).
"""

from __future__ import annotations

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
    payload: dict | str,
    tmp_vault: Path,
    extra_env: dict[str, str] | None = None,
    stdin_text: str | None = None,
) -> subprocess.CompletedProcess:
    """Run subagent_stop_hook.py as a subprocess against ``tmp_vault``.

    Args:
        payload: Dict to serialize as JSON on stdin. Ignored when ``stdin_text``
            is provided.
        tmp_vault: Temp directory to wire in as CLAUDE_VAULT.
        extra_env: Additional env overrides.
        stdin_text: Optional raw stdin (used to feed malformed payloads).
    """
    # SEC-P001: register tmp_vault in a test-local vaults.yaml so the
    # subprocess allowlist resolver accepts the CLAUDE_VAULT reference.
    _cfg_dir = tmp_vault / ".config" / "parsidion"
    _cfg_dir.mkdir(parents=True, exist_ok=True)
    (_cfg_dir / "vaults.yaml").write_text(
        f"vaults:\n  test: {tmp_vault}\n", encoding="utf-8"
    )
    env = {
        **os.environ,
        "CLAUDE_VAULT": str(tmp_vault),
        "XDG_CONFIG_HOME": str(tmp_vault / ".config"),
        # Unset the recursion guard so the hook runs.
        "CLAUDE_VAULT_STOP_ACTIVE": "",
    }
    # Tests must not pick up the user's real HOME-installed parsidion skill
    # when CLAUDE_VAULT_STOP_ACTIVE is unset by the parent. Force PYTHONPATH
    # to point at the source-under-test so subprocess import resolves there
    # regardless of any ~/.claude symlink.
    env["PYTHONPATH"] = f"{_SCRIPTS_DIR}{os.pathsep}{env.get('PYTHONPATH', '')}"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(_SCRIPTS_DIR / "subagent_stop_hook.py")],
        input=stdin_text if stdin_text is not None else json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )


def _write_pi_transcript(transcript_path: Path, n_messages: int = 4) -> None:
    """Write a synthetic pi-style agent transcript with N assistant turns.

    The hook's transcript parser lives in ``vault_common.parse_transcript_lines``
    and understands both Claude Code and pi message shapes. The pi shape is
    used here because it lives under ``<cwd>/.pi`` and so satisfies the
    ``is_allowed_transcript_path`` guard without needing HOME trickery.
    """
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for i in range(n_messages):
        lines.append(
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                # "error" keyword triggers error_fix category
                                "text": f"Fixed error #{i} by updating the parser.",
                            }
                        ],
                    },
                }
            )
        )
    transcript_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.timeout(20)
class TestSubagentStopHappyPath:
    """A valid payload queues exactly one entry with the right metadata."""

    def test_valid_payload_appends_one_subagent_entry(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        cwd = tmp_path / "project"
        cwd.mkdir()
        transcript = cwd / ".pi" / "agent" / "sessions" / "proj" / "agent.jsonl"
        _write_pi_transcript(transcript, n_messages=4)

        result = _run_hook(
            {
                "cwd": str(cwd),
                "agent_id": "agent-abc-001",
                "agent_type": "general-purpose",
                "agent_transcript_path": str(transcript),
            },
            vault,
        )

        assert result.returncode == 0
        # stdout must always be valid JSON (Claude Code parses it)
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, dict)

        pending = vault / "pending_summaries.jsonl"
        assert pending.exists(), "subagent should queue an entry"
        lines = [
            ln for ln in pending.read_text(encoding="utf-8").splitlines() if ln.strip()
        ]
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["source"] == "subagent"
        assert entry["agent_type"] == "general-purpose"
        assert entry["transcript_path"] == str(transcript)
        assert entry["session_id"] == "agent-abc-001"
        # The error_fix keyword should land in detected categories
        assert "error_fix" in entry["categories"]

    def test_valid_payload_writes_hook_events_entry(self, tmp_path: Path) -> None:
        """A successful queue must record a SubagentStop hook_events.log entry."""
        vault = tmp_path / "vault"
        vault.mkdir()
        cwd = tmp_path / "project"
        cwd.mkdir()
        transcript = cwd / ".pi" / "agent" / "sessions" / "proj" / "ev.jsonl"
        _write_pi_transcript(transcript, n_messages=4)

        result = _run_hook(
            {
                "cwd": str(cwd),
                "agent_id": "agent-ev-001",
                "agent_type": "general-purpose",
                "agent_transcript_path": str(transcript),
            },
            vault,
        )
        assert result.returncode == 0

        events_log = vault / "hook_events.log"
        assert events_log.exists()
        events = [
            json.loads(ln)
            for ln in events_log.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        subagent_events = [e for e in events if e.get("hook") == "SubagentStop"]
        assert len(subagent_events) == 1
        assert subagent_events[0]["agent_type"] == "general-purpose"


# ---------------------------------------------------------------------------
# Excluded agents
# ---------------------------------------------------------------------------


@pytest.mark.timeout(20)
class TestSubagentStopExcludedAgent:
    """``excluded_agents`` config must skip queueing entirely."""

    def test_excluded_agent_type_writes_nothing(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        cwd = tmp_path / "project"
        cwd.mkdir()
        transcript = cwd / ".pi" / "agent" / "sessions" / "proj" / "excl.jsonl"
        _write_pi_transcript(transcript, n_messages=4)

        # Default exclusion set is {vault-explorer, research-agent}
        result = _run_hook(
            {
                "cwd": str(cwd),
                "agent_id": "excl-001",
                "agent_type": "vault-explorer",
                "agent_transcript_path": str(transcript),
            },
            vault,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}
        assert not (vault / "pending_summaries.jsonl").exists()


# ---------------------------------------------------------------------------
# Dedup
# ---------------------------------------------------------------------------


@pytest.mark.timeout(20)
class TestSubagentStopDedup:
    """Duplicate ``agent_id`` must not double-append."""

    def test_duplicate_agent_id_is_idempotent(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        cwd = tmp_path / "project"
        cwd.mkdir()
        transcript = cwd / ".pi" / "agent" / "sessions" / "proj" / "dup.jsonl"
        _write_pi_transcript(transcript, n_messages=4)

        payload = {
            "cwd": str(cwd),
            "agent_id": "dup-001",
            "agent_type": "general-purpose",
            "agent_transcript_path": str(transcript),
        }
        first = _run_hook(payload, vault)
        second = _run_hook(payload, vault)

        assert first.returncode == 0
        assert second.returncode == 0
        pending = vault / "pending_summaries.jsonl"
        assert pending.exists()
        lines = [
            ln for ln in pending.read_text(encoding="utf-8").splitlines() if ln.strip()
        ]
        assert len(lines) == 1, "duplicate agent_id must not produce a second line"


# ---------------------------------------------------------------------------
# Empty / missing inputs
# ---------------------------------------------------------------------------


@pytest.mark.timeout(20)
class TestSubagentStopMissingInputs:
    """Missing or unusable inputs must exit 0 + ``{}`` without raising."""

    def test_missing_agent_transcript_path_returns_empty_json(
        self, tmp_path: Path
    ) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        result = _run_hook(
            {"cwd": str(tmp_path), "agent_id": "x", "agent_type": "y"},
            vault,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}
        assert not (vault / "pending_summaries.jsonl").exists()

    def test_nonexistent_transcript_returns_empty_json(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        cwd = tmp_path / "project"
        cwd.mkdir()
        result = _run_hook(
            {
                "cwd": str(cwd),
                "agent_id": "x",
                "agent_type": "y",
                "agent_transcript_path": str(cwd / "nope.jsonl"),
            },
            vault,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}
        assert not (vault / "pending_summaries.jsonl").exists()

    def test_too_few_messages_skips_queue(self, tmp_path: Path) -> None:
        """A transcript below the configured ``min_messages`` is skipped.

        pi paths default to ``min_messages=1`` (so a 1-message pi transcript
        IS queued); we therefore raise the floor via config to exercise the
        branch deterministically.
        """
        vault = tmp_path / "vault"
        vault.mkdir()
        # Bump min_messages above the 2-message transcript we write below.
        (vault / "config.yaml").write_text(
            "subagent_stop_hook:\n  min_messages: 5\n", encoding="utf-8"
        )
        cwd = tmp_path / "project"
        cwd.mkdir()
        transcript = cwd / ".pi" / "agent" / "sessions" / "proj" / "short.jsonl"
        _write_pi_transcript(transcript, n_messages=2)

        result = _run_hook(
            {
                "cwd": str(cwd),
                "agent_id": "short-001",
                "agent_type": "general-purpose",
                "agent_transcript_path": str(transcript),
            },
            vault,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}
        assert not (vault / "pending_summaries.jsonl").exists()


# ---------------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------------


@pytest.mark.timeout(20)
class TestSubagentStopMalformedInput:
    """Malformed stdin must not crash the hook or leak a traceback to stdout."""

    def test_malformed_stdin_returns_empty_json(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        result = _run_hook({}, vault, stdin_text="not valid json")
        assert result.returncode == 0
        assert result.stdout.strip() == "{}"
        assert not (vault / "pending_summaries.jsonl").exists()


# ---------------------------------------------------------------------------
# Internal-skip guard
# ---------------------------------------------------------------------------


@pytest.mark.timeout(20)
class TestSubagentStopInternalGuard:
    """``PARSIDION_INTERNAL`` must short-circuit before any work."""

    def test_parsidion_internal_env_short_circuits(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        result = _run_hook(
            {"cwd": str(tmp_path), "agent_id": "x", "agent_type": "y"},
            vault,
            extra_env={"PARSIDION_INTERNAL": "1"},
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}
        assert not (vault / "pending_summaries.jsonl").exists()
