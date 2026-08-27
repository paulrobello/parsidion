"""The Parsidion YAML subset -- the ONLY YAML this project understands.

ENH-024: this module is the single, shared implementation of the YAML
subset the tree's three parsers (config.yaml, note frontmatter, and
``vaults.yaml``) historically hand-rolled in three divergent copies.
Everything the project can read and write, in one place:

- ``key: value`` lines split on the first colon (:func:`split_key_value`);
- scalars -- str/int/float/bool (``true``/``false``/``yes``/``no``) and
  ``null``/``~`` (:func:`parse_scalar`), with pair-matching quote
  stripping for ``'`` and ``"`` (:func:`strip_quotes`);
- inline ``[a, b]`` arrays with quote- and escape-aware item splitting
  (:func:`parse_inline_list`, :func:`split_list_items`,
  :func:`parse_list_item`);
- trailing ``# comment`` stripping that respects quotes
  (:func:`strip_inline_comment`);
- one level of nested maps below a section header (the ``section:``
  walkers live in the consumers; the tokenization they walk with is here);
- the dump policy that guarantees an emitted scalar round-trips through
  the reader (:func:`scalar_needs_quotes`, :func:`quote_scalar`,
  :func:`dump_scalar`, :func:`dump_list_item`).

One historical variant is codified alongside the shared rules:
:func:`strip_quote_edges` is the vaults.yaml reader's permissive
edge-strip, kept (and tested here) so the reader's behavior is pinned
rather than silently unified with :func:`strip_quotes`.

This is a subset codification, not a YAML implementation. Anything
outside the list above -- anchors, tags, flow mappings, deeper nesting,
multi-document streams -- is unsupported everywhere, by design. New YAML
syntax must be added HERE, with tests in ``tests/test_yaml_lite.py``, in
one place; do not hand-roll a fourth dialect in a consumer. The
differential fixtures under ``tests/fixtures/yaml_lite/`` pin each
consumer's parse output to the pre-consolidation parsers.

Document-level structure (frontmatter ``---`` delimiters, block
sequences and multi-line scalars, config section nesting rules, the
vaults.yaml role model) deliberately stays in the consumers; this module
owns the token-level rules they share.
"""

from __future__ import annotations

import re
from typing import Any

__all__: list[str] = [
    # Tokenization
    "INLINE_LIST_RE",
    "split_key_value",
    "strip_quotes",
    "strip_quote_edges",
    "strip_inline_comment",
    "split_list_items",
    "parse_scalar",
    "parse_list_item",
    "parse_inline_list",
    # Dump policy
    "scalar_needs_quotes",
    "quote_scalar",
    "dump_scalar",
    "dump_list_item",
]

INLINE_LIST_RE = re.compile(r"^\[(.*)]\s*$")

# Characters that make a bare scalar ambiguous for the readers above (or
# for the TS parser in visualizer/lib/frontmatter.ts).
_SPECIAL_PREFIXES: tuple[str, ...] = (
    "-",
    "?",
    ":",
    "[",
    "]",
    "{",
    "}",
    ",",
    "#",
    "&",
    "*",
    "!",
    "|",
    ">",
    "'",
    '"',
    "%",
    "@",
    "`",
)
# Bare words the scalar reader coerces to bool/null instead of a string.
_COERCED_WORDS: frozenset[str] = frozenset(
    {"true", "yes", "false", "no", "null", "~", ""}
)


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------


def split_key_value(text: str) -> tuple[str, str] | None:
    """Split a ``key: value`` line on the first colon.

    Returns ``(key, value)`` with both sides stripped, or ``None`` when
    *text* contains no colon. URL-like values (``url: https://x/y``) keep
    their internal colons: only the first colon splits.
    """
    colon_idx = text.find(":")
    if colon_idx == -1:
        return None
    return text[:colon_idx].strip(), text[colon_idx + 1 :].strip()


