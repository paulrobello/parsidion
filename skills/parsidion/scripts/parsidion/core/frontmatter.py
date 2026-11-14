"""Frontmatter parsing, serialization, and note-title/slug helpers (ARC-203).

Owns everything that reads or writes the YAML frontmatter block of a note:

- :func:`parse_frontmatter` / :func:`serialize_frontmatter` — the single
  stdlib-only parser/emitter pair shared by the indexer, the doctor, the
  CLI merge/search tools, and (via the parity fixture) the visualizer;
- the parse-warning collector (:func:`record_parse_warning` /
  :func:`drain_parse_warnings`) so hook-invisible stderr warnings can be
  surfaced through ``write_hook_event()``;
- :func:`get_body` / :func:`extract_title` / :func:`slugify`.

Split out of the former monolithic ``core/vault_index.py`` (ARC-203). This
module is a leaf: it depends only on ``core.yaml_lite`` and the
``note_schema`` contract module. All public symbols are re-exported from
``core.vault_index`` (and its ``vault_index`` / ``vault_common`` shims) for
backward compatibility.
"""

from __future__ import annotations

import hashlib
import re
import sys
import unicodedata
from typing import Any

from .yaml_lite import (
    dump_list_item,
    dump_scalar,
    parse_inline_list,
    parse_list_item,
    parse_scalar,
    split_key_value,
)

# ARC-005: the canonical frontmatter key order lives in the note contract
# module (a leaf with no imports, so this cannot cycle).
from parsidion.note_schema import FRONTMATTER_FIELD_ORDER

__all__: list[str] = [
    # Frontmatter and content parsing
    "parse_frontmatter",
    "serialize_frontmatter",
    "get_body",
    "extract_title",
    # Parse warning collector
    "record_parse_warning",
    "drain_parse_warnings",
    "_PARSE_WARNINGS_MAX",  # tests assert the cap value
    # Slug utility
    "slugify",
    # Compiled regexes re-exported for the vault_links backlink scanner
    "_FRONTMATTER_RE",
    # Emitter policy
    "_ALWAYS_QUOTED_LIST_KEYS",
]

# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?\n)---\s*\n", re.DOTALL)
_SLUG_SPECIAL_RE = re.compile(r"[^a-z0-9\-]")
_SLUG_MULTI_HYPHEN_RE = re.compile(r"-{2,}")

# ---------------------------------------------------------------------------
# Parse warning collector
# ---------------------------------------------------------------------------
#
# Frontmatter parsing warnings are printed to stderr (see below), which hook
# scripts swallow -- making them invisible to users.  This in-process
# collector lets callers (e.g. update_index.py) surface them via
# write_hook_event() so they show up in `vault-stats --hooks N`.

_PARSE_WARNINGS_MAX = 200
_parse_warnings: list[str] = []


def record_parse_warning(msg: str) -> None:
    """Record a frontmatter parse warning for later retrieval via drain_parse_warnings().

    Capped at ``_PARSE_WARNINGS_MAX`` entries to bound memory during large
    index rebuilds; warnings beyond the cap are silently dropped.
    """
    if len(_parse_warnings) < _PARSE_WARNINGS_MAX:
        _parse_warnings.append(msg)


def drain_parse_warnings() -> list[str]:
    """Return all recorded parse warnings and clear the collector."""
    warnings = list(_parse_warnings)
    _parse_warnings.clear()
    return warnings


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


