"""ARC-005: Assert installer VAULT_DIRS tracks vault_path.VAULT_DIRS at runtime.

Previously this was a regex-parse of vault_common.py, but the canonical
definition moved to vault_path.py during the module split. The regex
silently failed and installer.VAULT_DIRS was always the hardcoded
fallback -- this test asserts the *mechanism*: monkeypatching
vault_path.VAULT_DIRS must be reflected in installer.VAULT_DIRS, so the
class of bug where the two drift silently cannot recur.
"""

import pytest

import vault_common
import vault_path
import installer.paths as installer_paths  # noqa: E402


class TestVaultDirsSync:
    """Ensure VAULT_DIRS in installer tracks the canonical vault_path copy."""

    def test_vault_dirs_identical(self) -> None:
        """VAULT_DIRS in vault_path and install must be the same set."""
        assert set(vault_path.VAULT_DIRS) == set(installer_paths.VAULT_DIRS), (
            "VAULT_DIRS mismatch!\n"
            f"  vault_path:   {sorted(vault_path.VAULT_DIRS)}\n"
            f"  install.py:   {sorted(installer_paths.VAULT_DIRS)}\n"
            "install.py should import VAULT_DIRS from vault_path (not parse "
            "vault_common.py source)."
        )

    def test_vault_dirs_same_length(self) -> None:
        """VAULT_DIRS in vault_path and install must have the same length."""
        assert len(vault_path.VAULT_DIRS) == len(installer_paths.VAULT_DIRS), (
            f"VAULT_DIRS length mismatch: "
            f"vault_path has {len(vault_path.VAULT_DIRS)} entries, "
            f"install.py has {len(installer_paths.VAULT_DIRS)} entries."
        )

    def test_vault_dirs_preserves_order(self) -> None:
        """VAULT_DIRS in install.py should preserve order from vault_path."""
        assert vault_path.VAULT_DIRS == installer_paths.VAULT_DIRS, (
            "VAULT_DIRS order mismatch!\n"
            f"  vault_path: {vault_path.VAULT_DIRS}\n"
            f"  install.py: {installer_paths.VAULT_DIRS}"
        )

    def test_vault_dirs_common_reexport_matches_path(self) -> None:
        """vault_common.VAULT_DIRS (back-compat facade) must match vault_path."""
        assert set(vault_common.VAULT_DIRS) == set(vault_path.VAULT_DIRS), (
            "vault_common.VAULT_DIRS has drifted from vault_path.VAULT_DIRS. "
            "The facade should re-export the canonical constant."
        )

    def test_mechanism_installer_tracks_vault_path_monkeypatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Patching vault_path.VAULT_DIRS must surface via installer_paths.VAULT_DIRS.

        This is the structural guard against the original bug: the regex
        parse silently returned the fallback, so the values happened to
        match while the mechanism was inoperative. With the direct import,
        re-calling the resolver after a monkeypatch must reflect the new
        list.
        """
        sentinel = ["__sentinel__", "Daily"]
        monkeypatch.setattr(vault_path, "VAULT_DIRS", sentinel)
        # Re-resolve from the patched source. _extract_vault_dirs reads
        # vault_path.VAULT_DIRS via the import path (the test path was
        # inserted at installer import time; sys.modules already has it).
        from installer import paths as installer_paths

        resolved = installer_paths._extract_vault_dirs()
        assert resolved == sentinel, (
            f"installer/paths.py did not pick up the patched vault_path.VAULT_DIRS; "
            f"got {resolved!r}. The mechanism is inoperative -- the import path is "
            "not being used."
        )
