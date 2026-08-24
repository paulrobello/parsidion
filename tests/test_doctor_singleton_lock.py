"""SEC-016 tests — doctor singleton exclusion via flock, not a PID-JSON dance.

The old guard in ``doctor/cli.py`` read a ``pid`` key from
``doctor_state.json``, checked it with ``is_process_running``, then wrote its
own pid back — an unlocked read-check-write (two doctors could both pass), and
``is_process_running`` returned True on PermissionError, so a leftover
``pid: 1`` blocked doctor runs forever. The guard is now the flock-based
``vault_fs.try_singleton_lock`` on ``<vault>/.doctor.lock``; the kernel
releases it when the holder dies, so stale-PID recovery disappears.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import vault_fs  # noqa: E402
import vault_hooks  # noqa: E402


class TestDoctorSingletonLock:
    def test_second_lock_attempt_is_refused_until_released(
        self, tmp_path: Path
    ) -> None:
        lock = tmp_path / ".doctor.lock"
        fd = vault_fs.try_singleton_lock(lock)
        assert fd is not None
        try:
            # A concurrent doctor (separate open, even in-process) must fail.
            assert vault_fs.try_singleton_lock(lock) is None
        finally:
            vault_fs.release_singleton_lock(fd)

        fd2 = vault_fs.try_singleton_lock(lock)
        assert fd2 is not None
        vault_fs.release_singleton_lock(fd2)

    def test_concurrent_doctor_run_exits_already_running(self, tmp_vault: Path) -> None:
        """Hold the lock like a live doctor; the real CLI must refuse to run."""
        fd = vault_fs.try_singleton_lock(tmp_vault / ".doctor.lock")
        assert fd is not None
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(_SCRIPTS_DIR / "vault_doctor.py"),
                    "--dry-run",
                    "--vault",
                    str(tmp_vault),
                ],
                capture_output=True,
                text=True,
                timeout=180,
            )
            assert proc.returncode == 1, (proc.stdout, proc.stderr)
            assert "already running" in proc.stderr
        finally:
            vault_fs.release_singleton_lock(fd)


class TestIsProcessRunningPermissionError:
    """SEC-016: PermissionError must not read as "our stale process is alive"."""

    def test_permission_error_reports_not_running(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raise_perm(pid: int, sig: int) -> None:
            raise PermissionError(f"no signal permission for {pid}")

        monkeypatch.setattr(os, "kill", raise_perm)
        # The stale `pid: 1` exploit: kill(1, 0) raises PermissionError for a
        # non-root user, which used to be reported as running.
        assert vault_hooks.is_process_running(1) is False

    def test_own_pid_reports_running(self) -> None:
        assert vault_hooks.is_process_running(os.getpid()) is True
