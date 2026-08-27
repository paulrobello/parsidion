"""``note_index`` SQLite upsert (ARC-005).

Extracted from ``update_index.py``. Re-exported by the entry shim so
``update_index._write_note_index_to_db`` keeps resolving for tests and
other callers.

Stdlib-only at module load (``sqlite3`` is lazy-imported inside the
function body, matching the original).
"""

from __future__ import annotations

import sys
from pathlib import Path

from core.vault_config import get_config
from core.vault_path import get_embeddings_db_path

from cli.index.models import NoteEntry


def _write_note_index_to_db(
    db_rows: list[NoteEntry], current_stems: set[str], vault: Path
) -> None:
    """Write per-note metadata rows to the note_index table in embeddings.db.

    No-op if embeddings are disabled or the DB file does not exist. Errors are
    printed to stderr so DB failures are visible without crashing the indexer.

    Args:
        db_rows: List of NoteEntry records to upsert into note_index.
        current_stems: Set of stems currently in the vault (used to prune deleted notes).
        vault: Path to the vault directory.
    """
    if not get_config("embeddings", "enabled", True):
        return
    try:
        import sqlite3 as _sqlite3
        from core.vault_index import ensure_note_index_schema

        db_path = get_embeddings_db_path(vault=vault)
        if not db_path.exists():
            return

        conn = _sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            ensure_note_index_schema(conn)

            conn.executemany(
                """
                INSERT INTO note_index (
                    stem, path, folder, title, summary, tags, note_type,
                    project, confidence, mtime, related, is_stale, incoming_links, date, prompt_version
                ) VALUES (
                    :stem, :path, :folder, :title, :summary, :tags, :note_type,
                    :project, :confidence, :mtime, :related, :is_stale, :incoming_links, :date, :prompt_version
                )
                ON CONFLICT(stem) DO UPDATE SET
                    path=excluded.path,
                    folder=excluded.folder,
                    title=excluded.title,
                    summary=excluded.summary,
                    tags=excluded.tags,
                    note_type=excluded.note_type,
                    project=excluded.project,
                    confidence=excluded.confidence,
                    mtime=excluded.mtime,
                    related=excluded.related,
                    is_stale=excluded.is_stale,
                    incoming_links=excluded.incoming_links,
                    date=excluded.date,
                    prompt_version=excluded.prompt_version
                """,
                [row._asdict() for row in db_rows],
            )

            # Prune rows for notes that no longer exist in the vault
            db_stems = conn.execute("SELECT stem FROM note_index").fetchall()
            stale = [(row[0],) for row in db_stems if row[0] not in current_stems]
            if stale:
                conn.executemany("DELETE FROM note_index WHERE stem = ?", stale)

            conn.commit()
        finally:
            # Close on all paths -- the broad except below must not leak conn
            conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"update_index DB error: {exc}", file=sys.stderr)
