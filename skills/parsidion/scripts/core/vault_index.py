"""Note indexing, search, frontmatter parsing, and context building.

Provides vault note discovery, frontmatter/body parsing, metadata-based search
(by tag, project, type, recency), and context block construction for hook injection.

This module is part of the vault_common split (ARC-005).  All public symbols
are re-exported from ``vault_common`` for backward compatibility.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .subproc_util import run_with_pgkill
from .vault_config import _parse_list_item, _parse_scalar, _split_list_items, get_config
from .vault_hooks import env_without_claudecode
from .vault_path import (
    EXCLUDE_DIRS,
    SCRIPTS_DIR,
    VAULT_DIRS,
    get_embeddings_db_path,
    is_path_inside_vault,
    is_symlink_inside_vault,
    resolve_vault,
)

# ARC-005: the canonical frontmatter key order lives in the note contract
# module (a leaf with no imports, so this cannot cycle).
from note_schema import FRONTMATTER_FIELD_ORDER

__all__: list[str] = [
    # Constants (re-exported from vault_path for convenience)
    "VAULT_DIRS",
    "EXCLUDE_DIRS",
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
    # Note search
    "find_notes_by_project",
    "find_notes_by_tag",
    "find_notes_by_type",
    "find_recent_notes",
    "all_vault_notes",
    "all_vault_notes_walk",
    "read_note_summary",
    # Context building
    "build_context_block",
    "build_compact_index",
    # DB helpers
    "ensure_note_index_schema",
    "query_note_index",
    "load_graph_metadata",
    # Index-rebuild subprocess owner (ARC-004)
    "run_index_rebuild",
    "note_index_age",
    # Graph parsing
    "parse_related_stems",
    # Private helpers re-exported because tests reach into them via the
    # vault_index shim (e.g. ``vault_index._walk_vault_notes(tmp_vault)``).
    "_find_notes_by_project_walk",
    "_find_notes_by_tag_walk",
    "_find_notes_by_type_walk",
    "_find_recent_notes_walk",
    "_walk_vault_notes",
]

# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?\n)---\s*\n", re.DOTALL)
_YAML_LIST_INLINE_RE = re.compile(r"^\[(.*)]\s*$")
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
# query_note_index defaults to a 200-row cap (sufficient for its original
# context-injection callers). The find_notes_by_* wrappers semantically return
# EVERY matching note (the walk does), so they pass this effectively-unbounded
# limit to override that cap.
_FIND_ALL_LIMIT = 10**9
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

    **Supported YAML subset** (stdlib-only; not a full YAML 1.2 parser):

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
    - Trailing inline comments (``# comment``) are stripped from scalar
      values, respecting surrounding quotes.

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
            current_list.append(_parse_list_item(stripped[2:].strip()))
            result[current_key] = current_list
            continue

        # If we were collecting a list and hit a non-list line, close it
        if current_list is not None:
            current_list = None

        # Skip blank lines and comments
        if not stripped or stripped.startswith("#"):
            continue

        # key: value pair
        colon_idx = line.find(":")
        if colon_idx == -1:
            continue

        key = line[:colon_idx].strip()
        value_str = line[colon_idx + 1 :].strip()

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
        list_match = _YAML_LIST_INLINE_RE.match(value_str)
        if list_match:
            inner = list_match.group(1).strip()
            if not inner:
                result[key] = []
            else:
                items = [
                    _parse_list_item(item.strip()) for item in _split_list_items(inner)
                ]
                result[key] = items
            current_list = None
            continue

        # Scalar value
        result[key] = _parse_scalar(value_str)
        current_list = None

    # Flush any remaining block at end of frontmatter
    _flush_block()

    return result


# ---------------------------------------------------------------------------
# Frontmatter serialization (ARC-005)
# ---------------------------------------------------------------------------

# Characters that make a bare YAML scalar ambiguous for the subset parser
# above (or for the TS parser in visualizer/lib/frontmatter.ts).
_YAML_SPECIAL_PREFIXES: tuple[str, ...] = (
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
_YAML_COERCED_WORDS: frozenset[str] = frozenset(
    {"true", "yes", "false", "no", "null", "~", ""}
)
# List fields whose items are always double-quoted: ``related`` holds
# ``[[wikilinks]]`` and the canonical form (CLAUDE.md conventions,
# visualizer/lib/frontmatter.ts) is ``related: ["[[a]]", "[[b]]"]``.
_ALWAYS_QUOTED_LIST_KEYS: frozenset[str] = frozenset({"related"})


def _scalar_needs_quotes(text: str) -> bool:
    """Return True when a bare YAML scalar would not round-trip exactly."""
    if not text or text != text.strip():
        return True
    if text[0] in _YAML_SPECIAL_PREFIXES:
        return True
    if ": " in text or text.endswith(":"):
        # Either a mapping indicator for the parser or an inline-comment /
        # key-value split hazard.
        return True
    if " #" in text:
        return True  # _strip_inline_comment would drop the tail
    if text.lower() in _YAML_COERCED_WORDS:
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


def _quote_yaml(text: str) -> str:
    """Wrap *text* in YAML quotes the subset parser strips on read.

    Single quotes are preferred when the value contains a double quote (the
    inline-list splitter toggles on double quotes), double quotes otherwise.
    Values containing both quote characters are double-quoted with ``\\"``
    escapes — the documented best-effort limit of the parser subset.
    """
    if '"' in text and "'" not in text:
        return f"'{text}'"
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _format_scalar(value: Any) -> str:
    """Render one frontmatter scalar value (non-list) as a YAML string.

    Only ``str`` values are ever quoted: a bare ``3`` (int) parses back to
    int 3, but a string ``"3"`` must be quoted or the parser would coerce it
    to an int and break the round-trip.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str) and _scalar_needs_quotes(value):
        return _quote_yaml(value)
    return str(value)


