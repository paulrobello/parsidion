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

All modes read from ~/ClaudeVault/embeddings.db (note_index table).
Falls back to a plain-text walk when the DB is absent.
"""

import argparse
import sqlite3
import sys
import time
from datetime import datetime, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import vault_common  # noqa: E402

from rich.console import Console
from rich.table import Table
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
    args = parser.parse_args()

    conn = _open_db()

    # If no explicit mode chosen, default to summary
    no_mode = not (
        args.summary
        or args.stale
        or args.by_project
        or args.top_linked is not None
        or args.growth is not None
    )

    if conn is None:
        if no_mode or args.summary:
            run_no_db_summary()
        else:
            _CONSOLE.print(
                "[yellow]note_index DB not found — run update_index.py first.[/yellow]"
            )
            sys.exit(1)
        return

    try:
        if no_mode or args.summary:
            run_summary(conn)
        elif args.stale:
            run_stale(conn)
        elif args.top_linked is not None:
            run_top_linked(conn, args.top_linked)
        elif args.by_project:
            run_by_project(conn)
        elif args.growth is not None:
            run_growth(conn, args.growth)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
