"""Full-page dashboard view (ARC-005).

Extracted from ``vault_stats.py``. The dashboard composes every overview
(folders, growth, top-linked, stale, tags) into one Rich layout; large
enough to warrant its own module rather than sitting with the simpler
single-table views.
"""

from __future__ import annotations

import sqlite3

import vault_metrics

from cli.stats._common import _get_console


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

    # ENH-019: hook latency table (count / p50 / p95 / max / timeouts over
    # the last 7 days) with the SessionStart budget warning.
    try:
        from cli.stats.operations import (  # noqa: PLC0415
            _render_latency_table,
            session_start_budget_warning,
            summarize_hook_latency,
        )

        hooks_data = vault_metrics.collect_hooks(500, None)
        aggregate = summarize_hook_latency(hooks_data.get("events", []), window_days=7)
        if aggregate:
            console.print(_render_latency_table(aggregate))
            warning = session_start_budget_warning(aggregate)
            if warning:
                console.print(f"[bold red]{warning}[/bold red]")
    except Exception:  # noqa: BLE001 — dashboard section is best-effort
        pass

    console.print()
