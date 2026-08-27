"""Weekly / monthly rollup note generation (ARC-005 / QA-010).

Extracted from ``vault_stats.py``. The two modes share the entire shape —
sweep daily notes → aggregate projects/categories/sessions → render a
rollup note → write or dry-run — so QA-010 collapsed the duplicated
~120-line aggregation/render bodies into ``_collect_daily_rollup`` +
``_render_rollup`` + ``_write_rollup``; the two ``run_*`` entrypoints now
own only their date-window sweep and labels.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from core.vault_path import resolve_vault

from cli.stats._common import _get_console

# Daily-note stems look like "23" or "23-probello" (DD or DD-{username}).
_DAILY_STEM_RE = re.compile(r"^(\d{2})(?:-.+)?$")
_WORD_RE = re.compile(r"\b[a-zA-Z][\w-]+\b")


@dataclass
class RollupData:
    """Aggregated facts from one rollup window's daily notes."""

    projects: set[str] = field(default_factory=set)
    categories: set[str] = field(default_factory=set)
    session_lines: list[str] = field(default_factory=list)
    daily_links: list[str] = field(default_factory=list)


def _collect_daily_rollup(daily_paths: list[Path]) -> RollupData:
    """Aggregate projects, categories, session lines, and daily links.

    Shared by the weekly and monthly rollups (QA-010). Reads each daily
    note (unreadable notes are skipped silently), captures the ``##
    Sessions`` section verbatim, and scans every line for ``project:`` and
    ``categor`` markers.
    """
    data = RollupData()
    for dp in daily_paths:
        try:
            text = dp.read_text(encoding="utf-8")
        except OSError:
            continue

        data.daily_links.append(f"[[{dp.stem}]]")

        in_sessions = False
        for line in text.splitlines():
            if line.startswith("## Sessions"):
                in_sessions = True
                continue
            if in_sessions and line.startswith("## "):
                in_sessions = False
            if in_sessions:
                data.session_lines.append(line)
            if "project:" in line.lower():
                parts = line.split(":", 1)
                if len(parts) == 2:
                    val = parts[1].strip()
                    if val and val not in {"", "null"}:
                        data.projects.add(val)
            if "categor" in line.lower():
                data.categories.update(_WORD_RE.findall(line))
    return data


def _render_rollup(
    *,
    data: RollupData,
    tag: str,
    heading: str,
    projects_heading: str,
    daily_notes_heading: str,
    today_str: str,
    categories_cap: int,
    sessions_cap: int,
) -> str:
    """Render the rollup note body shared by both modes (QA-010)."""
    related_field = ", ".join(f'"{lnk}"' for lnk in data.daily_links)
    projects_list = (
        "\n".join(f"- {p}" for p in sorted(data.projects)) or "- (none recorded)"
    )
    categories_list = ", ".join(sorted(data.categories)[:categories_cap]) or (
        "(none recorded)"
    )
    daily_links_str = "\n".join(f"- {lnk}" for lnk in data.daily_links)
    sessions_excerpt = (
        "\n".join(data.session_lines[:sessions_cap]).strip()
        or "(no sessions content found)"
    )

    return f"""---
date: {today_str}
type: daily
tags: [{tag}]
related: [{related_field}]
---

# {heading}

## {projects_heading}
{projects_list}

## Categories
{categories_list}

## Sessions Excerpt
{sessions_excerpt}

## {daily_notes_heading}
{daily_links_str}
"""


def _write_rollup(
    output_path: Path,
    content: str,
    *,
    dry_run: bool,
    title: str,
    summary: str,
) -> None:
    """Write the rollup note, or preview it under dry-run (QA-010)."""
    console = _get_console()
    if dry_run:
        console.print(
            f"\n[bold cyan]{title} (dry run)[/bold cyan] — would write to:\n"
            f"  [dim]{output_path}[/dim]\n"
        )
        console.print(content)
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    console.print(f"\n[green]{title} written:[/green] {output_path}\n  {summary}\n")


