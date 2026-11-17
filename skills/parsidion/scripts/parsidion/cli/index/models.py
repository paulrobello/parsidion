"""NamedTuples shared across the parsidion.cli.index subpackage (ARC-005).

Extracted from ``update_index.py``. Re-exported by the entry shim so
``update_index.NoteEntry`` and ``update_index.NoteRecord`` keep resolving
for tests and other callers.

ARC-2XX (card: hoist the row engine into core): the row/parse NamedTuples
now live in ``parsidion.core.note_index`` (next to the ``note_index``
schema they mirror); this module re-exports them so every ``cli.index.*``
consumer and the ``update_index`` shim keep resolving the same names.

Stdlib-only at module load.
"""

from __future__ import annotations

from parsidion.core.note_index import NoteEntry, NoteRecord

__all__: list[str] = [
    "NoteEntry",
    "NoteRecord",
]
