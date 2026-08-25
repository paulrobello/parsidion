"""Flat re-export shim for :mod:`core.transcript_reader` (ARC-004 pattern).

The implementation lives in ``core/transcript_reader.py``; this module keeps
``import transcript_reader`` working for hook scripts and external callers.
"""

from core.transcript_reader import (  # noqa: F401
    DEFAULT_MAX_LINE_BYTES,
    TranscriptPathError,
    TranscriptTail,
    read_tail,
    truncate_oversized_fields,
)

__all__ = [
    "DEFAULT_MAX_LINE_BYTES",
    "TranscriptPathError",
    "TranscriptTail",
    "read_tail",
    "truncate_oversized_fields",
]
