"""SEC-010: the daily-note username must stay a filename-safe segment.

``DD-{username}.md`` puts the username inside a path under
``Daily/YYYY-MM/``. An unvalidated value containing a separator (from
``vault.username`` config, ``$USER``, or ``--daily-username``) would move
the note outside the month directory during migration. These tests pin the
three guards: charset validation in ``get_vault_username()``, the argparse
``type=`` on ``--daily-username``, and the same-parent assertion in
``run_migrate_daily_notes``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "skills" / "parsidion/scripts")
)

import vault_fs  # noqa: E402
from doctor import cli as doctor_cli  # noqa: E402
from doctor.daily import run_migrate_daily_notes  # noqa: E402


@pytest.fixture()
def clean_username_env(tmp_vault: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # tmp_vault (conftest) wires resolve_vault()/load_config() to an empty
    # temp vault so the real ~/ParsidionVault config cannot leak in.
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)
    return tmp_vault


class TestGetVaultUsername:
    def test_valid_username_returned(
        self, monkeypatch: pytest.MonkeyPatch, clean_username_env: Path
    ) -> None:
        monkeypatch.setenv("USER", "probello")
        assert vault_fs.get_vault_username() == "probello"

    def test_empty_falls_back_to_unknown(
        self, monkeypatch: pytest.MonkeyPatch, clean_username_env: Path
    ) -> None:
        assert vault_fs.get_vault_username() == "unknown"

    def test_separator_username_falls_back_to_user(
        self,
        monkeypatch: pytest.MonkeyPatch,
        clean_username_env: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("USER", "al/../../ice")
        assert vault_fs.get_vault_username() == "user"
        assert "invalid vault username" in capsys.readouterr().err

    def test_overlong_username_falls_back_to_user(
        self, monkeypatch: pytest.MonkeyPatch, clean_username_env: Path
    ) -> None:
        monkeypatch.setenv("USER", "a" * 65)
        assert vault_fs.get_vault_username() == "user"

    def test_dots_and_dashes_are_allowed(
        self, monkeypatch: pytest.MonkeyPatch, clean_username_env: Path
    ) -> None:
        monkeypatch.setenv("USER", "paul.r_dark-star")
        assert vault_fs.get_vault_username() == "paul.r_dark-star"


class TestDailyUsernameArgparse:
    def test_separator_username_rejected_by_argparse_type(self) -> None:
        with pytest.raises(Exception, match="invalid --daily-username"):
            doctor_cli._valid_daily_username("x/../../y")

    def test_valid_username_accepted(self) -> None:
        assert doctor_cli._valid_daily_username("probello") == "probello"

    def test_empty_default_accepted(self) -> None:
        assert doctor_cli._valid_daily_username("") == ""


class TestMigrateDailyNotesParentGuard:
    def test_malicious_username_cannot_move_note_out_of_month_dir(
        self, tmp_path: Path
    ) -> None:
        vault = tmp_path
        month = vault / "Daily" / "2026-08"
        month.mkdir(parents=True)
        note = month / "23.md"
        note.write_text("---\ntype: daily\n---\n\n# Day\n", encoding="utf-8")

        # Programmatic call with an unvalidated username: the same-parent
        # guard must skip the rename rather than move the note.
        run_migrate_daily_notes(vault, dry_run=False, username="x/../../evil")

        assert note.exists(), "the legacy note must not be renamed/moved"
        assert list(month.glob("*evil*")) == []
        escaped = list(vault.rglob("*evil*"))
        assert escaped == [], f"file escaped the month dir: {escaped}"
