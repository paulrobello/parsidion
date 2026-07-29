"""Tests for vault_read and vault_write tools.

ARC-004/ARC-008: Updated to use resolve_vault() mock and expect exceptions
instead of sentinel error strings.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from parsidion_mcp.tools.notes import VaultToolError, vault_read, vault_write


# ---------------------------------------------------------------------------
# vault_read
# ---------------------------------------------------------------------------


def test_vault_read_returns_content(tmp_path: Path) -> None:
    note = tmp_path / "Patterns" / "my-note.md"
    note.parent.mkdir()
    note.write_text("---\ndate: 2026-01-01\n---\n\n# My Note\n", encoding="utf-8")

    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.resolve_vault.return_value = tmp_path
        result = vault_read("Patterns/my-note.md")

    assert "# My Note" in result


def test_vault_read_absolute_path(tmp_path: Path) -> None:
    note = tmp_path / "test.md"
    note.write_text("content", encoding="utf-8")

    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.resolve_vault.return_value = tmp_path
        result = vault_read(str(note))

    assert result == "content"


def test_vault_read_path_escape_raises(tmp_path: Path) -> None:
    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.resolve_vault.return_value = tmp_path
        with pytest.raises(VaultToolError, match="path escapes vault root"):
            vault_read("../../etc/passwd")


def test_vault_read_missing_file_raises(tmp_path: Path) -> None:
    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.resolve_vault.return_value = tmp_path
        with pytest.raises(VaultToolError, match="note not found"):
            vault_read("nonexistent.md")


def test_vault_read_missing_vault_raises(tmp_path: Path) -> None:
    absent = tmp_path / "NoVault"

    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.resolve_vault.return_value = absent
        with pytest.raises(VaultToolError, match="vault root not found"):
            vault_read("note.md")


# ---------------------------------------------------------------------------
# vault_write
# ---------------------------------------------------------------------------


def test_vault_write_creates_file(tmp_path: Path) -> None:
    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.resolve_vault.return_value = tmp_path
        result = vault_write("new-note.md", "# Hello\n")

    written = tmp_path / "new-note.md"
    assert written.exists()
    assert written.read_text(encoding="utf-8") == "# Hello\n"
    assert str(written) in result


def test_vault_write_creates_parent_dirs(tmp_path: Path) -> None:
    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.resolve_vault.return_value = tmp_path
        vault_write("Patterns/deep/note.md", "content")

    assert (tmp_path / "Patterns" / "deep" / "note.md").exists()


def test_vault_write_overwrites_existing(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("old", encoding="utf-8")

    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.resolve_vault.return_value = tmp_path
        vault_write("note.md", "new")

    assert note.read_text(encoding="utf-8") == "new"


def test_vault_write_path_escape_raises(tmp_path: Path) -> None:
    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.resolve_vault.return_value = tmp_path
        with pytest.raises(VaultToolError, match="path escapes vault root"):
            vault_write("../../evil.md", "content")


def test_vault_write_oserror_raises(tmp_path: Path) -> None:
    with (
        patch("parsidion_mcp.tools.notes.vault_common") as mock_vc,
        patch(
            "parsidion_mcp.tools.notes.Path.write_text",
            side_effect=OSError("disk full"),
        ),
    ):
        mock_vc.resolve_vault.return_value = tmp_path
        with pytest.raises(VaultToolError, match="disk full"):
            vault_write("note.md", "content")


# ---------------------------------------------------------------------------
# ARC-021: vault parameter threaded through to resolve_vault
# ---------------------------------------------------------------------------


class TestVaultParameter:
    """ARC-021: vault_read / vault_write accept an optional *vault* reference
    (name or path) that is forwarded to vault_common.resolve_vault(explicit=…)
    so the MCP layer can target a specific named vault instead of always
    hitting the default.
    """

    def test_vault_read_passes_explicit_vault_to_resolve_vault(
        self, tmp_path: Path
    ) -> None:
        note = tmp_path / "note.md"
        note.write_text("# body", encoding="utf-8")

        # Make resolve_vault return a MagicMock whose .resolve() and .exists()
        # behave like the tmp_path. We cannot monkeypatch PosixPath.exists.
        mock_vault_root = MagicMock()
        mock_vault_root.exists.return_value = True
        mock_vault_root.resolve.return_value = tmp_path
        # _resolve_vault_path computes (vault_root / raw).resolve() — that
        # walks Path code, not the mock, so make vault_root behave like a
        # Path that supports the division operator.
        mock_vault_root.__truediv__ = lambda self, key: tmp_path / key

        with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
            mock_vc.resolve_vault.return_value = mock_vault_root
            # Patch the inner Path resolution so vault_read finds the note.
            # Simpler: patch _resolve_vault_path directly to bypass path math.
            with patch(
                "parsidion_mcp.tools.notes._resolve_vault_path", return_value=note
            ):
                vault_read("note.md", vault="my-vault")

        for call in mock_vc.resolve_vault.call_args_list:
            assert call.kwargs.get("explicit") == "my-vault", (
                f"resolve_vault call missing explicit=vault: {call}"
            )

    def test_vault_write_passes_explicit_vault_to_resolve_vault(
        self, tmp_path: Path
    ) -> None:
        # Mirror the read test: patch _resolve_vault_path so we can verify
        # resolve_vault was called with the explicit vault reference.
        target = tmp_path / "Patterns" / "note.md"

        with (
            patch("parsidion_mcp.tools.notes.vault_common") as mock_vc,
            patch("parsidion_mcp.tools.notes._resolve_vault_path", return_value=target),
        ):
            vault_write("Patterns/note.md", "# body\n", vault="my-vault")

        for call in mock_vc.resolve_vault.call_args_list:
            assert call.kwargs.get("explicit") == "my-vault"
