"""QA-001: stdin/stdout contract tests for ``post_compact_hook.py``.

Mirrors the integration pattern in ``tests/test_hook_integration.py``: spawn the
hook as a real subprocess, feed minimal JSON on stdin, assert exit 0 + valid
JSON on stdout.

PostCompact hook stdout is NOT injected into model context (doc-verified
2026-11-10 against the Claude Code hooks reference: only UserPromptSubmit,
UserPromptExpansion, SessionStart, and PostModelSwitch output reaches Claude).
The hook therefore always writes ``{}``; its remaining job is the side effect —
a PostCompact event in ``<vault>/hook_events.log`` recording whether today's
daily note holds a snapshot. Snapshot re-injection lives in
``session_start_hook.py``'s SessionStart ``source == "compact"`` path and is
pinned by ``tests/test_session_start_hook.py``.

The hook swallows unexpected exceptions (``except Exception:  # noqa:
BLE001``) so a regression cannot break the user's session; the contract
pinned here is therefore "failures are recorded", not "failures are loud" —
the swallowed-failure path writes a traceback to
``parsidion-hook-errors.log`` via the shared ``log_hook_error``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

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
    """Run post_compact_hook.py as a subprocess against ``tmp_vault``."""
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
    }
    env["PYTHONPATH"] = f"{_SCRIPTS_DIR}{os.pathsep}{env.get('PYTHONPATH', '')}"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(_SCRIPTS_DIR / "post_compact_hook.py")],
        input=stdin_text if stdin_text is not None else json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )


def _today_daily_path(vault: Path) -> Path:
    """Return the daily-note path the hook will resolve for today.

    The username suffix is taken from ``vault.username`` config (then ``$USER``).
    We set ``vault.username: testuser`` in the temp vault's config.yaml so the
    path is deterministic regardless of the developer running the suite.
    """
    now = datetime.now()
    return (
        vault
        / "Daily"
        / f"{now.year:04d}-{now.month:02d}"
        / f"{now.day:02d}-testuser.md"
    )


def _seed_test_config(vault: Path) -> None:
    """Write a config.yaml pinning vault.username to a deterministic value."""
    (vault / "config.yaml").write_text(
        "vault:\n  username: testuser\n", encoding="utf-8"
    )


def _last_hook_event(vault: Path) -> dict[str, Any]:
    """Return the last JSON line of the vault's hook_events.log."""
    log = vault / "hook_events.log"
    assert log.is_file(), "hook_events.log missing after post_compact run"
    lines = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines, "hook_events.log is empty"
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# Snapshot present
# ---------------------------------------------------------------------------


