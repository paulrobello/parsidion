"""SEC-106 tests — symlinked .md files must not bypass vault containment.

``os.walk`` does not follow symlinked *directories* but it does list
symlinked *files*, so a shared-vault committer can plant
``Patterns/evil.md -> ~/.ssh/id_ed25519`` and have the indexer read it
(``_extract_summary`` would then write the first body line into the
synced ``CLAUDE.md`` / ``MANIFEST.md``). The fix skips any symlinked
``.md`` whose ``resolve()`` escapes the vault root, while preserving the
intentional ``Templates/`` symlink (excluded via ``EXCLUDE_DIRS`` before
the symlink guard runs).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import vault_index  # noqa: E402
import vault_metrics  # noqa: E402
from vault_path import is_symlink_inside_vault  # noqa: E402


class TestSymlinkGuardHelper:
    """Direct tests for ``vault_path.is_symlink_inside_vault``."""

    def test_regular_file_is_safe(self, tmp_path: Path) -> None:
        f = tmp_path / "note.md"
        f.write_text("# ok")
        assert is_symlink_inside_vault(f, tmp_path.resolve()) is True

    def test_symlink_inside_vault_is_safe(self, tmp_path: Path) -> None:
        target = tmp_path / "target.md"
        target.write_text("# inside")
        link = tmp_path / "link.md"
        link.symlink_to(target)
        assert is_symlink_inside_vault(link, tmp_path.resolve()) is True

    def test_symlink_outside_vault_is_unsafe(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "secret.md"
        target.write_text("sensitive")
        vault = tmp_path / "vault"
        vault.mkdir()
        link = vault / "evil.md"
        link.symlink_to(target)
        assert is_symlink_inside_vault(link, vault.resolve()) is False

    def test_dead_symlink_with_outside_target_is_unsafe(self, tmp_path: Path) -> None:
        # A broken symlink whose target would resolve outside the vault is
        # rejected. ``Path.resolve()`` without ``strict=True`` does not raise
        # for missing targets — the resolved path is computed lexically — so
        # the helper still sees the escape.
        outside = tmp_path / "outside"
        outside.mkdir()
        vault = tmp_path / "vault"
        vault.mkdir()
        link = vault / "evil.md"
        link.symlink_to(outside / "missing.md")
        assert is_symlink_inside_vault(link, vault.resolve()) is False


class TestWalkVaultNotesSymlinks:
    """``_walk_vault_notes`` skips escaping symlinks, preserves Templates/."""

    def test_evil_symlink_not_yielded(self, tmp_vault: Path) -> None:
        patterns = tmp_vault / "Patterns"
        patterns.mkdir(parents=True)
        (patterns / "real.md").write_text("# real\n")

        # Outside-the-vault target (sibling of the vault root).
        outside = tmp_vault.parent / "outside_sec106"
        outside.mkdir(exist_ok=True)
        secret = outside / "secret.md"
        secret.write_text("sensitive")

        evil = patterns / "evil.md"
        evil.symlink_to(secret)

        notes = {Path(n).name for n in vault_index._walk_vault_notes(tmp_vault)}
        assert "real.md" in notes
        assert "evil.md" not in notes

    def test_templates_symlink_preserved(self, tmp_vault: Path) -> None:
        """``Templates`` is in ``EXCLUDE_DIRS`` and is pruned *before* the
        new symlink guard runs — the intentional symlink must keep working
        (i.e. the symlink guard must not regress the exclude check).
        """
        templates_target = tmp_vault.parent / "real_templates"
        templates_target.mkdir(exist_ok=True)
        (templates_target / "tpl.md").write_text("# template\n")
        templates_link = tmp_vault / "Templates"
        templates_link.symlink_to(templates_target, target_is_directory=True)

        # Sanity: EXCLUDE_DIRS still contains Templates.
        from vault_path import EXCLUDE_DIRS

        assert "Templates" in EXCLUDE_DIRS

        notes = {Path(n).name for n in vault_index._walk_vault_notes(tmp_vault)}
        # Templates dir is pruned, so tpl.md is not yielded at all — this
        # is the existing behavior and the new symlink guard must not
        # change it.
        assert "tpl.md" not in notes


class TestMetricsRglobSymlinks:
    """The two ``vault.rglob('*.md')`` sites in vault_metrics skip escaping symlinks."""

    def test_collect_by_folder_skips_evil_symlink(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        patterns = vault / "Patterns"
        patterns.mkdir(parents=True)
        (patterns / "real.md").write_text("# real\n")

        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.md"
        secret.write_text("sensitive")
        (patterns / "evil.md").symlink_to(secret)

        result = vault_metrics.collect_no_db_summary(vault=vault)
        # Only real.md counts; evil.md is filtered.
        assert result["total"] == 1
        assert result["by_folder"][0]["folder"] == "Patterns"

    def test_collect_timeline_skips_evil_symlink(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        patterns = vault / "Patterns"
        patterns.mkdir(parents=True)
        (patterns / "real.md").write_text("# real\n")

        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.md"
        secret.write_text("sensitive")
        (patterns / "evil.md").symlink_to(secret)

        result = vault_metrics.collect_timeline(conn=None, days=5, vault=vault)
        total = sum(day["n"] for day in result)
        # Only real.md is counted; the symlinked file is skipped.
        assert total == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
