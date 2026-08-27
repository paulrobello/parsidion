"""Overview table views (ARC-005).

Extracted from ``vault_stats.py``. These five modes are simple Rich tables
rendered directly from ``vault_metrics.collect_*`` rows: stale notes, top
linked, by-project, growth, and the tag cloud. Grouped because they share
the exact same shape (collect -> table -> console).
"""

from __future__ import annotations

import sqlite3

from core import vault_metrics

from cli.stats._common import _get_console


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
