"""Tests for the note_index.date column (point-in-time search support)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import vault_index  # noqa: E402


class TestNoteIndexDateColumn:
    def test_schema_creates_date_column_and_index(self, tmp_path: Path) -> None:
        db = tmp_path / "embeddings.db"
        conn = sqlite3.connect(str(db))
        vault_index.ensure_note_index_schema(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(note_index)")}
        conn.close()
        assert "date" in cols

    def test_migration_adds_date_to_existing_db(self, tmp_path: Path) -> None:
        db = tmp_path / "embeddings.db"
        conn = sqlite3.connect(str(db))
        # Pre-create the OLD schema (no date column) to simulate an upgrade.
        conn.execute(
            "CREATE TABLE note_index (stem TEXT PRIMARY KEY, path TEXT, folder TEXT, "
            "title TEXT, summary TEXT, tags TEXT, note_type TEXT, project TEXT, "
            "confidence TEXT, mtime REAL, related TEXT, is_stale INTEGER, "
            "incoming_links INTEGER)"
        )
        conn.commit()
        # ensure_note_index_schema must idempotently add the missing column.
        vault_index.ensure_note_index_schema(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(note_index)")}
        conn.close()
        assert "date" in cols
