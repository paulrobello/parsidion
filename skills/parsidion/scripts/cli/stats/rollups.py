"""Weekly / monthly rollup note generation (ARC-005).

Extracted from ``vault_stats.py``. The two modes share substantial shape
(sweep daily notes -> aggregate projects/categories/sessions -> render a
rollup note -> write or dry-run), so they live together in one module.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import vault_common

from cli.stats._common import _get_console


def run_weekly(
    conn: sqlite3.Connection | None, dry_run: bool = False, vault: Path | None = None
) -> None:
    """Generate or preview a weekly rollup note for the current ISO week.

    Args:
        conn: Open DB connection (unused currently, reserved for future use).
        dry_run: If True, print the note content without writing it.
        vault: Optional vault path. Defaults to resolve_vault().
    """
    from datetime import date, timedelta
    import re as _re

    console = _get_console()
    vault = vault or vault_common.resolve_vault()

    today = date.today()
    iso_year, iso_week, iso_weekday = today.isocalendar()
    monday = today - timedelta(days=iso_weekday - 1)
    sunday = monday + timedelta(days=6)

    month_dir = vault / "Daily" / f"{today.year:04d}-{today.month:02d}"

    _daily_stem_re = _re.compile(r"^(\d{2})(?:-.+)?$")
    daily_paths: list[Path] = []
    for delta in range(7):
        day = monday + timedelta(days=delta)
        day_month_dir = vault / "Daily" / f"{day.year:04d}-{day.month:02d}"
        day_prefix = f"{day.day:02d}"
        if day_month_dir.exists():
            for p in sorted(day_month_dir.glob(f"{day_prefix}*.md")):
                m = _daily_stem_re.match(p.stem)
                if m and m.group(1) == day_prefix:
                    daily_paths.append(p)

    if not daily_paths:
        console.print(
            f"[yellow]No daily notes found for week {iso_week} "
            f"({monday} – {sunday}).[/yellow]"
        )
        return

    projects_seen: set[str] = set()
    categories_seen: set[str] = set()
    session_lines: list[str] = []
    links_to_daily: list[str] = []

    for dp in sorted(daily_paths):
        try:
            text = dp.read_text(encoding="utf-8")
        except OSError:
            continue

        links_to_daily.append(f"[[{dp.stem}]]")

        in_sessions = False
        for line in text.splitlines():
            if line.startswith("## Sessions"):
                in_sessions = True
                continue
            if in_sessions and line.startswith("## "):
                in_sessions = False
            if in_sessions:
                session_lines.append(line)
            if "project:" in line.lower():
                parts = line.split(":", 1)
                if len(parts) == 2:
                    val = parts[1].strip()
                    if val and val not in {"", "null"}:
                        projects_seen.add(val)
            if "categor" in line.lower():
                import re

                found = re.findall(r"\b[a-zA-Z][\w-]+\b", line)
                categories_seen.update(found)

    week_label = f"Week {iso_week:02d} ({monday.strftime('%b %d')} – {sunday.strftime('%b %d, %Y')})"
    today_str = today.strftime("%Y-%m-%d")

    related_field = ", ".join(f'"{lnk}"' for lnk in links_to_daily)
    projects_list = (
        "\n".join(f"- {p}" for p in sorted(projects_seen)) or "- (none recorded)"
    )
    categories_list = ", ".join(sorted(categories_seen)[:20]) or "(none recorded)"
    daily_links_str = "\n".join(f"- {lnk}" for lnk in links_to_daily)
    sessions_excerpt = (
        "\n".join(session_lines[:40]).strip() or "(no sessions content found)"
    )

    content = f"""---
date: {today_str}
type: daily
tags: [weekly-rollup]
related: [{related_field}]
---

# {week_label}

## Projects Active This Week
{projects_list}

## Categories
{categories_list}

## Sessions Excerpt
{sessions_excerpt}

