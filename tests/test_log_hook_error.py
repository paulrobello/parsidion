"""QA-003: the shared ``log_hook_error`` helper in ``core/vault_hooks.py``.

What was copy-pasted as ``_log_hook_error`` into five hook scripts is now one
function. These tests pin its observable contract: a timestamped traceback
entry is appended to ``<secure_log_dir>/parsidion-hook-errors.log`` and a
failure inside the logger itself never raises (hooks must stay fail-open).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import vault_common
import vault_hooks
from core import vault_hooks as core_vault_hooks


def test_log_hook_error_writes_timestamped_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A call inside an except block appends hook name + traceback to the log."""
    monkeypatch.setattr(core_vault_hooks, "secure_log_dir", lambda: tmp_path)
    try:
        raise ValueError("boom")
    except ValueError:
        core_vault_hooks.log_hook_error("session_stop_hook")

    log = tmp_path / "parsidion-hook-errors.log"
    assert log.exists()
    content = log.read_text(encoding="utf-8")
    assert "session_stop_hook" in content
    assert "ValueError: boom" in content
    # Timestamp prefix on the entry line.
    first_line = content.splitlines()[0]
    assert first_line.startswith("[") and "T" in first_line


def test_log_hook_error_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A failing rotation/write is reported on stderr, never raised."""
    monkeypatch.setattr(core_vault_hooks, "secure_log_dir", lambda: tmp_path)

    def _explode(log_path: Path, max_lines: int = 10) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(core_vault_hooks, "rotate_log_file", _explode)
    # Must not raise.
    core_vault_hooks.log_hook_error("pre_compact_hook")
    err = capsys.readouterr().err
    assert "hook error log write failed" in err


def test_log_hook_error_reexported_from_shims() -> None:
    """The vault_hooks shim and vault_common facade expose the same function."""
    assert vault_hooks.log_hook_error is core_vault_hooks.log_hook_error
    assert vault_common.log_hook_error is core_vault_hooks.log_hook_error
