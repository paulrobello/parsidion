"""Per-vault single-flight lock + cooldown stamp for AI note selection.

Extracted from ``session_start_hook.py`` (ARC-006).

The optional AI selection path (``--ai``) is expensive and serialised: at most
one AI selection may run per vault at a time (single-flight ``flock`` on
``.session_start_ai.lock``), and back-to-back invocations are short-circuited
by a cooldown stamp (``.session_start_ai.last_run``).  The orchestrator
(``_select_context_with_ai`` in ``session_start_hook.py``) calls these helpers
and stays in the shim because tests monkeypatch ``_release_ai_lock`` /
``_try_acquire_ai_lock`` on the ``session_start_hook`` module — bare-name
lookups from inside ``_select_context_with_ai`` resolve via the shim's
namespace, so the re-exported bindings below are what the tests actually
replace.
"""

from __future__ import annotations

import os
from datetime import datetime
from io import TextIOWrapper
from pathlib import Path

_AI_LOCK_FILENAME = ".session_start_ai.lock"
_AI_STAMP_FILENAME = ".session_start_ai.last_run"

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


def _ai_lock_path(vault_path: Path) -> Path:
    """Return the per-vault lock file path for AI SessionStart selection."""
    return vault_path / _AI_LOCK_FILENAME


def _try_acquire_ai_lock(vault_path: Path) -> TextIOWrapper | None:
    """Acquire the per-vault SessionStart AI lock, or return None if busy."""
    lock_path = _ai_lock_path(vault_path)
    lock_file = open(lock_path, "a+", encoding="utf-8")
    if fcntl is None:  # pragma: no cover - Windows fallback
        return lock_file
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(f"{os.getpid()}\n")
    lock_file.flush()
    return lock_file


def _ai_stamp_path(vault_path: Path) -> Path:
    """Return the per-vault cooldown stamp path for SessionStart AI."""
    return vault_path / _AI_STAMP_FILENAME


def _write_ai_cooldown_stamp(vault_path: Path) -> None:
    """Update the per-vault cooldown stamp after a completed AI selection attempt.

    Written on success AND on a failed/empty backend response: either way the
    expensive attempt ran, and ``ai_cooldown_seconds`` rate-limits the next one.
    """
    stamp_path = _ai_stamp_path(vault_path)
    try:
        stamp_path.write_text(f"{datetime.now().isoformat()}\n", encoding="utf-8")
    except OSError:
        pass


def _release_ai_lock(lock_file: TextIOWrapper | None) -> None:
    """Release and close a previously-acquired SessionStart AI lock."""
    if lock_file is None:
        return
    try:
        if fcntl is not None:  # pragma: no branch
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()
