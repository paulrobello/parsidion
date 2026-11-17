"""Shared tuning constants for the parsidion.cli.index subpackage (ARC-005).

Extracted from ``update_index.py``. Re-exported by the entry shim so
``update_index.FOLDER_ORDER``, ``update_index.SUMMARY_MAX_CHARS``, etc.
keep resolving for tests and other callers (``test_update_index.py``
reads ``update_index.SUMMARY_MAX_CHARS`` to bound the summary-length
assertion).

ARC-2XX (card: hoist the row engine into core): the constants now live in
core (``core.note_index`` for the parse/stale constants, ``core.note_docs``
for the doc-generation constants) and this module re-exports them so every
``cli.index.*`` consumer and the ``update_index`` shim keep resolving the
same names.

Stdlib-only at module load.
"""

from __future__ import annotations

from parsidion.core.frontmatter import SUMMARY_MAX_CHARS
from parsidion.core.note_docs import FOLDER_ORDER, RECENT_DAYS, RECENT_MAX
from parsidion.core.note_index import STALE_DAYS

__all__: list[str] = [
    "FOLDER_ORDER",
    "RECENT_DAYS",
    "RECENT_MAX",
    "SUMMARY_MAX_CHARS",
    "STALE_DAYS",
]
