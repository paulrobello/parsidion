"""Summary view + DB-absent fallback (ARC-005).

Extracted from ``vault_stats.py``. ``run_summary`` is the strict-DB view
and ``run_no_db_summary`` is its file-walk fallback, used by the dispatch
table in ``cli.stats.cli`` when ``embeddings.db`` is missing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from core import vault_metrics
from core.vault_path import resolve_vault

from cli.stats._common import _get_console


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


def run_no_db_summary(vault: Path | None = None) -> None:
    """Print a simple file-walk based note count when DB is absent."""
    from rich.table import Table  # noqa: PLC0415
    from rich import box  # noqa: PLC0415

    data = vault_metrics.collect_no_db_summary(vault)
    console = _get_console()

    if not data["vault_exists"]:
        console.print("[red]Vault not found at[/red] " + str(vault or resolve_vault()))
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
