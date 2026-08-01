"""Knowledge-graph analytics view (ARC-005).

Extracted from ``vault_stats.py``. Renders the retrieval-readiness panel
(expandable notes, dangling targets) plus the hub-notes and isolated-notes
tables.
"""

from __future__ import annotations

import sqlite3

import vault_metrics

from cli.stats._common import _get_console


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

    # Retrieval readiness — health of the graph-expansion retrieval feature.
    pct_expandable = (
        data["expandable_count"] / data["total"] * 100 if data["total"] else 0.0
    )
    console.print(
        "[bold cyan]Retrieval Readiness[/bold cyan] (graph-expansion feature)"
    )
    console.print(
        f"  Expandable notes: [green]{data['expandable_count']}[/green] "
        f"([white]{pct_expandable:.1f}%[/white] of total carry ≥1 related link)  ·  "
        f"Avg neighbours/note: [white]{data['avg_related_per_note']:.2f}[/white]"
    )
    if data["total_targets"]:
        live_targets = data["total_targets"] - data["dangling_targets"]
        console.print(
            f"  Related targets: [white]{data['total_targets']}[/white] total  ·  "
            f"[green]{live_targets}[/green] live  ·  "
            f"[yellow]{data['dangling_targets']}[/yellow] dangling"
        )
        if data["dangling_targets"]:
            console.print(
                "  [dim]Dangling targets are skipped at retrieval time. Repair with "
                "`vault_doctor.py --fix-frontmatter --execute`.[/dim]"
            )
    else:
        console.print(
            "  [dim]No related links in the vault — graph expansion cannot add "
            "neighbours.[/dim]"
        )
    console.print()

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
