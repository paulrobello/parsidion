"""Tests for vault_search.py — metadata/grep modes and DB-absent fallback.

QA-005: vault_search (879 lines) has zero dedicated tests.
These tests cover the metadata query() function and _apply_grep_filter,
both of which use only stdlib (no fastembed/sqlite-vec).
"""

from __future__ import annotations

import importlib
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

vault_search = importlib.import_module("vault_search")


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


def _make_db(vault: Path) -> sqlite3.Connection:
    """Create embeddings.db with note_index, return open connection."""
    db_path = vault / "embeddings.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE note_index (
            stem TEXT, path TEXT, folder TEXT, title TEXT, summary TEXT,
            tags TEXT, note_type TEXT, project TEXT, confidence TEXT,
            mtime REAL, related TEXT, is_stale INTEGER DEFAULT 0,
            incoming_links INTEGER DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()
    return sqlite3.connect(str(db_path))


def _insert_note(
    vault: Path,
    *,
    stem: str,
    folder: str = "Patterns",
    tags: str = "",
    note_type: str = "pattern",
    project: str = "",
    mtime: float = 1000.0,
    body: str = "# Test\nBody content.",
) -> Path:
    """Insert a note into note_index and write the actual file."""
    db_path = vault / "embeddings.db"
    conn = sqlite3.connect(str(db_path))
    path = vault / folder / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    conn.execute(
        "INSERT INTO note_index (stem, path, folder, tags, note_type, project, mtime) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (stem, str(path), folder, tags, note_type, project, mtime),
    )
    conn.commit()
    conn.close()
    return path


# ---------------------------------------------------------------------------
# query() — metadata mode
# ---------------------------------------------------------------------------


class TestMetadataQuery:
    def test_returns_empty_when_no_db(self, vault: Path) -> None:
        result = vault_search.query(tag="python", vault=vault)
        assert result == []

    def test_filters_by_tag(self, vault: Path) -> None:
        _make_db(vault)
        _insert_note(vault, stem="py-note", tags="python, vault")
        _insert_note(vault, stem="rust-note", tags="rust")

        results = vault_search.query(tag="python", vault=vault)
        stems = [r["stem"] for r in results]
        assert "py-note" in stems
        assert "rust-note" not in stems

    def test_filters_by_folder(self, vault: Path) -> None:
        _make_db(vault)
        _insert_note(vault, stem="pat-note", folder="Patterns")
        _insert_note(vault, stem="dbg-note", folder="Debugging")

        results = vault_search.query(folder="Patterns", vault=vault)
        stems = [r["stem"] for r in results]
        assert "pat-note" in stems
        assert "dbg-note" not in stems

    def test_filters_by_note_type(self, vault: Path) -> None:
        _make_db(vault)
        _insert_note(vault, stem="pat-note", note_type="pattern")
        _insert_note(vault, stem="dbg-note", note_type="debugging")

        results = vault_search.query(note_type="pattern", vault=vault)
        stems = [r["stem"] for r in results]
        assert "pat-note" in stems
        assert "dbg-note" not in stems

    def test_filters_by_project(self, vault: Path) -> None:
        _make_db(vault)
        _insert_note(vault, stem="proj-note", project="parsidion")
        _insert_note(vault, stem="other-note", project="other")

        results = vault_search.query(project="parsidion", vault=vault)
        stems = [r["stem"] for r in results]
        assert "proj-note" in stems
        assert "other-note" not in stems

    def test_returns_all_when_no_filters(self, vault: Path) -> None:
        _make_db(vault)
        _insert_note(vault, stem="note-a")
        _insert_note(vault, stem="note-b")

        results = vault_search.query(vault=vault)
        assert len(results) >= 2

    def test_result_score_is_none(self, vault: Path) -> None:
        _make_db(vault)
        _insert_note(vault, stem="some-note")
        results = vault_search.query(tag="", vault=vault)
        for r in results:
            assert r["score"] is None

    def test_tag_only_match(self, vault: Path) -> None:
        """Tag filter matches when tag is the sole entry."""
        _make_db(vault)
        _insert_note(vault, stem="single-tag", tags="python")

        results = vault_search.query(tag="python", vault=vault)
        assert any(r["stem"] == "single-tag" for r in results)

    def test_tag_match_first_in_csv(self, vault: Path) -> None:
        """Tag filter matches the first tag in a multi-tag CSV."""
        _make_db(vault)
        _insert_note(vault, stem="first-tag", tags="python, vault")
        results = vault_search.query(tag="python", vault=vault)
        assert any(r["stem"] == "first-tag" for r in results)

    def test_tag_match_last_in_csv(self, vault: Path) -> None:
        """Tag filter matches a trailing tag in a multi-tag CSV."""
        _make_db(vault)
        _insert_note(vault, stem="last-tag", tags="vault, python")
        results = vault_search.query(tag="python", vault=vault)
        assert any(r["stem"] == "last-tag" for r in results)


