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
# SEC-008: vault_read restricted to markdown notes
# ---------------------------------------------------------------------------

_EXCLUDE_DIRS = {".obsidian", "Templates", ".git", ".trash", "TagsRoutes"}


class TestVaultReadNoteOnly:
    """SEC-008: vault_read mirrors the write rules — .md notes only."""

    def test_read_config_local_yaml_rejected(self, tmp_path: Path) -> None:
        # config.local.yaml is the documented home for ANTHROPIC_API_KEY.
        (tmp_path / "config.local.yaml").write_text(
            "ai:\n  key: secret\n", encoding="utf-8"
        )
        with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
            mock_vc.resolve_vault.return_value = tmp_path
            mock_vc.EXCLUDE_DIRS = _EXCLUDE_DIRS
            with pytest.raises(VaultToolError, match=r"\.md files are readable"):
                vault_read("config.local.yaml")

    def test_read_git_config_rejected(self, tmp_path: Path) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("[core]\n", encoding="utf-8")
        with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
            mock_vc.resolve_vault.return_value = tmp_path
            mock_vc.EXCLUDE_DIRS = _EXCLUDE_DIRS
            # The .md-suffix guard fires first; the dot segment would also
            # be refused by the hidden-path guard.
            with pytest.raises(VaultToolError, match="readable"):
                vault_read(".git/config")

    def test_read_hidden_md_rejected(self, tmp_path: Path) -> None:
        # A .md file under a dot directory hits the hidden-path guard even
        # though the suffix is valid.
        hidden = tmp_path / ".obsidian"
        hidden.mkdir()
        (hidden / "workspace.md").write_text("# x\n", encoding="utf-8")
        with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
            mock_vc.resolve_vault.return_value = tmp_path
            mock_vc.EXCLUDE_DIRS = _EXCLUDE_DIRS
            with pytest.raises(VaultToolError, match="not readable"):
                vault_read(".obsidian/workspace.md")

    def test_read_excluded_dir_rejected(self, tmp_path: Path) -> None:
        tpl = tmp_path / "Templates"
        tpl.mkdir()
        (tpl / "x.md").write_text("# t\n", encoding="utf-8")
        with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
            mock_vc.resolve_vault.return_value = tmp_path
            mock_vc.EXCLUDE_DIRS = _EXCLUDE_DIRS
            with pytest.raises(VaultToolError, match="Excluded directory"):
                vault_read("Templates/x.md")

    def test_read_binary_file_raises_not_a_text_note(self, tmp_path: Path) -> None:
        note = tmp_path / "bin.md"
        note.write_bytes(b"\x80\x81\x00\xffnot-utf8")
        with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
            mock_vc.resolve_vault.return_value = tmp_path
            mock_vc.EXCLUDE_DIRS = _EXCLUDE_DIRS
            with pytest.raises(VaultToolError, match="not a text note"):
                vault_read("bin.md")

    def test_read_oversized_note_rejected(self, tmp_path: Path) -> None:
        note = tmp_path / "big.md"
        note.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
        with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
            mock_vc.resolve_vault.return_value = tmp_path
            mock_vc.EXCLUDE_DIRS = _EXCLUDE_DIRS
            with pytest.raises(VaultToolError, match="10 MB limit"):
                vault_read("big.md")

    def test_read_normal_note_still_works(self, tmp_path: Path) -> None:
        note = tmp_path / "Patterns" / "ok.md"
        note.parent.mkdir()
        note.write_text("# fine\n", encoding="utf-8")
        with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
            mock_vc.resolve_vault.return_value = tmp_path
            mock_vc.EXCLUDE_DIRS = _EXCLUDE_DIRS
            assert "# fine" in vault_read("Patterns/ok.md")


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
    # SEC-P003: vault_write now opens via os.open + writes through an fd, so
    # the failure point moved off Path.write_text. Patch os.open to raise.
    with (
        patch("parsidion_mcp.tools.notes.vault_common") as mock_vc,
        patch(
            "parsidion_mcp.tools.notes.os.open",
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
        # SEC-P003: vault_write now re-validates against a real vault_root
        # (re-resolve + is_relative_to) instead of trusting _resolve_vault_path's
        # patched return value. Use a real filesystem layout under tmp_path
        # so the re-validation succeeds and the explicit vault kwarg still
        # reaches resolve_vault.
        (tmp_path / "Patterns").mkdir()

        with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
            mock_vc.resolve_vault.return_value = tmp_path
            vault_write("Patterns/note.md", "# body\n", vault="my-vault")

        for call in mock_vc.resolve_vault.call_args_list:
            assert call.kwargs.get("explicit") == "my-vault"


# ---------------------------------------------------------------------------
# SEC-P003: TOCTOU hardening — symlink-swap on the write path
# ---------------------------------------------------------------------------


def test_vault_write_rejects_parent_symlink_swap(tmp_path: Path) -> None:
    """SEC-P003: a parent-dir symlink swap between validation and the write
    must be rejected, and the attacker's target file must not be modified.

    Simulates the TOCTOU the audit flagged: ``_resolve_vault_path`` validates
    ``Patterns/note.md`` as a clean path inside the vault, then a parent
    directory is swapped to a symlink that points outside the vault. The
    re-resolve + re-validate inside ``vault_write`` must catch this before
    the file is opened, and the attacker's file outside the vault must be
    left untouched.
    """
    # Original layout: <vault>/Patterns/note.md (a regular file).
    real_dir = tmp_path / "Patterns"
    real_dir.mkdir()
    target = real_dir / "note.md"
    target.write_text("orig", encoding="utf-8")
    # Attacker destination OUTSIDE the vault root.
    outside_dir = tmp_path.parent / "attacker_patterns"
    outside_dir.mkdir()
    outside_target = outside_dir / "note.md"
    outside_target.write_text("secret", encoding="utf-8")

    # Wrap _resolve_vault_path so the real validation runs first (path is
    # clean at T0), then the parent dir is swapped to a symlink pointing
    # outside the vault before the function returns. vault_write's later
    # re-resolve will follow the symlink and reject the write.
    from parsidion_mcp.tools.notes import _resolve_vault_path as real_resolver

    swap_done = {"value": False}

    def swapping_resolver(path: str, vault: str | None = None) -> Path:
        result = real_resolver(path, vault=vault)
        if not swap_done["value"]:
            target.unlink()
            real_dir.rmdir()
            real_dir.symlink_to(outside_dir)
            swap_done["value"] = True
        return result

    with (
        patch("parsidion_mcp.tools.notes.vault_common") as mock_vc,
        patch(
            "parsidion_mcp.tools.notes._resolve_vault_path",
            side_effect=swapping_resolver,
        ),
    ):
        mock_vc.resolve_vault.return_value = tmp_path
        with pytest.raises(VaultToolError, match="path escapes vault root"):
            vault_write("Patterns/note.md", "attacker content")

    # The attacker's file outside the vault was NOT modified.
    assert outside_target.read_text(encoding="utf-8") == "secret"


def test_vault_write_rejects_leaf_symlink_to_outside(tmp_path: Path) -> None:
    """SEC-P003: a leaf symlink that resolves outside the vault is rejected.

    Mirrors the leaf-swap TOCTOU. ``_resolve_vault_path`` is patched to skip
    its own validation (simulating a clean check at T0); the leaf on disk is
    a symlink pointing outside the vault. The re-resolve + re-validate inside
    ``vault_write`` must reject the write before any bytes are flushed.
    """
    # Attacker destination OUTSIDE the vault root.
    outside = tmp_path.parent / "outside_target.md"
    outside.write_text("secret", encoding="utf-8")
    # Symlink leaf inside the vault pointing outside.
    leaf = tmp_path / "note.md"
    leaf.symlink_to(outside)

    with (
        patch("parsidion_mcp.tools.notes.vault_common") as mock_vc,
        patch("parsidion_mcp.tools.notes._resolve_vault_path", return_value=leaf),
    ):
        mock_vc.resolve_vault.return_value = tmp_path
        with pytest.raises(VaultToolError, match="path escapes vault root"):
            vault_write("note.md", "attacker content")

    assert outside.read_text(encoding="utf-8") == "secret"
