"""Dry-run preview cache + execute-path locking (ARC-005).

Extracted from ``vault_merge.py``. The preview-cache path helpers, the
``_delete_preview`` cleanup, and the ``_merge_lock`` context manager move
here; the actual ``_write_preview`` / ``_load_fresh_preview`` /
``_hash_content`` helpers stay in the entry shim because the merge
orchestrator (``_merge_notes``) calls them via bare names while weaving
the AI body into the merged note. Re-exported by ``vault_merge.py`` for
backwards-compat with test attribute access (``vault_merge._merge_lock``,
``vault_merge._delete_preview``, ``vault_merge._preview_cache_path``).

Stdlib-only at module load (``fcntl`` is guarded for Windows portability).
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator
from pathlib import Path

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows fallback
    _fcntl = None

_PREVIEW_DIRNAME = ".merge_previews"
_MERGE_LOCK_FILENAME = ".merge.lock"


def _preview_dir(vault_path: Path) -> Path:
    """Return the vault's preview-cache directory, creating it if needed."""
    preview_dir = vault_path / _PREVIEW_DIRNAME
    preview_dir.mkdir(mode=0o700, exist_ok=True)
    return preview_dir


def _preview_cache_path(vault_path: Path, path_a: Path, path_b: Path) -> Path:
    """Return the JSON preview-cache path for a (keeper, loser) note pair."""
    return _preview_dir(vault_path) / f"{path_a.stem}--{path_b.stem}.json"


def _delete_preview(vault_path: Path, path_a: Path, path_b: Path) -> None:
    """Remove a pair's cached preview after a successful --execute."""
    _preview_cache_path(vault_path, path_a, path_b).unlink(missing_ok=True)


@contextlib.contextmanager
def _merge_lock(vault_path: Path) -> Iterator[None]:
    """Hold an exclusive, non-blocking lock around the merge mutation sequence.

    Guards read A/B -> write keeper -> trash loser -> rewrite backlinks so two
    concurrent ``--execute`` invocations against the same vault cannot
    interleave. A second invocation that cannot acquire the lock fails
    immediately with ``SystemExit`` instead of blocking, so a stuck or
    crashed holder can never wedge unrelated merges.

    ``vault_fs.flock_exclusive`` is not used here because it blocks
    indefinitely (no ``LOCK_NB``); a blocked second invocation would look
    like a hang rather than the clean, immediate failure this needs.
    """
    lock_path = _preview_dir(vault_path) / _MERGE_LOCK_FILENAME
    lock_file = open(lock_path, "a+", encoding="utf-8")
    if _fcntl is not None:
        try:
            _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            print(
                "Error: another vault-merge --execute is already running "
                f"against this vault (lock: {lock_path}). Try again shortly.",
                file=sys.stderr,
            )
            sys.exit(1)
    try:
        yield
    finally:
        if _fcntl is not None:
            _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_UN)
        lock_file.close()


__all__ = [
    "_MERGE_LOCK_FILENAME",
    "_PREVIEW_DIRNAME",
    "_delete_preview",
    "_merge_lock",
    "_preview_cache_path",
    "_preview_dir",
]
