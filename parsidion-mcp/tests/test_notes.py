"""Tests for vault_read and vault_write tools."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from parsidion_mcp.tools.notes import vault_read, vault_write


# ---------------------------------------------------------------------------
# vault_read
# ---------------------------------------------------------------------------

def test_vault_read_returns_content(tmp_path: Path) -> None:
    note = tmp_path / "Patterns" / "my-note.md"
    note.parent.mkdir()
    note.write_text("---\ndate: 2026-01-01\n---\n\n# My Note\n", encoding="utf-8")

    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        result = vault_read("Patterns/my-note.md")

    assert "# My Note" in result


def test_vault_read_absolute_path(tmp_path: Path) -> None:
    note = tmp_path / "test.md"
    note.write_text("content", encoding="utf-8")

    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        result = vault_read(str(note))

    assert result == "content"


def test_vault_read_path_escape_returns_error(tmp_path: Path) -> None:
    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        result = vault_read("../../etc/passwd")

    assert result.startswith("ERROR: path escapes vault root")


def test_vault_read_missing_file_returns_error(tmp_path: Path) -> None:
    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        result = vault_read("nonexistent.md")

    assert result.startswith("ERROR:")


def test_vault_read_missing_vault_returns_error(tmp_path: Path) -> None:
    absent = tmp_path / "NoVault"

    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = absent
        result = vault_read("note.md")

    assert "vault root not found" in result


# ---------------------------------------------------------------------------
# vault_write
# ---------------------------------------------------------------------------

def test_vault_write_creates_file(tmp_path: Path) -> None:
    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        result = vault_write("new-note.md", "# Hello\n")

    written = tmp_path / "new-note.md"
    assert written.exists()
    assert written.read_text(encoding="utf-8") == "# Hello\n"
    assert str(written) in result


def test_vault_write_creates_parent_dirs(tmp_path: Path) -> None:
    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        vault_write("Patterns/deep/note.md", "content")

    assert (tmp_path / "Patterns" / "deep" / "note.md").exists()


def test_vault_write_overwrites_existing(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("old", encoding="utf-8")

    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        vault_write("note.md", "new")

    assert note.read_text(encoding="utf-8") == "new"


def test_vault_write_path_escape_returns_error(tmp_path: Path) -> None:
    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        result = vault_write("../../evil.md", "content")

    assert result.startswith("ERROR: path escapes vault root")


def test_vault_write_oserror_returns_error(tmp_path: Path) -> None:
    with (
        patch("parsidion_mcp.tools.notes.vault_common") as mock_vc,
        patch("parsidion_mcp.tools.notes.Path.write_text", side_effect=OSError("disk full")),
    ):
        mock_vc.VAULT_ROOT = tmp_path
        result = vault_write("note.md", "content")

    assert result.startswith("ERROR:")