def _format_list_item(item: Any, *, always_quote: bool) -> str:
    """Render one inline-array item.

    List items are never type-coerced by the parser, so bare items only need
    quoting for structural characters (``[],`` and quote characters that would
    confuse the splitter or the item parser) and for ``: `` (a plain YAML
    scalar may not contain a colon+space — Obsidian's parser rejects it even
    though the subset parser here round-trips).
    """
    text = str(item)
    structural = any(ch in text for ch in ",[]\"'") or ": " in text
    if always_quote or (structural or text != text.strip() or not text):
        return _quote_yaml(text)
    return text


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
                    _format_list_item(
                        item, always_quote=key in _ALWAYS_QUOTED_LIST_KEYS
                    )
                    for item in value
                )
                lines.append(f"{key}: [{items}]")
            else:
                lines.append(f"{key}: []")
        else:
            lines.append(f"{key}: {_format_scalar(value)}")
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


# ---------------------------------------------------------------------------
# Note index DB helpers
# ---------------------------------------------------------------------------


def ensure_note_index_schema(conn: sqlite3.Connection) -> None:
    """Create the note_index table and indexes if they don't exist.

    Also idempotently adds the ``date`` column to databases created before
    Phase 2 of the memory-enhancements plan (point-in-time search support).

    Args:
        conn: An open sqlite3.Connection (caller sets WAL mode).
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS note_index (
            stem           TEXT    NOT NULL PRIMARY KEY,
            path           TEXT    NOT NULL,
            folder         TEXT    NOT NULL DEFAULT '',
            title          TEXT    NOT NULL DEFAULT '',
            summary        TEXT    NOT NULL DEFAULT '',
            tags           TEXT    NOT NULL DEFAULT '',
            note_type      TEXT    NOT NULL DEFAULT '',
            project        TEXT    NOT NULL DEFAULT '',
            confidence     TEXT    NOT NULL DEFAULT '',
            mtime          REAL    NOT NULL DEFAULT 0.0,
            related        TEXT    NOT NULL DEFAULT '',
            is_stale       INTEGER NOT NULL DEFAULT 0,
            incoming_links INTEGER NOT NULL DEFAULT 0,
            date           TEXT    NOT NULL DEFAULT '',
            prompt_version TEXT    NOT NULL DEFAULT ''
        )
        """
    )
    # Migration: add date column to pre-existing databases (CREATE IF NOT EXISTS
    # does not add columns to an already-present table).
    cols = {row[1] for row in conn.execute("PRAGMA table_info(note_index)")}
    if "date" not in cols:
        conn.execute("ALTER TABLE note_index ADD COLUMN date TEXT NOT NULL DEFAULT ''")
    # ENH-008 Step 3: prompt_version column for slicing note quality by the
    # prompt that produced each AI-generated note.
    if "prompt_version" not in cols:
        conn.execute(
            "ALTER TABLE note_index ADD COLUMN prompt_version TEXT NOT NULL DEFAULT ''"
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ni_folder    ON note_index(folder)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ni_note_type ON note_index(note_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ni_project   ON note_index(project)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ni_mtime     ON note_index(mtime DESC)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ni_tags      ON note_index(tags)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ni_date      ON note_index(date)")
    conn.commit()


def _paths_from_rows(rows: list[Any], vault_root_resolved: Path) -> list[Path]:
    """Re-validate DB-derived path strings against the vault root.

    SEC-005 / SEC-130: a tampered ``embeddings.db`` can store any path string;
    never trust SQLite output. Each path must exist and resolve inside the
    vault. Follows the precedent established inline in ``query_note_index``
    (the original vault_index.py:373-420 containment guard).

    Args:
        rows: Rows from a ``SELECT path ...`` query. Each row is either a
            ``(path_str,)`` tuple/list or a bare string.
        vault_root_resolved: The resolved vault root to check containment against.

    Returns:
        List of existing, in-vault ``Path`` objects (unsorted -- caller's SQL
        determines order).
    """
    result: list[Path] = []
    for row in rows:
        path_str = row[0] if isinstance(row, (tuple, list)) else str(row)
        p = Path(path_str)
        if p.exists() and is_path_inside_vault(p, vault_root_resolved):
            result.append(p)
    return result


def _note_index_enabled(vault: str | Path | None = None) -> bool:  # noqa: ARG001
    """Whether DB-first note reads are enabled.

    Reads ``search.use_note_index`` (default ``true``). When false, every
    ``find_notes_by_*`` and ``all_vault_notes`` call takes the filesystem-walk
    path regardless of DB availability -- the user-facing escape hatch when
    index staleness is suspected. The *vault* arg is accepted for API symmetry
    with the callers; ``get_config`` resolves the vault via the cached
    ``load_config()`` (CLAUDE_VAULT-aware, so tests with ``tmp_vault`` see the
    right config without an explicit thread).
    """
    return bool(get_config("search", "use_note_index", True))


def _build_note_index_where(
    *,
    tag: str | None = None,
    folder: str | None = None,
    note_type: str | None = None,
    project: str | None = None,
    recent_days: int | None = None,
    changed_since: str | None = None,
    as_of: str | None = None,
) -> tuple[str, list[object]]:
    """Build the note_index metadata-filter WHERE clause (QA-009).

    The single home for the condition assembly previously duplicated
    between ``query_note_index`` here and ``cli.search.metadata.query``
    (par-mem similarity 0.908). Callers keep their own SELECT lists,
    result mapping, and schema/containment guards.

    SECURITY: The SQL WHERE clause is assembled from literal condition
    fragments only -- no column names are ever derived from external input.
    All filter values are passed as bound parameters (?). Column names used
    form a static whitelist: tags, folder, note_type, project, mtime, date.
    Any future addition of a user-supplied column name must be added to
    this whitelist and reviewed for injection risk.

    Args:
        tag: Exact tag token to match in the comma-separated tags column.
        folder: Exact folder name to match.
        note_type: Exact note_type value to match.
        project: Exact project value to match.
        recent_days: Only return notes modified within this many days.
        changed_since: Only return notes modified on/after this ISO datetime.
        as_of: Only return notes whose frontmatter date is on/before this
            ISO date (empty dates excluded).

    Returns:
        ``(where, params)`` -- ``where`` is "" or "WHERE c1 AND c2 ...";
        ``params`` holds the bound values in placeholder order.
    """
    conditions: list[str] = []
    params: list[object] = []

    if tag is not None:
        # 4-pattern exact-token match to avoid partial hits (e.g. "python"
        # must not match "python-decorator").  Tags are stored as
        # ", ".join(sorted(tags_list)) -- canonical format enforced at write
        # time in update_index.py and build_embeddings.py.  See ARC-004.
        conditions.append("(tags = ? OR tags LIKE ? OR tags LIKE ? OR tags LIKE ?)")
        params.extend([tag, f"{tag},%", f"%, {tag}", f"%, {tag},%"])

    if folder is not None:
        conditions.append("folder = ?")
        params.append(folder)

    if note_type is not None:
        conditions.append("note_type = ?")
        params.append(note_type)

    if project is not None:
        conditions.append("project = ?")
        params.append(project)

    if recent_days is not None:
        cutoff = (datetime.now() - timedelta(days=recent_days)).timestamp()
        conditions.append("mtime >= ?")
        params.append(cutoff)

    if changed_since is not None:
        cutoff = datetime.fromisoformat(changed_since).timestamp()
        conditions.append("mtime >= ?")
        params.append(cutoff)

    if as_of is not None:
        # ISO YYYY-MM-DD strings sort lexicographically; exclude empty dates.
        conditions.append("date != '' AND date <= ?")
        params.append(as_of)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, params


def query_note_index(
    *,
    tag: str | None = None,
    folder: str | None = None,
    note_type: str | None = None,
    project: str | None = None,
    recent_days: int | None = None,
    limit: int = 200,
    vault: str | Path | None = None,
) -> list[Path] | None:
    """Query the note_index table in embeddings.db for fast metadata filtering.

    Returns None (not []) if the DB is absent or the table is missing,
    signalling the caller to fall back to a file walk. An empty result set
    is returned as ``[]`` and is distinguishable from ``None`` -- this is what
    makes the DB-first ``find_notes_by_*`` fallback correct (a project with
    zero notes returns ``[]``, not the walk fallback).

    Args:
        tag: Exact tag token to match in the comma-separated tags column.
        folder: Exact folder name to match.
        note_type: Exact note_type value to match.
        project: Exact project value to match.
        recent_days: Only return notes modified within this many days.
        limit: Maximum number of results (default 200).
        vault: Optional vault path used to locate embeddings.db and to validate
            returned paths against. Defaults to resolve_vault().

    Returns:
        List of existing Paths sorted by mtime descending, or None on DB error.
    """
    if isinstance(vault, str):  # be liberal: accept str paths from callers
        vault = Path(vault)
    db_path = get_embeddings_db_path(vault)
    if not db_path.exists():
        return None

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return None

    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='note_index'"
        ).fetchone()
        if row is None:
            return None

        # QA-009: shared WHERE builder (see _build_note_index_where for the
        # injection-safety contract).
        where, params = _build_note_index_where(
            tag=tag,
            folder=folder,
            note_type=note_type,
            project=project,
            recent_days=recent_days,
        )
        sql = f"SELECT path FROM note_index {where} ORDER BY mtime DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        # SEC-005 / SEC-130: paths from SQLite are re-validated against the
        # vault root via _paths_from_rows -- a tampered embeddings.db cannot
        # inject reads outside the vault.
        vault_root_resolved = (vault or resolve_vault()).resolve()
        return _paths_from_rows(rows, vault_root_resolved)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Vault note traversal
# ---------------------------------------------------------------------------


def _walk_vault_notes(vault: str | Path | None = None) -> list[Path]:
    """Walk the vault tree and return all .md files, excluding EXCLUDE_DIRS, CLAUDE.md, TAGS.md, and MANIFEST.md.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().
    """
    if isinstance(vault, str):  # be liberal: accept str paths from callers
        vault = Path(vault)
    vault = vault or resolve_vault()
    notes: list[Path] = []
    if not vault.is_dir():
        return notes

    # SEC-106: resolve once so the symlink guard is not defeated by a
    # symlinked vault root. ``Templates/`` is excluded via EXCLUDE_DIRS
    # *before* the symlink guard runs, so the intentional
    # ``Templates -> skills/parsidion/templates`` symlink is preserved.
    vault_root_resolved = vault.resolve()

    for dirpath, dirnames, filenames in os.walk(vault):
        # Prune excluded directories in-place so os.walk skips them
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            if Path(dirpath) == vault and fname in ("CLAUDE.md", "TAGS.md"):
                continue
            if fname == "MANIFEST.md":
                continue
            p = Path(dirpath) / fname
            # SEC-106: skip symlinked .md files whose target escapes the
            # vault; ``os.walk`` lists them like regular files.
            if not is_symlink_inside_vault(p, vault_root_resolved):
                continue
            notes.append(p)

    return notes


def _find_notes_by_field(
    field: str, value: str, vault: str | Path | None = None
) -> list[Path]:
    """Find all notes where a frontmatter *field* matches *value* (case-insensitive).

    For scalar fields (``project``, ``type``), matches the value directly.
    For list fields (``tags``), matches if any element equals *value*.

    Args:
        field: The frontmatter field name to search (e.g. ``"project"``).
        value: The target value to match (compared case-insensitively).
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        List of matching note paths.
    """
    matches: list[Path] = []
    target = value.lower()
    for note_path in _walk_vault_notes(vault):
        try:
            content = note_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = parse_frontmatter(content)
        field_val = fm.get(field)
        if isinstance(field_val, str) and field_val.lower() == target:
            matches.append(note_path)
        elif isinstance(field_val, list):
            if any(
                isinstance(item, str) and item.lower() == target for item in field_val
            ):
                matches.append(note_path)
    return matches


# ---------------------------------------------------------------------------
# Walk-based implementations (fallback + differential-test oracle).
#
# These iterate the filesystem directly via _walk_vault_notes. They are kept
# verbatim as the no-DB fallback for the public DB-first find_notes_by_*
# functions, and as the oracle the differential tests compare the DB path
# against so the two cannot silently drift.
# ---------------------------------------------------------------------------


def _find_notes_by_project_walk(
    project: str, vault: str | Path | None = None
) -> list[Path]:
    """Walk fallback for :func:`find_notes_by_project`."""
    return _find_notes_by_field("project", project, vault=vault)


def _find_notes_by_tag_walk(tag: str, vault: str | Path | None = None) -> list[Path]:
    """Walk fallback for :func:`find_notes_by_tag`."""
    return _find_notes_by_field("tags", tag, vault=vault)


def _find_notes_by_type_walk(
    note_type: str, vault: str | Path | None = None
) -> list[Path]:
    """Walk fallback for :func:`find_notes_by_type`."""
    return _find_notes_by_field("type", note_type, vault=vault)


def _find_recent_notes_walk(
    days: int = 3, vault: str | Path | None = None
) -> list[Path]:
    """Walk fallback for :func:`find_recent_notes`."""
    cutoff = datetime.now() - timedelta(days=days)
    cutoff_ts = cutoff.timestamp()
    recent: list[tuple[float, Path]] = []

    for note_path in _walk_vault_notes(vault):
        try:
            mtime = note_path.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff_ts:
            recent.append((mtime, note_path))

    recent.sort(key=lambda x: x[0], reverse=True)
    return [path for _, path in recent]


def find_notes_by_project(project: str, vault: str | Path | None = None) -> list[Path]:
    """Find all notes with a matching ``project`` field in frontmatter.

    Reads ``note_index`` when available and ``search.use_note_index`` is true
    (default); falls back to a filesystem walk so behaviour is unchanged on a
    vault with no embeddings.db.
    """
    if _note_index_enabled(vault):
        result = query_note_index(project=project, vault=vault, limit=_FIND_ALL_LIMIT)
        if result is not None:
            return result
    return _find_notes_by_project_walk(project, vault=vault)


def find_notes_by_tag(tag: str, vault: str | Path | None = None) -> list[Path]:
    """Find all notes containing the given tag in their ``tags`` list.

    DB-first with walk fallback; see :func:`find_notes_by_project`.
    """
    if _note_index_enabled(vault):
        result = query_note_index(tag=tag, vault=vault, limit=_FIND_ALL_LIMIT)
        if result is not None:
            return result
    return _find_notes_by_tag_walk(tag, vault=vault)


def find_notes_by_type(note_type: str, vault: str | Path | None = None) -> list[Path]:
    """Find all notes with a matching ``type`` field in frontmatter.

    DB-first with walk fallback; see :func:`find_notes_by_project`.
    """
    if _note_index_enabled(vault):
        result = query_note_index(
            note_type=note_type, vault=vault, limit=_FIND_ALL_LIMIT
        )
        if result is not None:
            return result
    return _find_notes_by_type_walk(note_type, vault=vault)


def find_recent_notes(days: int = 3, vault: str | Path | None = None) -> list[Path]:
    """Find notes modified within the last *days* days, sorted by mtime descending.

    DB-first with walk fallback; see :func:`find_notes_by_project`.
    """
    if _note_index_enabled(vault):
        result = query_note_index(recent_days=days, vault=vault, limit=_FIND_ALL_LIMIT)
        if result is not None:
            return result
    return _find_recent_notes_walk(days, vault=vault)


def read_note_summary(path: Path, max_lines: int = 5) -> str:
    """Read a note and return its title (first ``#`` heading) plus the first
    *max_lines* of body content. Used for building context blocks.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""

    body = get_body(content)
    lines = body.strip().splitlines()

    title: str = path.stem  # fallback title
    body_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            body_start = i + 1
            break

    # Collect up to max_lines of non-empty body content after the title
    summary_lines: list[str] = []
    for line in lines[body_start:]:
        stripped = line.strip()
        if not stripped:
            continue
        # Skip HTML comments
        if stripped.startswith("<!--"):
            continue
        summary_lines.append(stripped)
        if len(summary_lines) >= max_lines:
            break

    result = title
    if summary_lines:
        result += "\n" + "\n".join(summary_lines)
    return result


def all_vault_notes(vault: str | Path | None = None) -> list[Path]:
    """Return all ``.md`` files in the vault (excluding ``EXCLUDE_DIRS``, ``CLAUDE.md``, ``TAGS.md``, ``MANIFEST.md``).

    DB-first (follow-up to ENH-004): reads ``note_index`` when available and
    falls back to the filesystem walk otherwise. ``note_index`` and
    ``note_embeddings`` share ``embeddings.db``, so callers that already depend
    on embeddings being current gain no new staleness from this.

    Callers that require the AUTHORITATIVE current filesystem set -- index
    builders (``update_index.py``, ``build_embeddings.py``) and mutation paths
    (``doctor/*``, ``vault_merge``, ``vault_export``, backlink validation in
    ``vault_links``) -- must call :func:`all_vault_notes_walk` instead: a stale
    index must never silently change what those operations see. The escape
    hatch is ``search.use_note_index: false`` in config (forces the walk).

    Args:
        vault: Optional vault path. Defaults to resolve_vault().
    """
    if _note_index_enabled(vault):
        result = query_note_index(vault=vault, limit=_FIND_ALL_LIMIT)
        if result is not None:
            return result
    return _walk_vault_notes(vault)


def all_vault_notes_walk(vault: str | Path | None = None) -> list[Path]:
    """Authoritative filesystem enumeration of every vault note.

    Always walks the tree -- never reads ``note_index``. Use this wherever the
    caller makes a write/mutation decision based on the note set, or is itself
    populating the index (a stale DB view would be a silent bug there). For
    pure read/display callers that already tolerate index staleness on par
    with ``find_notes_by_*``, prefer :func:`all_vault_notes`.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().
    """
    return _walk_vault_notes(vault)


def note_index_age(vault: str | Path | None = None) -> float:
    """Return how stale ``note_index`` is, in seconds.

    Computes ``max(on-disk .md mtime) - max(note_index mtime)``. Returns
    ``0.0`` when the index is current or ahead, when the DB is absent, or
    when either source is unreadable. A positive value means on-disk notes
    exist that the index does not yet reflect -- the DB-first read path may
    return stale results, and the user should rebuild via
    ``update_index.py``.

    This is a *signal*, not a trigger: read paths never auto-rebuild, because
    a read doing surprising write work is worse than a slightly stale result.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().
    """
    resolved = vault or resolve_vault()
    db_path = get_embeddings_db_path(resolved)
    if not db_path.exists():
        return 0.0
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return 0.0
    try:
        row = conn.execute("SELECT MAX(mtime) FROM note_index").fetchone()
    except sqlite3.Error:
        conn.close()
        return 0.0
    conn.close()
    db_max = float(row[0]) if row and row[0] is not None else 0.0

    disk_max = 0.0
    for p in _walk_vault_notes(resolved):
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > disk_max:
            disk_max = m
    if disk_max <= db_max:
        return 0.0
    return disk_max - db_max


# ---------------------------------------------------------------------------
# Context building
# ---------------------------------------------------------------------------


def build_context_block(notes: list[Path], max_chars: int = 4000) -> str:
    """Build a context string from a list of notes, respecting *max_chars* budget.

    Each note is formatted as::

        ### Note Title (folder/filename)
        [summary lines]

    Stops adding notes when approaching *max_chars*.
    """
    parts: list[str] = []
    char_count = 0

    vault_root = resolve_vault()
    for note_path in notes:
        # Determine the relative folder/filename label
        try:
            rel = note_path.relative_to(vault_root)
        except ValueError:
            rel = Path(note_path.parent.name) / note_path.name

        summary = read_note_summary(note_path)
        if not summary:
            continue

        # Extract title from first line of summary
        summary_lines = summary.splitlines()
        title = summary_lines[0] if summary_lines else note_path.stem
        body = "\n".join(summary_lines[1:]) if len(summary_lines) > 1 else ""

        block = f"### {title} ({rel})\n"
        if body:
            block += body + "\n"
        block += "\n"

        if char_count + len(block) > max_chars:
            break

        parts.append(block)
        char_count += len(block)

    return "".join(parts).rstrip("\n")


def _load_note_index_map() -> dict[str, tuple[str, str, str]] | None:
    """Load a stem -> (title, tags, folder) map from the note_index DB.

    QA-005: Used by build_compact_index and build_context_block to avoid
    N+1 file reads when the DB is available.

    Returns:
        Dict mapping stem to (title, tags_str, folder), or None if DB unavailable.
    """
    db_path = get_embeddings_db_path()
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return None
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='note_index'"
        ).fetchone()
        if row is None:
            return None
        rows = conn.execute(
            "SELECT stem, title, tags, folder FROM note_index"
        ).fetchall()
        return {r[0]: (r[1], r[2], r[3]) for r in rows}
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def parse_related_stems(related_str: str) -> list[str]:
    """Extract note stems from a ``note_index`` ``related`` column value.

    The indexer stores ``related`` as bare comma-separated stems
    (``", ".join(stems)``); legacy/raw notes may use ``[[wikilink]]`` form.
    Both are accepted for robustness.  Mirrors ``build_graph.parse_related_stems``
    so the vault reads the graph the same way it writes it.

    Args:
        related_str: Raw value of the ``related`` column (or frontmatter field).

    Returns:
        Ordered list of bare note stems (no brackets, stripped).
    """
    if not related_str:
        return []
    if "[[" in related_str:
        return re.findall(r"\[\[([^\]]+)\]\]", related_str)
    return [s.strip() for s in related_str.split(",") if s.strip()]


def load_graph_metadata() -> dict[str, dict[str, object]] | None:
    """Load per-note graph metadata from the ``note_index`` table.

    Used by ``session_start_hook`` for 1-hop neighbor expansion (Tier 1) and
    graph-aware reranking (Tier 2).  Returns ``None`` when ``embeddings.db``
    or the ``note_index`` table is absent, signalling the caller to skip graph
    features gracefully -- mirrors ``query_note_index``'s None-sentinel
    contract so callers fall back to the existing retrieval path.

    Paths are returned verbatim (unvalidated); callers that resolve stems to
    filesystem paths must apply the SEC-005 vault-containment guard themselves.

    Returns:
        Mapping of ``stem -> {"path": str, "related": str,
        "incoming_links": int, "tags": str}``, or ``None`` on DB error.
    """
    db_path = get_embeddings_db_path()
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return None
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='note_index'"
        ).fetchone()
        if row is None:
            return None
        rows = conn.execute(
            "SELECT stem, path, related, incoming_links, tags FROM note_index"
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return {
        r[0]: {
            "path": r[1],
            "related": r[2] or "",
            "incoming_links": int(r[3] or 0),
            "tags": r[4] or "",
        }
        for r in rows
    }


def build_compact_index(
    notes: list[Path], max_chars: int = 2000, vault: Path | None = None
) -> str:
    """Build a compact one-line-per-note index: title [tags] (folder).

    Much smaller than build_context_block -- use when vault is large or
    token budget is tight. Full note content is available via the parsidion skill.

    QA-005: Queries note_index DB first (title, tags, folder already indexed);
    falls back to file reads only when DB is absent.

    Args:
        notes: List of note paths to include.
        max_chars: Maximum total characters before truncating with a count line.
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        A compact index string, or empty string if notes is empty.
    """
    vault = vault or resolve_vault()
    # QA-005: Try DB-backed lookup to avoid N+1 file reads
    index_map = _load_note_index_map()
    lines: list[str] = []
    total = 0
    for path in notes:
        db_entry = index_map.get(path.stem) if index_map else None
        if db_entry:
            title, tags_str, folder = db_entry
            tags = (
                [t.strip() for t in tags_str.split(",") if t.strip()]
                if tags_str
                else []
            )
            folder = folder or "root"
        else:
            # Fallback: read the file
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            fm = parse_frontmatter(content)
            title = extract_title(content, path.stem)
            tags = fm.get("tags", [])
            if isinstance(tags, str):
                tags = [tags]
            folder = path.parent.name if path.parent != vault else "root"
        tag_str = " ".join(f"`{t}`" for t in tags) if tags else ""
        entry = f"- [[{path.stem}]] {title} ({folder})" + (
            " — " + tag_str if tag_str else ""
        )
        total += len(entry) + 1
        if total > max_chars:
            lines.append(
                f"- ... ({len(notes) - len(lines)} more notes, "
                "use parsidion skill to browse)"
            )
            break
        lines.append(entry)

    if not lines:
        return ""

    header = (
        "**Available vault notes** (compact index — "
        "use `parsidion` skill to load full content):\n"
    )
    return header + "\n".join(lines)


# ---------------------------------------------------------------------------
# Index-rebuild subprocess owner (ARC-004)
# ---------------------------------------------------------------------------


def run_index_rebuild(
    vault: Path | None = None,
    *,
    rebuild_graph: bool | None = None,
    graph_include_daily: bool | None = None,
    timeout: float = 300.0,
    scripts_dir: Path | None = None,
) -> tuple[str, subprocess.CompletedProcess[str] | None]:
    """Run ``update_index.py`` via ``uv run --no-project``.

    ARC-004: single owner of the index-rebuild subprocess contract shared by
    the installer, the MCP ``rebuild_index`` tool, and the summarizer queue.
    Previously three independent launchers disagreed on argv (the installer
    omitted ``--no-project``), environment (only the summarizer stripped
    ``CLAUDECODE``), script discovery, and timeout handling.

    Contract:

    - argv always starts ``["uv", "run", "--no-project", <update_index.py>]``
      so uv never discovers a ``pyproject.toml`` in the inherited cwd and
      syncs an unrelated project's dependencies;
    - the child environment is :func:`core.vault_hooks.env_without_claudecode`
      so a ``claude``-backed update does not refuse to nest;
    - timeout kills the whole process group
      (:func:`core.subproc_util.run_with_pgkill`), not just the parent.

    Script discovery: when *scripts_dir* is given it is used exclusively
    (missing script ⇒ ``("launch", None)`` — the installer relies on this to
    warn about a broken install target instead of rebuilding through some
    other copy). Otherwise the directory of this ``core`` package wins (keeps
    the subprocess consistent with the running code, ARC-021) with the
    installed ``~/.claude/skills/parsidion/scripts`` as fallback.

    Args:
        vault: Vault to rebuild; ``None`` omits ``--vault`` so
            ``resolve_vault()`` default precedence applies.
        rebuild_graph: Pass ``--rebuild-graph`` when True; ``None`` sends no
            flag.
        graph_include_daily: Pass ``--graph-include-daily`` when True (only
            meaningful with *rebuild_graph*); ``None`` sends no flag.
        timeout: Seconds before the process group is killed.
        scripts_dir: Directory containing ``update_index.py``; see discovery
            rules above.

    Returns:
        ``(reason, proc)`` as documented for
        :func:`core.subproc_util.run_with_pgkill` — ``("ok", CompletedProcess)``
        with any returncode, ``("launch", None)`` when uv or the script is
        unavailable, ``("timeout", None)``. Never raises.
    """
    if scripts_dir is not None:
        script = scripts_dir / "update_index.py"
        if not script.exists():
            return "launch", None
    else:
        source_dir = Path(__file__).resolve().parent.parent
        script = next(
            (
                candidate
                for candidate in (
                    source_dir / "update_index.py",
                    SCRIPTS_DIR / "update_index.py",
                )
                if candidate.exists()
            ),
            None,
        )
        if script is None:
            return "launch", None

    argv = ["uv", "run", "--no-project", str(script)]
    if vault is not None:
        argv += ["--vault", str(vault)]
    if rebuild_graph:
        argv.append("--rebuild-graph")
    if graph_include_daily:
        argv.append("--graph-include-daily")

    return run_with_pgkill(
        argv,
        cwd=vault,
        timeout=timeout,
        env=env_without_claudecode(),
    )
