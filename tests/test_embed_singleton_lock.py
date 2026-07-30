"""Singleton lock guarding embeddings builds against concurrent ONNX loads.

``build_embeddings.py`` loads a ~67 MB ONNX runtime per process and is
spawned detached in the background by ``update_index`` on every index
rebuild. A second trigger landing while the first build still holds the
runtime loads another, and so on — concurrent builds deplete memory.
``vault_fs.try_singleton_lock`` is the per-vault non-blocking guard the
build holds for its lifetime; these tests pin its semantics.

Note: ``fcntl.flock`` locks are per-*process*, so the real contention path
(cross-process) is exercised via a spawned second process below.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fcntl")  # the no-op lock on Windows is not testable here

SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from vault_fs import release_singleton_lock, try_singleton_lock  # noqa: E402


def test_acquire_returns_fd_and_creates_lockfile(tmp_path: Path) -> None:
    lock = tmp_path / "embeddings.db.lock"
    fd = try_singleton_lock(lock)
    assert fd is not None
    assert lock.exists()
    release_singleton_lock(fd)


def test_release_lets_next_acquire_succeed(tmp_path: Path) -> None:
    lock = tmp_path / "embeddings.db.lock"
    fd1 = try_singleton_lock(lock)
    assert fd1 is not None
    release_singleton_lock(fd1)
    fd2 = try_singleton_lock(lock)
    assert fd2 is not None
    release_singleton_lock(fd2)


def test_concurrent_process_is_rejected(tmp_path: Path) -> None:
    """A second *process* holding the lock makes acquire return None.

    This is the production contention path: each ``build_embeddings`` run is
    its own process, so cross-process flock exclusion is what prevents two
    concurrent ONNX loads. (Same-process double-acquire does not contend —
    flock is per-process — which is why this spawns a subprocess.)
    """
    lock = tmp_path / "embeddings.db.lock"
    fd = try_singleton_lock(lock)
    assert fd is not None
    try:
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "from vault_fs import try_singleton_lock as t; "
                "sys.exit(0 if t(sys.argv[2]) is None else 1)",
                str(SCRIPTS_DIR),
                str(lock),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert probe.returncode == 0, (
            "second process should have seen the lock as held:\n" + probe.stderr
        )
    finally:
        release_singleton_lock(fd)


def test_locks_are_independent_per_path(tmp_path: Path) -> None:
    fd_a = try_singleton_lock(tmp_path / "a.lock")
    fd_b = try_singleton_lock(tmp_path / "b.lock")
    assert fd_a is not None and fd_b is not None
    release_singleton_lock(fd_a)
    release_singleton_lock(fd_b)