## Daily Notes
{daily_links_str}
"""

    output_path = month_dir / f"week-{iso_week:02d}.md"

    if dry_run:
        console.print(
            f"\n[bold cyan]Weekly Rollup (dry run)[/bold cyan] — would write to:\n"
            f"  [dim]{output_path}[/dim]\n"
        )
        console.print(content)
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    console.print(
        f"\n[green]Weekly rollup written:[/green] {output_path}\n"
        f"  Covered {len(daily_paths)} daily notes, "
        f"{len(projects_seen)} project(s), "
        f"{len(links_to_daily)} day link(s).\n"
    )


def run_monthly(
    conn: sqlite3.Connection | None, dry_run: bool = False, vault: Path | None = None
) -> None:
    """Generate or preview a monthly rollup note for the current month.

    Args:
        conn: Open DB connection (unused currently, reserved for future use).
        dry_run: If True, print the note content without writing it.
        vault: Optional vault path. Defaults to resolve_vault().
    """
    from datetime import date
    import calendar
    import re as _re

    console = _get_console()
    vault = vault or vault_common.resolve_vault()

    today = date.today()
    month_dir = vault / "Daily" / f"{today.year:04d}-{today.month:02d}"

    _daily_stem_re = _re.compile(r"^(\d{2})(?:-.+)?$")
    daily_paths: list[Path] = []
    if month_dir.exists():
        for dp in sorted(month_dir.glob("*.md")):
            if _daily_stem_re.match(dp.stem):
                daily_paths.append(dp)

    if not daily_paths:
        console.print(
            f"[yellow]No daily notes found for "
            f"{today.strftime('%B %Y')} in {month_dir}.[/yellow]"
        )
        return

    projects_seen: set[str] = set()
    categories_seen: set[str] = set()
    session_lines: list[str] = []
    links_to_daily: list[str] = []

    for dp in daily_paths:
        try:
            text = dp.read_text(encoding="utf-8")
        except OSError:
            continue

        links_to_daily.append(f"[[{dp.stem}]]")

        in_sessions = False
        for line in text.splitlines():
            if line.startswith("## Sessions"):
                in_sessions = True
                continue
            if in_sessions and line.startswith("## "):
                in_sessions = False
            if in_sessions:
                session_lines.append(line)
            if "project:" in line.lower():
                parts = line.split(":", 1)
                if len(parts) == 2:
                    val = parts[1].strip()
                    if val and val not in {"", "null"}:
                        projects_seen.add(val)
            if "categor" in line.lower():
                import re

                found = re.findall(r"\b[a-zA-Z][\w-]+\b", line)
                categories_seen.update(found)

    _, days_in_month = calendar.monthrange(today.year, today.month)
    month_label = today.strftime("%B %Y")
    today_str = today.strftime("%Y-%m-%d")

    related_field = ", ".join(f'"{lnk}"' for lnk in links_to_daily)
    projects_list = (
        "\n".join(f"- {p}" for p in sorted(projects_seen)) or "- (none recorded)"
    )
    categories_list = ", ".join(sorted(categories_seen)[:30]) or "(none recorded)"
    daily_links_str = "\n".join(f"- {lnk}" for lnk in links_to_daily)
    sessions_excerpt = (
        "\n".join(session_lines[:60]).strip() or "(no sessions content found)"
    )

    content = f"""---
date: {today_str}
type: daily
tags: [monthly-rollup]
related: [{related_field}]
---

# {month_label} — Monthly Rollup

## Projects Active This Month
{projects_list}

## Categories
{categories_list}

## Sessions Excerpt
{sessions_excerpt}

## Daily Notes ({len(daily_paths)} of {days_in_month} days covered)
{daily_links_str}
"""

    output_path = month_dir / "monthly.md"

    if dry_run:
        console.print(
            f"\n[bold cyan]Monthly Rollup (dry run)[/bold cyan] — would write to:\n"
            f"  [dim]{output_path}[/dim]\n"
        )
        console.print(content)
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    console.print(
        f"\n[green]Monthly rollup written:[/green] {output_path}\n"
        f"  Covered {len(daily_paths)} daily notes, "
        f"{len(projects_seen)} project(s).\n"
    )
