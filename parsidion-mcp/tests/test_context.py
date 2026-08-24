"""Tests for vault_context tool."""

from pathlib import Path
from unittest.mock import patch

from parsidion_mcp.tools.context import vault_context


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
        mock_vc.build_compact_index.return_value = "COMPACT INDEX"

        vault_context(project="myproject", recent_days=3)

    # SEC-032: the (default None) vault is passed explicitly; no global swap.
    mock_vc.find_notes_by_project.assert_called_once_with("myproject", vault=None)
    mock_vc.find_recent_notes.assert_called_once_with(3, vault=None)
    mock_vc.build_compact_index.assert_called_once()


def test_vault_context_deduplicates_notes(tmp_path: Path) -> None:
    note = tmp_path / "dup.md"
    note.write_text("---\ntags: []\n---\n# Dup\n", encoding="utf-8")

    with patch("parsidion_mcp.tools.context.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        mock_vc.find_notes_by_project.return_value = [note]
        mock_vc.find_recent_notes.return_value = [note]  # same note
        mock_vc.build_compact_index.return_value = "INDEX"

        vault_context(project="x")

    # Deduplicated list passed to build_compact_index
    args = mock_vc.build_compact_index.call_args[0]
    assert len(args[0]) == 1  # only one note, not two


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


# ---------------------------------------------------------------------------
# ARC-021 + SEC-032: vault parameter is threaded explicitly, never a global swap
# ---------------------------------------------------------------------------


def test_vault_context_with_explicit_vault_threads_root(tmp_path: Path) -> None:
    """SEC-032: when *vault* is provided, the resolved root is passed
    explicitly to the helpers; the module-global VAULT_ROOT is never
    touched (concurrent multi-vault calls cannot read the wrong vault)."""
    note = tmp_path / "note.md"
    note.write_text("---\ntags: []\n---\n# Note\n", encoding="utf-8")

    sentinel_root = Path("/tmp/sentinel-default-root")
    with patch("parsidion_mcp.tools.context.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = sentinel_root
        mock_vc.resolve_vault.return_value = tmp_path
        mock_vc.find_notes_by_project.return_value = []
        mock_vc.find_recent_notes.return_value = [note]
        mock_vc.build_compact_index.return_value = "INDEX"

        vault_context(project="proj", vault="my-vault")

        mock_vc.resolve_vault.assert_called_once_with(explicit="my-vault")
        mock_vc.find_notes_by_project.assert_called_once_with("proj", vault=tmp_path)
        mock_vc.find_recent_notes.assert_called_once_with(3, vault=tmp_path)
        mock_vc.build_compact_index.assert_called_once()
        # The global was never read for resolution and never mutated.
        assert mock_vc.VAULT_ROOT == sentinel_root


def test_vault_context_without_vault_does_not_swap_root(tmp_path: Path) -> None:
    """Without *vault*, vault_context must not touch VAULT_ROOT."""
    note = tmp_path / "note.md"
    note.write_text("---\ntags: []\n---\n# Note\n", encoding="utf-8")

    sentinel_root = Path("/tmp/sentinel-default-root")
    with patch("parsidion_mcp.tools.context.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = sentinel_root
        mock_vc.find_notes_by_project.return_value = []
        mock_vc.find_recent_notes.return_value = [note]
        mock_vc.build_compact_index.return_value = "INDEX"

        vault_context()

        mock_vc.resolve_vault.assert_not_called()
        assert mock_vc.VAULT_ROOT == sentinel_root
