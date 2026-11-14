"""The ``note_index`` database access layer (ARC-203).

Owns every read of the ``note_index`` table in ``embeddings.db``:

- the schema (``ensure_note_index_schema``) and its migration columns;
- the shared metadata-filter WHERE builder (``_build_note_index_where``,
  QA-009) used here and by ``cli.search.metadata``;
- :func:`query_note_index` / :func:`load_note_index_metadata` — the
  DB-first query surface with the ``None``-means-fallback sentinel;
- the per-run snapshot (``SessionIndexRow`` / ``SessionIndexSnapshot`` /
  ``load_session_index_snapshot``, PRF-104) plus its derived-view helpers
  (``_incoming_stems_from_column``, ``parse_related_stems``,
  ``load_graph_metadata``, ``_load_note_index_map``);
- ``_paths_from_rows`` — the SEC-005 / SEC-130 containment guard applied
  to every DB-derived path string.

Split out of the former monolithic ``core/vault_index.py`` (ARC-203). This
module has NO filesystem-walk dependency: the DB-first *policy* (deciding
between a DB read and a walk) and the walk implementations stay in
``core.vault_index``, which imports this module — one dependency direction,
no cycle. All public symbols are re-exported from ``core.vault_index``
(and its ``vault_index`` / ``vault_common`` shims) for backward
compatibility.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

from .vault_path import (
    get_embeddings_db_path,
    is_path_inside_vault,
    resolve_vault,
)

# Supersession status constant lives in the note contract module (a leaf).
from parsidion.note_schema import STATUS_SUPERSEDED

__all__: list[str] = [
    # DB schema
    "ensure_note_index_schema",
    # DB-first queries
    "query_note_index",
    "load_note_index_metadata",
    # Per-run note_index snapshot (PRF-104)
    "SessionIndexRow",
    "SessionIndexSnapshot",
    "load_session_index_snapshot",
    # Graph parsing / metadata
    "parse_related_stems",
    "load_graph_metadata",
    # Containment guard + WHERE builder + QA-002 plumbing re-exported for internal consumers
    "_paths_from_rows",
    "_open_note_index_ro",
    "_query_note_index_results",
    "_build_note_index_where",
    "_incoming_stems_from_column",
    "_load_note_index_map",
]


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
            prompt_version TEXT    NOT NULL DEFAULT '',
            incoming_stems TEXT    NOT NULL DEFAULT '',
            status         TEXT    NOT NULL DEFAULT 'live'
        )
        """
    )
    # Migration: add date column to pre-existing databases (CREATE IF NOT EXISTS
    # does not add columns to an already-present table).
    cols = {row[1] for row in conn.execute("PRAGMA table_info(note_index)")}
    if "date" not in cols:
        conn.execute("ALTER TABLE note_index ADD COLUMN date TEXT NOT NULL DEFAULT ''")
    # Supersession (note retirement): status column; pre-existing rows are
    # 'live' by definition (an unmarked note was never retired).
    if "status" not in cols:
        conn.execute(
            "ALTER TABLE note_index ADD COLUMN status TEXT NOT NULL DEFAULT 'live'"
        )
    # ENH-008 Step 3: prompt_version column for slicing note quality by the
    # prompt that produced each AI-generated note.
    if "prompt_version" not in cols:
        conn.execute(
            "ALTER TABLE note_index ADD COLUMN prompt_version TEXT NOT NULL DEFAULT ''"
        )
    # ENH-021: incoming_stems column -- the reverse-link adjacency (JSON array
    # of source stems), inverted from every note's outgoing ``related`` links
    # at index time. Empty on pre-ENH-021 rows until the next full rebuild;
    # readers treat empty as "not populated" and fall back to deriving the
    # inversion from ``related`` (see SessionIndexSnapshot).
    if "incoming_stems" not in cols:
        conn.execute(
            "ALTER TABLE note_index ADD COLUMN incoming_stems TEXT NOT NULL DEFAULT ''"
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


def _build_note_index_where(
    *,
    tag: str | None = None,
    folder: str | None = None,
    note_type: str | None = None,
    project: str | None = None,
    recent_days: int | None = None,
    changed_since: str | None = None,
    as_of: str | None = None,
    has_status: bool = True,
    include_superseded: bool = False,
) -> tuple[str, list[object]]:
    """Build the note_index metadata-filter WHERE clause (QA-009).

    The single home for the condition assembly previously duplicated
    between ``query_note_index`` here and ``cli.search.metadata.query``
    (parsight similarity 0.908). Callers keep their own SELECT lists,
    result mapping, and schema/containment guards.

    SECURITY: The SQL WHERE clause is assembled from literal condition
    fragments only -- no column names are ever derived from external input.
    All filter values are passed as bound parameters (?). Column names used
    form a static whitelist: tags, folder, note_type, project, mtime, date,
    status. Any future addition of a user-supplied column name must be added
    to this whitelist and reviewed for injection risk.

    Args:
        tag: Exact tag token to match in the comma-separated tags column.
        folder: Exact folder name to match.
        note_type: Exact note_type value to match.
        project: Exact project value to match.
        recent_days: Only return notes modified within this many days.
        changed_since: Only return notes modified on/after this ISO datetime.
        as_of: Only return notes whose frontmatter date is on/before this
            ISO date (empty dates excluded).
        include_superseded: When False (the default), retired notes
            (``status: superseded``) are excluded from every query. Pass
            True only for explicit history queries.

    Returns:
        ``(where, params)`` -- ``where`` is "" or "WHERE c1 AND c2 ...";
        ``params`` holds the bound values in placeholder order.
    """
    conditions: list[str] = []
    params: list[object] = []

    if not include_superseded and has_status:
        conditions.append("status != 'superseded'")

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


def _open_note_index_ro(db_path: Path) -> tuple[sqlite3.Connection, set[str]] | None:
    """Open embeddings.db read-only with the note_index table present (QA-002).

    Shared plumbing for the note_index SQL paths: existence check, read-only
    connect, table-presence check, and the PRAGMA column set. Returns None
    when the DB is absent, cannot be opened, or has no note_index table --
    callers map None to their own sentinel (None walk-fallback in
    :func:`query_note_index`, empty list in :func:`_query_note_index_results`).
    The connection is closed on every failure; the caller closes on success.
    Row factory is deliberately left unset (callers set their own).
    """
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return None
    try:
        if (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='note_index'"
            ).fetchone()
            is None
        ):
            conn.close()
            return None
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(note_index)").fetchall()
        }
    except sqlite3.Error:
        conn.close()
        return None
    return conn, columns


