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


def summarize_hook_latency(
    events: list[dict],
    *,
    window_days: int | None = 7,
    timeout_map: dict[str, int] | None = None,
) -> dict[str, dict[str, float | int]]:
    """Aggregate per-hook ``duration_ms`` percentiles and timeout counts.

    ENH-019: pure function over the collected events (no vault IO) so
    ``vault-stats --hooks``, ``--dashboard``, and the health component all
    share one aggregation. Hooks with no registered timeout never count
    timeouts (they are async registrations with nothing to exceed).

    Args:
        events: Hook events ( dicts with ``hook`` and ``duration_ms``).
        window_days: Drop events whose ``ts`` is older than this many days
            before aggregating; None keeps everything (bad/missing ``ts``
            counts as recent).
        timeout_map: Hook name -> registered timeout in ms. Defaults to
            :data:`vault_constants.HOOK_TIMEOUTS_MS`.

    Returns:
        ``{hook: {count, p50_ms, p95_ms, max_ms, timeouts}}`` keyed by hook
        name, insertion-ordered by first appearance.
    """
    from datetime import datetime, timedelta

    if timeout_map is None:
        from vault_constants import HOOK_TIMEOUTS_MS

        timeout_map = HOOK_TIMEOUTS_MS

    cutoff: datetime | None = None
    if window_days is not None and window_days > 0:
        cutoff = datetime.now() - timedelta(days=window_days)

    by_hook: dict[str, list[float]] = {}
    for event in events:
        hook = str(event.get("hook") or "")
        raw = event.get("duration_ms")
        if not hook or not isinstance(raw, (int, float)):
            continue
        if cutoff is not None:
            ts = event.get("ts")
            try:
                when = datetime.fromisoformat(str(ts))
            except (TypeError, ValueError):
                when = None
            if when is not None and when < cutoff:
                continue
        by_hook.setdefault(hook, []).append(float(raw))

    out: dict[str, dict[str, float | int]] = {}
    for hook, durations in by_hook.items():
        durations.sort()
        count = len(durations)
        if count >= 2:
            # inclusive so the extremes are reachable percentiles
            qs = _percentiles(durations, (0.50, 0.95))
            p50, p95 = qs[0], qs[1]
        else:
            p50 = p95 = durations[0]
        timeout_ms = timeout_map.get(hook)
        timeouts = (
            sum(1 for d in durations if timeout_ms is not None and d > timeout_ms)
            if timeout_ms is not None
            else 0
        )
        out[hook] = {
            "count": count,
            "p50_ms": int(round(p50)),
            "p95_ms": int(round(p95)),
            "max_ms": int(round(durations[-1])),
            "timeouts": timeouts,
        }
    return out


def _percentiles(sorted_values: list[float], qs: tuple[float, ...]) -> list[float]:
    """Inclusive-method percentiles (statistics.quantiles, small-sample safe)."""
    import statistics

    n = len(sorted_values)
    out: list[float] = []
    for q in qs:
        # statistics.quantiles with n=100 inclusive: index k = q*(n-1)+1
        try:
            data = statistics.quantiles(sorted_values, n=100, method="inclusive")
            out.append(data[min(99, int(round(q * 100)))])
        except statistics.StatisticsError:
            out.append(sorted_values[min(n - 1, int(round(q * (n - 1))))])
    return out


def _render_latency_table(aggregate: dict[str, dict[str, float | int]]) -> object:
    """Build the Rich latency table (or a plain tuple list fallback)."""
    from rich import box
    from rich.table import Table

    t = Table(box=box.SIMPLE_HEAD, show_lines=False, title=None)
    t.add_column("Hook", style="cyan")
    t.add_column("count", justify="right")
    t.add_column("p50 ms", justify="right", style="green")
    t.add_column("p95 ms", justify="right", style="yellow")
    t.add_column("max ms", justify="right")
    t.add_column("timeouts", justify="right", style="red")
    for hook, a in aggregate.items():
        t.add_row(
            hook,
            str(a["count"]),
            str(a["p50_ms"]),
            str(a["p95_ms"]),
            str(a["max_ms"]),
            str(a["timeouts"]),
        )
    return t


def session_start_budget_warning(
    aggregate: dict[str, dict[str, float | int]],
    *,
    budget_ratio: float = 0.70,
    timeout_map: dict[str, int] | None = None,
) -> str | None:
    """One-line warning when SessionStart p95 exceeds *budget_ratio* of its timeout."""
    if timeout_map is None:
        from vault_constants import HOOK_TIMEOUTS_MS

        timeout_map = HOOK_TIMEOUTS_MS
    timeout_ms = timeout_map.get("SessionStart")
    agg = aggregate.get("SessionStart")
    if not timeout_ms or not agg:
        return None
    p95 = float(agg["p95_ms"])  # type: ignore[arg-type]
    if p95 <= budget_ratio * timeout_ms:
        return None
    pct = int(round(100 * p95 / timeout_ms))
    return (
        f"⚠ SessionStart p95 {int(p95):,} ms is {pct}% of its "
        f"{timeout_ms // 1000}s registered timeout — the hook is at risk of "
        f"being cancelled by the runtime. Slow AI selector or cold code-memory "
        f"daemon are the usual causes."
    )


def run_hooks(
    last_n: int = 20,
    vault: Path | None = None,
    window_days: int = 7,
) -> None:
    """Print the per-hook latency aggregate, then the last N raw events.

    ENH-019: the aggregate table (count / p50 / p95 / max / timeouts over
    the window, timeouts counted against the registered per-hook timeout)
    prints above the raw tail, with a budget warning when SessionStart p95
    exceeds 70% of its 60s timeout.

    Args:
        last_n: Number of most-recent events to show.
        vault: Optional vault path. Defaults to resolve_vault().
        window_days: Aggregation window in days for the latency table.
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

    aggregate = summarize_hook_latency(events, window_days=window_days)
    if aggregate:
        console.print(
            f"\n[bold cyan]Hook Latency[/bold cyan] — last "
            f"{window_days} day(s), over the {len(events)} events shown\n"
        )
        console.print(_render_latency_table(aggregate))
        warning = session_start_budget_warning(aggregate)
        if warning:
            console.print(f"\n[bold red]{warning}[/bold red]")

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