@pytest.mark.timeout(20)
class TestPostCompactSnapshotPresent:
    """A daily note with a snapshot emits ``{}`` and logs the side effect."""

    def test_snapshot_emits_empty_json_and_logs_event(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir(parents=True)
        _seed_test_config(vault)
        daily = _today_daily_path(vault)
        daily.parent.mkdir(parents=True, exist_ok=True)
        daily.write_text(
            "# Today\n\n"
            "Earlier prose.\n\n"
            "## Pre-Compact Snapshot (14:32)\n"
            "- **Project**: myproject\n"
            "- **Working on**: refactoring the parser\n"
            "- **Files touched**: src/parser.py, tests/test_parser.py\n\n"
            "## Later Section\n"
            "After snapshot.\n",
            encoding="utf-8",
        )

        result = _run_hook({"cwd": str(tmp_path)}, vault)
        assert result.returncode == 0
        # PostCompact stdout is never injected; the contract is {} always.
        assert json.loads(result.stdout) == {}

        event = _last_hook_event(vault)
        assert event["hook"] == "PostCompact"
        assert event["snapshot_found"] is True


# ---------------------------------------------------------------------------
# Snapshot absent
# ---------------------------------------------------------------------------


@pytest.mark.timeout(20)
class TestPostCompactSnapshotAbsent:
    """Without a snapshot, the hook returns valid empty JSON and logs it."""

    def test_daily_without_snapshot_returns_empty_json(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir(parents=True)
        _seed_test_config(vault)
        daily = _today_daily_path(vault)
        daily.parent.mkdir(parents=True, exist_ok=True)
        daily.write_text(
            "# Today\n\nJust ordinary daily prose, no snapshot here.\n",
            encoding="utf-8",
        )

        result = _run_hook({"cwd": str(tmp_path)}, vault)
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

        event = _last_hook_event(vault)
        assert event["hook"] == "PostCompact"
        assert event["snapshot_found"] is False

    def test_no_daily_note_returns_empty_json(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir(parents=True)
        result = _run_hook({"cwd": str(tmp_path)}, vault)
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

        event = _last_hook_event(vault)
        assert event["hook"] == "PostCompact"
        assert event["snapshot_found"] is False


# ---------------------------------------------------------------------------
# Malformed / edge cases — must not raise
# ---------------------------------------------------------------------------


@pytest.mark.timeout(20)
class TestPostCompactMalformedInput:
    """The hook must never break the user's session on bad input."""

    def test_malformed_stdin_still_returns_valid_json(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir(parents=True)
        result = _run_hook({}, vault, stdin_text="not valid json at all")
        assert result.returncode == 0
        # PostCompact always writes {}; stdout MUST be valid JSON either way.
        assert json.loads(result.stdout) == {}

    def test_daily_with_garbage_bytes_does_not_raise(self, tmp_path: Path) -> None:
        """A daily note containing invalid UTF-8 must not crash the hook."""
        vault = tmp_path / "vault"
        vault.mkdir(parents=True)
        _seed_test_config(vault)
        daily = _today_daily_path(vault)
        daily.parent.mkdir(parents=True, exist_ok=True)
        # Write raw bytes that are not valid UTF-8 plus a snapshot heading
        daily.write_bytes(b"## Pre-Compact Snapshot\n\xff\xfe\xfd garbage bytes\n")
        result = _run_hook({"cwd": str(tmp_path)}, vault)
        assert result.returncode == 0
        # The hook reads with encoding="utf-8"; UnicodeDecodeError is caught
        # and the hook emits {}. Pin that contract.
        assert json.loads(result.stdout) == {}


# ---------------------------------------------------------------------------
# Internal-skip guard
# ---------------------------------------------------------------------------


@pytest.mark.timeout(20)
class TestPostCompactInternalGuard:
    """``PARSIDION_INTERNAL`` must short-circuit before any work."""

    def test_parsidion_internal_env_short_circuits(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir(parents=True)
        _seed_test_config(vault)
        # Even with a daily note present, the internal guard must emit {}.
        daily = _today_daily_path(vault)
        daily.parent.mkdir(parents=True, exist_ok=True)
        daily.write_text(
            "## Pre-Compact Snapshot\n- should be ignored\n",
            encoding="utf-8",
        )
        result = _run_hook(
            {"cwd": str(tmp_path)},
            vault,
            extra_env={"PARSIDION_INTERNAL": "1"},
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}
        # The short-circuit fires before vault resolution and event logging.
        assert not (vault / "hook_events.log").exists()


# ---------------------------------------------------------------------------
# Unit test: extract_latest_snapshot logic (now in parsidion.core.vault_fs)
# ---------------------------------------------------------------------------


class TestExtractLatestSnapshot:
    """Direct unit tests for the snapshot extraction logic."""

    def test_returns_none_when_no_snapshot(self) -> None:
        from parsidion.core.vault_fs import extract_latest_snapshot

        assert extract_latest_snapshot("no snapshot here") is None

    def test_returns_section_text_with_heading(self) -> None:
        from parsidion.core.vault_fs import extract_latest_snapshot

        content = "## Pre-Compact Snapshot\n- task A\n\n## Next Section\ntrailer\n"
        result = extract_latest_snapshot(content)
        assert result is not None
        assert "Pre-Compact Snapshot" in result
        assert "task A" in result
        # Collection stops at the next ## heading
        assert "Next Section" not in result
        assert "trailer" not in result

    def test_returns_most_recent_when_multiple(self) -> None:
        from parsidion.core.vault_fs import extract_latest_snapshot

        content = (
            "## Pre-Compact Snapshot (early)\n"
            "- old\n\n"
            "## Pre-Compact Snapshot (late)\n"
            "- new\n"
        )
        result = extract_latest_snapshot(content)
        assert result is not None
        assert "(late)" in result
        assert "(early)" not in result
