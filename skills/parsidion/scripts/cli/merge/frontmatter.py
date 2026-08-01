"""Frontmatter field parsing helpers (ARC-005).

Extracted from ``vault_merge.py``. Re-exported by the entry shim so
``vault_merge._parse_related_list``, ``vault_merge._parse_tags_list``,
and ``vault_merge._WIKILINK_SPAN_RE`` keep resolving for tests and other
callers.

Stdlib-only at module load.
"""

from __future__ import annotations

import re

_WIKILINK_SPAN_RE = re.compile(r"\[\[([^\[\]]+)\]\]")


def _parse_related_list(fm: dict) -> list[str]:
    """Extract ``[[wikilink]]`` entries from the related field, robustly.

    Handles list, bare-string, and *malformed* values — e.g. a leaked template
    comment like ``[]  # inline quoted array: ["note-one", "note-two"]`` — by
    extracting only the actual ``[[wikilink]]`` spans. Never echoes raw comment
    text back into the field (which previously produced mangled ``related``
    values when a note with an unusual field was the merge keeper).
    """
    raw = fm.get("related", [])
    text = "".join(str(r) for r in raw) if isinstance(raw, list) else str(raw or "")
    seen: set[str] = set()
    out: list[str] = []
    for m in _WIKILINK_SPAN_RE.finditer(text):
        span = m.group(0)
        if span.lower() not in seen:
            seen.add(span.lower())
            out.append(span)
    return out


def _parse_tags_list(fm: dict) -> list[str]:
    """Extract the tags field as a list of strings.

    Args:
        fm: Parsed frontmatter dict.

    Returns:
        List of tag strings.
    """
    raw = fm.get("tags", [])
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    if isinstance(raw, str) and raw.strip():
        # Handle "[tag1, tag2]" or "tag1, tag2"
        inner = raw.strip().strip("[]")
        return [t.strip().strip('"').strip("'") for t in inner.split(",") if t.strip()]
    return []
