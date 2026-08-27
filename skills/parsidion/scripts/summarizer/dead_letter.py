"""Dead-letter queue (``dead_letters.jsonl``) read / append / prune.

Extracted from ``summarize_sessions.py`` (ARC-009).

A dead-lettered entry is a session that failed summarization (transient
retry-exhaustion or a deterministic write-gate / validation failure) and was
purged from ``pending_summaries.jsonl``.  The record is retained in
``dead_letters.jsonl`` so a stop-hook re-queue is caught by the ``_DEAD`` guard
in ``summarize_one`` rather than re-billing an AI call for a session already
judged not worth a note.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from core.vault_fs import flock_exclusive as _flock_exclusive
from core.vault_fs import funlock as _funlock


def _dead_lettered_ids(vault: Path) -> set[str]:
    """Return session_ids already recorded in ``dead_letters.jsonl``.

    A re-queued dead-lettered session (prior failure or write-gate skip) must
    not be re-processed. Best-effort: any read error yields an empty set so a
    missing/unreadable file never blocks summarization.
    """
    ids: set[str] = set()
    path = vault / "dead_letters.jsonl"
    if not path.is_file():
        return ids
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            sid = str(record.get("session_id", ""))
            if sid:
                ids.add(sid)
    except OSError:
        pass
    return ids


def _append_dead_letter(
    pending_path: Path,
    entry: dict[str, object],
    attempts: int,
    last_failure: str,
) -> None:
    """Best-effort append of a dead-lettered entry to dead_letters.jsonl.

    Mirrors vault_fs.append_to_pending's permission/lock conventions (0o600,
    exclusive flock) but must never raise -- the entry has already been
    purged from the queue by the caller, so a write failure here is only
    a loss of visibility, not a correctness problem.

    ARC-013: matches ``vault_fs.append_to_pending``'s inode-retry loop so a
    concurrent ``_prune_dead_letters`` (which rewrites via ``os.replace``)
    cannot leave the append writing to an unlinked inode. Without the retry,
    prune's replace would silently drop an appended entry that landed during
    the rewrite window.

    Args:
        pending_path: Path to the pending JSONL file (dead_letters.jsonl is
            written as a sibling in the same vault directory). When the path
            given IS dead_letters.jsonl, the sibling resolution still works
            because the parent is the same.
        entry: The original queue entry being purged.
        attempts: Final attempts count that triggered the purge.
        last_failure: The failure reason recorded for this attempt.
    """
    # ARC-013: accept either the pending path or the dead-letter path itself
    # so callers can target dead_letters.jsonl directly (tests use this).
    dead_letter_path = (
        pending_path
        if pending_path.name == "dead_letters.jsonl"
        else pending_path.parent / "dead_letters.jsonl"
    )
    record = dict(entry)
    record["attempts"] = attempts
    record["last_failure"] = last_failure
    record["dead_lettered_at"] = datetime.now().isoformat()
    try:
        # Inode-retry loop: a concurrent prune rewrites via os.replace, which
        # changes the path's inode out from under our open handle. Re-check
        # under the lock and reopen if the inode drifted, matching the
        # append_to_pending pattern.
        for _attempt in range(5):
            fd = os.open(str(dead_letter_path), os.O_CREAT | os.O_RDWR, 0o600)
            with open(fd, "r+", encoding="utf-8") as f:
                _flock_exclusive(f)
                try:
                    try:
                        if (
                            os.fstat(f.fileno()).st_ino
                            != os.stat(dead_letter_path).st_ino
                        ):
                            continue  # File was replaced; retry on the new inode
                    except OSError:
                        continue
                    f.seek(0, 2)
                    f.write(json.dumps(record) + "\n")
                    return
                finally:
                    _funlock(f)
        # Retry exhaustion is a silent data-loss path otherwise: the caller
        # sees success and the entry never lands. Loud so the next occurrence
        # is diagnosable (5 consecutive replaces beat every reopen attempt).
        print(
            "Warning: could not write dead-letter record: file was replaced "
            "on every inode-retry attempt",
            file=sys.stderr,
        )
    except OSError as e:
        print(f"Warning: could not write dead-letter record: {e}", file=sys.stderr)


def _prune_dead_letters(vault: Path, retention_days: int) -> int:
    """Drop ``dead_letters.jsonl`` entries older than ``retention_days``.

    write-gate skips are made sticky so a stop-hook re-queue is caught by the
    ``_DEAD`` guard, which means dead_letters.jsonl otherwise grows without
    bound (every transient session is retained forever). This bounds it: each
    run, entries whose ``dead_lettered_at`` is older than the retention window
    are removed. Best-effort and never raises; reuses the exclusive-flock +
    0o600 convention from ``_append_dead_letter``. Entries with a missing or
    unparseable timestamp are kept (cannot be dated safely). Returns the number
    of entries pruned. ``retention_days <= 0`` disables pruning.

    ARC-013 / SEC-129: the read now happens INSIDE the exclusive lock. The
    original implementation read the file unlocked and took LOCK_EX only for
    the truncate, 25 lines later -- any ``_append_dead_letter`` (which does
    lock) landing in that window was destroyed by the subsequent truncate.
    The rewrite also drops the in-place seek/truncate in favour of tmp +
    ``os.replace`` (same pattern as ``remove_processed``) so a crash mid-prune
    cannot truncate the file, and preserves 0o600 on the replaced file.
    """
    if retention_days <= 0:
        return 0
    path = vault / "dead_letters.jsonl"
    if not path.is_file():
        return 0
    cutoff = datetime.now() - timedelta(days=retention_days)
    # ARC-013/SEC-129: take the lock BEFORE reading. The previous code read
    # unlocked and any concurrent _append_dead_letter between the read and
    # the later truncate was silently lost.
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as e:
        print(f"Warning: could not open dead-letter file: {e}", file=sys.stderr)
        return 0
    kept: list[str] = []
    pruned = 0
    try:
        with open(fd, "r+", encoding="utf-8") as f:
            _flock_exclusive(f)
            try:
                # Read inside the lock so an _append_dead_letter that
                # lands between read and rewrite is observed and preserved.
                raw_lines = f.read().splitlines()
            finally:
                # Seek back to 0 so a potential fall-through writes from
                # the top (not used now but kept for safety).
                f.seek(0)
            for raw in raw_lines:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    record = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    kept.append(raw)
                    continue
                ts = record.get("dead_lettered_at")
                try:
                    when = datetime.fromisoformat(str(ts)) if ts else None
                except ValueError:
                    when = None
                if when is not None and when < cutoff:
                    pruned += 1
                    continue
                kept.append(raw)
            if pruned == 0:
                return 0
            # Crash-atomic rewrite: write survivors to a sibling tmp file
            # (preserving 0o600) and os.replace under the lock. Matches the
            # remove_processed pattern; the in-place seek/truncate was not
            # crash-atomic and could leave the file truncated on Ctrl-C.
            tmp = path.with_suffix(".jsonl.tmp")
            try:
                tmp_fd = os.open(str(tmp), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
                with open(tmp_fd, "w", encoding="utf-8") as out:
                    if kept:
                        out.write("\n".join(kept) + "\n")
                os.replace(tmp, path)
            finally:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass
    except OSError as e:
        print(f"Warning: could not prune dead-letter records: {e}", file=sys.stderr)
        return 0
    return pruned


def _read_dead_letters(vault: Path) -> list[dict[str, object]]:
    """Return every record in ``dead_letters.jsonl`` (best-effort read).

    Companion to :func:`_dead_lettered_ids` for callers that need the full
    record — e.g. ``--retry-dead-letters``, which filters on ``last_failure``
    and ``dead_lettered_at``. Best-effort: any read error or malformed line is
    skipped, never raised.
    """
    path = vault / "dead_letters.jsonl"
    if not path.is_file():
        return []
    records: list[dict[str, object]] = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(record, dict):
                records.append(record)
    except OSError:
        pass
    return records


def _remove_dead_letters_by_session_ids(vault: Path, session_ids: set[str]) -> int:
    """Atomically drop dead-letter records whose ``session_id`` is in the set.

    Used by ``--retry-dead-letters`` to pull entries back into the live queue:
    once removed here, :func:`_dead_lettered_ids` no longer returns them, so
    ``_early_gate`` stops skipping the re-queued session. Mirrors
    :func:`_prune_dead_letters`' locked read + tmp/``os.replace`` rewrite
    (crash-atomic, preserves 0o600). Returns the number removed.
    """
    if not session_ids or not (vault / "dead_letters.jsonl").is_file():
        return 0
    path = vault / "dead_letters.jsonl"
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as e:
        print(f"Warning: could not open dead-letter file: {e}", file=sys.stderr)
        return 0
    kept: list[str] = []
    removed = 0
    try:
        with open(fd, "r+", encoding="utf-8") as f:
            _flock_exclusive(f)
            try:
                raw_lines = f.read().splitlines()
            finally:
                f.seek(0)
            for raw in raw_lines:
                s = raw.strip()
                if not s:
                    continue
                try:
                    record = json.loads(s)
                except (json.JSONDecodeError, ValueError):
                    kept.append(s)
                    continue
                if str(record.get("session_id", "")) in session_ids:
                    removed += 1
                    continue
                kept.append(s)
            if removed == 0:
                return 0
            tmp = path.with_suffix(".jsonl.tmp")
            try:
                tmp_fd = os.open(str(tmp), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
                with open(tmp_fd, "w", encoding="utf-8") as out:
                    if kept:
                        out.write("\n".join(kept) + "\n")
                os.replace(tmp, path)
            finally:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass
        return removed
    except OSError as e:
        print(f"Warning: could not rewrite dead-letter file: {e}", file=sys.stderr)
        return 0
