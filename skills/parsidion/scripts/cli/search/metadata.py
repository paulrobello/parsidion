"""Metadata SQL query + all-notes fetcher + grep body filter (ARC-005).

Extracted from ``vault_search.py``. Three concerns that share the
``score=None`` result-dict shape: the metadata SQL query (``query``), the
all-notes-as-results helper used by the grep filter and the file-walk
fallback (``_get_all_notes_as_results``), and the regex body filter
(``_apply_grep_filter``).
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

import vault_common
from core.vault_index import _build_note_index_where
from vault_path import is_path_inside_vault

# SEC-020: one stderr note per process when DB-sourced paths get skipped.
_skipped_outside_warned = False

# SEC-021: bounds for --grep — giant patterns scale catastrophic
# backtracking, and unbounded body reads let one huge note dominate a scan.
_MAX_GREP_PATTERN_CHARS = 512
_MAX_GREP_SCAN_BYTES = 1024 * 1024  # 1 MiB per note


def query(
    *,
    tag: str | None = None,
    folder: str | None = None,
    note_type: str | None = None,
    project: str | None = None,
    recent_days: int | None = None,
    changed_since: str | None = None,
    as_of: str | None = None,
    limit: int = 50,
    vault: Path | None = None,
) -> list[dict[str, object]]:
    """Query the note_index table for metadata-filtered results.

    Returns an empty list (not None) if the DB is absent or table missing.

    Args:
        tag: Exact tag token to match.
        folder: Exact folder name to match.
        note_type: Exact note_type to match.
        project: Exact project name to match.
        recent_days: Notes modified within this many days.
        limit: Maximum result count.
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        List of result dicts with score set to null, sorted by mtime descending.
    """
    db_path = vault_common.get_embeddings_db_path(vault)
    if not db_path.exists():
        return []

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError:
        return []

    try:
        if (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='note_index'"
            ).fetchone()
            is None
        ):
            return []

        # Defensive: a note_index predating the `date` migration (e.g. an
        # embeddings-disabled vault never rebuilt) lacks the column. SELECTing
        # `date` would raise OperationalError and silently return []. Detect it
        # instead: non-date queries omit the column (still work); --as-of (which
        # genuinely needs it) warns and returns [].
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(note_index)").fetchall()
        }
        has_date = "date" in columns
        if as_of is not None and not has_date:
            print(
                "note_index schema is stale (no 'date' column); "
                "run update_index.py to enable --as-of.",
                file=sys.stderr,
            )
            return []

        # QA-009: shared WHERE builder — the condition assembly that was
        # duplicated with query_note_index lives once in
        # core.vault_index._build_note_index_where (injection-safety
        # contract documented there).
        where, params = _build_note_index_where(
            tag=tag,
            folder=folder,
            note_type=note_type,
            project=project,
            recent_days=recent_days,
            changed_since=changed_since,
            as_of=as_of,
        )
        date_col = ", date" if has_date else ""
        sql = (
            f"SELECT stem, path, folder, title, summary, tags, note_type, "
            f"project, confidence, mtime, related, is_stale, incoming_links"
            f"{date_col} "
            f"FROM note_index {where} ORDER BY mtime DESC LIMIT ?"
        )
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    results: list[dict[str, object]] = []
    for row in rows:
        d = dict(row)
        tags_str: str = d.get("tags", "") or ""
        related_str: str = d.get("related", "") or ""
        results.append(
            {
                "score": None,
                "stem": d.get("stem", ""),
                "title": d.get("title", ""),
                "folder": d.get("folder", ""),
                "tags": [t.strip() for t in tags_str.split(",") if t.strip()],
                "path": d.get("path", ""),
                "summary": d.get("summary", ""),
                "note_type": d.get("note_type", ""),
                "project": d.get("project", ""),
                "confidence": d.get("confidence", ""),
                "mtime": d.get("mtime"),
                "related": [r.strip() for r in related_str.split(",") if r.strip()],
                "is_stale": bool(d.get("is_stale", 0)),
                "incoming_links": d.get("incoming_links", 0),
                "date": d.get("date", ""),
            }
        )
    return results


def _get_all_notes_as_results(
    limit: int, vault: Path | None = None
) -> list[dict[str, Any]]:
    """Return all vault notes as result dicts suitable for grep filtering.

    Tries the note_index DB first; falls back to a file walk via
    ``vault_common.all_vault_notes()``.

    Args:
        limit: Maximum number of notes to return.
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        List of result dicts with ``score`` set to ``None``.
    """
    vault = vault or vault_common.resolve_vault()
    db_path = vault_common.get_embeddings_db_path(vault)
    if db_path.exists():
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='note_index'"
            ).fetchone()
            if row is not None:
                sql = (
                    "SELECT stem, path, folder, title, summary, tags, note_type, "
                    "project, confidence, mtime, related, is_stale, incoming_links "
                    "FROM note_index ORDER BY mtime DESC LIMIT ?"
                )
                rows = conn.execute(sql, (limit,)).fetchall()
                conn.close()
                results: list[dict[str, Any]] = []
                for r in rows:
                    d = dict(r)
                    tags_str: str = d.get("tags", "") or ""
                    related_str: str = d.get("related", "") or ""
                    results.append(
                        {
                            "score": None,
                            "stem": d.get("stem", ""),
                            "title": d.get("title", ""),
                            "folder": d.get("folder", ""),
                            "tags": [
                                t.strip() for t in tags_str.split(",") if t.strip()
                            ],
                            "path": d.get("path", ""),
                            "summary": d.get("summary", ""),
                            "note_type": d.get("note_type", ""),
                            "project": d.get("project", ""),
                            "confidence": d.get("confidence", ""),
                            "mtime": d.get("mtime"),
                            "related": [
                                r2.strip()
                                for r2 in related_str.split(",")
                                if r2.strip()
                            ],
                            "is_stale": bool(d.get("is_stale", 0)),
                            "incoming_links": d.get("incoming_links", 0),
                        }
                    )
                return results
            conn.close()
        except sqlite3.Error:
            pass

    # Fallback: file walk
    fallback_results: list[dict[str, Any]] = []
    for path in vault_common.all_vault_notes(vault)[:limit]:
        stem = path.stem
        folder = path.parent.name if path.parent != vault else ""
        fallback_results.append(
            {
                "score": None,
                "stem": stem,
                "title": stem.replace("-", " ").title(),
                "folder": folder,
                "tags": [],
                "path": str(path),
                "summary": "",
                "note_type": "",
                "project": "",
                "confidence": "",
                "mtime": None,
                "related": [],
                "is_stale": False,
                "incoming_links": 0,
            }
        )
    return fallback_results


def _apply_grep_filter(
    results: list[dict[str, Any]],
    pattern: str,
    case_sensitive: bool,
    has_filters: bool,
    has_query: bool,
    limit: int,
    vault: Path | None = None,
) -> list[dict[str, Any]]:
    """Filter *results* (or all vault notes) by a regex pattern applied to note bodies.

    When used standalone (no metadata filters and no semantic query), fetches
    candidate notes from the DB or file walk first.

    Args:
        results: Existing results from semantic or metadata search (may be empty).
        pattern: Regular expression pattern for ``re.search``.
        case_sensitive: If True, disables ``re.IGNORECASE``.
        has_filters: Whether metadata filter flags were supplied.
        has_query: Whether a semantic query was supplied.
        limit: Max results cap when fetching all notes standalone.
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        Filtered list of result dicts whose note bodies match *pattern*.
    """
    flags = 0 if case_sensitive else re.IGNORECASE

    # SEC-021: compile once; reject oversized patterns before compiling.
    if len(pattern) > _MAX_GREP_PATTERN_CHARS:
        print(
            f"--grep: pattern exceeds {_MAX_GREP_PATTERN_CHARS} characters",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        compiled = re.compile(pattern, flags)
    except re.error as exc:
        print(f"--grep: invalid regex pattern: {exc}", file=sys.stderr)
        sys.exit(2)

    # Standalone grep — no prior results from semantic or metadata mode
    if not has_filters and not has_query:
        results = _get_all_notes_as_results(limit, vault)

    matched: list[dict[str, Any]] = []
    global _skipped_outside_warned
    vault_root = vault if vault is not None else vault_common.resolve_vault()
    for result in results:
        note_path_str = result.get("path", "")
        if not note_path_str:
            continue
        note_path = Path(note_path_str)
        if not note_path.exists():
            continue
        # SEC-020: results can carry DB-sourced path strings (note_index /
        # embeddings.db); never read a note body outside the vault.
        if not is_path_inside_vault(note_path, vault_root):
            if not _skipped_outside_warned:
                _skipped_outside_warned = True
                print(
                    "vault-search: skipped results whose paths resolve outside "
                    "the vault (tampered embeddings.db?); SEC-020",
                    file=sys.stderr,
                )
            continue
        try:
            # SEC-021: read at most 1 MiB per note so one huge file cannot
            # dominate the scan; decode errors are replaced, not fatal.
            with note_path.open("rb") as f:
                raw = f.read(_MAX_GREP_SCAN_BYTES)
            content = raw.decode("utf-8", errors="replace")
        except OSError:
            continue
        body = vault_common.get_body(content)
        if compiled.search(body):
            # Normalise score to None for grep-only results
            matched.append({**result, "score": result.get("score")})

    return matched
