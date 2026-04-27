"""Note indexing, search, frontmatter parsing, and context building.

Provides vault note discovery, frontmatter/body parsing, metadata-based search
(by tag, project, type, recency), and context block construction for hook injection.

This module is part of the vault_common split (ARC-005).  All public symbols
are re-exported from ``vault_common`` for backward compatibility.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from vault_config import _parse_scalar, _split_list_items
from vault_path import (
    EXCLUDE_DIRS,
    VAULT_DIRS,
    get_embeddings_db_path,
    resolve_vault,
)

__all__: list[str] = [
    # Constants (re-exported from vault_path for convenience)
    "VAULT_DIRS",
    "EXCLUDE_DIRS",
    # Frontmatter and content parsing
    "parse_frontmatter",
    "get_body",
    "extract_title",
    # Slug utility
    "slugify",
    # Note search
    "find_notes_by_project",
    "find_notes_by_tag",
    "find_notes_by_type",
    "find_recent_notes",
    "all_vault_notes",
    "read_note_summary",
    # Context building
    "build_context_block",
    "build_compact_index",
    # DB helpers
    "ensure_note_index_schema",
    "query_note_index",
]

# ---------------------------------------------------------------------------
# Compiled regexes
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?\n)---\s*\n", re.DOTALL)
_YAML_LIST_INLINE_RE = re.compile(r"^\[(.*)]\s*$")
_SLUG_SPECIAL_RE = re.compile(r"[^a-z0-9\-]")
_SLUG_MULTI_HYPHEN_RE = re.compile(r"-{2,}")

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
      Quoted items may contain commas.
    - Block sequence lists: ``key:`` followed by ``  - item`` lines.
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
            current_list.append(_parse_scalar(stripped[2:].strip()))
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
                    _parse_scalar(item.strip()) for item in _split_list_items(inner)
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
    removes special characters, and collapses multiple consecutive hyphens.
    """
    slug = text.lower().strip()
    slug = slug.replace(" ", "-").replace("_", "-")
    slug = _SLUG_SPECIAL_RE.sub("", slug)
    slug = _SLUG_MULTI_HYPHEN_RE.sub("-", slug)
    slug = slug.strip("-")
    return slug


# ---------------------------------------------------------------------------
# Note index DB helpers
# ---------------------------------------------------------------------------


def ensure_note_index_schema(conn: sqlite3.Connection) -> None:
    """Create the note_index table and indexes if they don't exist.

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
            incoming_links INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ni_folder    ON note_index(folder)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ni_note_type ON note_index(note_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ni_project   ON note_index(project)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ni_mtime     ON note_index(mtime DESC)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ni_tags      ON note_index(tags)")
    conn.commit()


def query_note_index(
    *,
    tag: str | None = None,
    folder: str | None = None,
    note_type: str | None = None,
    project: str | None = None,
    recent_days: int | None = None,
    limit: int = 200,
) -> list[Path] | None:
    """Query the note_index table in embeddings.db for fast metadata filtering.

    Returns None (not []) if the DB is absent or the table is missing,
    signalling the caller to fall back to a file walk.

    Args:
        tag: Exact tag token to match in the comma-separated tags column.
        folder: Exact folder name to match.
        note_type: Exact note_type value to match.
        project: Exact project value to match.
        recent_days: Only return notes modified within this many days.
        limit: Maximum number of results (default 200).

    Returns:
        List of existing Paths sorted by mtime descending, or None on DB error.
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

        # SECURITY: The SQL WHERE clause is assembled from literal condition fragments
        # only -- no column names are ever derived from external input.  All filter
        # values are passed as bound parameters (?).  Column names used below form a
        # static whitelist: tags, folder, note_type, project, mtime.  Any future
        # addition of a user-supplied column name must be added to this whitelist and
        # reviewed for injection risk.
        # Static whitelist (documentation only -- all conditions below are literals):
        #   _ALLOWED_QUERY_COLUMNS = {"tags", "folder", "note_type", "project", "mtime"}
        conditions: list[str] = []
        params: list[object] = []

        if tag is not None:
            # 4-pattern exact-token match to avoid partial hits (e.g. "python" must not
            # match "python-decorator").  Tags are stored as ", ".join(sorted(tags_list))
            # -- canonical format enforced at write time in update_index.py and
            # build_embeddings.py.  See ARC-004.
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

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT path FROM note_index {where} ORDER BY mtime DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        # SEC-005: Reject any path that resolves outside the vault -- guards
        # against a tampered embeddings.db injecting arbitrary file paths.
        vault_root_resolved = resolve_vault().resolve()
        return [
            p
            for (path_str,) in rows
            if (p := Path(path_str)).exists()
            and p.resolve().is_relative_to(vault_root_resolved)
        ]
    except sqlite3.Error:
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Vault note traversal
# ---------------------------------------------------------------------------