def parse_frontmatter(content: str) -> dict[str, Any]:
    """Parse YAML frontmatter from markdown content using regex.

    **Supported YAML subset** (stdlib-only; not a full YAML 1.2 parser) --
    scalar/list tokenization lives in ``core/yaml_lite`` (ENH-024); only
    the frontmatter-specific structure (delimiters, block sequences,
    multi-line scalars) lives here:

    - Scalars: bare strings, single/double-quoted strings, integers, floats,
      booleans (``true``/``false``/``yes``/``no``), ``null``/``~``, and
      date strings (``YYYY-MM-DD`` kept as strings).
    - Inline lists: ``key: [a, b, c]`` with optional quoting of items.
      Quoted items may contain commas.  List items are always kept as
      strings (never coerced to bool/int/float) so numeric-looking tags
      like ``tags: [2026, python]`` remain findable.
    - Block sequence lists: ``key:`` followed by ``  - item`` lines
      (items kept as strings, same as inline lists).
    - Multi-line scalars: ``>`` (folded -- joins continuation lines with a
      space), ``|`` (literal -- joins with newlines), and strip variants
      ``>-`` / ``|-``.  Only indented continuation lines (indent > 0) are
      collected; the block ends at the next bare key or blank line.
    - Comment LINES (first non-blank character ``#``) are skipped; trailing
      inline comments are NOT stripped from scalar values (``key: v # c``
      reads back as the string ``v # c``), unlike the config.yaml parser.

    **Not supported** (silently ignored or returned as bare strings):
    - Nested mappings deeper than 1 level (``key: {a: 1}`` or indented
      sub-mappings).
    - YAML anchors, aliases, and tags (``!!str``, etc.).
    - Multi-document streams (``---`` as a separator within a value).
    - Flow mappings.

    Returns an empty dict when no frontmatter block is found or when the
    opening/closing ``---`` delimiters are missing.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}

    raw = match.group(1)
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None
    # Multi-line scalar state: block_style is ">" (folded) or "|" (literal)
    block_style: str | None = None
    block_parts: list[str] = []

    def _flush_block() -> None:
        """Finalize a multi-line scalar block and store it in result."""
        nonlocal block_style, block_parts
        if current_key is not None and block_style is not None and block_parts:
            if block_style in (">", ">-"):
                result[current_key] = " ".join(block_parts)
            else:  # "|" or "|-"
                result[current_key] = "\n".join(block_parts)
        block_style = None
        block_parts = []

    for line in raw.splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())

        # If we're collecting a multi-line scalar block, check for continuation
        if block_style is not None:
            if indent > 0 and stripped:
                block_parts.append(stripped)
                continue
            else:
                _flush_block()
                # Fall through to process this line normally

        # Continuation of a multi-line list (- item form)
        if (
            stripped.startswith("- ")
            and current_key is not None
            and current_list is not None
        ):
            current_list.append(parse_list_item(stripped[2:].strip()))
            result[current_key] = current_list
            continue

        # If we were collecting a list and hit a non-list line, close it
        if current_list is not None:
            current_list = None

        # Skip blank lines and comments
        if not stripped or stripped.startswith("#"):
            continue

        # key: value pair
        key_value = split_key_value(line)
        if key_value is None:
            continue

        key, value_str = key_value

        if not key:
            continue

        # QA-008: warn when an indented sub-key looks like a nested mapping.
        # Nested mappings are not supported by this parser (see docstring).
        # Emit a stderr warning so vault-doctor checks don't silently mislead.
        # This is safe: hook scripts print JSON to stdout; stderr is ignored.
        if indent > 0 and key:
            _warning = (
                f"parse_frontmatter: nested mapping key '{key}' (indented) is not "
                "supported and will be treated as a top-level scalar. "
                "Consider flattening this YAML key."
            )
            print(_warning, file=sys.stderr)
            record_parse_warning(_warning)

        current_key = key

        # Empty value -- could be start of a multi-line list
        if not value_str:
            current_list = []
            result[key] = current_list
            continue

        # Multi-line scalar block indicators: >, |, >-, |-
        if value_str in (">", "|", ">-", "|-"):
            block_style = value_str
            block_parts = []
            current_list = None
            continue

        # Inline list: [a, b, c]
        items = parse_inline_list(value_str)
        if items is not None:
            result[key] = items
            current_list = None
            continue

        # Scalar value
        result[key] = parse_scalar(value_str)
        current_list = None

    # Flush any remaining block at end of frontmatter
    _flush_block()

    return result


# ---------------------------------------------------------------------------
# Frontmatter serialization (ARC-005)
# ---------------------------------------------------------------------------

# Scalar/array emission policy (quoting, round-trip rules) lives in
# core/yaml_lite (ENH-024): scalar_needs_quotes / quote_scalar /
# dump_scalar / dump_list_item. Only the frontmatter-specific policy
# stays here.
# List fields whose items are always double-quoted: ``related`` holds
# ``[[wikilinks]]`` and the canonical form (CLAUDE.md conventions,
# visualizer/lib/frontmatter.ts) is ``related: ["[[a]]", "[[b]]"]``.
_ALWAYS_QUOTED_LIST_KEYS: frozenset[str] = frozenset({"related"})


def serialize_frontmatter(fields: dict[str, Any]) -> str:
    """Serialize a frontmatter dict to the canonical YAML block.

    ARC-005: the single schema-aware emitter shared by ``vault_new``,
    ``vault_merge``, the ``tools/migrate_*`` importers, and (via the parity
    fixture ``tests/fixtures/parity/frontmatter.json``) the visualizer's
    ``frontmatter.ts``. Replaces four hand-built ``_build_frontmatter``
    copies whose quoting and key order had drifted.

    Canonical form:

    - keys in :data:`note_schema.FRONTMATTER_FIELD_ORDER` first (when
      present), remaining keys after them in insertion order;
    - ``None`` and empty-string values are dropped (the writer that has an
      opinion about empties filters before calling);
    - ``tags``/``sources`` as inline arrays with bare items unless an item
      needs quoting; ``related`` items always double-quoted (the
      ``["[[wikilink]]"]`` convention);
    - scalars bare unless quoting is required for an exact round-trip
      through :func:`parse_frontmatter`.

    Returns the ``---\\n...\\n---\\n`` block (no trailing blank line —
    callers append the body).
    """
    ordered = [k for k in FRONTMATTER_FIELD_ORDER if k in fields]
    ordered += [k for k in fields if k not in FRONTMATTER_FIELD_ORDER]

    lines: list[str] = ["---"]
    for key in ordered:
        value = fields[key]
        if value is None or value == "":
            continue
        if isinstance(value, list):
            if value:
                items = ", ".join(
                    dump_list_item(item, always_quote=key in _ALWAYS_QUOTED_LIST_KEYS)
                    for item in value
                )
                lines.append(f"{key}: [{items}]")
            else:
                lines.append(f"{key}: []")
        else:
            lines.append(f"{key}: {dump_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def get_body(content: str) -> str:
    """Return markdown content after the frontmatter block.

    If no frontmatter is found, returns the entire content.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return content
    return content[match.end() :]


