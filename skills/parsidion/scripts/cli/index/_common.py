"""Shared tuning constants for the cli.index subpackage (ARC-005).

Extracted from ``update_index.py``. Re-exported by the entry shim so
``update_index.FOLDER_ORDER``, ``update_index.SUMMARY_MAX_CHARS``, etc.
keep resolving for tests and other callers (``test_update_index.py``
reads ``update_index.SUMMARY_MAX_CHARS`` to bound the summary-length
assertion).

Stdlib-only at module load.
"""

from __future__ import annotations

# Canonical folder order for index sections
FOLDER_ORDER: list[str] = [
    "Daily",
    "Projects",
    "Languages",
    "Frameworks",
    "Patterns",
    "Debugging",
    "Tools",
    "Research",
    "History",
]

RECENT_DAYS: int = 7
RECENT_MAX: int = 20
SUMMARY_MAX_CHARS: int = 80
STALE_DAYS: int = 30
