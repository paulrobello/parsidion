"""Per-note parsing helpers (ARC-005).

Extracted from ``update_index.py``. Re-exported by the entry shim so
``update_index._extract_summary``, ``update_index._folder_name``,
``update_index._extract_wikilink_stems``, and ``update_index._extract_title``
keep resolving for tests and other callers.

ARC-2XX (card: hoist the row engine into core): the helpers now live in
``parsidion.core.frontmatter`` next to the rest of the note-content parsing
(:func:`parse_frontmatter`, :func:`extract_title`, :func:`get_body`);
this module re-exports them. ``_parse_note_record`` stays in the
``update_index`` entry shim because the test suite patches
``update_index.parse_frontmatter`` and that shim resolves the parser
through its own module globals -- see the shim's docstring.

Stdlib-only at module load.
"""

from __future__ import annotations

from parsidion.core.frontmatter import (
    _WIKILINK_RE,
    _extract_summary,
    _extract_wikilink_stems,
    _folder_name,
    _wikilink,
)
from parsidion.core.vault_index import extract_title

# QA-013: _extract_title thin wrapper removed -- call extract_title() directly.
# Local alias kept for call-site compatibility (tests import
# ``update_index._extract_title``).
_extract_title = extract_title

__all__: list[str] = [
    "_WIKILINK_RE",
    "_extract_summary",
    "_extract_title",
    "_extract_wikilink_stems",
    "_folder_name",
    "_wikilink",
    "extract_title",
]
