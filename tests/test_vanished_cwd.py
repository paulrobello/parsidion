"""Vanished-process-cwd hardening, round 2 (kanban 01a1ecc537ad71568251fa68c669a772).

Round 1 (fix/hook-vanished-cwd @ fec75a0, card 01a1e7c2) guarded
``session_start_hook`` only. This file pins the shared surface the incident
family shares: :func:`parsidion.core.vault_path.safe_cwd`, the no-arg
``resolve_vault()`` / ``get_config()`` callers whose ``Path.cwd()`` fallback
was the detonation point one layer down, and the three sibling hooks
end-to-end via subprocess — same contract as round 1: valid JSON out, exit 0,
no traceback, no hook-error log.
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

import parsidion.core.vault_config as vault_config  # noqa: E402
import parsidion.core.vault_path as vault_path  # noqa: E402


class TestSafeCwdHelper:
    """``safe_cwd()`` mirrors session_start_hook's landed chdir(home) guard."""

    def test_healthy_cwd_returns_process_cwd(self, tmp_path: Path) -> None:
        original = os.getcwd()
        try:
            os.chdir(tmp_path)
            assert vault_path.safe_cwd() == Path.cwd()
        finally:
            os.chdir(original)

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows cannot rmdir the cwd of a live process",
    )
    def test_vanished_cwd_falls_back_to_home(self, tmp_path: Path) -> None:
        scratch = tmp_path / "gone"
        scratch.mkdir()
        original = os.getcwd()
        os.chdir(scratch)
        os.rmdir(scratch)
        try:
            assert vault_path.safe_cwd() == Path.home()
        finally:
            os.chdir(original)


