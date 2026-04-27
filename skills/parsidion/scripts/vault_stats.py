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
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, UTC
from pathlib import Path

import vault_common

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box


_CONSOLE = Console()


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _open_db(vault: Path | None = None) -> sqlite3.Connection | None:
    """Open the embeddings.db in read-only mode.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        An open connection, or None if the DB is absent or unreadable.
    """
    db_path = vault_common.get_embeddings_db_path(vault)
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


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
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def run_summary(conn: sqlite3.Connection) -> None:
    """Print note counts by folder and by type.

    Args:
        conn: Open DB connection.
    """
    total = _fetch_all(conn, "SELECT COUNT(*) AS n FROM note_index")[0]["n"]

    folder_rows = _fetch_all(
        conn,
        "SELECT folder, COUNT(*) AS n FROM note_index GROUP BY folder ORDER BY n DESC",
    )
    type_rows = _fetch_all(
        conn,
        "SELECT note_type, COUNT(*) AS n FROM note_index GROUP BY note_type ORDER BY n DESC",
    )

    _CONSOLE.print(f"\n[bold cyan]Vault Summary[/bold cyan] — {total} notes total\n")

    # Folder table
    t = Table(title="Notes by Folder", box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column("Folder", style="cyan")
    t.add_column("Count", justify="right", style="white")
    t.add_column("Bar", style="green")
    max_n = folder_rows[0]["n"] if folder_rows else 1
    for row in folder_rows:
        bar = "▄" * max(1, int(row["n"] / max_n * 20))
        t.add_row(row["folder"] or "(root)", str(row["n"]), bar)
    _CONSOLE.print(t)

    # Type table
    t2 = Table(title="Notes by Type", box=box.SIMPLE_HEAD, show_lines=False)
    t2.add_column("Type", style="magenta")
    t2.add_column("Count", justify="right", style="white")
    for row in type_rows:
        t2.add_row(row["note_type"] or "(unset)", str(row["n"]))
    _CONSOLE.print(t2)


def run_stale(conn: sqlite3.Connection) -> None:
    """Print notes flagged as stale.

    Args:
        conn: Open DB connection.
    """
    rows = _fetch_all(
        conn,
        "SELECT stem, title, folder, mtime FROM note_index WHERE is_stale = 1 ORDER BY mtime ASC",
    )

    if not rows:
        _CONSOLE.print("[green]No stale notes found.[/green]")
        return

    _CONSOLE.print(f"\n[bold yellow]Stale Notes[/bold yellow] — {len(rows)} found\n")
    t = Table(box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column("Note", style="cyan")
    t.add_column("Folder", style="dim")
    t.add_column("Last Modified", style="white")
    for row in rows:
        try:
            dt = datetime.fromtimestamp(row["mtime"], tz=UTC)
            age = dt.strftime("%Y-%m-%d")
        except (OSError, ValueError):
            age = "unknown"
        t.add_row(f"[[{row['stem']}]]", row["folder"] or "(root)", age)
    _CONSOLE.print(t)


def run_top_linked(conn: sqlite3.Connection, top_n: int = 10) -> None:
    """Print the top N most-linked-to notes.

    Args:
        conn: Open DB connection.
        top_n: Number of notes to display.
    """
    rows = _fetch_all(
        conn,
        "SELECT stem, title, folder, incoming_links FROM note_index "
        "WHERE incoming_links > 0 "
        "ORDER BY incoming_links DESC LIMIT ?",
        (top_n,),
    )

    if not rows:
        _CONSOLE.print("[dim]No notes with incoming links found.[/dim]")
        return

    _CONSOLE.print(f"\n[bold cyan]Top {top_n} Most-Linked Notes[/bold cyan]\n")
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
    _CONSOLE.print(t)


def run_by_project(conn: sqlite3.Connection) -> None:
    """Print note counts per project.

    Args:
        conn: Open DB connection.
    """
    rows = _fetch_all(
        conn,
        "SELECT project, COUNT(*) AS n FROM note_index "
        "WHERE project != '' "
        "GROUP BY project ORDER BY n DESC",
    )

    untagged = _fetch_all(
        conn,
        "SELECT COUNT(*) AS n FROM note_index WHERE project = ''",
    )
    untagged_n = untagged[0]["n"] if untagged else 0

    if not rows:
        _CONSOLE.print("[dim]No project-tagged notes found.[/dim]")
        return

    _CONSOLE.print("\n[bold cyan]Notes by Project[/bold cyan]\n")
    t = Table(box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column("Project", style="cyan")
    t.add_column("Count", justify="right", style="white")
    for row in rows:
        t.add_row(row["project"], str(row["n"]))
    if untagged_n:
        t.add_row("[dim](no project)[/dim]", f"[dim]{untagged_n}[/dim]")
    _CONSOLE.print(t)


def run_growth(conn: sqlite3.Connection, weeks: int = 8) -> None:
    """Print notes created per week for the last N weeks.

    Uses mtime as a proxy for creation time (first indexed time).

    Args:
        conn: Open DB connection.
        weeks: Number of weeks to display.
    """
    now = time.time()
    week_secs = 7 * 24 * 3600
    cutoff = now - weeks * week_secs

    rows = _fetch_all(
        conn,
        "SELECT mtime FROM note_index WHERE mtime >= ? ORDER BY mtime ASC",
        (cutoff,),
    )

    # Bin into weeks
    buckets: dict[int, int] = {}
    for row in rows:
        week_num = int((now - row["mtime"]) / week_secs)
        week_num = min(week_num, weeks - 1)
        buckets[week_num] = buckets.get(week_num, 0) + 1

    _CONSOLE.print(f"\n[bold cyan]Note Growth — last {weeks} weeks[/bold cyan]\n")
    t = Table(box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column("Week", style="dim")
    t.add_column("Count", justify="right", style="white")
    t.add_column("Bar", style="green")
    max_count = max(buckets.values()) if buckets else 1
    for w in range(weeks - 1, -1, -1):
        n = buckets.get(w, 0)
        label = "this week" if w == 0 else f"{w}w ago"
        bar = "▄" * max(0, int(n / max_count * 20)) if n else ""
        t.add_row(label, str(n), bar)
    _CONSOLE.print(t)


def _collect_tags(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Collect all tags from note_index, returning (tag, count) sorted by count desc.

    The ``tags`` column stores either a comma-separated string
    (``"python, vault, hooks"``) or a JSON array (``["python", "vault"]``).
    Both formats are handled; malformed values are skipped silently.

    Args:
        conn: Open DB connection.

    Returns:
        List of (tag, count) tuples sorted by count descending.
    """
    rows = _fetch_all(
        conn, "SELECT tags FROM note_index WHERE tags IS NOT NULL AND tags != ''"
    )
    counts: dict[str, int] = {}
    for row in rows:
        raw = row["tags"]
        # Try JSON array first, fall back to comma-separated string
        try:
            parsed = json.loads(raw)
            tag_list: list[str] = parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            tag_list = [t.strip() for t in raw.split(",")]
        for tag in tag_list:
            t = str(tag).strip()
            if t:
                counts[t] = counts.get(t, 0) + 1
    return sorted(counts.items(), key=lambda x: -x[1])


def run_tags(conn: sqlite3.Connection, top_n: int = 30) -> None:
    """Print a tag cloud showing the most-used tags.

    Args:
        conn: Open DB connection.
        top_n: Maximum number of tags to display.
    """
    tags = _collect_tags(conn)[:top_n]
    if not tags:
        _CONSOLE.print("[dim]No tags found.[/dim]")
        return

    _CONSOLE.print(
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
    _CONSOLE.print(t)


def run_dashboard(conn: sqlite3.Connection) -> None:
    """Print a full-page analytics dashboard combining all views.

    Shows: vault overview, folder distribution, note growth (8 weeks),
    top 10 most-linked notes, top 10 stale notes, and tag cloud.

    Args:
        conn: Open DB connection.
    """
    now = time.time()
    week_secs = 7 * 24 * 3600

    # --- collect data ---
    total = _fetch_all(conn, "SELECT COUNT(*) AS n FROM note_index")[0]["n"]
    stale_count = _fetch_all(
        conn, "SELECT COUNT(*) AS n FROM note_index WHERE is_stale = 1"
    )[0]["n"]
    linked_count = _fetch_all(
        conn, "SELECT COUNT(*) AS n FROM note_index WHERE incoming_links > 0"
    )[0]["n"]
    folder_rows = _fetch_all(
        conn,
        "SELECT folder, COUNT(*) AS n FROM note_index GROUP BY folder ORDER BY n DESC",
    )
    top_linked_rows = _fetch_all(
        conn,
        "SELECT stem, title, incoming_links FROM note_index "
        "WHERE incoming_links > 0 ORDER BY incoming_links DESC LIMIT 10",
    )
    stale_rows = _fetch_all(
        conn,
        "SELECT stem, folder, mtime FROM note_index WHERE is_stale = 1 ORDER BY mtime ASC LIMIT 10",
    )
    growth_rows = _fetch_all(
        conn,
        "SELECT mtime FROM note_index WHERE mtime >= ? ORDER BY mtime ASC",
        (now - 8 * week_secs,),
    )
    tags_data = _collect_tags(conn)[:20]

    # --- header ---
    _CONSOLE.print()
    _CONSOLE.rule("[bold cyan]Parsidion vault Dashboard[/bold cyan]")
    _CONSOLE.print(
        f"\n  [bold white]{total}[/bold white] notes  ·  "
        f"[yellow]{stale_count}[/yellow] stale  ·  "
        f"[green]{linked_count}[/green] linked  ·  "
        f"[dim]{datetime.now(tz=UTC).strftime('%Y-%m-%d %H:%M UTC')}[/dim]\n"
    )

    # --- folder distribution ---
    folder_table = Table(title="Notes by Folder", box=box.SIMPLE_HEAD, show_lines=False)
    folder_table.add_column("Folder", style="cyan")
    folder_table.add_column("Count", justify="right", style="white")
    folder_table.add_column("Bar", style="green")
    max_n = folder_rows[0]["n"] if folder_rows else 1
    for row in folder_rows:
        bar = "▄" * max(1, int(row["n"] / max_n * 16))
        folder_table.add_row(row["folder"] or "(root)", str(row["n"]), bar)

    # --- weekly growth ---
    buckets: dict[int, int] = {}
    for row in growth_rows:
        w = int((now - row["mtime"]) / week_secs)
        w = min(w, 7)
        buckets[w] = buckets.get(w, 0) + 1
    growth_table = Table(
        title="Note Growth (8w)", box=box.SIMPLE_HEAD, show_lines=False
    )
    growth_table.add_column("Week", style="dim")
    growth_table.add_column("n", justify="right", style="white")
    growth_table.add_column("Bar", style="green")
    max_g = max(buckets.values()) if buckets else 1
    for w in range(7, -1, -1):
        n = buckets.get(w, 0)
        label = "this week" if w == 0 else f"{w}w ago"
        bar = "▄" * max(0, int(n / max_g * 16)) if n else ""
        growth_table.add_row(label, str(n), bar)

    _CONSOLE.print(Columns([folder_table, growth_table], equal=False, expand=False))

    # --- top linked ---
    _CONSOLE.print()
    linked_table = Table(
        title="Top 10 Most-Linked Notes", box=box.SIMPLE_HEAD, show_lines=False
    )
    linked_table.add_column("Note", style="cyan")
    linked_table.add_column("Title", style="white")
    linked_table.add_column("Links", justify="right", style="green")
    if top_linked_rows:
        for row in top_linked_rows:
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
    if stale_rows:
        for row in stale_rows:
            try:
                age = datetime.fromtimestamp(row["mtime"], tz=UTC).strftime("%Y-%m-%d")
            except (OSError, ValueError):
                age = "unknown"
            stale_table.add_row(f"[[{row['stem']}]]", row["folder"] or "(root)", age)
    else:
        stale_table.add_row("[dim]—[/dim]", "[dim]no stale notes[/dim]", "")

    _CONSOLE.print(Columns([linked_table, stale_table], equal=False, expand=False))

    # --- tag cloud ---
    _CONSOLE.print()
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
        _CONSOLE.print(Panel(tag_text, title="Tag Cloud (top 20)", border_style="dim"))
    else:
        _CONSOLE.print("[dim]No tags found.[/dim]")

    _CONSOLE.print()


def run_pending(vault: Path | None = None) -> None:
    """Print a summary of pending_summaries.jsonl queue.

    Shows total entries queued, breakdown by source (session vs subagent),
    projects with pending summaries, oldest pending entry timestamp, and a
    rough token estimate (100 tokens per entry as proxy).
    """
    vault = vault or vault_common.resolve_vault()
    pending_path = vault / "pending_summaries.jsonl"
    if not pending_path.exists():
        _CONSOLE.print("[dim]No pending_summaries.jsonl found — queue is empty.[/dim]")
        return

    entries: list[dict] = []
    try:
        with open(pending_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError as exc:
        _CONSOLE.print(f"[red]Cannot read pending_summaries.jsonl: {exc}[/red]")
        return

    if not entries:
        _CONSOLE.print("[green]Queue is empty (0 entries).[/green]")
        return

    total = len(entries)
    # Source breakdown
    source_counts: dict[str, int] = {}
    project_counts: dict[str, int] = {}
    oldest_ts: str | None = None

    for entry in entries:
        src = entry.get("source", "session")
        source_counts[src] = source_counts.get(src, 0) + 1
        project = entry.get("project", "")
        if project:
            project_counts[project] = project_counts.get(project, 0) + 1
        ts = entry.get("timestamp", "")
        if ts and (oldest_ts is None or ts < oldest_ts):
            oldest_ts = ts

    token_estimate = total * 100

    _CONSOLE.print(
        f"\n[bold cyan]Pending Summaries Queue[/bold cyan] — {total} entries "
        f"(~{token_estimate:,} tokens estimated)\n"
    )

    # Source breakdown table
    src_table = Table(title="By Source", box=box.SIMPLE_HEAD, show_lines=False)
    src_table.add_column("Source", style="cyan")
    src_table.add_column("Count", justify="right", style="white")
    for src, count in sorted(source_counts.items(), key=lambda x: -x[1]):
        src_table.add_row(src, str(count))
    _CONSOLE.print(src_table)

    # Projects table
    if project_counts:
        _CONSOLE.print()
        proj_table = Table(title="By Project", box=box.SIMPLE_HEAD, show_lines=False)
        proj_table.add_column("Project", style="cyan")
        proj_table.add_column("Count", justify="right", style="white")
        for proj, count in sorted(project_counts.items(), key=lambda x: -x[1]):
            proj_table.add_row(proj, str(count))
        _CONSOLE.print(proj_table)

    if oldest_ts:
        _CONSOLE.print(f"\n  [dim]Oldest entry:[/dim] {oldest_ts}")
    _CONSOLE.print()


def run_graph(conn: sqlite3.Connection) -> None:
    """Print knowledge graph analytics from the note_index.

    Shows average incoming_links per note, hub notes (incoming_links >= 5),
    isolated notes (zero incoming_links AND empty related field), and the
    total linked vs unlinked ratio.

    Args:
        conn: Open DB connection.
    """
    all_rows = _fetch_all(
        conn,
        "SELECT stem, title, folder, incoming_links, related FROM note_index",
    )
    if not all_rows:
        _CONSOLE.print("[dim]No notes in index.[/dim]")
        return

    total = len(all_rows)
    total_links = sum(r["incoming_links"] or 0 for r in all_rows)
    avg_links = total_links / total if total else 0.0

    linked_count = sum(1 for r in all_rows if (r["incoming_links"] or 0) > 0)
    unlinked_count = total - linked_count

    hub_rows = [r for r in all_rows if (r["incoming_links"] or 0) >= 5]
    hub_rows.sort(key=lambda r: -(r["incoming_links"] or 0))
    hub_rows = hub_rows[:10]

    isolated_rows = [
        r
        for r in all_rows
        if (r["incoming_links"] or 0) == 0 and not (r["related"] or "").strip()
    ]

    _CONSOLE.print("\n[bold cyan]Knowledge Graph Analytics[/bold cyan]\n")
    _CONSOLE.print(
        f"  Total notes: [white]{total}[/white]  ·  "
        f"Avg incoming links: [white]{avg_links:.2f}[/white]  ·  "
        f"Linked: [green]{linked_count}[/green]  ·  "
        f"Unlinked: [yellow]{unlinked_count}[/yellow]\n"
    )

    # Hub notes
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
        _CONSOLE.print(hub_table)
    else:
        _CONSOLE.print("[dim]No hub notes (none with ≥5 incoming links).[/dim]")

    _CONSOLE.print()

    # Isolated notes
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
        _CONSOLE.print(iso_table)
    else:
        _CONSOLE.print("[green]No isolated notes found.[/green]")

    _CONSOLE.print()


def run_hooks(last_n: int = 20, vault: Path | None = None) -> None:
    """Print the last N events from hook_events.log.

    Each line is a JSON object with: hook, ts, project, duration_ms, plus
    optional extra fields.

    Args:
        last_n: Number of most-recent events to show.
        vault: Optional vault path. Defaults to resolve_vault().
    """
    vault = vault or vault_common.resolve_vault()
    log_path = vault / "hook_events.log"
    if not log_path.exists():
        _CONSOLE.print("[dim]No hook_events.log found.[/dim]")
        return

    events: list[dict] = []
    try:
        with open(log_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError as exc:
        _CONSOLE.print(f"[red]Cannot read hook_events.log: {exc}[/red]")
        return

    if not events:
        _CONSOLE.print("[dim]hook_events.log is empty.[/dim]")
        return

    recent = events[-last_n:]

    _CONSOLE.print(
        f"\n[bold cyan]Hook Events[/bold cyan] — last {len(recent)} of {len(events)} total\n"
    )
    t = Table(box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column("Timestamp", style="dim")
    t.add_column("Hook", style="cyan")
    t.add_column("Project", style="white")
    t.add_column("ms", justify="right", style="green")
    t.add_column("Extra", style="dim")

    _KNOWN_FIELDS = {"hook", "ts", "project", "duration_ms"}
    for event in recent:
        ts = event.get("ts", "")
        hook = event.get("hook", "")
        project = event.get("project", "") or ""
        duration_ms = event.get("duration_ms")
        dur_str = str(duration_ms) if duration_ms is not None else ""
        extra_items = {k: v for k, v in event.items() if k not in _KNOWN_FIELDS}
        extra_str = "  ".join(f"{k}={v}" for k, v in list(extra_items.items())[:3])
        t.add_row(ts, hook, project[:30], dur_str, extra_str[:60])

    _CONSOLE.print(t)


def run_weekly(
    conn: sqlite3.Connection | None, dry_run: bool = False, vault: Path | None = None
) -> None:
    """Generate or preview a weekly rollup note for the current ISO week.

    Reads all daily notes from the current week's directory, extracts
    ## Sessions sections, and writes a ``Daily/YYYY-MM/week-NN.md`` summary
    note with project activity, categories mentioned, and links to daily notes.

    Args:
        conn: Open DB connection (unused currently, reserved for future use).
        dry_run: If True, print the note content without writing it.
        vault: Optional vault path. Defaults to resolve_vault().
    """
    vault = vault or vault_common.resolve_vault()
    from datetime import date, timedelta

    today = date.today()
    iso_year, iso_week, iso_weekday = today.isocalendar()
    # Monday of this ISO week
    monday = today - timedelta(days=iso_weekday - 1)
    sunday = monday + timedelta(days=6)

    month_dir = vault / "Daily" / f"{today.year:04d}-{today.month:02d}"

    # Collect daily note paths for this week (supports both DD.md and DD-{user}.md)
    import re as _re

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
        _CONSOLE.print(
            f"[yellow]No daily notes found for week {iso_week} "
            f"({monday} – {sunday}).[/yellow]"
        )
        return

    # Parse each daily note
    projects_seen: set[str] = set()
    categories_seen: set[str] = set()
    session_lines: list[str] = []
    links_to_daily: list[str] = []

    for dp in sorted(daily_paths):
        try:
            text = dp.read_text(encoding="utf-8")
        except OSError:
            continue

        # Derive wikilink stem: e.g. Daily/2026-03/17 → "17" (relative stem)
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
            # Look for project mentions
            if "project:" in line.lower():
                parts = line.split(":", 1)
                if len(parts) == 2:
                    val = parts[1].strip()
                    if val and val not in {"", "null"}:
                        projects_seen.add(val)
            # Look for category mentions
            if "categor" in line.lower():
                import re

                found = re.findall(r"\b[a-zA-Z][\w-]+\b", line)
                categories_seen.update(found)

    # Build note content
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
        _CONSOLE.print(
            f"\n[bold cyan]Weekly Rollup (dry run)[/bold cyan] — would write to:\n"
            f"  [dim]{output_path}[/dim]\n"
        )
        _CONSOLE.print(content)
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    _CONSOLE.print(
        f"\n[green]Weekly rollup written:[/green] {output_path}\n"
        f"  Covered {len(daily_paths)} daily notes, "
        f"{len(projects_seen)} project(s), "
        f"{len(links_to_daily)} day link(s).\n"
    )


def run_monthly(
    conn: sqlite3.Connection | None, dry_run: bool = False, vault: Path | None = None
) -> None:
    """Generate or preview a monthly rollup note for the current month.

    Reads all daily notes from the current month's directory, extracts
    ## Sessions sections, and writes ``Daily/YYYY-MM/monthly.md``.

    Args:
        conn: Open DB connection (unused currently, reserved for future use).
        dry_run: If True, print the note content without writing it.
        vault: Optional vault path. Defaults to resolve_vault().
    """
    vault = vault or vault_common.resolve_vault()
    from datetime import date
    import calendar

    today = date.today()
    month_dir = vault / "Daily" / f"{today.year:04d}-{today.month:02d}"

    # Collect all daily note files in this month's directory (DD.md and DD-{user}.md)
    import re as _re

    _daily_stem_re = _re.compile(r"^(\d{2})(?:-.+)?$")
    daily_paths: list[Path] = []
    if month_dir.exists():
        for dp in sorted(month_dir.glob("*.md")):
            if _daily_stem_re.match(dp.stem):
                daily_paths.append(dp)

    if not daily_paths:
        _CONSOLE.print(
            f"[yellow]No daily notes found for "
            f"{today.strftime('%B %Y')} in {month_dir}.[/yellow]"
        )
        return

    # Parse each daily note
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
        _CONSOLE.print(
            f"\n[bold cyan]Monthly Rollup (dry run)[/bold cyan] — would write to:\n"
            f"  [dim]{output_path}[/dim]\n"
        )
        _CONSOLE.print(content)
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    _CONSOLE.print(
        f"\n[green]Monthly rollup written:[/green] {output_path}\n"
        f"  Covered {len(daily_paths)} daily notes, "
        f"{len(projects_seen)} project(s).\n"
    )


def run_timeline(
    conn: sqlite3.Connection | None, days: int = 30, vault: Path | None = None
) -> None:
    """Print a bar chart of notes created per day for the last N days.

    Uses mtime from note_index. Falls back to a file walk if DB is absent.

    Args:
        conn: Open DB connection, or None for file-walk fallback.
        days: Number of days to display (default: 30).
        vault: Optional vault path. Defaults to resolve_vault().
    """
    from datetime import date, timedelta

    today = date.today()
    now_ts = time.time()
    day_secs = 24 * 3600
    cutoff_ts = now_ts - days * day_secs

    # Build per-day counts
    day_counts: dict[int, int] = {i: 0 for i in range(days)}

    if conn is not None:
        rows = _fetch_all(
            conn,
            "SELECT mtime FROM note_index WHERE mtime >= ?",
            (cutoff_ts,),
        )
        for row in rows:
            age_days = int((now_ts - row["mtime"]) / day_secs)
            age_days = min(age_days, days - 1)
            day_counts[age_days] = day_counts.get(age_days, 0) + 1
    else:
        vault = vault or vault_common.resolve_vault()
        if vault.exists():
            for md in vault.rglob("*.md"):
                try:
                    mtime = md.stat().st_mtime
                except OSError:
                    continue
                if mtime < cutoff_ts:
                    continue
                age_days = int((now_ts - mtime) / day_secs)
                age_days = min(age_days, days - 1)
                day_counts[age_days] = day_counts.get(age_days, 0) + 1

    _CONSOLE.print(f"\n[bold cyan]Note Timeline[/bold cyan] — last {days} days\n")
    t = Table(box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column("Date", style="dim")
    t.add_column("Count", justify="right", style="white")
    t.add_column("Bar", style="green")

    max_count = max(day_counts.values()) if day_counts else 1
    max_count = max(max_count, 1)

    for d in range(days - 1, -1, -1):
        day_date = today - timedelta(days=d)
        n = day_counts.get(d, 0)
        label = day_date.strftime("%Y-%m-%d")
        if d == 0:
            label += " [dim](today)[/dim]"
        bar = "▄" * max(0, int(n / max_count * 24)) if n else ""
        t.add_row(label, str(n) if n else "[dim]0[/dim]", bar)

    _CONSOLE.print(t)


def run_summarizer_progress() -> None:
    """Print current summarizer progress from ~/.claude/logs/parsidion-summarizer-progress.json.

    Shows: total, processed, written, skipped, errors, current.
    If the file is absent, reports that no summarizer is currently running.
    """
    progress_path = (
        Path.home() / ".claude" / "logs" / "parsidion-summarizer-progress.json"
    )
    if not progress_path.exists():
        _CONSOLE.print("[dim]No summarizer currently running.[/dim]")
        return

    try:
        data = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _CONSOLE.print(f"[red]Cannot read progress file: {exc}[/red]")
        return

    total = data.get("total", 0)
    processed = data.get("processed", 0)
    written = data.get("written", 0)
    skipped = data.get("skipped", 0)
    errors = data.get("errors", 0)
    current = data.get("current", "")

    pct = f"{processed / total * 100:.1f}%" if total else "—"

    _CONSOLE.print("\n[bold cyan]Summarizer Progress[/bold cyan]\n")
    t = Table(box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column("Field", style="cyan")
    t.add_column("Value", style="white")
    t.add_row("Total", str(total))
    t.add_row("Processed", f"{processed} ({pct})")
    t.add_row("Written", str(written))
    t.add_row("Skipped", str(skipped))
    t.add_row("Errors", str(errors) if errors == 0 else f"[red]{errors}[/red]")
    if current:
        t.add_row("Current", current[:60])
    _CONSOLE.print(t)
    _CONSOLE.print()


def run_no_db_summary(vault: Path | None = None) -> None:
    """Print a simple file-walk based note count when DB is absent.

    Counts .md files per vault subfolder as a fallback.
    """
    vault = vault or vault_common.resolve_vault()
    if not vault.exists():
        _CONSOLE.print("[red]Vault not found at[/red] " + str(vault))
        return

    counts: dict[str, int] = {}
    total = 0
    for md in vault.rglob("*.md"):
        folder = md.parent.name if md.parent != vault else "(root)"
        counts[folder] = counts.get(folder, 0) + 1
        total += 1

    _CONSOLE.print(
        f"\n[bold cyan]Vault Summary (file walk)[/bold cyan] — {total} notes\n"
    )
    t = Table(title="Notes by Folder", box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column("Folder", style="cyan")
    t.add_column("Count", justify="right", style="white")
    for folder, n in sorted(counts.items(), key=lambda x: -x[1]):
        t.add_row(folder, str(n))
    _CONSOLE.print(t)


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

    # Vault selection flag
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

    # Resolve vault path
    vault_path = vault_common.resolve_vault(explicit=args.vault, cwd=os.getcwd())

    conn = _open_db(vault_path)

    # If no explicit mode chosen, default to summary
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

    # Modes that don't require a DB connection
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
            _CONSOLE.print(
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
            _CONSOLE.print(
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
