"""Failure classification helpers for the summarizer.

Extracted from ``summarize_sessions.py`` (ARC-009).  Pure-stdlib helpers that
record and render the structured failure-reason record stored on a pending-queue
entry by ``_mark_failure`` and consumed by ``remove_processed`` for the
dead-letter decision.
"""

from __future__ import annotations

from summarizer._state_const import (
    _FAILURE_REASON_KEY,
    FailureReason,
)


def _mark_failure(
    entry: dict[str, object],
    reason: FailureReason,
    detail: str = "",
) -> None:
    """Record why *entry* failed so the dead-letter purge can classify it.

    Stores a structured record under ``_FAILURE_REASON_KEY``:
    ``{"kind": "merge_validation", "retryable": False, "detail": "..."}``.
    ``remove_processed`` reads ``retryable`` to decide whether to dead-letter
    on attempt 1 (deterministic) or after ``_MAX_ATTEMPTS`` retries.

    Args:
        entry: The pending-queue entry that failed.
        reason: Classified failure kind (member of :class:`FailureReason`).
        detail: Optional human-readable detail (exception text, validation
            error message, etc.) included in the dead-letter warning.
    """
    entry[_FAILURE_REASON_KEY] = {
        "kind": reason.kind,
        "retryable": reason.retryable,
        "detail": detail or reason.kind,
    }


def _format_failure_record(record: object, default: str = "unknown failure") -> str:
    """Render a stored failure record (dict or legacy string) for display.

    Accepts both the structured dict produced by :func:`_mark_failure` and
    legacy plain-string reasons (e.g. older tests that built the value by
    hand), so callers downstream of the public queue API keep working.
    """
    if isinstance(record, dict):
        detail = str(record.get("detail") or record.get("kind") or default)
        kind = str(record.get("kind") or "")
        return f"{kind}: {detail}" if kind and kind != detail else detail
    if isinstance(record, str) and record:
        return record
    return default


def _failure_record_retryable(record: object) -> bool:
    """Return whether *record* is retryable. Defaults to True when unknown.

    A legacy plain-string record (no ``retryable`` field) is treated as
    retryable so old queued entries that pre-date ARC-030 still get the
    ``_MAX_ATTEMPTS`` retry budget rather than being dead-lettered on sight.
    """
    if isinstance(record, dict):
        value = record.get("retryable")
        if isinstance(value, bool):
            return value
    return True