class TestCoreResolutionSurvivesVanishedCwd:
    """No-arg resolve_vault()/get_config() callers must degrade, not raise."""

    def test_resolve_vault_no_cwd_survives_vanished_process_cwd(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        scratch = tmp_path / "gone"
        scratch.mkdir()
        original = os.getcwd()
        os.chdir(scratch)
        os.rmdir(scratch)
        try:
            assert vault_path.resolve_vault() == tmp_vault
        finally:
            os.chdir(original)

    def test_get_config_no_vault_survives_vanished_process_cwd(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        vault_config.clear_config_cache()
        control = vault_config.get_config(
            "pre_compact_hook", "lines", 999, vault=tmp_vault
        )
        scratch = tmp_path / "gone"
        scratch.mkdir()
        original = os.getcwd()
        os.chdir(scratch)
        os.rmdir(scratch)
        try:
            no_vault = vault_config.get_config("pre_compact_hook", "lines", 999)
        finally:
            os.chdir(original)
        assert no_vault == control


def _run_hook_in_vanished_cwd(
    tmp_path: Path, hook_module: str, payload: str, fake_home: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run *hook_module*'s ``main()`` in a genuinely vanished process cwd.

    The child chdirs into a scratch dir, removes it, then runs the hook —
    the incident 01a1e7e9d8d shape. Vault/config writes stay hermetic via
    HOME + CLAUDE_VAULT + XDG_CONFIG_HOME overrides (the tmp_vault recipe,
    mirrored here because the child does not see pytest fixtures).
    Pass *fake_home* to pre-create it and seed files (e.g. a real
    transcript) under it before the child runs.
    """

    if fake_home is None:
        fake_home = tmp_path / "fake-home"
        fake_home.mkdir()
    vanished_dir = tmp_path / "gone"
    vanished_dir.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    vaults_dir = tmp_path / ".config" / "parsidion"
    vaults_dir.mkdir(parents=True)
    (vaults_dir / "vaults.yaml").write_text(
        f"vaults:\n  test: {vault}\n", encoding="utf-8"
    )
    runner = tmp_path / f"run_{hook_module}.py"
    runner.write_text(
        "import os, sys\n"
        f"os.chdir({str(vanished_dir)!r})\n"
        f"os.rmdir({str(vanished_dir)!r})\n"
        f"sys.path.insert(0, {str(_SCRIPTS_DIR)!r})\n"
        f"import {hook_module}\n"
        f"{hook_module}.main()\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    # The child is a fresh hook process: strip the flags the hooks set in
    # their own process env (recursion guard, PARSIDION_INTERNAL) — an
    # earlier in-process test may have left them set, and inheriting them
    # makes the child exit early at its guards.
    env.pop("CLAUDE_VAULT_STOP_ACTIVE", None)
    env.pop("PARSIDION_INTERNAL", None)
    env["HOME"] = str(fake_home)
    env["CLAUDE_VAULT"] = str(vault)
    env["XDG_CONFIG_HOME"] = str(tmp_path / ".config")
    # Keep the child away from a real parsight daemon (the conftest recipe).
    env["PARSIGHT_MCP_URL"] = "http://127.0.0.1:1/mcp"
    return subprocess.run(
        [sys.executable, "-I", str(runner)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


class _VanishedCwdHookContract:
    """Shared assertions for the round-1 contract: exit 0, clean JSON out,
    no traceback, no hook-error log under the fake home."""

    def assert_contract(
        self, proc: subprocess.CompletedProcess[str], fake_home: Path
    ) -> dict[str, object]:
        assert proc.returncode == 0, proc.stderr
        assert "Traceback" not in proc.stderr
        parsed = json.loads(proc.stdout)
        assert isinstance(parsed, dict)
        hook_error_log = fake_home / ".claude" / "logs" / "parsidion-hook-errors.log"
        assert not hook_error_log.exists(), hook_error_log.read_text(encoding="utf-8")
        return parsed


class TestSessionStopHookVanishedCwd(_VanishedCwdHookContract):
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows cannot rmdir the cwd of a live process",
    )
    def test_survives_vanished_process_cwd(self, tmp_path: Path) -> None:
        proc = _run_hook_in_vanished_cwd(
            tmp_path,
            "session_stop_hook",
            '{"cwd": "", "session_id": "sess-1", "transcript_path": "/nonexistent.jsonl"}',
        )
        self.assert_contract(proc, tmp_path / "fake-home")

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows cannot rmdir the cwd of a live process",
    )
    def test_deep_pipeline_runs_from_fallback_cwd(self, tmp_path: Path) -> None:
        """The session-end pipeline itself must complete, not just the guards.

        A REAL transcript under the SEC-004-allowed ~/.claude root, so
        ``_should_skip`` passes and ``run_session_end`` — whose
        ``_resolve_transcript`` carried the last bare ``Path.cwd()``
        fallback — runs to its hook-event entry instead of dying in the
        broad handler. Reviewer finding F1: the shallow-path test above
        could not see that detonation.
        """
        fake_home = tmp_path / "fake-home"
        transcript_dir = fake_home / ".claude" / "projects" / "proj"
        transcript_dir.mkdir(parents=True)
        transcript = transcript_dir / "sess-1.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": "Fix the stale manifest bug in the vault index",
                    },
                    "timestamp": "2026-11-17T20:00:00Z",
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "Regenerated the manifests."}
                        ],
                    },
                    "timestamp": "2026-11-17T20:01:00Z",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        proc = _run_hook_in_vanished_cwd(
            tmp_path,
            "session_stop_hook",
            json.dumps(
                {
                    "cwd": "",
                    "session_id": "sess-1",
                    "transcript_path": str(transcript),
                }
            ),
            fake_home=fake_home,
        )
        self.assert_contract(proc, fake_home)
        # Pipeline completion proof: the SessionEnd hook-event entry is the
        # last stage of run_session_end.
        event_log = tmp_path / "vault" / "hook_events.log"
        assert event_log.exists(), "run_session_end never completed"
        events = [
            json.loads(line)
            for line in event_log.read_text(encoding="utf-8").strip().splitlines()
        ]
        assert any(event.get("hook") == "SessionEnd" for event in events)


class TestPreCompactHookVanishedCwd(_VanishedCwdHookContract):
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows cannot rmdir the cwd of a live process",
    )
    def test_survives_vanished_process_cwd_and_still_snapshots(
        self, tmp_path: Path
    ) -> None:
        proc = _run_hook_in_vanished_cwd(
            tmp_path, "pre_compact_hook", '{"cwd": "", "transcript_path": ""}'
        )
        fake_home = tmp_path / "fake-home"
        parsed = self.assert_contract(proc, fake_home)
        assert parsed == {}
        # The pipeline actually ran from the fallback cwd: the snapshot was
        # still appended to the daily note in the CLAUDE_VAULT target.
        daily_notes = list((tmp_path / "vault" / "Daily").glob("*/*.md"))
        assert daily_notes, "no daily note written"
        snapshot_note = daily_notes[0].read_text(encoding="utf-8")
        assert "Pre-Compact Snapshot" in snapshot_note


class TestUserPromptSubmitHookVanishedCwd(_VanishedCwdHookContract):
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows cannot rmdir the cwd of a usable cwd",
    )
    def test_recall_runs_instead_of_skipping(self, tmp_path: Path) -> None:
        proc = _run_hook_in_vanished_cwd(
            tmp_path,
            "user_prompt_submit_hook",
            '{"prompt": "how does the vault resolve stale manifest entries after a note move", "cwd": ""}',
        )
        parsed = self.assert_contract(proc, tmp_path / "fake-home")
        assert parsed == {}
        # Unfixed, line 402's bare os.getcwd() raised into the broad handler
        # and the recall was skipped entirely ("recall skipped: ..." on
        # stderr). With the safe-cwd fallback the recall pipeline runs to its
        # natural empty result instead.
        assert "recall skipped" not in proc.stderr


class TestCodexSessionStartVanishedCwd(_VanishedCwdHookContract):
    """The adapter SessionStart path (codex/antigravity/pi) shares
    agent_adapter.run_session_start, whose cwd fallback was the last bare
    Path.cwd() in the family (card 01a1ecfa, round 3)."""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows cannot rmdir the cwd of a live process",
    )
    def test_survives_vanished_process_cwd(self, tmp_path: Path) -> None:
        proc = _run_hook_in_vanished_cwd(
            tmp_path, "codex_session_start_hook", '{"cwd": ""}'
        )
        parsed = self.assert_contract(proc, tmp_path / "fake-home")
        # SessionStart JSON on the success path, {} on the degraded path —
        # both are valid adapter-hook output; the contract is no traceback
        # and a dict.
        assert isinstance(parsed, dict)


class TestPreToolUseHookVanishedCwd(_VanishedCwdHookContract):
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows cannot rmdir the cwd of a live process",
    )
    def test_recall_runs_instead_of_skipping(self, tmp_path: Path) -> None:
        proc = _run_hook_in_vanished_cwd(
            tmp_path,
            "pre_tool_use_hook",
            json.dumps(
                {
                    "tool_name": "Read",
                    "tool_input": {"file_path": "/tmp/example.py"},
                    "cwd": "",
                }
            ),
        )
        parsed = self.assert_contract(proc, tmp_path / "fake-home")
        assert parsed == {}
        # Same discriminator as user_prompt_submit: the bare os.getcwd()
        # fallback raised into the never-block handler and the recall was
        # skipped entirely ("recall skipped: ..." on stderr) when unfixed.
        assert "recall skipped" not in proc.stderr
