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

    mock_vc.find_notes_by_project.assert_called_once_with("myproject")
    mock_vc.find_recent_notes.assert_called_once_with(3)
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
# ARC-021: vault parameter swaps VAULT_ROOT for the call duration
# ---------------------------------------------------------------------------


def test_vault_context_with_explicit_vault_restores_root(tmp_path: Path) -> None:
    """ARC-021: when *vault* is provided, vault_common.VAULT_ROOT is swapped
    for the duration of the call and restored on exit so a long-lived MCP
    server's globals stay stable across requests."""
    note = tmp_path / "note.md"
    note.write_text("---\ntags: []\n---\n# Note\n", encoding="utf-8")

    sentinel_root = Path("/tmp/sentinel-default-root")
    with patch("parsidion_mcp.tools.context.vault_common") as mock_vc:
        # Initial VAULT_ROOT (what the server "had" before the call).
        mock_vc.VAULT_ROOT = sentinel_root
        # resolve_vault returns the explicit vault path.
        mock_vc.resolve_vault.return_value = tmp_path
        mock_vc.find_notes_by_project.return_value = []
        mock_vc.find_recent_notes.return_value = [note]
        mock_vc.build_compact_index.return_value = "INDEX"

        vault_context(vault="my-vault")

        # resolve_vault was called with the explicit vault reference.
        mock_vc.resolve_vault.assert_any_call(explicit="my-vault")
        # VAULT_ROOT was restored to the sentinel after the call.
        assert mock_vc.VAULT_ROOT == sentinel_root, (
            "vault_context did not restore VAULT_ROOT after the call"
        )


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

        # resolve_vault was not called (the early branch in the impl).
        mock_vc.resolve_vault.assert_not_called()
        assert mock_vc.VAULT_ROOT == sentinel_root
