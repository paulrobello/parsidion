"""Tests for vault_search tool."""

import json
from pathlib import Path
from unittest.mock import patch

from parsidion_mcp.tools.search import vault_search

_FAKE_NOTE = {
    "score": 0.85,
    "stem": "my-note",
    "title": "My Note",
    "folder": "Patterns",
    "tags": ["python", "pattern"],
    "path": "/vault/Patterns/my-note.md",
    "summary": "",
    "note_type": "pattern",
    "project": "",
    "confidence": "high",
    "mtime": 1700000000.0,
    "related": [],
    "is_stale": False,
    "incoming_links": 2,
}


# ---------------------------------------------------------------------------
# Semantic mode
# ---------------------------------------------------------------------------

def test_semantic_search_returns_json(tmp_path: Path) -> None:
    db = tmp_path / "embeddings.db"
    db.touch()

    with (
        patch("parsidion_mcp.tools.search.vault_common") as mock_vc,
        patch("parsidion_mcp.tools.search._vault_search_module") as mock_vs,
    ):
        mock_vc.get_embeddings_db_path.return_value = db
        mock_vs.search.return_value = [_FAKE_NOTE]

        result = vault_search(query="python patterns")

    parsed = json.loads(result)
    assert len(parsed) == 1
    assert parsed[0]["stem"] == "my-note"
    mock_vs.search.assert_called_once_with("python patterns", top=10, min_score=0.35)


def test_semantic_search_missing_db_returns_error(tmp_path: Path) -> None:
    absent_db = tmp_path / "missing.db"

    with patch("parsidion_mcp.tools.search.vault_common") as mock_vc:
        mock_vc.get_embeddings_db_path.return_value = absent_db
        result = vault_search(query="anything")

    assert result.startswith("ERROR: embeddings DB not found")


def test_semantic_search_respects_top_k_and_min_score(tmp_path: Path) -> None:
    db = tmp_path / "embeddings.db"
    db.touch()

    with (
        patch("parsidion_mcp.tools.search.vault_common") as mock_vc,
        patch("parsidion_mcp.tools.search._vault_search_module") as mock_vs,
    ):
        mock_vc.get_embeddings_db_path.return_value = db
        mock_vs.search.return_value = []
        vault_search(query="q", top_k=5, min_score=0.6)

    mock_vs.search.assert_called_once_with("q", top=5, min_score=0.6)


# ---------------------------------------------------------------------------
# Metadata mode
# ---------------------------------------------------------------------------

def test_metadata_search_returns_json() -> None:
    with patch("parsidion_mcp.tools.search._vault_search_module") as mock_vs:
        mock_vs.query.return_value = [_FAKE_NOTE]
        result = vault_search(tag="python", folder="Patterns")

    parsed = json.loads(result)
    assert parsed[0]["folder"] == "Patterns"
    mock_vs.query.assert_called_once_with(
        tag="python",
        folder="Patterns",
        note_type=None,
        project=None,
        recent_days=None,
        limit=10,
    )


def test_metadata_search_empty_returns_empty_json() -> None:
    with patch("parsidion_mcp.tools.search._vault_search_module") as mock_vs:
        mock_vs.query.return_value = []
        result = vault_search(folder="Nonexistent")

    assert json.loads(result) == []