def extract_title(content: str, stem: str) -> str:
    """Extract the display title from a vault note.

    Searches the note body for the first top-level ``# `` heading (a single
    hash followed by a space, never ``##`` or deeper).  Falls back to the
    filename *stem* converted to title-case if no heading is found.

    This is the canonical title-extraction function for the vault.  All
    scripts that need a note title should call this instead of duplicating
    the logic.  See ARC-009.

    Args:
        content: Full note content (frontmatter + body).
        stem: Filename stem (without extension) used as fallback title.

    Returns:
        Title string -- either the heading text or the humanized stem.
    """
    body = get_body(content)
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return stem.replace("-", " ").title()


# ---------------------------------------------------------------------------
# Slug utility
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    """Convert text to a kebab-case filename slug.

    Lowercases the text, replaces spaces and underscores with hyphens,
    transliterates non-ASCII characters to their closest ASCII equivalent
    (e.g. "é" -> "e"), removes remaining special characters, and collapses
    multiple consecutive hyphens.

    Titles that are entirely non-transliterable (e.g. CJK) would otherwise
    collapse to an empty slug and collide with each other; in that case a
    stable hash of the original title is used instead so distinct titles
    never collide. Purely-ASCII titles that strip to nothing (e.g. "!!!")
    still return "" -- vault_new.py relies on that to reject garbage titles.
    """
    has_non_ascii = any(ord(ch) > 127 for ch in text)
    slug = text.lower().strip()
    slug = slug.replace(" ", "-").replace("_", "-")
    # Transliterate accented/non-ASCII characters before stripping specials
    # so e.g. "café" -> "cafe" instead of "caf".
    slug = unicodedata.normalize("NFKD", slug).encode("ascii", "ignore").decode()
    slug = _SLUG_SPECIAL_RE.sub("", slug)
    slug = _SLUG_MULTI_HYPHEN_RE.sub("-", slug)
    slug = slug.strip("-")
    if not slug and has_non_ascii:
        slug = "note-" + hashlib.sha1(text.encode()).hexdigest()[:8]
    return slug
