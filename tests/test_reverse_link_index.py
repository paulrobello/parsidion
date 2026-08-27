"""Tests for ENH-021 — the persisted reverse-link adjacency in note_index.

Covers:
- Schema: the ``incoming_stems`` column is created fresh and ALTER-migrated
  onto pre-ENH-021 databases.
- Inversion correctness at index time: a note's column lists exactly the
  stems whose ``related`` links to it, dangling targets are dropped, notes
  nobody links to get ``[]`` (not ``''``), and the length agrees with the
  ``incoming_links`` count column on duplicate-free fixtures.
- Incremental rebuild: editing a note's ``related`` and deleting a note,
  then re-running the indexer, updates the affected rows' adjacency.
- Parity: ``_graph_neighbors`` returns identical neighbours via the
  column-backed snapshot and via the legacy per-run inversion.
- Snapshot fallback: empty, unparseable, and absent columns all degrade to
  the legacy inversion instead of failing the snapshot load.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


import session_start_hook
import update_index
import vault_common

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OLD_SCHEMA_SQL = """
CREATE TABLE note_index (
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
    date           TEXT    NOT NULL DEFAULT ''
)
"""


def _make_note(path: Path, *, related: list[str] | None = None) -> Path:
    """Write a minimal note whose ``related`` field links to *related* stems."""
    related_items = ", ".join(f'"[[{stem}]]"' for stem in (related or []))
    parts = [
        "---",
        "type: pattern",
        f"related: [{related_items}]",
        "---\n",
        f"# {path.stem}\nbody\n",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def _build_index(vault: Path) -> None:
    """Populate embeddings.db/note_index via the real build + persist path.

    Mirrors ``main()``: ``build_index`` produces the rows, then
    ``_write_note_index_to_db`` upserts them (see
    tests/test_index_enhancements.py::_build_index).
    """
    db_path = vault_common.get_embeddings_db_path(vault)
    if not db_path.exists():
        conn = sqlite3.connect(str(db_path))
        try:
            vault_common.ensure_note_index_schema(conn)
        finally:
            conn.close()
    result = update_index.build_index(vault=vault)
    db_rows = result[4]
    current_stems = {row.stem for row in db_rows}
    update_index._write_note_index_to_db(db_rows, current_stems, vault=vault)


def _seed_fixture(vault: Path) -> dict[str, Path]:
    """Create the standard ENH-021 fixture and return stem -> path.

    Topology (outgoing ``related`` links):
        alpha -> beta, missing   (missing is a dangling target)
        beta  -> delta
        gamma -> beta
        delta -> (none)
    """
    notes = {
        "alpha": _make_note(
            vault / "Patterns" / "alpha.md", related=["beta", "missing"]
        ),
        "beta": _make_note(vault / "Patterns" / "beta.md", related=["delta"]),
        "gamma": _make_note(vault / "Patterns" / "gamma.md", related=["beta"]),
        "delta": _make_note(vault / "Patterns" / "delta.md", related=[]),
    }
    return notes


def _read_rows(vault: Path) -> dict[str, tuple[str, int]]:
    """Return stem -> (incoming_stems column value, incoming_links count)."""
    db_path = vault_common.get_embeddings_db_path(vault)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT stem, incoming_stems, incoming_links FROM note_index"
        ).fetchall()
    finally:
        conn.close()
    return {stem: (stems, count) for stem, stems, count in rows}


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestIncomingStemsSchema:
    """ensure_note_index_schema creates / migrates the incoming_stems column."""

    def test_schema_creates_incoming_stems_column(self, tmp_path: Path) -> None:
        db = tmp_path / "embeddings.db"
        conn = sqlite3.connect(str(db))
        try:
            vault_common.ensure_note_index_schema(conn)
            cols = {
                row[1]: row[2].upper()
                for row in conn.execute("PRAGMA table_info(note_index)")
            }
        finally:
            conn.close()
        assert cols.get("incoming_stems") == "TEXT"

    def test_migration_adds_incoming_stems_to_existing_db(self, tmp_path: Path) -> None:
        db = tmp_path / "embeddings.db"
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(_OLD_SCHEMA_SQL)
            conn.commit()
            cols_before = {
                row[1] for row in conn.execute("PRAGMA table_info(note_index)")
            }
            assert "incoming_stems" not in cols_before

            vault_common.ensure_note_index_schema(conn)
            rows = conn.execute("PRAGMA table_info(note_index)").fetchall()
        finally:
            conn.close()
        col = next(r for r in rows if r[1] == "incoming_stems")
        assert col[2].upper() == "TEXT"
        assert col[4] == "''"  # default: empty = "not populated"


# ---------------------------------------------------------------------------
# Inversion correctness
# ---------------------------------------------------------------------------


class TestIncomingStemsInversion:
    """The persisted adjacency is the exact inversion of outgoing links."""

    def test_inversion_lists_sources_of_each_note(self, tmp_vault: Path) -> None:
        _seed_fixture(tmp_vault)
        _build_index(tmp_vault)

        rows = _read_rows(tmp_vault)
        assert json.loads(rows["beta"][0]) == ["alpha", "gamma"]
        assert json.loads(rows["delta"][0]) == ["beta"]
        # Nobody links to alpha/gamma: populated empty array, not ''.
        assert json.loads(rows["alpha"][0]) == []
        assert json.loads(rows["gamma"][0]) == []
        # Dangling targets have no row to hold adjacency on.
        assert "missing" not in rows

    def test_count_column_agrees_with_array_length(self, tmp_vault: Path) -> None:
        """On duplicate-free fixtures len(incoming_stems) == incoming_links."""
        _seed_fixture(tmp_vault)
        _build_index(tmp_vault)

        for stem, (stems_json, count) in _read_rows(tmp_vault).items():
            assert len(json.loads(stems_json)) == count, stem

    def test_incremental_rebuild_updates_affected_rows(self, tmp_vault: Path) -> None:
        notes = _seed_fixture(tmp_vault)
        _build_index(tmp_vault)
        assert json.loads(_read_rows(tmp_vault)["beta"][0]) == ["alpha", "gamma"]

        # alpha now links to delta instead of beta; gamma is deleted outright.
        _make_note(notes["alpha"], related=["delta"])
        notes["gamma"].unlink()
        _build_index(tmp_vault)

        rows = _read_rows(tmp_vault)
        assert json.loads(rows["beta"][0]) == []  # lost both alpha and gamma
        assert json.loads(rows["delta"][0]) == ["alpha", "beta"]
        assert "gamma" not in rows  # pruned with the note


# ---------------------------------------------------------------------------
# Consumer parity (graph_retrieval)
# ---------------------------------------------------------------------------


class TestGraphNeighborsParity:
    """The column-backed snapshot and the legacy inversion agree everywhere."""

    def test_graph_neighbors_identical_column_vs_legacy(self, tmp_vault: Path) -> None:
        notes = _seed_fixture(tmp_vault)
        _build_index(tmp_vault)

        snapshot = vault_common.load_session_index_snapshot(vault=tmp_vault)
        assert snapshot is not None
        meta = snapshot.graph_metadata()
        seeds = [notes["beta"], notes["gamma"]]

        from_column = session_start_hook._graph_neighbors(
            seeds, meta, tmp_vault, 8, snapshot=snapshot
        )

        # Blank the column to force the legacy per-run inversion, then
        # recompute: same fixture, same snapshot API, different source.
        db_path = vault_common.get_embeddings_db_path(tmp_vault)
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("UPDATE note_index SET incoming_stems = ''")
            conn.commit()
        finally:
            conn.close()
        legacy_snapshot = vault_common.load_session_index_snapshot(vault=tmp_vault)
        assert legacy_snapshot is not None

        from_legacy = session_start_hook._graph_neighbors(
            seeds,
            legacy_snapshot.graph_metadata(),
            tmp_vault,
            8,
            snapshot=legacy_snapshot,
        )

        assert from_column == from_legacy
        # beta -> delta (outgoing) plus alpha (incoming-only via the column).
        assert {p.stem for p in from_column} == {"alpha", "delta"}

    def test_adjacency_matches_legacy_inversion_per_stem(self, tmp_vault: Path) -> None:
        _seed_fixture(tmp_vault)
        _build_index(tmp_vault)

        snapshot = vault_common.load_session_index_snapshot(vault=tmp_vault)
        assert snapshot is not None
        legacy = {
            "alpha": set(),
            "beta": {"alpha", "gamma"},
            "gamma": set(),
            "delta": {"beta"},
        }
        for stem, expected in legacy.items():
            assert snapshot.incoming_stems(stem) == expected, stem


# ---------------------------------------------------------------------------
# Snapshot fallback (pre-ENH-021 databases)
# ---------------------------------------------------------------------------


class TestSnapshotFallback:
    """Empty / corrupt / absent columns degrade to the legacy inversion."""

    def _snapshot_with_column_value(self, vault: Path, value: str):
        db_path = vault_common.get_embeddings_db_path(vault)
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("UPDATE note_index SET incoming_stems = ?", (value,))
            conn.commit()
        finally:
            conn.close()
        return vault_common.load_session_index_snapshot(vault=vault)

    def test_empty_column_falls_back_to_inversion(self, tmp_vault: Path) -> None:
        _seed_fixture(tmp_vault)
        _build_index(tmp_vault)
        snapshot = self._snapshot_with_column_value(tmp_vault, "")
        assert snapshot is not None
        assert snapshot.incoming_stems("beta") == {"alpha", "gamma"}

    def test_unparseable_column_falls_back_to_inversion(self, tmp_vault: Path) -> None:
        _seed_fixture(tmp_vault)
        _build_index(tmp_vault)
        snapshot = self._snapshot_with_column_value(tmp_vault, "not json")
        assert snapshot is not None
        assert snapshot.incoming_stems("beta") == {"alpha", "gamma"}

    def test_absent_column_still_loads_and_falls_back(self, tmp_vault: Path) -> None:
        """A table created before ENH-021 (column missing entirely)."""
        _seed_fixture(tmp_vault)
        db_path = vault_common.get_embeddings_db_path(tmp_vault)
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(_OLD_SCHEMA_SQL)
            for stem in ("alpha", "beta", "gamma", "delta"):
                conn.execute(
                    "INSERT INTO note_index (stem, path, folder, title, related, mtime) "
                    "VALUES (?, ?, 'Patterns', ?, ?, 1000.0)",
                    (
                        stem,
                        str(tmp_vault / "Patterns" / f"{stem}.md"),
                        stem,
                        {"alpha": "beta", "beta": "delta", "gamma": "beta"}.get(
                            stem, ""
                        ),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        snapshot = vault_common.load_session_index_snapshot(vault=tmp_vault)
        assert snapshot is not None  # must not fail the whole read
        assert snapshot.incoming_stems("beta") == {"alpha", "gamma"}
