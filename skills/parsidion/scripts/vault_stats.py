#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "rich>=13.0",
# ]
# ///
"""vault-stats — analytics over the Parsidion vault note_index database.

Modes (mutually exclusive; default is --summary):
    --summary              Count notes by folder and type
    --stale                List stale notes (is_stale = 1)
    --top-linked N         Top N most-linked notes (default: 10)
    --by-project           Count notes per project
    --growth N             Notes created per week for the last N weeks (default: 8)
    --tags                 Show tag cloud (top 30 most-used tags)
    --dashboard            Full-page analytics dashboard (combines all views)
    --pending              Show pending_summaries.jsonl queue stats
    --graph                Knowledge graph analytics (hubs, isolated, ratios)
    --hooks N              Show last N hook events from hook_events.log (default: 20)
    --weekly               Generate/preview weekly rollup note for current ISO week
    --monthly              Generate/preview monthly rollup note for current month
    --timeline N           Bar chart of notes created per day for last N days (default: 30)
    --summarizer-progress  Show current summarizer progress from ~/.claude/logs

All modes read from the resolved vault's embeddings.db (note_index table).
Falls back to a plain-text walk when the DB is absent.
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

import vault_common
import vault_metrics


# ---------------------------------------------------------------------------
# Lazy rich accessor — keeps module importable without rich installed
# ---------------------------------------------------------------------------


def _get_console():  # type: ignore[return]
    """Return the shared Rich Console instance, importing rich lazily."""
    from rich.console import Console  # noqa: PLC0415

    if not hasattr(_get_console, "_instance"):
        _get_console._instance = Console()  # type: ignore[attr-defined]
    return _get_console._instance  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# DB helpers (thin wrappers; real implementation in vault_metrics)
# ---------------------------------------------------------------------------


def _open_db(vault: Path | None = None) -> sqlite3.Connection | None:
    """Open the embeddings.db in read-only mode.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        An open connection, or None if the DB is absent or unreadable.
    """
    return vault_metrics.open_db(vault)


def _fetch_all(
    conn: sqlite3.Connection, sql: str, params: tuple = ()
) -> list[sqlite3.Row]:
    """Execute *sql* and return all rows.

    Args:
        conn: Open DB connection.
        sql: SQL query string.
        params: Query parameters.

    Returns:
        List of Row objects.
    """
    return vault_metrics.fetch_all(conn, sql, params)


def _collect_tags(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Collect all tags from note_index; delegate to vault_metrics.

    The ``tags`` column stores either a comma-separated string or a JSON
    array; both formats are handled.  Kept as a thin wrapper so that
    existing callers (including test_vault_stats.py) continue to work
    without change.

    Args:
        conn: Open DB connection.

    Returns:
        List of (tag, count) tuples sorted by count descending.
    """
    return vault_metrics.collect_tags(conn)


# ---------------------------------------------------------------------------
# Display functions (rich-dependent; rich is imported lazily inside each fn)
# ---------------------------------------------------------------------------