# ---------------------------------------------------------------------------
# _apply_grep_filter
# ---------------------------------------------------------------------------


class TestApplyGrepFilter:
    def test_matches_body_content(self, vault: Path) -> None:
        _make_db(vault)
        path = _insert_note(
            vault, stem="grep-note", body="# Title\nUnique phrase here."
        )
        results = [
            {
                "score": None,
                "stem": "grep-note",
                "path": str(path),
                "title": "Title",
                "folder": "Patterns",
                "tags": [],
                "note_type": "pattern",
                "project": "",
                "confidence": "",
                "mtime": None,
                "related": [],
                "is_stale": False,
                "incoming_links": 0,
            }
        ]
        matched = vault_search._apply_grep_filter(
            results,
            "Unique phrase",
            case_sensitive=False,
            has_filters=True,
            has_query=False,
            limit=50,
            vault=vault,
        )
        assert len(matched) == 1
        assert matched[0]["stem"] == "grep-note"

    def test_case_insensitive_by_default(self, vault: Path) -> None:
        _make_db(vault)
        path = _insert_note(
            vault, stem="ci-note", body="# Title\nCASE INSENSITIVE content."
        )
        results = [
            {
                "score": None,
                "stem": "ci-note",
                "path": str(path),
                "title": "",
                "folder": "Patterns",
                "tags": [],
                "note_type": "",
                "project": "",
                "confidence": "",
                "mtime": None,
                "related": [],
                "is_stale": False,
                "incoming_links": 0,
            }
        ]
        matched = vault_search._apply_grep_filter(
            results,
            "case insensitive",
            case_sensitive=False,
            has_filters=True,
            has_query=False,
            limit=50,
            vault=vault,
        )
        assert len(matched) == 1

    def test_case_sensitive_no_match(self, vault: Path) -> None:
        _make_db(vault)
        path = _insert_note(vault, stem="cs-note", body="# Title\nlowercase content.")
        results = [
            {
                "score": None,
                "stem": "cs-note",
                "path": str(path),
                "title": "",
                "folder": "Patterns",
                "tags": [],
                "note_type": "",
                "project": "",
                "confidence": "",
                "mtime": None,
                "related": [],
                "is_stale": False,
                "incoming_links": 0,
            }
        ]
        matched = vault_search._apply_grep_filter(
            results,
            "LOWERCASE",
            case_sensitive=True,
            has_filters=True,
            has_query=False,
            limit=50,
            vault=vault,
        )
        assert len(matched) == 0

    def test_skips_nonexistent_files(self, vault: Path) -> None:
        results = [
            {
                "score": None,
                "stem": "missing",
                "path": str(vault / "missing.md"),
                "title": "",
                "folder": "",
                "tags": [],
                "note_type": "",
                "project": "",
                "confidence": "",
                "mtime": None,
                "related": [],
                "is_stale": False,
                "incoming_links": 0,
            }
        ]
        matched = vault_search._apply_grep_filter(
            results,
            "anything",
            case_sensitive=False,
            has_filters=True,
            has_query=False,
            limit=50,
            vault=vault,
        )
        assert matched == []

    def test_standalone_grep_fetches_all_notes(self, vault: Path) -> None:
        """When has_filters=False and has_query=False, fetches notes from vault walk."""
        _make_db(vault)
        _insert_note(vault, stem="standalone", body="# Title\nFindable content here.")
        matched = vault_search._apply_grep_filter(
            [],
            "Findable content",
            case_sensitive=False,
            has_filters=False,
            has_query=False,
            limit=50,
            vault=vault,
        )
        assert any(r["stem"] == "standalone" for r in matched)


# ---------------------------------------------------------------------------
# _get_all_notes_as_results — file-walk fallback
# ---------------------------------------------------------------------------


class TestGetAllNotesAsResults:
    def test_file_walk_fallback_when_no_db(self, vault: Path) -> None:
        note = vault / "Patterns" / "walk-note.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# Walk Note\nContent.\n", encoding="utf-8")

        results = vault_search._get_all_notes_as_results(50, vault)
        stems = [r["stem"] for r in results]
        assert "walk-note" in stems

    def test_result_has_expected_keys(self, vault: Path) -> None:
        note = vault / "Patterns" / "key-check.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# Key Check\n", encoding="utf-8")

        results = vault_search._get_all_notes_as_results(50, vault)
        assert results
        r = results[0]
        for key in ("stem", "path", "folder", "tags", "score", "title"):
            assert key in r, f"Missing key: {key}"
