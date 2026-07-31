"""Tests for the note_index.prompt_version column (ENH-008 Step 3).

Follows the precedent of ``tests/test_note_index_date.py``: the column is
created on fresh schemas and added idempotently to pre-existing databases.
"""

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


class TestNoteIndexPromptVersionColumn:
    def test_schema_creates_prompt_version_column(self, tmp_path: Path) -> None:
        db = tmp_path / "embeddings.db"
        conn = sqlite3.connect(str(db))
        vault_index.ensure_note_index_schema(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(note_index)")}
        conn.close()
        assert "prompt_version" in cols

    def test_migration_adds_prompt_version_to_existing_db(self, tmp_path: Path) -> None:
        db = tmp_path / "embeddings.db"
        conn = sqlite3.connect(str(db))
        # Pre-create the OLD schema (no prompt_version column) to simulate an
        # upgrade from a pre-ENH-008 database.
        conn.execute(
            "CREATE TABLE note_index (stem TEXT PRIMARY KEY, path TEXT, folder TEXT, "
            "title TEXT, summary TEXT, tags TEXT, note_type TEXT, project TEXT, "
            "confidence TEXT, mtime REAL, related TEXT, is_stale INTEGER, "
            "incoming_links INTEGER)"
        )
        conn.commit()
        vault_index.ensure_note_index_schema(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(note_index)")}
        conn.close()
        assert "prompt_version" in cols

    def test_existing_rows_keep_default_empty_prompt_version(
        self, tmp_path: Path
    ) -> None:
        """Pre-existing rows are not NULL after the migration adds the column."""
        db = tmp_path / "embeddings.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE note_index (stem TEXT PRIMARY KEY, path TEXT, folder TEXT, "
            "title TEXT, summary TEXT, tags TEXT, note_type TEXT, project TEXT, "
            "confidence TEXT, mtime REAL, related TEXT, is_stale INTEGER, "
            "incoming_links INTEGER)"
        )
        conn.execute("INSERT INTO note_index (stem, path) VALUES ('foo', '/x.md')")
        conn.commit()
        vault_index.ensure_note_index_schema(conn)
        row = conn.execute(
            "SELECT prompt_version FROM note_index WHERE stem = ?", ("foo",)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == ""
