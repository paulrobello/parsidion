"""Tests for the atomic-write + pre-mutation-backup enhancement.

Covers:
- ``vault_fs.atomic_write_text``: writes content, preserves the target's
  existing permission bits, and leaves no ``.tmp`` file behind even when
  ``Path.replace`` fails.
- ``vault_doctor._backup_note``: creates a pre-mutation backup under
  ``.trash/backup/<date>/<relative-path>``, is idempotent within a single
  run, and never blocks the caller's fix when the copy itself fails.
- Execute-mode content mutations and renames call ``_backup_note`` before
  touching the note.
"""

from __future__ import annotations

import stat
from datetime import date
from pathlib import Path

import pytest

import vault_doctor
import vault_fs


@pytest.fixture(autouse=True)
def _patch_vault(tmp_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Wire resolve_vault() to a fresh tmp dir and point vault_doctor there."""
    monkeypatch.setattr(vault_doctor, "_vault_path", tmp_vault)


@pytest.fixture(autouse=True)
def _clear_backup_run_state() -> None:
    """Reset the per-run backup dedup set so tests don't leak into each other."""
    vault_doctor._backed_up_this_run.clear()


@pytest.fixture()
def vault(tmp_vault: Path) -> Path:
    """Return the tmp vault path and create standard dirs."""
    import vault_common

    for d in vault_common.VAULT_DIRS:
        (tmp_vault / d).mkdir(exist_ok=True)
    return tmp_vault


def _write_note(vault: Path, rel_path: str, content: str) -> Path:
    """Helper: write a note file and return its Path."""
    full = vault / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return full


def _boom_replace(self: Path, target: object) -> None:
    raise OSError("simulated replace failure")


# ---------------------------------------------------------------------------
# vault_fs.atomic_write_text
# ---------------------------------------------------------------------------


class TestAtomicWriteText:
    def test_writes_content_no_tmp_leftover(self, tmp_path: Path) -> None:
        target = tmp_path / "note.md"
        vault_fs.atomic_write_text(target, "hello\n")
        assert target.read_text(encoding="utf-8") == "hello\n"
        assert not (tmp_path / "note.md.tmp").exists()

    def test_preserves_existing_permission_bits(self, tmp_path: Path) -> None:
        target = tmp_path / "note.md"
        target.write_text("old\n", encoding="utf-8")
        target.chmod(0o640)

        vault_fs.atomic_write_text(target, "new\n")

        assert target.read_text(encoding="utf-8") == "new\n"
        assert stat.S_IMODE(target.stat().st_mode) == 0o640

    def test_no_tmp_leftover_on_replace_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "note.md"
        target.write_text("old\n", encoding="utf-8")
        monkeypatch.setattr(Path, "replace", _boom_replace)

        with pytest.raises(OSError):
            vault_fs.atomic_write_text(target, "new\n")

        assert target.read_text(encoding="utf-8") == "old\n"
        assert not (tmp_path / "note.md.tmp").exists()


# ---------------------------------------------------------------------------
# vault_doctor._backup_note
# ---------------------------------------------------------------------------


class TestBackupNote:
    def test_creates_backup_with_original_content(self, vault: Path) -> None:
        note = _write_note(vault, "Patterns/note.md", "original content\n")

        vault_doctor._backup_note(vault, note)

        backup = (
            vault
            / ".trash"
            / "backup"
            / date.today().isoformat()
            / "Patterns"
            / "note.md"
        )
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == "original content\n"

    def test_second_call_same_run_does_not_duplicate(self, vault: Path) -> None:
        note = _write_note(vault, "Patterns/note.md", "original content\n")
        vault_doctor._backup_note(vault, note)
        backup = (
            vault
            / ".trash"
            / "backup"
            / date.today().isoformat()
            / "Patterns"
            / "note.md"
        )
        assert backup.read_text(encoding="utf-8") == "original content\n"

        # Note mutates on disk; a second backup call in the same run must
        # not overwrite the first-captured (pre-mutation) version.
        note.write_text("mutated content\n", encoding="utf-8")
        vault_doctor._backup_note(vault, note)

        assert backup.read_text(encoding="utf-8") == "original content\n"

    def test_backup_failure_does_not_block_fix(
        self,
        vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        content = "---\ndate: 2026-03-25\ntype: pattern\n---\n\n## Heading\n"
        note = _write_note(vault, "Patterns/heading.md", content)

        def boom(*args: object, **kwargs: object) -> None:
            raise OSError("simulated backup failure")

        monkeypatch.setattr(vault_doctor.shutil, "copy2", boom)

        result = vault_doctor._auto_fix_headings(note)

        assert result is True
        updated = note.read_text(encoding="utf-8")
        assert "# Heading" in updated
        assert "## Heading" not in updated
        assert "backup failed" in capsys.readouterr().err
        backup_dir = vault / ".trash" / "backup" / date.today().isoformat()
        assert not (backup_dir / "Patterns" / "heading.md").exists()


# ---------------------------------------------------------------------------
# Execute-mode mutation paths call _backup_note before writing/renaming
# ---------------------------------------------------------------------------


class TestExecuteModeBackups:
    def test_content_mutation_backs_up_before_write(self, vault: Path) -> None:
        content = (
            "---\n"
            "date: 2026-03-25\n"
            "type: pattern\n"
            'related: ["[[a]]", "[[a]]"]\n'
            "---\n\n# Test\n"
        )
        note = _write_note(vault, "Patterns/dup.md", content)
        original = note.read_text(encoding="utf-8")

        fixed = vault_doctor.dedup_related_links(dry_run=False, vault_path=vault)

        assert fixed == 1
        backup = (
            vault
            / ".trash"
            / "backup"
            / date.today().isoformat()
            / "Patterns"
            / "dup.md"
        )
        assert backup.read_text(encoding="utf-8") == original
        assert note.read_text(encoding="utf-8") != original

    def test_rename_path_backs_up(self, vault: Path) -> None:
        _write_note(vault, "Projects/myapp/myapp-overview.md", "# Overview\n")

        vault_doctor.run_strip_prefixes(
            dry_run=False, vault_path=vault, auto_reindex=False
        )

        assert (vault / "Projects" / "myapp" / "overview.md").exists()
        backup = (
            vault
            / ".trash"
            / "backup"
            / date.today().isoformat()
            / "Projects"
            / "myapp"
            / "myapp-overview.md"
        )
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == "# Overview\n"
