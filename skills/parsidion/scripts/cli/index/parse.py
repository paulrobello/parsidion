"""Per-note parsing helpers (ARC-005).

Extracted from ``update_index.py``. These functions take a note's raw
markdown content and produce the first-pass fields (title, summary,
folder, tags, related-link stems) that ``build_index`` accumulates into
the index. Re-exported by the entry shim so ``update_index._extract_summary``,
``update_index._folder_name``, ``update_index._extract_wikilink_stems``,
and ``update_index._extract_title`` keep resolving for tests and other
callers.

Note: ``_parse_note_record`` stays in the entry shim (not here) because
the test suite patches ``update_index.parse_frontmatter`` and the
bare-name call from inside ``_parse_note_record`` must resolve through
the patched module's globals — see the ``update_index.py`` docstring.

Stdlib-only at module load.
"""

from __future__ import annotations

import re
from pathlib import Path

from vault_common import extract_title, get_body

from cli.index._common import SUMMARY_MAX_CHARS

# Regex to extract wikilink stems like [[note-stem]] from a string
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# QA-013: _extract_title thin wrapper removed — call extract_title() directly.
# Local alias kept for call-site compatibility (tests import
# ``update_index._extract_title``).
_extract_title = extract_title


def _extract_summary(content: str) -> str:
    """Return the first non-empty, non-heading, non-comment body line, truncated to 80 chars."""
    body: str = get_body(content)
    for line in body.splitlines():
        stripped: str = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("<!--"):
            continue
        if len(stripped) > SUMMARY_MAX_CHARS:
            return stripped[: SUMMARY_MAX_CHARS - 3] + "..."
        return stripped
    return ""


def _folder_name(note_path: Path, vault: Path) -> str:
    """Return the immediate parent folder name relative to *vault*.

    For notes directly in *vault*, returns an empty string.

    ARC-003: *vault* is threaded explicitly from :func:`build_index` rather
    than read from the module-level ``VAULT_ROOT`` constant. The constant is
    still imported for back-compat with any external caller that imported
    ``update_index._folder_name`` with the old signature, but the public
    resolution flow no longer depends on a runtime mutation of
    ``vault_common.VAULT_ROOT``.
    """
    try:
        rel: Path = note_path.relative_to(vault)
    except ValueError:
        return ""
    parts: tuple[str, ...] = rel.parts
    if len(parts) <= 1:
        return ""
    return parts[0]


def _wikilink(note_path: Path) -> str:
    """Return a wikilink ``[[stem]]`` for the note."""
    return f"[[{note_path.stem}]]"


def _extract_wikilink_stems(related: object) -> list[str]:
    """Extract note stems from a ``related`` frontmatter field.

    The field is expected to be a list of strings like ``["[[note-a]]", "[[note-b]]"]``,
    but also handles bare wikilinks and plain strings.

    Args:
        related: The value of the ``related`` frontmatter field.

    Returns:
        A list of note stem strings (without brackets).
    """
    stems: list[str] = []
    if not isinstance(related, list):
        return stems
    for item in related:
        if not isinstance(item, str):
            continue
        # Extract all [[stem]] patterns from the item
        found = _WIKILINK_RE.findall(item)
        if found:
            stems.extend(found)
        else:
            # Bare string (no brackets) — treat as a stem directly
            stripped = item.strip()
            if stripped:
                stems.append(stripped)
    return stems
