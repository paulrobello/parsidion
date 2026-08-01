"""Operational / activity views (ARC-005).

Extracted from ``vault_stats.py``. These four modes all render operational
state rather than note content: the pending-summaries queue (+ dead
letters), the hook-events log, the notes-per-day timeline, and the live
summarizer-progress feed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import vault_metrics

from cli.stats._common import _get_console


def run_pending(vault: Path | None = None) -> None:
    """Print a summary of pending_summaries.jsonl queue and dead_letters.jsonl."""
    from rich.table import Table  # noqa: PLC0415
    from rich import box  # noqa: PLC0415

    data = vault_metrics.collect_pending(vault)
    console = _get_console()

    if not data["exists"]:
        console.print("[dim]No pending_summaries.jsonl found — queue is empty.[/dim]")
    elif data.get("error"):
        console.print("[red]Cannot read pending_summaries.jsonl[/red]")
    elif data["total"] == 0:
        console.print("[green]Queue is empty (0 entries).[/green]")
    else:
        total = data["total"]
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
            proj_table = Table(
                title="By Project", box=box.SIMPLE_HEAD, show_lines=False
            )
            proj_table.add_column("Project", style="cyan")
            proj_table.add_column("Count", justify="right", style="white")
            for proj, count in sorted(
                data["project_counts"].items(), key=lambda x: -x[1]
            ):
                proj_table.add_row(proj, str(count))
            console.print(proj_table)

        if data["oldest_ts"]:
            console.print(f"\n  [dim]Oldest entry:[/dim] {data['oldest_ts']}")
        console.print()

    # --- Dead-letter status (always shown, even when the queue is empty) ---
    dl_data = vault_metrics.collect_dead_letters(vault)
    if not dl_data["exists"] or dl_data.get("error") or dl_data["total"] == 0:
        console.print("[dim]Dead letters: 0[/dim]")
        return

    console.print(
        f"\n[bold red]Dead Letters[/bold red] — {dl_data['total']} entries "
        f"(dead_letters.jsonl)\n"
    )
    for entry in dl_data["recent"]:
        console.print(
            f"  [dim]{entry['dead_lettered_at']}[/dim] "
            f"[cyan]{entry['project']}[/cyan] — {entry['last_failure']}"
        )
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
