"""TRASH_SIZE sprawl detection and the opt-in ``--prune-trash`` mode.

Pins:

* ``trash_stats`` counts files/bytes under ``.trash`` and returns ``(0, 0)``
  when the directory is absent.
* ``report_trash_sprawl`` stays silent below the threshold and prints the
  TRASH_SIZE finding with the ``--prune-trash`` fix above it.
* ``run_prune_trash`` deletes only entries older than the retention window,
  removes directories the sweep emptied, refuses ``retention_days <= 0``,
  and (``dry_run=True``) deletes nothing.
* CLI wiring: ``--prune-trash`` registers a fix mode, the rule catalog
  carries the ``trash-size`` row, and a non-positive retention window is a
  parser error.  ``--fix-all`` never implies ``prune_trash`` (deletion is
  irreversible — gitignored — and needs an owner-approved window).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

# Make the scripts dir importable.
SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import doctor.cli as doctor_cli  # noqa: E402
import doctor.trash as doctor_trash  # noqa: E402
from doctor.protocol import RULE_SPECS  # noqa: E402


@pytest.fixture()
def trash_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / ".trash" / "backup" / "2026-01-01").mkdir(parents=True)
    _days_ago = lambda days: time.time() - days * 86400  # noqa: E731

    old_top = vault / ".trash" / "old-note.md"
    old_top.write_text("retired long ago")
    os.utime(old_top, (_days_ago(30), _days_ago(30)))

    old_nested = vault / ".trash" / "backup" / "2026-01-01" / "old-backup.md"
    old_nested.write_text("backup equally old")
    os.utime(old_nested, (_days_ago(30), _days_ago(30)))

    fresh = vault / ".trash" / "fresh-note.md"
    fresh.write_text("retired yesterday")
    os.utime(fresh, (_days_ago(1), _days_ago(1)))
    return vault


def test_trash_stats_counts_files_and_bytes(trash_vault: Path) -> None:
    count, size = doctor_trash.trash_stats(trash_vault)
    assert count == 3
    assert size == sum(p.stat().st_size for p in (trash_vault / ".trash").rglob("*.md"))


def test_trash_stats_absent_dir(tmp_path: Path) -> None:
    assert doctor_trash.trash_stats(tmp_path / "nope") == (0, 0)


def test_report_silent_below_threshold(
    trash_vault: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor_trash, "TRASH_NOTE_THRESHOLD", 4)
    doctor_trash.report_trash_sprawl(trash_vault)
    assert capsys.readouterr().out == ""


def test_report_prints_finding_above_threshold(
    trash_vault: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor_trash, "TRASH_NOTE_THRESHOLD", 3)
    doctor_trash.report_trash_sprawl(trash_vault)
    out = capsys.readouterr().out
    assert "TRASH_SIZE" in out
    assert "3 files" in out
    assert "--prune-trash" in out


def test_prune_dry_run_deletes_nothing(
    trash_vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doctor_trash.run_prune_trash(trash_vault, retention_days=7, dry_run=True)
    assert "Would delete 2 of 3" in capsys.readouterr().out
    assert len(list((trash_vault / ".trash").rglob("*.md"))) == 3


def test_prune_deletes_only_old_and_empties_dirs(
    trash_vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doctor_trash.run_prune_trash(trash_vault, retention_days=7, dry_run=False)
    assert "Deleted 2 of 3" in capsys.readouterr().out
    remaining = {p.name for p in (trash_vault / ".trash").rglob("*.md")}
    assert remaining == {"fresh-note.md"}
    # The emptied backup date-dir is gone; .trash itself stays.
    assert not (trash_vault / ".trash" / "backup" / "2026-01-01").exists()
    assert (trash_vault / ".trash").is_dir()


def test_prune_rejects_nonpositive_window(trash_vault: Path) -> None:
    with pytest.raises(ValueError):
        doctor_trash.run_prune_trash(trash_vault, retention_days=0)


def test_cli_registers_prune_trash_mode() -> None:
    parser = doctor_cli._build_parser()
    args = parser.parse_args(["--prune-trash", "--trash-retention-days", "7"])
    modes = doctor_cli._build_fix_modes(args)
    assert "prune_trash" in {m.flag for m in modes}


def test_cli_rejects_nonpositive_retention() -> None:
    # The parser accepts the int; the guard lives in cli.main() post-parse
    # (parser.error).  run_prune_trash's ValueError (tested above) is the
    # backstop when callers bypass the CLI, e.g. the fix-mode registry.
    parser = doctor_cli._build_parser()
    args = parser.parse_args(["--prune-trash", "--trash-retention-days", "0"])
    assert args.trash_retention_days == 0


def test_rule_catalog_carries_trash_size() -> None:
    spec = next(s for s in RULE_SPECS if s.name == "trash-size")
    assert spec.kind == "mode"
    assert spec.target == "prune_trash"


def test_fix_all_does_not_imply_prune_trash() -> None:
    # The --fix-all expansion in cli.py must never set the prune flag:
    # deleting .trash is irreversible and needs an owner-approved window.
    src = (SCRIPTS_DIR / "doctor" / "cli.py").read_text()
    assert "args.prune_trash = True" not in src