def run_weekly(dry_run: bool = False, vault: Path | None = None) -> None:
    """Generate or preview a weekly rollup note for the current ISO week.

    Args:
        dry_run: If True, print the note content without writing it.
        vault: Optional vault path. Defaults to resolve_vault().
    """
    console = _get_console()
    vault = vault or resolve_vault()

    today = date.today()
    iso_year, iso_week, iso_weekday = today.isocalendar()
    monday = today - timedelta(days=iso_weekday - 1)
    sunday = monday + timedelta(days=6)

    month_dir = vault / "Daily" / f"{today.year:04d}-{today.month:02d}"

    daily_paths: list[Path] = []
    for delta in range(7):
        day = monday + timedelta(days=delta)
        day_month_dir = vault / "Daily" / f"{day.year:04d}-{day.month:02d}"
        day_prefix = f"{day.day:02d}"
        if day_month_dir.exists():
            for p in sorted(day_month_dir.glob(f"{day_prefix}*.md")):
                m = _DAILY_STEM_RE.match(p.stem)
                if m and m.group(1) == day_prefix:
                    daily_paths.append(p)

    if not daily_paths:
        console.print(
            f"[yellow]No daily notes found for week {iso_week} "
            f"({monday} – {sunday}).[/yellow]"
        )
        return

    data = _collect_daily_rollup(sorted(daily_paths))
    week_label = f"Week {iso_week:02d} ({monday.strftime('%b %d')} – {sunday.strftime('%b %d, %Y')})"
    content = _render_rollup(
        data=data,
        tag="weekly-rollup",
        heading=week_label,
        projects_heading="Projects Active This Week",
        daily_notes_heading="Daily Notes",
        today_str=today.strftime("%Y-%m-%d"),
        categories_cap=20,
        sessions_cap=40,
    )

    _write_rollup(
        month_dir / f"week-{iso_week:02d}.md",
        content,
        dry_run=dry_run,
        title="Weekly Rollup",
        summary=(
            f"Covered {len(daily_paths)} daily notes, "
            f"{len(data.projects)} project(s), "
            f"{len(data.daily_links)} day link(s)."
        ),
    )


def run_monthly(dry_run: bool = False, vault: Path | None = None) -> None:
    """Generate or preview a monthly rollup note for the current month.

    Args:
        dry_run: If True, print the note content without writing it.
        vault: Optional vault path. Defaults to resolve_vault().
    """
    console = _get_console()
    vault = vault or resolve_vault()

    today = date.today()
    month_dir = vault / "Daily" / f"{today.year:04d}-{today.month:02d}"

    daily_paths: list[Path] = []
    if month_dir.exists():
        for dp in sorted(month_dir.glob("*.md")):
            if _DAILY_STEM_RE.match(dp.stem):
                daily_paths.append(dp)

    if not daily_paths:
        console.print(
            f"[yellow]No daily notes found for "
            f"{today.strftime('%B %Y')} in {month_dir}.[/yellow]"
        )
        return

    data = _collect_daily_rollup(daily_paths)
    _, days_in_month = calendar.monthrange(today.year, today.month)
    month_label = today.strftime("%B %Y")
    content = _render_rollup(
        data=data,
        tag="monthly-rollup",
        heading=f"{month_label} — Monthly Rollup",
        projects_heading="Projects Active This Month",
        daily_notes_heading=f"Daily Notes ({len(daily_paths)} of {days_in_month} days covered)",
        today_str=today.strftime("%Y-%m-%d"),
        categories_cap=30,
        sessions_cap=60,
    )

    _write_rollup(
        month_dir / "monthly.md",
        content,
        dry_run=dry_run,
        title="Monthly Rollup",
        summary=(
            f"Covered {len(daily_paths)} daily notes, {len(data.projects)} project(s)."
        ),
    )