def strip_quotes(value: str) -> str:
    """Strip a matching pair of surrounding ``'`` or ``"`` quotes.

    Quotes are stripped only when the first and last characters are the
    same quote character (``"x"`` and ``'x'`` yes; ``"x'`` unchanged, so
    the stray quote survives into the value). An inner scan never runs
    here -- see :func:`parse_list_item` for the double-quote unescaping
    the list emitters rely on.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def strip_quote_edges(value: str) -> str:
    """Strip every leading/trailing ``'``/``"`` character (vaults.yaml dialect).

    The historical ``vaults.yaml`` reader tokenizes names and paths with a
    permissive edge strip rather than :func:`strip_quotes` pair matching,
    so ``"'t'"`` reads back as ``t``. Codified here unchanged so the
    reader's behavior is pinned by tests instead of living inline.
    """
    return value.strip("'\"")


def strip_inline_comment(value: str) -> str:
    """Strip a trailing ``# comment`` from a value, respecting quotes.

    A ``#`` starts a comment only when preceded by a space or tab, so
    ``value#tag`` and ``https://x#anchor`` survive unquoted. A ``#``
    inside a quoted string never starts a comment.
    """
    in_quote: str | None = None
    for i, ch in enumerate(value):
        if in_quote:
            if ch == in_quote:
                in_quote = None
        elif ch in ('"', "'"):
            in_quote = ch
        elif ch == "#" and i > 0 and value[i - 1] in (" ", "\t"):
            return value[:i].rstrip()
    return value


def split_list_items(text: str) -> list[str]:
    """Split a comma-separated list, respecting quoted strings.

    SEC-033(c): an escaped double quote (``\\"``) does not close the
    string -- writers (vault_merge's frontmatter emitter) escape embedded
    quotes, and the split must not let one toggle the quote state and
    split mid-item.
    """
    items: list[str] = []
    current: list[str] = []
    in_quote: str | None = None

    for i, ch in enumerate(text):
        if in_quote:
            current.append(ch)
            if ch == in_quote:
                # The quote is escaped (does not close the string) only when
                # preceded by an odd run of backslashes.
                j = i - 1
                run = 0
                while j >= 0 and text[j] == "\\":
                    run += 1
                    j -= 1
                if run % 2 == 0:
                    in_quote = None
        elif ch in ('"', "'"):
            in_quote = ch
            current.append(ch)
        elif ch == ",":
            items.append("".join(current).strip())
            current = []
        else:
            current.append(ch)

    remaining = "".join(current).strip()
    if remaining:
        items.append(remaining)

    return items


def parse_scalar(value: str) -> Any:
    """Parse a scalar YAML value into a Python type.

    Handles booleans, None/null, integers, floats, quoted strings, and
    bare strings. Date strings (YYYY-MM-DD) are kept as strings for
    simplicity. Quoted values are never coerced: ``"true"`` and ``"42"``
    read back as strings, and ``''``/``""`` read back as the empty
    string, not null.
    """
    # Strip surrounding quotes; a quoted value is never coerced further
    unquoted = strip_quotes(value)
    if unquoted != value:
        return unquoted

    lower = value.lower()
    if lower in ("true", "yes"):
        return True
    if lower in ("false", "no"):
        return False
    if lower in ("null", "~", ""):
        return None

    # Try integer
    try:
        return int(value)
    except ValueError:
        pass

    # Try float
    try:
        return float(value)
    except ValueError:
        pass

    return value


def parse_list_item(value: str) -> str:
    """Parse a YAML list item, keeping it as a string.

    Unlike :func:`parse_scalar`, list items are never coerced to
    bool/int/float: frontmatter list fields (``tags``, ``sources``,
    ``related``) are always string-valued, and coercing e.g.
    ``tags: [2026, python]`` to an int makes the tag silently unfindable
    downstream. Surrounding quotes are stripped.

    SEC-033(c): double-quoted items unescape ``\\"`` → ``"`` and ``\\\\``
    → ``\\``, matching what the frontmatter emitters write.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        inner = value[1:-1]
        if value[0] == '"':
            out: list[str] = []
            i = 0
            while i < len(inner):
                if (
                    inner[i] == "\\"
                    and i + 1 < len(inner)
                    and inner[i + 1] in ('"', "\\")
                ):
                    out.append(inner[i + 1])
                    i += 2
                else:
                    out.append(inner[i])
                    i += 1
            return "".join(out)
        return inner
    return value


def parse_inline_list(value: str) -> list[str] | None:
    """Parse an inline ``[a, b]`` array, or return None if *value* is not one.

    Items are parsed with :func:`parse_list_item` (strings only, quotes
    stripped, double-quote escapes unescaped). ``[]`` parses as the empty
    list. An unterminated opening bracket (``[a, b``) does not match and
    returns None -- the caller then treats the value as a scalar.
    """
    list_match = INLINE_LIST_RE.match(value)
    if not list_match:
        return None
    inner = list_match.group(1).strip()
    if not inner:
        return []
    return [parse_list_item(item.strip()) for item in split_list_items(inner)]


# ---------------------------------------------------------------------------
# Dump policy (emission that round-trips through the readers above)
# ---------------------------------------------------------------------------


def scalar_needs_quotes(text: str) -> bool:
    """Return True when a bare YAML scalar would not round-trip exactly."""
    if not text or text != text.strip():
        return True
    if text[0] in _SPECIAL_PREFIXES:
        return True
    if ": " in text or text.endswith(":"):
        # Either a mapping indicator for the reader or an inline-comment /
        # key-value split hazard.
        return True
    if " #" in text:
        return True  # strip_inline_comment would drop the tail
    if text.lower() in _COERCED_WORDS:
        return True  # would parse as bool/null instead of the string
    try:
        int(text)
        return True
    except ValueError:
        pass
    try:
        float(text)
        return True
    except ValueError:
        pass
    return False


def quote_scalar(text: str) -> str:
    """Wrap *text* in YAML quotes the readers above strip on read.

    Single quotes are preferred when the value contains a double quote
    (the inline-list splitter toggles on double quotes), double quotes
    otherwise. Values containing both quote characters are double-quoted
    with ``\\"`` escapes -- the documented best-effort limit of the
    subset.
    """
    if '"' in text and "'" not in text:
        return f"'{text}'"
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def dump_scalar(value: Any) -> str:
    """Render one scalar value as a YAML string.

    Only ``str`` values are ever quoted: a bare ``3`` (int) parses back
    to int 3, but a string ``"3"`` must be quoted or the reader would
    coerce it to an int and break the round-trip.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str) and scalar_needs_quotes(value):
        return quote_scalar(value)
    return str(value)


def dump_list_item(item: Any, *, always_quote: bool = False) -> str:
    """Render one inline-array item.

    List items are never type-coerced by the readers, so bare items only
    need quoting for structural characters (``,[]`` and quote characters
    that would confuse the splitter or the item parser) and for ``: ``
    (a plain YAML scalar may not contain a colon+space -- Obsidian's
    parser rejects it even though the subset reader here round-trips).
    """
    text = str(item)
    structural = any(ch in text for ch in ",[]\"'") or ": " in text
    if always_quote or (structural or text != text.strip() or not text):
        return quote_scalar(text)
    return text
