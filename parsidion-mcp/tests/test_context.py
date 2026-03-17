"""Tests for vault_context tool."""

from pathlib import Path
from unittest.mock import patch

from parsidion_mcp.tools.context import vault_context, _build_compact_index


# ---------------------------------------------------------------------------
# _build_compact_index
# ---------------------------------------------------------------------------


def test_build_compact_index_formats_notes(tmp_path: Path) -> None:
    note = tmp_path / "Patterns" / "test.md"
    note.parent.mkdir()
    note.write_text(
        "---\ntags: [python, pattern]\n---\n# Test Note\n", encoding="utf-8"
    )

    with patch("parsidion_mcp.tools.context.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        mock_vc.parse_frontmatter.return_value = {"tags": ["python", "pattern"]}
        mock_vc.extract_title.return_value = "Test Note"

        result = _build_compact_index([note])

    assert "[[test]]" in result
    assert "Test Note" in result
    assert "`python`" in result
    assert "Patterns" in result
    assert "**Available vault notes**" in result


def test_build_compact_index_empty_returns_message() -> None:
    with patch("parsidion_mcp.tools.context.vault_common"):
        result = _build_compact_index([])

    assert "No vault notes" in result


def test_build_compact_index_truncates_at_max_chars(tmp_path: Path) -> None:
    notes = []
    for i in range(20):
        n = tmp_path / f"note-{i}.md"
        n.write_text("---\ntags: []\n---\n# Note\n", encoding="utf-8")
        notes.append(n)

    with patch("parsidion_mcp.tools.context.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        mock_vc.parse_frontmatter.return_value = {"tags": []}
        mock_vc.extract_title.return_value = "A" * 80  # long title

        result = _build_compact_index(notes, max_chars=200)

    assert "more notes" in result


# ---------------------------------------------------------------------------
# vault_context
# ---------------------------------------------------------------------------


def test_vault_context_with_project(tmp_path: Path) -> None:
    note = tmp_path / "proj.md"
    note.write_text("---\ntags: []\n---\n# Proj\n", encoding="utf-8")

    with patch("parsidion_mcp.tools.context.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        mock_vc.find_notes_by_project.return_value = [note]
        mock_vc.find_recent_notes.return_value = []
        mock_vc.parse_frontmatter.return_value = {"tags": []}
        mock_vc.extract_title.return_value = "Proj"

        result = vault_context(project="myproject", recent_days=3)

    mock_vc.find_notes_by_project.assert_called_once_with("myproject")
    mock_vc.find_recent_notes.assert_called_once_with(3)
    assert "[[proj]]" in result


def test_vault_context_deduplicates_notes(tmp_path: Path) -> None:
    note = tmp_path / "dup.md"
    note.write_text("---\ntags: []\n---\n# Dup\n", encoding="utf-8")

    with patch("parsidion_mcp.tools.context.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        mock_vc.find_notes_by_project.return_value = [note]
        mock_vc.find_recent_notes.return_value = [note]  # same note
        mock_vc.parse_frontmatter.return_value = {"tags": []}
        mock_vc.extract_title.return_value = "Dup"

        result = vault_context(project="x")

    # Should appear only once
    assert result.count("[[dup]]") == 1


def test_vault_context_verbose_calls_build_context_block(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("---\ntags: []\n---\n# Note\n", encoding="utf-8")

    with patch("parsidion_mcp.tools.context.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        mock_vc.find_notes_by_project.return_value = []
        mock_vc.find_recent_notes.return_value = [note]
        mock_vc.build_context_block.return_value = "VERBOSE CONTEXT"

        result = vault_context(verbose=True)

    assert result == "VERBOSE CONTEXT"
    mock_vc.build_context_block.assert_called_once()


def test_vault_context_no_notes_returns_message() -> None:
    with patch("parsidion_mcp.tools.context.vault_common") as mock_vc:
        mock_vc.find_recent_notes.return_value = []
        result = vault_context()

    assert "No relevant" in result