def query_note_index(
    *,
    tag: str | None = None,
    folder: str | None = None,
    note_type: str | None = None,
    project: str | None = None,
    recent_days: int | None = None,
    limit: int = 200,
    vault: str | Path | None = None,
    include_superseded: bool = False,
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
    opened = _open_note_index_ro(get_embeddings_db_path(vault))
    if opened is None:
        return None
    conn, columns = opened

    try:
        # QA-009: shared WHERE builder (see _build_note_index_where for the
        # injection-safety contract). A pre-migration index lacks the status
        # column (ro connections cannot ALTER); the filter then drops out
        # instead of failing the whole query.
        where, params = _build_note_index_where(
            tag=tag,
            folder=folder,
            note_type=note_type,
            project=project,
            recent_days=recent_days,
            has_status="status" in columns,
            include_superseded=include_superseded,
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


def _query_note_index_results(
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
    include_superseded: bool = False,
) -> list[dict[str, object]]:
    """Run the note_index metadata query and return vault-search result dicts.

    QA-002: single home of the SQL previously duplicated in
    ``parsidion.cli.search.metadata.query`` -- the CLI function is now a
    thin delegate. Same guard semantics as :func:`query_note_index`
    (empty list when the DB or the note_index table is absent), plus the
    CLI-specific details that :func:`query_note_index` intentionally does
    not carry: the full result-dict projection, ``date``-column detection
    for pre-migration indexes (non-date queries omit the column; ``--as-of``
    on a stale schema warns to stderr and returns []), and supersession
    vacuous-status handling via the shared WHERE builder.
    """
    db_path = get_embeddings_db_path(vault)
    opened = _open_note_index_ro(db_path)
    if opened is None:
        return []
    conn, columns = opened
    conn.row_factory = sqlite3.Row

    try:
        # Defensive: a note_index predating the `date` migration (e.g. an
        # embeddings-disabled vault never rebuilt) lacks the column. SELECTing
        # `date` would raise OperationalError and silently return []. Detect it
        # instead: non-date queries omit the column (still work); --as-of (which
        # genuinely needs it) warns and returns [].
        has_date = "date" in columns
        if as_of is not None and not has_date:
            print(
                "note_index schema is stale (no 'date' column); "
                "run update_index.py to enable --as-of.",
                file=sys.stderr,
            )
            return []
        # Supersession: a note_index predating the status migration lacks the
        # column; the filter is then vacuously satisfied (nothing can be
        # retired yet) instead of breaking every metadata query.
        has_status = "status" in columns

        # QA-009: shared WHERE builder -- the condition assembly that was
        # duplicated with query_note_index lives once in
        # parsidion.core.vault_index._build_note_index_where (injection-safety
        # contract documented there).
        where, params = _build_note_index_where(
            tag=tag,
            folder=folder,
            note_type=note_type,
            project=project,
            recent_days=recent_days,
            changed_since=changed_since,
            as_of=as_of,
            has_status=has_status,
            include_superseded=include_superseded,
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


def load_note_index_metadata(
    vault: str | Path | None = None,
    limit: int = 5000,
) -> list[dict[str, Any]] | None:
    """Return note_index metadata rows for term-scoring consumers.

    Unlike :func:`query_note_index` (metadata FILTERS returning paths) this
    is a bounded full scan returning the raw per-note metadata the
    pre-tool-use hook scores in Python against file-derived tokens. Returns
    None (not []) when the DB or the table is absent, matching
    :func:`query_note_index`'s fall-back contract; [] means an empty index.

    SECURITY: rows carry DB-sourced ``path`` strings; a consumer that reads
    files from them must re-validate containment (``is_path_inside_vault``)
    first — same contract as SEC-005/SEC-130 on ``query_note_index``.

    Args:
        vault: Optional vault path used to locate embeddings.db. Defaults to
            resolve_vault().
        limit: Row cap so a corrupt/pathological index cannot balloon the
            hook's latency.

    Returns:
        List of row dicts (stem, path, folder, title, summary, tags,
        note_type, project, mtime) or None when the DB/table is missing.
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
        conn.row_factory = sqlite3.Row
        if (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='note_index'"
            ).fetchone()
            is None
        ):
            return None
        rows = conn.execute(
            "SELECT stem, path, folder, title, summary, tags, note_type, project, "
            "mtime FROM note_index ORDER BY mtime DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return None
    finally:
        conn.close()


class SessionIndexRow(NamedTuple):
    """One ``note_index`` row, in the shape the SessionStart path consumes."""

    stem: str
    path: str
    title: str
    tags: str
    folder: str
    project: str
    related: str
    incoming_links: int
    mtime: float
    incoming_stems: str = ""
    status: str = "live"


def _incoming_stems_from_column(
    rows: list[SessionIndexRow],
) -> dict[str, set[str]] | None:
    """Parse the persisted ``incoming_stems`` column into an adjacency map.

    ENH-021: ``update_index`` inverts every note's outgoing ``related`` links
    in one pass and stores the per-note source stems as a JSON array. This
    returns that adjacency, or ``None`` when any row's value is empty
    (pre-ENH-021 index, not yet rebuilt) or unparseable -- the caller then
    falls back to deriving the inversion from ``related``.
    """
    parsed: dict[str, set[str]] = {}
    for row in rows:
        if not row.incoming_stems:
            return None
        try:
            raw_list = json.loads(row.incoming_stems)
        except json.JSONDecodeError:
            return None
        if not isinstance(raw_list, list):
            return None
        parsed[row.stem] = {s for s in raw_list if isinstance(s, str)}
    return parsed


class SessionIndexSnapshot:
    """One read of ``note_index``, shared across a single hook invocation.

    PRF-104: a standard SessionStart used to perform roughly three full
    ``note_index`` reads (the project and recent queries, the graph-metadata
    load, and another full load inside ``build_compact_index``), each opening
    its own connection, plus an O(N x seeds) incoming-link scan rebuilt per
    call. None of that is algorithmically wrong -- it is the SHAPE of the work
    that grows with the vault -- so the fix is one snapshot per run rather than
    micro-optimising each site.

    The derived views (compact-index map, graph metadata, and the reverse-link
    adjacency the Tier-1 neighbour expansion needs) are computed once here in a
    single pass. Path validation is NOT skipped: callers that resolve rows to
    filesystem paths still apply the SEC-005 / SEC-130 containment guard -- the
    saving is doing it once per run instead of once per query.

    Construct via :func:`load_session_index_snapshot`, which returns ``None``
    when the DB or table is absent, mirroring ``query_note_index``'s sentinel
    contract so every caller keeps its existing fallback.
    """

    __slots__ = ("vault", "rows", "by_stem", "_graph_meta", "_incoming", "_compact")

    def __init__(self, vault: Path, rows: list[SessionIndexRow]) -> None:
        self.vault = vault
        # Supersession: self.rows keeps every row (paths_where's
        # include_superseded escape hatch needs them); the retrieval-only
        # derived views below are built from the LIVE subset.
        self.rows = rows
        live_rows = [r for r in rows if r.status != STATUS_SUPERSEDED]
        self.by_stem: dict[str, SessionIndexRow] = {r.stem: r for r in rows}
        self._graph_meta: dict[str, dict[str, object]] = {
            r.stem: {
                "path": r.path,
                "related": r.related,
                "incoming_links": r.incoming_links,
                "tags": r.tags,
            }
            for r in live_rows
        }
        self._compact: dict[str, tuple[str, str, str]] = {
            r.stem: (r.title, r.tags, r.folder) for r in live_rows
        }
        # Reverse adjacency. ENH-021: read the inversion persisted at index
        # time in the ``incoming_stems`` column; only when the column is not
        # populated (pre-ENH-021 index) rebuild it here in one
        # O(N x avg_links) pass -- the PRF-104 behaviour.
        incoming = _incoming_stems_from_column(rows)
        if incoming is None:
            incoming = {}
            for row in rows:
                for target in parse_related_stems(row.related):
                    incoming.setdefault(target, set()).add(row.stem)
        self._incoming = incoming

    def graph_metadata(self) -> dict[str, dict[str, object]]:
        """The mapping ``load_graph_metadata`` returns, computed once."""
        return self._graph_meta

    def compact_index_map(self) -> dict[str, tuple[str, str, str]]:
        """The stem -> (title, tags, folder) map ``build_compact_index`` uses."""
        return self._compact

    def incoming_stems(self, stem: str) -> set[str]:
        """Stems whose ``related`` field points at *stem* (precomputed)."""
        return self._incoming.get(stem, set())

    def paths_where(
        self,
        *,
        project: str | None = None,
        recent_days: int | None = None,
        limit: int = 200,
        include_superseded: bool = False,
    ) -> list[Path]:
        """Rows matching the filters, newest first, as validated Paths.

        Mirrors ``query_note_index``'s filtering, ordering, containment guard
        and limit semantics -- against the snapshot instead of a fresh query.
        Superseded rows are excluded unless *include_superseded* is set.
        """
        cutoff = (
            (datetime.now() - timedelta(days=recent_days)).timestamp()
            if recent_days is not None
            else None
        )
        matched = [
            row
            for row in self.rows
            if (include_superseded or row.status != STATUS_SUPERSEDED)
            and (project is None or row.project == project)
            and (cutoff is None or row.mtime >= cutoff)
        ]
        matched.sort(key=lambda r: r.mtime, reverse=True)
        return _paths_from_rows(
            [(r.path,) for r in matched[:limit]], self.vault.resolve()
        )


def load_session_index_snapshot(
    vault: Path | None = None,
) -> SessionIndexSnapshot | None:
    """Read ``note_index`` once for a hook invocation (PRF-104).

    Args:
        vault: Optional vault path used to locate embeddings.db (ARC-101).
            Defaults to resolve_vault().

    Returns:
        A :class:`SessionIndexSnapshot`, or ``None`` when ``embeddings.db`` or
        the ``note_index`` table is absent -- the same sentinel every other
        DB-first reader here uses to signal "take the fallback path".
    """
    resolved = vault or resolve_vault()
    db_path = get_embeddings_db_path(resolved)
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return None
    try:
        if (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='note_index'"
            ).fetchone()
            is None
        ):
            return None
        # ENH-021: incoming_stems is absent on tables created before the
        # column existed and not yet re-ensured; select a literal '' for
        # those so the snapshot (and its legacy-inversion fallback) keeps
        # working instead of failing the whole read.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(note_index)")}
        incoming_sel = (
            "incoming_stems" if "incoming_stems" in cols else "'' AS incoming_stems"
        )
        status_sel = "status" if "status" in cols else "'live' AS status"
        raw = conn.execute(
            f"SELECT stem, path, title, tags, folder, project, related, "
            f"incoming_links, mtime, {incoming_sel}, {status_sel} FROM note_index"
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    rows = [
        SessionIndexRow(
            stem=r[0],
            path=r[1],
            title=r[2] or "",
            tags=r[3] or "",
            folder=r[4] or "",
            project=r[5] or "",
            related=r[6] or "",
            incoming_links=int(r[7] or 0),
            mtime=float(r[8] or 0.0),
            incoming_stems=str(r[9] or ""),
            status=str(r[10] or "live"),
        )
        for r in raw
    ]
    return SessionIndexSnapshot(Path(resolved), rows)


def _load_note_index_map(
    vault: Path | None = None,
) -> dict[str, tuple[str, str, str]] | None:
    """Load a stem -> (title, tags, folder) map from the note_index DB.

    QA-005: Used by build_compact_index and build_context_block to avoid
    N+1 file reads when the DB is available.

    PRF-104: a thin wrapper over :func:`load_session_index_snapshot` so
    external callers (parsidion-mcp reaches this through
    ``build_compact_index``) keep a one-off-snapshot entry point, while the
    SessionStart path passes its shared snapshot in directly.

    Args:
        vault: Optional vault path used to locate embeddings.db (ARC-101).
            Defaults to resolve_vault().

    Returns:
        Dict mapping stem to (title, tags_str, folder), or None if DB unavailable.
    """
    snapshot = load_session_index_snapshot(vault)
    return snapshot.compact_index_map() if snapshot is not None else None


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


def load_graph_metadata(
    vault: Path | None = None,
) -> dict[str, dict[str, object]] | None:
    """Load per-note graph metadata from the ``note_index`` table.

    Used by ``session_start_hook`` for 1-hop neighbor expansion (Tier 1) and
    graph-aware reranking (Tier 2).  Returns ``None`` when ``embeddings.db``
    or the ``note_index`` table is absent, signalling the caller to skip graph
    features gracefully -- mirrors ``query_note_index``'s None-sentinel
    contract so callers fall back to the existing retrieval path.

    Paths are returned verbatim (unvalidated); callers that resolve stems to
    filesystem paths must apply the SEC-005 vault-containment guard themselves.

    Args:
        vault: Optional vault path used to locate embeddings.db (ARC-101).
            Defaults to resolve_vault().

    PRF-104: a thin wrapper over :func:`load_session_index_snapshot`. The
    SessionStart path takes the mapping off its shared snapshot instead of
    calling this and paying a second full table read.

    Returns:
        Mapping of ``stem -> {"path": str, "related": str,
        "incoming_links": int, "tags": str}``, or ``None`` on DB error.
    """
    snapshot = load_session_index_snapshot(vault)
    return snapshot.graph_metadata() if snapshot is not None else None