def _walk_vault_notes(vault: Path | None = None) -> list[Path]:
    """Walk the vault tree and return all .md files, excluding EXCLUDE_DIRS and CLAUDE.md.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().
    """
    vault = vault or resolve_vault()
    notes: list[Path] = []
    if not vault.is_dir():
        return notes

    for dirpath, dirnames, filenames in os.walk(vault):
        # Prune excluded directories in-place so os.walk skips them
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            if fname == "CLAUDE.md" and Path(dirpath) == vault:
                continue
            notes.append(Path(dirpath) / fname)

    return notes


def _find_notes_by_field(field: str, value: str) -> list[Path]:
    """Find all notes where a frontmatter *field* matches *value* (case-insensitive).

    For scalar fields (``project``, ``type``), matches the value directly.
    For list fields (``tags``), matches if any element equals *value*.

    Args:
        field: The frontmatter field name to search (e.g. ``"project"``).
        value: The target value to match (compared case-insensitively).

    Returns:
        List of matching note paths.
    """
    matches: list[Path] = []
    target = value.lower()
    for note_path in _walk_vault_notes():
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


def find_notes_by_project(project: str) -> list[Path]:
    """Find all notes with a matching ``project`` field in frontmatter."""
    result = query_note_index(project=project)
    if result is not None:
        return result
    return _find_notes_by_field("project", project)


def find_notes_by_tag(tag: str) -> list[Path]:
    """Find all notes containing the given tag in their ``tags`` list."""
    result = query_note_index(tag=tag)
    if result is not None:
        return result
    return _find_notes_by_field("tags", tag)


def find_notes_by_type(note_type: str) -> list[Path]:
    """Find all notes with a matching ``type`` field in frontmatter."""
    result = query_note_index(note_type=note_type)
    if result is not None:
        return result
    return _find_notes_by_field("type", note_type)


def find_recent_notes(days: int = 3) -> list[Path]:
    """Find notes modified within the last *days* days, sorted by mtime descending."""
    result = query_note_index(recent_days=days)
    if result is not None:
        return result
    cutoff = datetime.now() - timedelta(days=days)
    cutoff_ts = cutoff.timestamp()
    recent: list[tuple[float, Path]] = []

    for note_path in _walk_vault_notes():
        try:
            mtime = note_path.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff_ts:
            recent.append((mtime, note_path))

    recent.sort(key=lambda x: x[0], reverse=True)
    return [path for _, path in recent]


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


def all_vault_notes(vault: Path | None = None) -> list[Path]:
    """Return all ``.md`` files in the vault, excluding ``EXCLUDE_DIRS`` and ``CLAUDE.md``.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().
    """
    return _walk_vault_notes(vault)


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
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='note_index'"
        ).fetchone()
        if row is None:
            conn.close()
            return None
        rows = conn.execute(
            "SELECT stem, title, tags, folder FROM note_index"
        ).fetchall()
        conn.close()
        return {r[0]: (r[1], r[2], r[3]) for r in rows}
    except sqlite3.Error:
        return None


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
