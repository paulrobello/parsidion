"""Tests for rebuild_index and vault_doctor tools."""

import subprocess
from unittest.mock import patch, MagicMock

from parsidion_mcp.tools.ops import rebuild_index, vault_doctor


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


def test_rebuild_index_nonzero_exit_returns_error() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(returncode=1, stderr="something failed")
        result = rebuild_index()

    assert result.startswith("ERROR:")
    assert "something failed" in result


def test_rebuild_index_timeout_returns_error() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="uv", timeout=30)
        result = rebuild_index()

    assert "timed out" in result


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


def test_vault_doctor_nonzero_exit_returns_error() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(returncode=1, stderr="crashed")
        result = vault_doctor()

    assert result.startswith("ERROR:")


def test_vault_doctor_timeout_returns_error() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="uv", timeout=120)
        result = vault_doctor()

    assert "timed out" in result


def test_vault_doctor_timeout_is_120s() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc()
        vault_doctor()

    assert mock_run.call_args[1]["timeout"] == 120
