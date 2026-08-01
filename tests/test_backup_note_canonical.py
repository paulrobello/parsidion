"""Regression test for the QA-001 backup_note consolidation.

The repo had two ``_backup_note`` implementations with reversed parameter
orders:

* ``doctor/_state.py::_backup_note(vault, note_path)`` — vault first.
* ``summarizer/notes.py::_backup_note(note_path, vault)`` — note_path first.

Both were ``Path``-typed, so the type checker could not see a swapped-args
call. QA-001 extracted a single canonical helper
``vault_fs.backup_note(note_path, vault)`` (note_path first) and rewired
both wrappers to delegate to it without changing their public signatures.

This test pins the canonical order: a call with the correct order produces
a backup at ``<vault>/.trash/backup/<date>/<rel>``; a call with swapped
arguments produces no backup (the swapped vault path is not relative_to
the swapped note path, so the helper silently no-ops).

It also asserts both legacy wrappers still behave correctly so a future
edit that flips the delegation direction is caught.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import vault_doctor
import vault_fs
from summarizer import notes as summarizer_notes


def _write_note(vault: Path, rel_path: str, content: str) -> Path:
    """Write a note under *vault* and return its absolute path."""
    full = vault / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return full


def _backup_path(vault: Path, rel_path: str) -> Path:
    """Return the expected backup destination for *rel_path* under *vault*."""
    return vault / ".trash" / "backup" / date.today().isoformat() / rel_path


@pytest.fixture(autouse=True)
def _clear_doctor_backup_run_state() -> None:
    """Reset doctor's per-run dedup set so tests don't leak into each other."""
    vault_doctor._backed_up_this_run.clear()


# ---------------------------------------------------------------------------
# Canonical helper: vault_fs.backup_note(note_path, vault)
# ---------------------------------------------------------------------------


class TestCanonicalBackupNote:
    def test_correct_order_produces_backup(self, tmp_vault: Path) -> None:
        note = _write_note(tmp_vault, "Patterns/note.md", "original\n")

        vault_fs.backup_note(note, tmp_vault)

        backup = _backup_path(tmp_vault, "Patterns/note.md")
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == "original\n"

    def test_swapped_order_produces_no_backup(self, tmp_vault: Path) -> None:
        """Calling with (vault, note) — args reversed — must not back up.

        The swapped call passes the *vault* as ``note_path`` and the *note*
        as ``vault``. ``vault.relative_to(note)`` raises ``ValueError``
        (the vault is not under the note), and the helper silently returns.
        No backup file is created. This is the regression guard: a refactor
        that accidentally flips the canonical order would make the swapped
        call start producing backups under a nonsense path.
        """
        note = _write_note(tmp_vault, "Patterns/note.md", "original\n")

        vault_fs.backup_note(tmp_vault, note)  # swapped on purpose

        # No backup tree created at all (the helper returned before mkdir).
        assert not (tmp_vault / ".trash" / "backup").exists()

    def test_first_version_of_day_wins(self, tmp_vault: Path) -> None:
        note = _write_note(tmp_vault, "Patterns/note.md", "original\n")
        backup = _backup_path(tmp_vault, "Patterns/note.md")

        vault_fs.backup_note(note, tmp_vault)
        assert backup.read_text(encoding="utf-8") == "original\n"

        # Mutate the note and back up again — the first snapshot must stay.
        note.write_text("mutated\n", encoding="utf-8")
        vault_fs.backup_note(note, tmp_vault)
        assert backup.read_text(encoding="utf-8") == "original\n"

    def test_note_outside_vault_no_ops(self, tmp_vault: Path, tmp_path: Path) -> None:
        # tmp_vault is tmp_path itself (see conftest), so use a sibling dir
        # outside the vault to actually exercise the relative_to escape.
        outside_dir = tmp_path.parent / "outside_notes"
        outside_dir.mkdir(parents=True, exist_ok=True)
        outside = outside_dir / "note.md"
        outside.write_text("hi\n", encoding="utf-8")

        vault_fs.backup_note(outside, tmp_vault)

        assert not (tmp_vault / ".trash").exists()

    def test_raises_on_copy_failure(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        note = _write_note(tmp_vault, "Patterns/note.md", "original\n")

        def boom(*args: object, **kwargs: object) -> None:
            raise OSError("simulated copy failure")

        monkeypatch.setattr(vault_doctor.shutil, "copy2", boom)
        with pytest.raises(OSError, match="simulated copy failure"):
            vault_fs.backup_note(note, tmp_vault)


# ---------------------------------------------------------------------------
# Legacy wrappers still delegate correctly (signature-compat regression)
# ---------------------------------------------------------------------------


class TestLegacyWrappers:
    """doctor._backup_note(vault, note) and summarizer._backup_note(note, vault)

    Both must keep working with their existing parameter order — their many
    call sites were not touched by QA-001. This pins the delegation direction
    so a future edit that accidentally swaps the wrapper signatures is caught.
    """

    def test_doctor_wrapper_vault_first(self, tmp_vault: Path) -> None:
        note = _write_note(tmp_vault, "Patterns/doctor.md", "doctor\n")

        # Doctor's call signature is (vault, note) — vault first.
        vault_doctor._backup_note(tmp_vault, note)

        backup = _backup_path(tmp_vault, "Patterns/doctor.md")
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == "doctor\n"

    def test_summarizer_wrapper_note_first(self, tmp_vault: Path) -> None:
        note = _write_note(tmp_vault, "Patterns/summ.md", "summ\n")

        # Summarizer's call signature is (note, vault) — note first.
        summarizer_notes._backup_note(note, tmp_vault)

        backup = _backup_path(tmp_vault, "Patterns/summ.md")
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == "summ\n"

    def test_doctor_wrapper_swallows_oserror(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Doctor's contract is "never raise" — OSError must be caught."""
        note = _write_note(tmp_vault, "Patterns/doctor.md", "doctor\n")

        def boom(*args: object, **kwargs: object) -> None:
            raise OSError("simulated copy failure")

        monkeypatch.setattr(vault_doctor.shutil, "copy2", boom)

        # Must NOT raise — doctor wraps the helper in try/except.
        vault_doctor._backup_note(tmp_vault, note)

    def test_summarizer_wrapper_propagates_oserror(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Summarizer's contract is "let OSError propagate"."""
        note = _write_note(tmp_vault, "Patterns/summ.md", "summ\n")

        def boom(*args: object, **kwargs: object) -> None:
            raise OSError("simulated copy failure")

        monkeypatch.setattr(vault_doctor.shutil, "copy2", boom)

        with pytest.raises(OSError, match="simulated copy failure"):
            summarizer_notes._backup_note(note, tmp_vault)
