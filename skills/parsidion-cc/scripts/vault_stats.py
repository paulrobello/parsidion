#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "rich>=13.0",
# ]
# ///
"""vault-stats — analytics over the Claude Vault note_index database.

Modes (mutually exclusive; default is --summary):
    --summary        Count notes by folder and type
    --stale          List stale notes (is_stale = 1)
    --top-linked N   Top N most-linked notes (default: 10)
    --by-project     Count notes per project
    --growth N       Notes created per week for the last N weeks (default: 8)
    --tags           Show tag cloud (top 30 most-used tags)
    --dashboard      Full-page analytics dashboard (combines all views)

All modes read from ~/ClaudeVault/embeddings.db (note_index table).
Falls back to a plain-text walk when the DB is absent.
"""

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import vault_common  # noqa: E402

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


def _open_db() -> sqlite3.Connection | None:
    """Open the embeddings.db in read-only mode.

    Returns:
        An open connection, or None if the DB is absent or unreadable.
    """
    db_path = vault_common.get_embeddings_db_path()
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
        bar = "█" * max(1, int(row["n"] / max_n * 20))
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
        bar = "█" * max(0, int(n / max_count * 20)) if n else ""
        t.add_row(label, str(n), bar)
    _CONSOLE.print(t)


def _collect_tags(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Collect all tags from note_index, returning (tag, count) sorted by count desc.

    The ``tags`` column stores a JSON array as text (e.g. ``["python", "vault"]``).
    Falls back gracefully when parsing fails for any row.

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
        try:
            tags = json.loads(row["tags"])
            if not isinstance(tags, list):
                continue
            for tag in tags:
                t = str(tag).strip()
                if t:
                    counts[t] = counts.get(t, 0) + 1
        except (json.JSONDecodeError, TypeError):
            continue
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
        bar = "█" * max(1, int(count / max_count * 20))
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
    _CONSOLE.rule("[bold cyan]Claude Vault Dashboard[/bold cyan]")
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
        bar = "█" * max(1, int(row["n"] / max_n * 16))
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
        bar = "█" * max(0, int(n / max_g * 16)) if n else ""
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


def run_no_db_summary() -> None:
    """Print a simple file-walk based note count when DB is absent.

    Counts .md files per vault subfolder as a fallback.
    """
    vault_root = vault_common.VAULT_ROOT
    if not vault_root.exists():
        _CONSOLE.print("[red]Vault not found at[/red] " + str(vault_root))
        return

    counts: dict[str, int] = {}
    total = 0
    for md in vault_root.rglob("*.md"):
        folder = md.parent.name if md.parent != vault_root else "(root)"
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
    args = parser.parse_args()

    conn = _open_db()

    # If no explicit mode chosen, default to summary
    no_mode = not (
        args.summary
        or args.stale
        or args.by_project
        or args.top_linked is not None
        or args.growth is not None
        or args.tags is not None
        or args.dashboard
    )

    if conn is None:
        if no_mode or args.summary or args.dashboard:
            run_no_db_summary()
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
    finally:
        conn.close()


if __name__ == "__main__":
    main()
