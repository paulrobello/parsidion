"""ARC-002: Assert that VAULT_DIRS in vault_common.py and install.py are identical.

This test is the programmatic enforcement of the "keep in sync" comment that
previously existed only as a manual reminder.  It catches the case where a
developer adds a new vault folder to one file but forgets to update the other.
"""

import sys
from pathlib import Path

# vault_common
sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "skills" / "claude-vault" / "scripts"),
)
import vault_common  # noqa: E402

# install
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
import install  # noqa: E402


class TestVaultDirsSync:
    """Ensure VAULT_DIRS is identical in vault_common.py and install.py."""

    def test_vault_dirs_identical(self) -> None:
        """VAULT_DIRS in vault_common and install must be the same set."""
        assert set(vault_common.VAULT_DIRS) == set(install.VAULT_DIRS), (
            "VAULT_DIRS mismatch!\n"
            f"  vault_common: {sorted(vault_common.VAULT_DIRS)}\n"
            f"  install.py:   {sorted(install.VAULT_DIRS)}\n"
            "Add the missing directory to both files."
        )

    def test_vault_dirs_same_length(self) -> None:
        """VAULT_DIRS in vault_common and install must have the same length (no duplicates)."""
        assert len(vault_common.VAULT_DIRS) == len(install.VAULT_DIRS), (
            f"VAULT_DIRS length mismatch: "
            f"vault_common has {len(vault_common.VAULT_DIRS)} entries, "
            f"install.py has {len(install.VAULT_DIRS)} entries."
        )
