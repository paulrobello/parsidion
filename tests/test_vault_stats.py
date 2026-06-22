"""Tests for vault_stats.py — core modes and sqlite-absent fallback.

QA-005: vault_stats (1,325 lines) has zero dedicated tests.
These tests cover _collect_tags, _open_db (absent fallback), and run_pending.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import vault_common  # noqa: E402

vault_stats = importlib.import_module("vault_stats")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vault_common, "VAULT_ROOT", tmp_path)
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    vault_common.load_config.cache_clear()  # type: ignore[attr-defined]


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    for d in vault_common.VAULT_DIRS:
        (tmp_path / d).mkdir(exist_ok=True)
    return tmp_path


def _make_db(vault: Path) -> Path:
    """Create a minimal embeddings.db with note_index table."""
    db_path = vault / "embeddings.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE note_index (
            stem TEXT, path TEXT, folder TEXT, title TEXT, summary TEXT,
            tags TEXT, note_type TEXT, project TEXT, confidence TEXT,
            mtime REAL, related TEXT, is_stale INTEGER, incoming_links INTEGER
        )
        """
    )
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# _open_db
# ---------------------------------------------------------------------------


class TestOpenDb:
    def test_returns_none_when_db_absent(self, vault: Path) -> None:
        conn = vault_stats._open_db(vault)
        assert conn is None

    def test_returns_connection_when_db_present(self, vault: Path) -> None:
        _make_db(vault)
        conn = vault_stats._open_db(vault)
        assert conn is not None
        conn.close()


# ---------------------------------------------------------------------------
# _collect_tags
# ---------------------------------------------------------------------------


class TestCollectTags:
    def test_collects_csv_tags(self, vault: Path) -> None:
        db_path = _make_db(vault)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO note_index (tags) VALUES (?), (?), (?)",
            ("python, vault", "python, hook", "vault"),
        )
        conn.commit()

        result = vault_stats._collect_tags(conn)
        conn.close()

        counts = dict(result)
        assert counts["python"] == 2
        assert counts["vault"] == 2
        assert counts["hook"] == 1

    def test_collects_json_array_tags(self, vault: Path) -> None:
        db_path = _make_db(vault)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO note_index (tags) VALUES (?), (?)",
            ('["python", "vault"]', '["python", "rust"]'),
        )
        conn.commit()

        result = vault_stats._collect_tags(conn)
        conn.close()

        counts = dict(result)
        assert counts["python"] == 2
        assert counts["vault"] == 1
        assert counts["rust"] == 1

    def test_skips_empty_tags(self, vault: Path) -> None:
        db_path = _make_db(vault)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("INSERT INTO note_index (tags) VALUES (?), (?)", ("", None))
        conn.commit()

        result = vault_stats._collect_tags(conn)
        conn.close()
        assert result == []

    def test_returns_sorted_by_count_desc(self, vault: Path) -> None:
        db_path = _make_db(vault)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO note_index (tags) VALUES (?), (?), (?)",
            ("a, b, b", "b", "a"),
        )
        conn.commit()

        result = vault_stats._collect_tags(conn)
        conn.close()

        tags = [t for t, _ in result]
        # 'b' appears 3 times, 'a' appears 2 times
        assert tags[0] == "b"
        assert tags[1] == "a"


# ---------------------------------------------------------------------------
# run_pending (file I/O path)
# ---------------------------------------------------------------------------


class TestRunPending:
    def test_handles_missing_file_gracefully(self, vault: Path) -> None:
        # Should not raise; prints a "no pending" message
        vault_stats.run_pending(vault)

    def test_reads_pending_entries(self, vault: Path) -> None:
        pending = vault / "pending_summaries.jsonl"
        entries = [
            {
                "session_id": "abc",
                "project": "parsidion",
                "source": "session",
                "timestamp": "2026-01-01T00:00:00",
            },
            {
                "session_id": "def",
                "project": "parsidion",
                "source": "subagent",
                "timestamp": "2026-01-02T00:00:00",
            },
        ]
        pending.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8"
        )
        # run_pending prints via rich Console — just confirm no exception
        vault_stats.run_pending(vault)

    def test_handles_empty_pending_file(self, vault: Path) -> None:
        pending = vault / "pending_summaries.jsonl"
        pending.write_text("", encoding="utf-8")
        vault_stats.run_pending(vault)

    def test_handles_malformed_lines(self, vault: Path) -> None:
        pending = vault / "pending_summaries.jsonl"
        pending.write_text(
            '{"session_id": "ok", "project": "x"}\nnot-json\n{"session_id": "ok2"}\n',
            encoding="utf-8",
        )
        # Should skip malformed lines without raising
        vault_stats.run_pending(vault)


# ---------------------------------------------------------------------------
# collect_graph retrieval-readiness metrics
# ---------------------------------------------------------------------------


class TestCollectGraphReadiness:
    """Retrieval-readiness metrics added for the graph-expansion feature."""

    def _conn_with(self, vault: Path, rows: list[dict]) -> sqlite3.Connection:
        db_path = _make_db(vault)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        for row in rows:
            conn.execute(
                "INSERT INTO note_index (stem, related, incoming_links) "
                "VALUES (?, ?, ?)",
                (row["stem"], row.get("related", ""), row.get("incoming_links", 0)),
            )
        conn.commit()
        return conn

    def test_expandable_count_and_avg_neighbours(self, vault: Path) -> None:
        # a->b (1 outgoing), b->a,c (2 outgoing), c (0 outgoing)
        conn = self._conn_with(
            vault,
            [
                {"stem": "a", "related": "b", "incoming_links": 1},
                {"stem": "b", "related": "a, c", "incoming_links": 1},
                {"stem": "c", "related": "", "incoming_links": 1},
            ],
        )
        data = vault_stats.vault_metrics.collect_graph(conn)
        conn.close()
        assert data["total"] == 3
        assert data["expandable_count"] == 2  # only a and b carry related links
        assert data["total_targets"] == 3  # b + (a, c)
        assert data["avg_related_per_note"] == pytest.approx(1.0)

    def test_dangling_targets_detected(self, vault: Path) -> None:
        # a links to b (exists) and ghost (does not exist)
        conn = self._conn_with(
            vault,
            [
                {"stem": "a", "related": "b, ghost", "incoming_links": 0},
                {"stem": "b", "related": "", "incoming_links": 1},
            ],
        )
        data = vault_stats.vault_metrics.collect_graph(conn)
        conn.close()
        assert data["total_targets"] == 2
        assert data["dangling_targets"] == 1  # ghost

    def test_empty_graph_readiness_defaults(self, vault: Path) -> None:
        db_path = _make_db(vault)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        data = vault_stats.vault_metrics.collect_graph(conn)
        conn.close()
        assert data["total"] == 0
        assert data["expandable_count"] == 0
        assert data["total_targets"] == 0
        assert data["dangling_targets"] == 0
        assert data["avg_related_per_note"] == 0.0
