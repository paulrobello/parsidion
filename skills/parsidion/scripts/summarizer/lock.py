"""Cross-process singleton lock for the summarizer.

Extracted from ``summarize_sessions.py`` (ARC-009).

Mirrors ``vault_doctor.py``'s ``doctor_state.json`` PID lock: claim on start,
release via atexit, and detect stale PIDs (killed/crashed runs) so a dead lock
never blocks the next run.  Prevents the auto-summarizer launched by the stop
hook from racing a manual ``--run-doctor`` invocation.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from core.vault_fs import flock_exclusive as _flock_exclusive
from core.vault_fs import funlock as _funlock
from core.vault_hooks import is_process_running

from summarizer._state_const import _SUMMARIZER_STATE_FILENAME


def _summarizer_state_file(vault_path: Path) -> Path:
    """Return the singleton-guard state file path for *vault_path*."""
    return vault_path / _SUMMARIZER_STATE_FILENAME


def _load_summarizer_state(vault_path: Path) -> dict:
    """Load summarizer_state.json, returning {} if missing/corrupt."""
    try:
        return json.loads(
            _summarizer_state_file(vault_path).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {}


def _write_summarizer_state(state: dict, vault_path: Path) -> None:
    """Write summarizer_state.json atomically via a sibling .tmp file."""
    dest = _summarizer_state_file(vault_path)
    dest.parent.mkdir(parents=True, exist_ok=True)  # vault dir may not exist yet
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(dest)


def _summarizer_claim_lock_file(vault_path: Path) -> Path:
    """Return the flock guard path serializing summarizer_state.json updates."""
    return _summarizer_state_file(vault_path).with_suffix(".lock")


def claim_summarizer_lock(vault_path: Path) -> bool:
    """Claim the singleton summarizer lock for *vault_path*.

    Returns True if this process now holds the lock, False if another
    summarizer is already running. A stale PID (dead process) is reclaimed.

    The read-check-write on summarizer_state.json is serialized under an
    exclusive flock on a sibling .lock file so two near-simultaneous
    SessionEnd hooks cannot both read the pre-claim state and both "win".
    """
    lock_path = _summarizer_claim_lock_file(vault_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)  # vault dir may not exist yet
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        _flock_exclusive(lock_file)
        try:
            state = _load_summarizer_state(vault_path)
            existing_pid = state.get("pid")
            if (
                existing_pid
                and existing_pid != os.getpid()
                and is_process_running(existing_pid)
            ):
                print(
                    f"summarize_sessions is already running (PID {existing_pid}). Skipping.",
                    file=sys.stderr,
                )
                return False
            _write_summarizer_state(
                {
                    "pid": os.getpid(),
                    "last_run": datetime.now().isoformat(timespec="seconds"),
                },
                vault_path,
            )
            return True
        finally:
            _funlock(lock_file)


def release_summarizer_lock(vault_path: Path) -> None:
    """Clear our PID from summarizer_state.json (best-effort, idempotent)."""
    try:
        with open(
            _summarizer_claim_lock_file(vault_path), "a+", encoding="utf-8"
        ) as lock_file:
            _flock_exclusive(lock_file)
            try:
                state = _load_summarizer_state(vault_path)
                if state.get("pid") == os.getpid():
                    state.pop("pid", None)
                    _write_summarizer_state(state, vault_path)
            finally:
                _funlock(lock_file)
    except Exception as exc:  # noqa: BLE001
        print(f"summarizer lock release best-effort: {exc}", file=sys.stderr)
        pass
