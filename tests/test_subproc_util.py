"""Tests for the shared process-group-kill subprocess helper (SEC-122/ARC-048f)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skills" / "parsidion" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import subproc_util  # noqa: E402


def test_run_with_pgkill_returns_ok_on_normal_completion(tmp_path: Path) -> None:
    """A child that finishes within the timeout returns ('ok', CompletedProcess)."""
    reason, proc = subproc_util.run_with_pgkill(
        [sys.executable, "-c", "print('hello'); raise SystemExit(0)"],
        cwd=tmp_path,
        timeout=10.0,
    )
    assert reason == "ok"
    assert proc is not None
    assert proc.returncode == 0
    assert proc.stdout.strip() == "hello"


def test_run_with_pgkill_returns_launch_on_missing_binary(tmp_path: Path) -> None:
    """An OSError on Popen (binary not found) returns ('launch', None)."""
    reason, proc = subproc_util.run_with_pgkill(
        ["/nonexistent/binary/that/does/not/exist"],
        cwd=tmp_path,
        timeout=5.0,
    )
    assert reason == "launch"
    assert proc is None


def test_run_with_pgkill_returns_timeout_and_kills_process_group(
    tmp_path: Path,
) -> None:
    """A child that exceeds the timeout is SIGTERM'd and returns ('timeout', None)."""
    # Sleep 30s; we will time out at 0.5s. Use a child that writes its PID so
    # we can verify the process actually exited (no orphan).
    pid_file = tmp_path / "child_pid.txt"
    child_script = (
        f"import os, sys, time; "
        f"open({str(pid_file)!r},'w').write(str(os.getpid())); "
        f"time.sleep(30)"
    )
    start = time.monotonic()
    reason, proc = subproc_util.run_with_pgkill(
        [sys.executable, "-c", child_script],
        cwd=tmp_path,
        timeout=0.5,
    )
    elapsed = time.monotonic() - start
    assert reason == "timeout"
    assert proc is None
    # Returned quickly (within PGKILL_GRACE_SECS + a small buffer).
    assert elapsed < subproc_util.PGKILL_GRACE_SECS + 5.0
    # Verify the child PID is no longer alive (process group was killed).
    assert pid_file.exists(), "child should have written its PID before being killed"
    child_pid = int(pid_file.read_text().strip())
    # Give the OS a moment to reap; on POSIX a killed process may briefly linger.
    time.sleep(0.2)
    try:
        os.kill(child_pid, 0)
        pytest.fail(
            f"child process {child_pid} still alive after pgkill — orphaned grandchild?"
        )
    except (ProcessLookupError, OSError):
        pass  # expected — the process is gone


def test_run_with_pgkill_zero_timeout_means_no_timeout(tmp_path: Path) -> None:
    """``timeout <= 0`` is treated as 'no timeout' (communicate(timeout=None))."""
    reason, proc = subproc_util.run_with_pgkill(
        [sys.executable, "-c", "import sys; sys.exit(7)"],
        cwd=tmp_path,
        timeout=0,
    )
    assert reason == "ok"
    assert proc is not None
    assert proc.returncode == 7


def test_run_with_pgkill_propagates_env(tmp_path: Path) -> None:
    """The env mapping is honoured — used to drop CLAUDECODE for child backends."""
    reason, proc = subproc_util.run_with_pgkill(
        [sys.executable, "-c", "import os; print(os.environ.get('MY_TEST_VAR', ''))"],
        cwd=tmp_path,
        timeout=5.0,
        env={"MY_TEST_VAR": "abc123", "PATH": os.environ.get("PATH", "")},
    )
    assert reason == "ok"
    assert proc is not None
    assert proc.stdout.strip() == "abc123"