def run_summary(conn: sqlite3.Connection) -> None:
    """Print note counts by folder and by type.

    Args:
        conn: Open DB connection.
    """
    from rich.table import Table  # noqa: PLC0415
    from rich import box  # noqa: PLC0415

    data = vault_metrics.collect_summary(conn)
    console = _get_console()

    console.print(
        f"\n[bold cyan]Vault Summary[/bold cyan] — {data['total']} notes total\n"
    )

    t = Table(title="Notes by Folder", box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column("Folder", style="cyan")
    t.add_column("Count", justify="right", style="white")
    t.add_column("Bar", style="green")
    folder_rows = data["by_folder"]
    max_n = folder_rows[0]["n"] if folder_rows else 1
    for row in folder_rows:
        bar = "▄" * max(1, int(row["n"] / max_n * 20))
        t.add_row(row["folder"] or "(root)", str(row["n"]), bar)
    console.print(t)

    t2 = Table(title="Notes by Type", box=box.SIMPLE_HEAD, show_lines=False)
    t2.add_column("Type", style="magenta")
    t2.add_column("Count", justify="right", style="white")
    for row in data["by_type"]:
        t2.add_row(row["note_type"] or "(unset)", str(row["n"]))
    console.print(t2)


def run_stale(conn: sqlite3.Connection) -> None:
    """Print notes flagged as stale.

    Args:
        conn: Open DB connection.
    """
    from rich.table import Table  # noqa: PLC0415
    from rich import box  # noqa: PLC0415

    rows = vault_metrics.collect_stale(conn)
    console = _get_console()

    if not rows:
        console.print("[green]No stale notes found.[/green]")
        return

    console.print(f"\n[bold yellow]Stale Notes[/bold yellow] — {len(rows)} found\n")
    t = Table(box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column("Note", style="cyan")
    t.add_column("Folder", style="dim")
    t.add_column("Last Modified", style="white")
    for row in rows:
        t.add_row(
            f"[[{row['stem']}]]",
            row["folder"] or "(root)",
            row["age"],
        )
    console.print(t)


def run_top_linked(conn: sqlite3.Connection, top_n: int = 10) -> None:
    """Print the top N most-linked-to notes.

    Args:
        conn: Open DB connection.
        top_n: Number of notes to display.
    """
    from rich.table import Table  # noqa: PLC0415
    from rich import box  # noqa: PLC0415

    rows = vault_metrics.collect_top_linked(conn, top_n)
    console = _get_console()

    if not rows:
        console.print("[dim]No notes with incoming links found.[/dim]")
        return

    console.print(f"\n[bold cyan]Top {top_n} Most-Linked Notes[/bold cyan]\n")
    t = Table(box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column("Note", style="cyan")
    t.add_column("Title", style="white")
    t.add_column("Folder", style="dim")
    t.add_column("Incoming Links", justify="right", style="green")
    for row in rows:
        t.add_row(
            f"[[{row['stem']}]]",
            (row["title"] or row["stem"])[:50],
            row["folder"] or "(root)",
            str(row["incoming_links"]),
        )
    console.print(t)


def run_by_project(conn: sqlite3.Connection) -> None:
    """Print note counts per project.

    Args:
        conn: Open DB connection.
    """
    from rich.table import Table  # noqa: PLC0415
    from rich import box  # noqa: PLC0415

    data = vault_metrics.collect_by_project(conn)
    console = _get_console()

    if not data["by_project"]:
        console.print("[dim]No project-tagged notes found.[/dim]")
        return

    console.print("\n[bold cyan]Notes by Project[/bold cyan]\n")
    t = Table(box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column("Project", style="cyan")
    t.add_column("Count", justify="right", style="white")
    for row in data["by_project"]:
        t.add_row(row["project"], str(row["n"]))
    if data["untagged_n"]:
        t.add_row("[dim](no project)[/dim]", f"[dim]{data['untagged_n']}[/dim]")
    console.print(t)


def run_growth(conn: sqlite3.Connection, weeks: int = 8) -> None:
    """Print notes created per week for the last N weeks.

    Args:
        conn: Open DB connection.
        weeks: Number of weeks to display.
    """
    from rich.table import Table  # noqa: PLC0415
    from rich import box  # noqa: PLC0415

    rows = vault_metrics.collect_growth(conn, weeks)
    console = _get_console()

    console.print(f"\n[bold cyan]Note Growth — last {weeks} weeks[/bold cyan]\n")
    t = Table(box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column("Week", style="dim")
    t.add_column("Count", justify="right", style="white")
    t.add_column("Bar", style="green")
    max_count = max((r["n"] for r in rows), default=1)
    max_count = max(max_count, 1)
    for row in rows:
        n = row["n"]
        bar = "▄" * max(0, int(n / max_count * 20)) if n else ""
        t.add_row(row["label"], str(n), bar)
    console.print(t)


def run_tags(conn: sqlite3.Connection, top_n: int = 30) -> None:
    """Print a tag cloud showing the most-used tags.

    Args:
        conn: Open DB connection.
        top_n: Maximum number of tags to display.
    """
    from rich.table import Table  # noqa: PLC0415
    from rich import box  # noqa: PLC0415

    tags = vault_metrics.collect_tags(conn)[:top_n]
    console = _get_console()

    if not tags:
        console.print("[dim]No tags found.[/dim]")
        return

    console.print(
        f"\n[bold cyan]Tag Cloud[/bold cyan] — top {min(top_n, len(tags))} tags\n"
    )
    t = Table(box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column("Tag", style="cyan")
    t.add_column("Count", justify="right", style="white")
    t.add_column("Bar", style="blue")
    max_count = tags[0][1] if tags else 1
    for tag, count in tags:
        bar = "▄" * max(1, int(count / max_count * 20))
        t.add_row(tag, str(count), bar)
    console.print(t)


def run_dashboard(conn: sqlite3.Connection) -> None:
    """Print a full-page analytics dashboard combining all views.

    Shows: vault overview, folder distribution, note growth (8 weeks),
    top 10 most-linked notes, top 10 stale notes, and tag cloud.

    Args:
        conn: Open DB connection.
    """
    from rich.columns import Columns  # noqa: PLC0415
    from rich.panel import Panel  # noqa: PLC0415
    from rich.table import Table  # noqa: PLC0415
    from rich.text import Text  # noqa: PLC0415
    from rich import box  # noqa: PLC0415

    data = vault_metrics.collect_dashboard(conn)
    console = _get_console()

    console.print()
    console.rule("[bold cyan]Parsidion vault Dashboard[/bold cyan]")
    console.print(
        f"\n  [bold white]{data['total']}[/bold white] notes  ·  "
        f"[yellow]{data['stale_count']}[/yellow] stale  ·  "
        f"[green]{data['linked_count']}[/green] linked  ·  "
        f"[dim]{data['timestamp']}[/dim]\n"
    )

    # --- folder distribution ---
    folder_table = Table(title="Notes by Folder", box=box.SIMPLE_HEAD, show_lines=False)
    folder_table.add_column("Folder", style="cyan")
    folder_table.add_column("Count", justify="right", style="white")
    folder_table.add_column("Bar", style="green")
    folder_rows = data["by_folder"]
    max_n = folder_rows[0]["n"] if folder_rows else 1
    for row in folder_rows:
        bar = "▄" * max(1, int(row["n"] / max_n * 16))
        folder_table.add_row(row["folder"] or "(root)", str(row["n"]), bar)

    # --- weekly growth ---
    growth_table = Table(
        title="Note Growth (8w)", box=box.SIMPLE_HEAD, show_lines=False
    )
    growth_table.add_column("Week", style="dim")
    growth_table.add_column("n", justify="right", style="white")
    growth_table.add_column("Bar", style="green")
    growth = data["growth"]
    max_g = max((r["n"] for r in growth), default=1)
    max_g = max(max_g, 1)
    for row in growth:
        n = row["n"]
        bar = "▄" * max(0, int(n / max_g * 16)) if n else ""
        growth_table.add_row(row["label"], str(n), bar)

    console.print(Columns([folder_table, growth_table], equal=False, expand=False))

    # --- top linked ---
    console.print()
    linked_table = Table(
        title="Top 10 Most-Linked Notes", box=box.SIMPLE_HEAD, show_lines=False
    )
    linked_table.add_column("Note", style="cyan")
    linked_table.add_column("Title", style="white")
    linked_table.add_column("Links", justify="right", style="green")
    top_linked = data["top_linked"]
    if top_linked:
        for row in top_linked:
            linked_table.add_row(
                f"[[{row['stem']}]]",
                (row["title"] or row["stem"])[:40],
                str(row["incoming_links"]),
            )
    else:
        linked_table.add_row("[dim]—[/dim]", "[dim]no linked notes[/dim]", "")

    # --- stale notes ---
    stale_table = Table(
        title="Top 10 Stale Notes", box=box.SIMPLE_HEAD, show_lines=False
    )
    stale_table.add_column("Note", style="yellow")
    stale_table.add_column("Folder", style="dim")
    stale_table.add_column("Modified", style="white")
    stale = data["stale"]
    if stale:
        for row in stale:
            stale_table.add_row(
                f"[[{row['stem']}]]",
                row["folder"] or "(root)",
                row["age"],
            )
    else:
        stale_table.add_row("[dim]—[/dim]", "[dim]no stale notes[/dim]", "")

    console.print(Columns([linked_table, stale_table], equal=False, expand=False))

    # --- tag cloud ---
    console.print()
    tags_data = data["tags"]
    if tags_data:
        tag_text = Text()
        max_count = tags_data[0][1]
        for i, (tag, count) in enumerate(tags_data):
            ratio = count / max_count
            if ratio >= 0.7:
                style = "bold cyan"
            elif ratio >= 0.4:
                style = "cyan"
            elif ratio >= 0.2:
                style = "blue"
            else:
                style = "dim"
            if i > 0:
                tag_text.append("  ")
            tag_text.append(f"{tag}({count})", style=style)
        console.print(Panel(tag_text, title="Tag Cloud (top 20)", border_style="dim"))
    else:
        console.print("[dim]No tags found.[/dim]")

    console.print()


def run_pending(vault: Path | None = None) -> None:
    """Print a summary of pending_summaries.jsonl queue."""
    from rich.table import Table  # noqa: PLC0415
    from rich import box  # noqa: PLC0415

    data = vault_metrics.collect_pending(vault)
    console = _get_console()

    if not data["exists"]:
        console.print("[dim]No pending_summaries.jsonl found — queue is empty.[/dim]")
        return

    if data.get("error"):
        console.print("[red]Cannot read pending_summaries.jsonl[/red]")
        return

    total = data["total"]
    if total == 0:
        console.print("[green]Queue is empty (0 entries).[/green]")
        return

    token_estimate = data["token_estimate"]
    console.print(
        f"\n[bold cyan]Pending Summaries Queue[/bold cyan] — {total} entries "
        f"(~{token_estimate:,} tokens estimated)\n"
    )

    src_table = Table(title="By Source", box=box.SIMPLE_HEAD, show_lines=False)
    src_table.add_column("Source", style="cyan")
    src_table.add_column("Count", justify="right", style="white")
    for src, count in sorted(data["source_counts"].items(), key=lambda x: -x[1]):
        src_table.add_row(src, str(count))
    console.print(src_table)

    if data["project_counts"]:
        console.print()
        proj_table = Table(title="By Project", box=box.SIMPLE_HEAD, show_lines=False)
        proj_table.add_column("Project", style="cyan")
        proj_table.add_column("Count", justify="right", style="white")
        for proj, count in sorted(data["project_counts"].items(), key=lambda x: -x[1]):
            proj_table.add_row(proj, str(count))
        console.print(proj_table)

    if data["oldest_ts"]:
        console.print(f"\n  [dim]Oldest entry:[/dim] {data['oldest_ts']}")
    console.print()


def run_graph(conn: sqlite3.Connection) -> None:
    """Print knowledge graph analytics from the note_index.

    Args:
        conn: Open DB connection.
    """
    from rich.table import Table  # noqa: PLC0415
    from rich import box  # noqa: PLC0415

    data = vault_metrics.collect_graph(conn)
    console = _get_console()

    if data["total"] == 0:
        console.print("[dim]No notes in index.[/dim]")
        return

    console.print("\n[bold cyan]Knowledge Graph Analytics[/bold cyan]\n")
    console.print(
        f"  Total notes: [white]{data['total']}[/white]  ·  "
        f"Avg incoming links: [white]{data['avg_links']:.2f}[/white]  ·  "
        f"Linked: [green]{data['linked_count']}[/green]  ·  "
        f"Unlinked: [yellow]{data['unlinked_count']}[/yellow]\n"
    )

    hub_rows = data["hub_notes"]
    if hub_rows:
        hub_table = Table(
            title="Hub Notes (≥5 incoming links, top 10)",
            box=box.SIMPLE_HEAD,
            show_lines=False,
        )
        hub_table.add_column("Note", style="cyan")
        hub_table.add_column("Title", style="white")
        hub_table.add_column("Folder", style="dim")
        hub_table.add_column("Incoming", justify="right", style="green")
        for row in hub_rows:
            hub_table.add_row(
                f"[[{row['stem']}]]",
                (row["title"] or row["stem"])[:45],
                row["folder"] or "(root)",
                str(row["incoming_links"]),
            )
        console.print(hub_table)
    else:
        console.print("[dim]No hub notes (none with ≥5 incoming links).[/dim]")

    console.print()

    isolated_rows = data["isolated_notes"]
    if isolated_rows:
        iso_table = Table(
            title=f"Isolated Notes ({len(isolated_rows)} total — no incoming links, no related)",
            box=box.SIMPLE_HEAD,
            show_lines=False,
        )
        iso_table.add_column("Note", style="yellow")
        iso_table.add_column("Folder", style="dim")
        for row in isolated_rows[:20]:
            iso_table.add_row(f"[[{row['stem']}]]", row["folder"] or "(root)")
        if len(isolated_rows) > 20:
            iso_table.add_row(f"[dim]… and {len(isolated_rows) - 20} more[/dim]", "")
        console.print(iso_table)
    else:
        console.print("[green]No isolated notes found.[/green]")

    console.print()


def run_hooks(last_n: int = 20, vault: Path | None = None) -> None:
    """Print the last N events from hook_events.log.

    Args:
        last_n: Number of most-recent events to show.
        vault: Optional vault path. Defaults to resolve_vault().
    """
    from rich.table import Table  # noqa: PLC0415
    from rich import box  # noqa: PLC0415

    data = vault_metrics.collect_hooks(last_n, vault)
    console = _get_console()

    if not data["exists"]:
        console.print("[dim]No hook_events.log found.[/dim]")
        return

    if data.get("error"):
        console.print("[red]Cannot read hook_events.log[/red]")
        return

    events = data["events"]
    if not events:
        console.print("[dim]hook_events.log is empty.[/dim]")
        return

    console.print(
        f"\n[bold cyan]Hook Events[/bold cyan] — last {len(events)} of {data['total']} total\n"
    )
    t = Table(box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column("Timestamp", style="dim")
    t.add_column("Hook", style="cyan")
    t.add_column("Project", style="white")
    t.add_column("ms", justify="right", style="green")
    t.add_column("Extra", style="dim")

    _KNOWN_FIELDS = {"hook", "ts", "project", "duration_ms"}
    for event in events:
        ts = event.get("ts", "")
        hook = event.get("hook", "")
        project = event.get("project", "") or ""
        duration_ms = event.get("duration_ms")
        dur_str = str(duration_ms) if duration_ms is not None else ""
        extra_items = {k: v for k, v in event.items() if k not in _KNOWN_FIELDS}
        extra_str = "  ".join(f"{k}={v}" for k, v in list(extra_items.items())[:3])
        t.add_row(ts, hook, project[:30], dur_str, extra_str[:60])

    console.print(t)


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


def run_timeline(
    conn: sqlite3.Connection | None, days: int = 30, vault: Path | None = None
) -> None:
    """Print a bar chart of notes created per day for the last N days.

    Args:
        conn: Open DB connection, or None for file-walk fallback.
        days: Number of days to display (default: 30).
        vault: Optional vault path. Defaults to resolve_vault().
    """
    from rich.table import Table  # noqa: PLC0415
    from rich import box  # noqa: PLC0415

    rows = vault_metrics.collect_timeline(conn, days, vault)
    console = _get_console()

    console.print(f"\n[bold cyan]Note Timeline[/bold cyan] — last {days} days\n")
    t = Table(box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column("Date", style="dim")
    t.add_column("Count", justify="right", style="white")
    t.add_column("Bar", style="green")

    max_count = max((r["n"] for r in rows), default=1)
    max_count = max(max_count, 1)

    for row in rows:
        n = row["n"]
        label = row["date"]
        if row["is_today"]:
            label += " [dim](today)[/dim]"
        bar = "▄" * max(0, int(n / max_count * 24)) if n else ""
        t.add_row(label, str(n) if n else "[dim]0[/dim]", bar)

    console.print(t)


def run_summarizer_progress() -> None:
    """Print current summarizer progress."""
    from rich.table import Table  # noqa: PLC0415
    from rich import box  # noqa: PLC0415

    data = vault_metrics.collect_summarizer_progress()
    console = _get_console()

    if not data["exists"]:
        console.print("[dim]No summarizer currently running.[/dim]")
        return

    if data.get("error"):
        console.print(f"[red]Cannot read progress file: {data['error']}[/red]")
        return

    console.print("\n[bold cyan]Summarizer Progress[/bold cyan]\n")
    t = Table(box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column("Field", style="cyan")
    t.add_column("Value", style="white")
    t.add_row("Total", str(data["total"]))
    t.add_row("Processed", f"{data['processed']} ({data['pct']})")
    t.add_row("Written", str(data["written"]))
    t.add_row("Skipped", str(data["skipped"]))
    errors = data["errors"]
    t.add_row(
        "Errors",
        str(errors) if errors == 0 else f"[red]{errors}[/red]",
    )
    if data.get("current"):
        t.add_row("Current", data["current"][:60])
    console.print(t)
    console.print()


def run_no_db_summary(vault: Path | None = None) -> None:
    """Print a simple file-walk based note count when DB is absent."""
    from rich.table import Table  # noqa: PLC0415
    from rich import box  # noqa: PLC0415

    data = vault_metrics.collect_no_db_summary(vault)
    console = _get_console()

    if not data["vault_exists"]:
        console.print(
            "[red]Vault not found at[/red] "
            + str(vault or vault_common.resolve_vault())
        )
        return

    console.print(
        f"\n[bold cyan]Vault Summary (file walk)[/bold cyan] — {data['total']} notes\n"
    )
    t = Table(title="Notes by Folder", box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column("Folder", style="cyan")
    t.add_column("Count", justify="right", style="white")
    for row in data["by_folder"]:
        t.add_row(row["folder"], str(row["n"]))
    console.print(t)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point for vault-stats."""
    parser = argparse.ArgumentParser(
        prog="vault-stats",
        description="Vault analytics from the note_index database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--vault",
        "-V",
        metavar="PATH|NAME",
        default=None,
        help="Vault path or named vault (default: ~/ParsidionVault, or legacy ~/ClaudeVault if it exists)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--summary",
        "-s",
        action="store_true",
        default=False,
        help="Count notes by folder and type (default mode)",
    )
    mode.add_argument(
        "--stale",
        action="store_true",
        default=False,
        help="List stale notes",
    )
    mode.add_argument(
        "--top-linked",
        "-l",
        metavar="N",
        nargs="?",
        const=10,
        type=int,
        help="Show top N most-linked notes (default: 10)",
    )
    mode.add_argument(
        "--by-project",
        "-P",
        action="store_true",
        default=False,
        help="Count notes per project",
    )
    mode.add_argument(
        "--growth",
        "-g",
        metavar="N",
        nargs="?",
        const=8,
        type=int,
        help="Notes created per week for the last N weeks (default: 8)",
    )
    mode.add_argument(
        "--tags",
        "-t",
        metavar="N",
        nargs="?",
        const=30,
        type=int,
        help="Show tag cloud — top N most-used tags (default: 30)",
    )
    mode.add_argument(
        "--dashboard",
        "-d",
        action="store_true",
        default=False,
        help="Full-page analytics dashboard combining all views",
    )
    mode.add_argument(
        "--pending",
        action="store_true",
        default=False,
        help="Show pending_summaries.jsonl queue stats",
    )
    mode.add_argument(
        "--graph",
        action="store_true",
        default=False,
        help="Knowledge graph analytics (hubs, isolated notes, linked ratio)",
    )
    mode.add_argument(
        "--hooks",
        metavar="N",
        nargs="?",
        const=20,
        type=int,
        help="Show last N hook events from hook_events.log (default: 20)",
    )
    mode.add_argument(
        "--weekly",
        action="store_true",
        default=False,
        help="Generate (or preview with --dry-run) weekly rollup note for current ISO week",
    )
    mode.add_argument(
        "--monthly",
        action="store_true",
        default=False,
        help="Generate (or preview with --dry-run) monthly rollup note for current month",
    )
    mode.add_argument(
        "--timeline",
        metavar="N",
        nargs="?",
        const=30,
        type=int,
        help="Bar chart of notes created per day for last N days (default: 30)",
    )
    mode.add_argument(
        "--summarizer-progress",
        action="store_true",
        default=False,
        help="Show current summarizer progress from /tmp",
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        default=False,
        help="Preview output without writing files (applies to --weekly and --monthly)",
    )
    args = parser.parse_args()

    vault_path = vault_common.resolve_vault(explicit=args.vault, cwd=os.getcwd())
    conn = _open_db(vault_path)

    no_mode = not (
        args.summary
        or args.stale
        or args.by_project
        or args.top_linked is not None
        or args.growth is not None
        or args.tags is not None
        or args.dashboard
        or args.pending
        or args.graph
        or args.hooks is not None
        or args.weekly
        or args.monthly
        or args.timeline is not None
        or args.summarizer_progress
    )

    if args.pending:
        run_pending(vault_path)
        return
    if args.hooks is not None:
        run_hooks(args.hooks, vault_path)
        return
    if args.summarizer_progress:
        run_summarizer_progress()
        return

    if conn is None:
        if no_mode or args.summary or args.dashboard:
            run_no_db_summary(vault_path)
        elif args.graph:
            _get_console().print(
                "[yellow]note_index DB not found — run update_index.py first.[/yellow]"
            )
            sys.exit(1)
        elif args.timeline is not None:
            run_timeline(None, args.timeline, vault_path)
        elif args.weekly:
            run_weekly(None, dry_run=args.dry_run, vault=vault_path)
        elif args.monthly:
            run_monthly(None, dry_run=args.dry_run, vault=vault_path)
        else:
            _get_console().print(
                "[yellow]note_index DB not found — run update_index.py first.[/yellow]"
            )
            sys.exit(1)
        return

    try:
        if args.dashboard:
            run_dashboard(conn)
        elif no_mode or args.summary:
            run_summary(conn)
        elif args.stale:
            run_stale(conn)
        elif args.top_linked is not None:
            run_top_linked(conn, args.top_linked)
        elif args.by_project:
            run_by_project(conn)
        elif args.growth is not None:
            run_growth(conn, args.growth)
        elif args.tags is not None:
            run_tags(conn, args.tags)
        elif args.graph:
            run_graph(conn)
        elif args.timeline is not None:
            run_timeline(conn, args.timeline, vault_path)
        elif args.weekly:
            run_weekly(conn, dry_run=args.dry_run, vault=vault_path)
        elif args.monthly:
            run_monthly(conn, dry_run=args.dry_run, vault=vault_path)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
