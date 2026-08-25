"""Tests for the code_search MCP tool (parsight bridge, Task 8)."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from parsidion_mcp.tools.code_search import code_search

_FAKE_NOTE = {
    "score": 0.0512,
    "stem": "my-note",
    "title": "My Note",
    "folder": "Patterns",
    "tags": ["python"],
    "path": "/vault/Patterns/my-note.md",
    "summary": "A summary.",
    "note_type": "pattern",
    "project": "",
    "confidence": "high",
    "mtime": 1700000000.0,
    "related": [],
    "is_stale": False,
    "incoming_links": 2,
}

_FAKE_CODE_HIT = {"file_path": "src/lib.rs", "score": 0.031, "name": "resolve_worktree"}


def test_unavailable_backend_raises() -> None:
    with patch("parsidion_mcp.tools.code_search.parsight_backend") as mock_pb:
        mock_pb.resolve_parsight_backend.return_value = False
        with pytest.raises(ValueError, match="parsight unavailable"):
            code_search(query="anything")


def test_vault_mode_delegates_to_parsight_search() -> None:
    with patch("parsidion_mcp.tools.code_search.parsight_backend") as mock_pb:
        mock_pb.resolve_parsight_backend.return_value = True
        mock_pb.parsight_search.return_value = [_FAKE_NOTE]
        result = code_search(query="hook patterns", top_k=5)
    parsed = json.loads(result)
    assert parsed[0]["stem"] == "my-note"
    mock_pb.parsight_search.assert_called_once_with("hook patterns", top_k=5)


def test_repo_mode_returns_raw_code_hits(tmp_path: Path) -> None:
    with patch("parsidion_mcp.tools.code_search.parsight_backend") as mock_pb:
        mock_pb.resolve_parsight_backend.return_value = True
        mock_pb.find_code_raw.return_value = [_FAKE_CODE_HIT]
        result = code_search(query="worktree resolution", repo_path=str(tmp_path))
    parsed = json.loads(result)
    assert parsed == [_FAKE_CODE_HIT]
    mock_pb.find_code_raw.assert_called_once_with(
        "worktree resolution", top_k=10, cwd=tmp_path
    )


def test_missing_repo_path_raises(tmp_path: Path) -> None:
    with patch("parsidion_mcp.tools.code_search.parsight_backend") as mock_pb:
        mock_pb.resolve_parsight_backend.return_value = True
        with pytest.raises(ValueError, match="repo_path does not exist"):
            code_search(query="q", repo_path=str(tmp_path / "nope"))


def test_query_failure_raises() -> None:
    with patch("parsidion_mcp.tools.code_search.parsight_backend") as mock_pb:
        mock_pb.resolve_parsight_backend.return_value = True
        mock_pb.parsight_search.return_value = None
        with pytest.raises(ValueError, match="parsight query failed"):
            code_search(query="q")


def test_empty_results_are_valid_json() -> None:
    with patch("parsidion_mcp.tools.code_search.parsight_backend") as mock_pb:
        mock_pb.resolve_parsight_backend.return_value = True
        mock_pb.parsight_search.return_value = []
        assert json.loads(code_search(query="q")) == []
