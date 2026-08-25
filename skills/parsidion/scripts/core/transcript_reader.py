"""One byte-bounded transcript tail reader for every runtime (ENH-018).

Every hook and adapter reads session transcripts through :func:`read_tail`
so the byte bound, the path allowlist, and huge-line handling behave the
same for Claude, Codex, Gemini, pi, and omp alike. The failure mode this
exists for: a large subagent transcript whose SINGLE JSONL line (a giant
tool result) exceeds the whole byte budget — tail-by-lines then returns
zero usable records and the session summarizes as "No result" and
dead-letters. ``read_tail`` never discards such a line: it keeps the
record and replaces string fields longer than 64 KiB with a
``<truncated N bytes>`` marker, so the surrounding dialogue survives.

Stdlib-only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "TranscriptTail",
    "TranscriptPathError",
    "read_tail",
    "truncate_oversized_fields",
]

# A JSONL line longer than this is a giant tool result; its record is kept,
# but its string fields are capped (see _MAX_FIELD_BYTES).
DEFAULT_MAX_LINE_BYTES = 256 * 1024
# String values longer than this inside an oversized line's record are
# replaced with a "<truncated N bytes>" marker.
_MAX_FIELD_BYTES = 64 * 1024


class TranscriptPathError(ValueError):
    """A transcript path outside the allowed roots (SEC-004/SEC-010)."""


@dataclass
class TranscriptTail:
    """Result of one :func:`read_tail` call.

    Attributes:
        lines: The retained raw JSONL text lines (oversized lines carried in
            field-truncated form so downstream line parsers see the same
            shape the records do).
        records: The lines that parsed as JSON objects, in tail order.
        truncated: True when the byte window cut lines off the head of the
            file (the file is larger than *max_bytes*).
        bytes_read: Size of the trailing byte window actually read.
        oversized_lines: Count of retained lines longer than
            ``max_line_bytes`` (kept, field-truncated — not discarded).
    """

    lines: list[str] = field(default_factory=list)
    records: list[dict] = field(default_factory=list)
    truncated: bool = False
    bytes_read: int = 0
    oversized_lines: int = 0


def truncate_oversized_fields(value: object) -> object:
    """Recursively cap long strings inside a parsed record.

    Strings longer than :data:`_MAX_FIELD_BYTES` become
    ``<truncated N bytes>`` (N = the original length). Dicts and lists are
    walked; everything else passes through unchanged.
    """
    if isinstance(value, str):
        if len(value) > _MAX_FIELD_BYTES:
            return f"<truncated {len(value)} bytes>"
        return value
    if isinstance(value, dict):
        return {k: truncate_oversized_fields(v) for k, v in value.items()}
    if isinstance(value, list):
        return [truncate_oversized_fields(v) for v in value]
    return value


def _scan_back_to_line_start(fh, window_start: int, budget: int) -> int | None:
    """Find the byte offset where the line containing *window_start* begins.

    Reads backwards in chunks of at most *budget* total looking for a
    newline before *window_start*; returns the offset just after it, 0 when
    the line starts at the file head, or None when no newline is found
    within the budget (the line is longer than we allow ourselves to read).
    """
    seek_to = max(0, window_start - budget)
    fh.seek(seek_to)
    prefix = fh.read(window_start - seek_to)
    idx = prefix.rfind(b"\n")
    if idx == -1:
        return 0 if seek_to == 0 else None
    return seek_to + idx + 1


def read_tail(
    path: Path,
    *,
    tail_lines: int,
    max_bytes: int,
    max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
    cwd: str | None = None,
    require_allowed: bool = False,
) -> TranscriptTail:
    """Read the last *tail_lines* JSONL records of *path*, byte-bounded.

    Algorithm (SEC-111 shape plus huge-line chunking): seek to
    ``size - max_bytes``, read the window forward, drop the partial first
    line (cut mid-record), keep at most *tail_lines* whole lines. A line
    longer than *max_line_bytes* is kept with its record's long string
    fields truncated (:func:`truncate_oversized_fields`) instead of being
    discarded — the "No result" dead-letter class disappears because the
    records on both sides of a multi-MB line survive.

    Args:
        path: Transcript JSONL path.
        tail_lines: Maximum number of trailing lines to retain.
        max_bytes: Ceiling on the trailing byte window.
        max_line_bytes: Lines longer than this are counted as oversized and
            field-truncated (default 256 KiB).
        cwd: Working directory for project-local allowlist roots
            (``<cwd>/.pi``, ``<cwd>/.gemini``).
        require_allowed: When True, raise :class:`TranscriptPathError` if
            *path* is outside the transcript allowlist (SEC-004/SEC-010).
            Hook entrypoints validate before calling; the default keeps the
            reader usable on arbitrary paths (tests, --sessions replays).

    Returns:
        A :class:`TranscriptTail`. Missing files yield an empty tail.
    """
    if require_allowed:
        # Imported lazily: vault_hooks pulls a wide import graph that this
        # leaf module should not join at import time.
        from .vault_hooks import is_allowed_transcript_path

        if not is_allowed_transcript_path(path, cwd=cwd):
            raise TranscriptPathError(f"transcript path outside allowed roots: {path}")

    tail = TranscriptTail()
    try:
        size = path.stat().st_size
    except OSError:
        return tail

    window = min(size, max_bytes) if max_bytes and max_bytes > 0 else size
    read_from = size - window
    try:
        with path.open("rb") as fh:
            if read_from > 0:
                # Does the window start mid-line? (The byte just before it
                # is not a newline.) If so, recover the line's start by
                # scanning back at most max_line_bytes so the record is
                # KEPT (field-truncated below) instead of dropped as a
                # partial fragment — the "No result" failure mode.
                fh.seek(read_from - 1)
                starts_mid_line = fh.read(1) != b"\n"
                if starts_mid_line:
                    line_start = _scan_back_to_line_start(fh, read_from, max_line_bytes)
                    if line_start is not None:
                        read_from = line_start
            fh.seek(read_from)
            raw = fh.read(size - read_from)
    except OSError:
        return TranscriptTail()

    tail.bytes_read = size - read_from
    tail.truncated = read_from > 0

    text = raw.decode("utf-8", errors="replace")
    lines = text.split("\n")
    # A trailing newline yields one empty final element; drop it.
    if lines and lines[-1] == "":
        lines.pop()
    if tail_lines > 0:
        lines = lines[-tail_lines:]

    for line in lines:
        if not line.strip():
            continue
        oversized = len(line.encode("utf-8", errors="replace")) > max_line_bytes
        if oversized:
            tail.oversized_lines += 1
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            # Not JSON (or truncated head remnant): keep the raw line for
            # line-based consumers, skip from records.
            tail.lines.append(line)
            continue
        if not isinstance(record, dict):
            tail.lines.append(line)
            continue
        if oversized:
            shrunk = truncate_oversized_fields(record)
            if isinstance(shrunk, dict):
                tail.lines.append(json.dumps(shrunk, ensure_ascii=False))
                tail.records.append(shrunk)
            else:  # unreachable: dicts truncate to dicts
                tail.lines.append(line)
        else:
            tail.lines.append(line)
            tail.records.append(record)
    return tail
