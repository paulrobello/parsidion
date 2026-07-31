"""Tests for rebuild_index and vault_doctor tools.

ARC-008: Updated to expect OpsToolError instead of sentinel error strings.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from parsidion_mcp.tools.ops import (
    OpsToolError,
    rebuild_index,
    vault_doctor,
    vault_health,
)


def _make_proc(returncode: int = 0, stdout: str = "ok", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# rebuild_index
# ---------------------------------------------------------------------------


def test_rebuild_index_success() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(stdout="Index rebuilt.")
        result = rebuild_index()

    assert result == "Index rebuilt."
    cmd = mock_run.call_args[0][0]
    assert "update_index.py" in cmd[-1]
    assert cmd[:3] == ["uv", "run", "--no-project"]


def test_rebuild_index_nonzero_exit_raises() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(returncode=1, stderr="something failed")
        with pytest.raises(OpsToolError, match="something failed"):
            rebuild_index()


def test_rebuild_index_timeout_raises() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="uv", timeout=30)
        with pytest.raises(OpsToolError, match="timed out"):
            rebuild_index()


def test_rebuild_index_timeout_is_30s() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc()
        rebuild_index()

    assert mock_run.call_args[1]["timeout"] == 30


# ---------------------------------------------------------------------------
# vault_doctor
# ---------------------------------------------------------------------------


def test_vault_doctor_scan_only_omits_fix_flag() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(stdout="2 issues found.")
        result = vault_doctor(fix=False)

    cmd = mock_run.call_args[0][0]
    assert "--fix" not in cmd
    assert result == "2 issues found."


def test_vault_doctor_fix_true_includes_fix_flag() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(stdout="Fixed 2 notes.")
        vault_doctor(fix=True)

    cmd = mock_run.call_args[0][0]
    assert "--fix" in cmd


def test_vault_doctor_errors_only_flag() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc()
        vault_doctor(errors_only=True)

    cmd = mock_run.call_args[0][0]
    assert "--errors-only" in cmd


def test_vault_doctor_limit_flag() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc()
        vault_doctor(limit=5)

    cmd = mock_run.call_args[0][0]
    assert "--limit" in cmd
    assert "5" in cmd


def test_vault_doctor_limit_none_omits_flag() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc()
        vault_doctor(limit=None)

    cmd = mock_run.call_args[0][0]
    assert "--limit" not in cmd


def test_vault_doctor_nonzero_exit_raises() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(returncode=1, stderr="crashed")
        with pytest.raises(OpsToolError):
            vault_doctor()


def test_vault_doctor_timeout_raises() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="uv", timeout=120)
        with pytest.raises(OpsToolError, match="timed out"):
            vault_doctor()


def test_vault_doctor_timeout_is_120s() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc()
        vault_doctor()

    assert mock_run.call_args[1]["timeout"] == 120


# ---------------------------------------------------------------------------
# vault_health (ENH-007)
# ---------------------------------------------------------------------------


def test_vault_health_subprocesses_health_json() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(
            stdout='{"overall": 82, "grade": "B", "dimensions": []}'
        )
        result = vault_health()

    cmd = mock_run.call_args[0][0]
    # The subprocess must request both --health and --json; the JSON form is
    # what the MCP tool contract promises.
    assert "--health" in cmd
    assert "--json" in cmd
    assert cmd[:3] == ["uv", "run", "--no-project"]
    assert result == '{"overall": 82, "grade": "B", "dimensions": []}'


def test_vault_health_fast_flag_appended() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(stdout='{"overall": 100}')
        vault_health(fast=True)

    cmd = mock_run.call_args[0][0]
    assert "--fast" in cmd


def test_vault_health_without_vault_omits_vault_flag() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(stdout='{"overall": 100}')
        vault_health()

    cmd = mock_run.call_args[0][0]
    assert "--vault" not in cmd


def test_vault_health_with_vault_appends_vault_flag() -> None:
    with (
        patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run,
        patch("parsidion_mcp.tools.ops.vault_common") as mock_vc,
    ):
        mock_run.return_value = _make_proc(stdout='{"overall": 100}')
        mock_vc.resolve_vault.return_value = Path("/tmp/work-vault")
        vault_health(vault="work-vault")

    cmd = mock_run.call_args[0][0]
    assert "--vault" in cmd
    assert "/tmp/work-vault" in cmd
    mock_vc.resolve_vault.assert_called_once_with(explicit="work-vault")


def test_vault_health_nonzero_exit_raises() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(returncode=1, stderr="boom")
        with pytest.raises(OpsToolError):
            vault_health()


def test_vault_health_timeout_is_60s() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(stdout='{"overall": 100}')
        vault_health()

    assert mock_run.call_args[1]["timeout"] == 60


def test_vault_health_timeout_raises() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="uv", timeout=60)
        with pytest.raises(OpsToolError, match="timed out"):
            vault_health()


# ---------------------------------------------------------------------------
# ARC-021: SCRIPTS_DIR resolved from imported package + vault parameter
# ---------------------------------------------------------------------------


class TestScriptsDirFromImportedPackage:
    """ARC-021: SCRIPTS_DIR must come from the imported vault_path.__file__,
    not the hardwired ~/.claude path. On Unix this matches the symlinked
    install; on Windows (where the installer copies) it follows whatever
    the editable install points at — keeping import and subprocess consistent.
    """

    def test_scripts_dir_is_inside_imported_package(self) -> None:
        import vault_path

        from parsidion_mcp.tools.ops import SCRIPTS_DIR

        # SCRIPTS_DIR must be the parent of vault_path.py — the file the
        # process is actually importing. A drift here would mean we are
        # subprocess-ing a different copy of the code than the one we import.
        expected_parent = Path(vault_path.__file__).resolve().parent
        assert SCRIPTS_DIR == expected_parent

    def test_scripts_dir_not_hardwired_to_home_claude(self, monkeypatch) -> None:
        # Even if ~/.claude/skills/parsidion/scripts does not exist, SCRIPTS_DIR
        # must still resolve (because it derives from __file__, not from $HOME).
        import os

        monkeypatch.setenv("HOME", "/nonexistent-home-" + str(os.getpid()))
        # Force a re-import to re-evaluate SCRIPTS_DIR (cached at module load).
        import importlib

        import parsidion_mcp.tools.ops as ops_mod

        importlib.reload(ops_mod)
        try:
            assert ops_mod.SCRIPTS_DIR.exists(), (
                f"SCRIPTS_DIR {ops_mod.SCRIPTS_DIR} does not exist; "
                "the resolution is not from the imported package"
            )
            assert (
                "vault_path" in str(ops_mod.SCRIPTS_DIR)
                or ops_mod.SCRIPTS_DIR.name == "scripts"
            )
        finally:
            # Restore the original module so other tests don't see the reload.
            importlib.reload(ops_mod)


class TestVaultParameterReachesArgv:
    """ARC-021: the optional *vault* parameter must reach the subprocess argv."""

    def test_rebuild_index_without_vault_omits_vault_flag(self) -> None:
        with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
            mock_run.return_value = _make_proc(stdout="Index rebuilt.")
            rebuild_index()

        cmd = mock_run.call_args[0][0]
        assert "--vault" not in cmd

    def test_rebuild_index_with_vault_appends_vault_flag(self) -> None:
        with (
            patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run,
            patch("parsidion_mcp.tools.ops.vault_common") as mock_vc,
        ):
            mock_run.return_value = _make_proc(stdout="Index rebuilt.")
            mock_vc.resolve_vault.return_value = Path("/tmp/my-vault")
            rebuild_index(vault="my-vault")

        cmd = mock_run.call_args[0][0]
        assert "--vault" in cmd
        assert "/tmp/my-vault" in cmd
        # resolve_vault was called with the explicit reference.
        mock_vc.resolve_vault.assert_called_once_with(explicit="my-vault")

    def test_vault_doctor_without_vault_omits_vault_flag(self) -> None:
        with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
            mock_run.return_value = _make_proc(stdout="ok")
            vault_doctor()

        cmd = mock_run.call_args[0][0]
        assert "--vault" not in cmd

    def test_vault_doctor_with_vault_appends_vault_flag(self) -> None:
        with (
            patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run,
            patch("parsidion_mcp.tools.ops.vault_common") as mock_vc,
        ):
            mock_run.return_value = _make_proc(stdout="ok")
            mock_vc.resolve_vault.return_value = Path("/tmp/work")
            vault_doctor(vault="work")

        cmd = mock_run.call_args[0][0]
        assert "--vault" in cmd
        assert "/tmp/work" in cmd
        mock_vc.resolve_vault.assert_called_once_with(explicit="work")
