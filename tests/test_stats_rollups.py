"""QA-010: weekly/monthly rollup generation against a fixture vault.

Also closes the QA-015 gap for ``cli/stats/rollups.py`` — it previously
had no test file referencing it.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from cli.stats.rollups import run_monthly, run_weekly


def _make_daily(vault: Path, day: date, username: str = "tester") -> Path:
    """Create a daily note shaped like the hook's output for *day*."""
    month_dir = vault / "Daily" / f"{day.year:04d}-{day.month:02d}"
    month_dir.mkdir(parents=True, exist_ok=True)
    p = month_dir / f"{day.day:02d}-{username}.md"
    p.write_text(
        "---\n"
        "type: daily\n"
        f"date: {day.isoformat()}\n"
        "tags: []\n"
        "related: []\n"
        "---\n"
        "# Daily\n\n"
        "## Sessions\n"
        f"- worked on parsight (project: parsight)\n"
        "- fixed a bug; categories: error_fix, python\n"
        "## Notes\n"
        "free text\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def week_vault(tmp_path: Path) -> Path:
    """Vault with two daily notes in the current ISO week."""
    vault = tmp_path / "week_vault"
    vault.mkdir()
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    _make_daily(vault, today)
    _make_daily(
        vault,
        monday if monday != today else monday + timedelta(days=1),
        username="other",
    )
    return vault


@pytest.fixture()
def month_vault(tmp_path: Path) -> Path:
    """Vault with two daily notes guaranteed inside the current month."""
    vault = tmp_path / "month_vault"
    vault.mkdir()
    today = date.today()
    day1 = date(today.year, today.month, 1)
    day2 = date(today.year, today.month, 2)
    _make_daily(vault, day1)
    _make_daily(vault, day2, username="other")
    return vault


def test_run_weekly_writes_rollup_with_aggregates(week_vault: Path) -> None:
    run_weekly(vault=week_vault)

    today = date.today()
    iso_week = today.isocalendar().week
    month_dir = week_vault / "Daily" / f"{today.year:04d}-{today.month:02d}"
    out = month_dir / f"week-{iso_week:02d}.md"
    assert out.exists(), "weekly rollup note was not written"
    text = out.read_text(encoding="utf-8")
    assert "tags: [weekly-rollup]" in text
    assert "- parsight" in text
    assert "error_fix" in text
    # Both daily notes are wikilinked.
    assert "[[01-tester]]" in text or "[[" in text
    assert text.count("[[") >= 2


def test_run_weekly_dry_run_writes_nothing(week_vault: Path, capsys) -> None:
    run_weekly(dry_run=True, vault=week_vault)
    out_text = capsys.readouterr().out
    assert "dry run" in out_text.lower()

    today = date.today()
    month_dir = week_vault / "Daily" / f"{today.year:04d}-{today.month:02d}"
    assert not (month_dir / f"week-{today.isocalendar().week:02d}.md").exists()


def test_run_monthly_writes_rollup_with_days_covered(month_vault: Path) -> None:
    run_monthly(vault=month_vault)

    today = date.today()
    month_dir = month_vault / "Daily" / f"{today.year:04d}-{today.month:02d}"
    out = month_dir / "monthly.md"
    assert out.exists(), "monthly rollup note was not written"
    text = out.read_text(encoding="utf-8")
    assert "tags: [monthly-rollup]" in text
    assert "Monthly Rollup" in text
    assert "2 of " in text and "days covered" in text
    assert "- parsight" in text


def test_run_monthly_empty_vault_prints_notice(tmp_path: Path, capsys) -> None:
    vault = tmp_path / "empty-vault"
    vault.mkdir()
    run_monthly(vault=vault)
    out_text = capsys.readouterr().out
    assert "No daily notes found" in out_text
    assert not (vault / "Daily").exists()


def test_daily_note_read_errors_are_skipped(week_vault: Path) -> None:
    # Corrupt one daily note into an unreadable file (permission-denied
    # on POSIX) — the rollup must still cover the other note.
    today = date.today()
    bad = (
        week_vault
        / "Daily"
        / f"{today.year:04d}-{today.month:02d}"
        / f"{today.day:02d}-tester.md"
    )
    bad.chmod(0o000)
    try:
        run_weekly(vault=week_vault)
    finally:
        bad.chmod(0o644)

    iso_week = today.isocalendar().week
    month_dir = week_vault / "Daily" / f"{today.year:04d}-{today.month:02d}"
    out = month_dir / f"week-{iso_week:02d}.md"
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    # The readable note's project still appears.
    assert "- parsight" in text
