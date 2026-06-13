"""vault_metrics — stdlib-only data layer for vault analytics.

Provides pure data-gathering functions over the note_index database and the
vault file system.  No third-party dependencies (rich, fastembed, sqlite-vec).

This module is used by ``vault_stats`` (the CLI display layer) and may be
imported directly by other vault tools or tests without requiring the
``[tools]`` optional-dependency group.

ARC-007: separating the data/query layer from the display/CLI layer so
``vault_stats`` can be imported as a library without triggering an
``ImportError`` when ``rich`` is not installed.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, UTC
from pathlib import Path

import vault_common


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def open_db(vault: Path | None = None) -> sqlite3.Connection | None:
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


def fetch_all(
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
# Data-gathering functions (return plain dicts/lists — no rich dependency)
# ---------------------------------------------------------------------------


def collect_summary(conn: sqlite3.Connection) -> dict:
    """Return note counts by folder and by type.

    Args:
        conn: Open DB connection.

    Returns:
        Dict with keys: total (int), by_folder (list of {folder, n}),
        by_type (list of {note_type, n}).
    """
    total = fetch_all(conn, "SELECT COUNT(*) AS n FROM note_index")[0]["n"]
    folder_rows = fetch_all(
        conn,
        "SELECT folder, COUNT(*) AS n FROM note_index GROUP BY folder ORDER BY n DESC",
    )
    type_rows = fetch_all(
        conn,
        "SELECT note_type, COUNT(*) AS n FROM note_index GROUP BY note_type ORDER BY n DESC",
    )
    return {
        "total": total,
        "by_folder": [dict(r) for r in folder_rows],
        "by_type": [dict(r) for r in type_rows],
    }


def collect_stale(conn: sqlite3.Connection) -> list[dict]:
    """Return notes flagged as stale.

    Args:
        conn: Open DB connection.

    Returns:
        List of dicts with keys: stem, title, folder, mtime, age (YYYY-MM-DD str).
    """
    rows = fetch_all(
        conn,
        "SELECT stem, title, folder, mtime FROM note_index WHERE is_stale = 1 ORDER BY mtime ASC",
    )
    results = []
    for row in rows:
        try:
            dt = datetime.fromtimestamp(row["mtime"], tz=UTC)
            age = dt.strftime("%Y-%m-%d")
        except (OSError, ValueError):
            age = "unknown"
        results.append(
            {
                "stem": row["stem"],
                "title": row["title"],
                "folder": row["folder"],
                "mtime": row["mtime"],
                "age": age,
            }
        )
    return results


def collect_top_linked(conn: sqlite3.Connection, top_n: int = 10) -> list[dict]:
    """Return the top N most-linked-to notes.

    Args:
        conn: Open DB connection.
        top_n: Number of notes to return.

    Returns:
        List of dicts with keys: stem, title, folder, incoming_links.
    """
    rows = fetch_all(
        conn,
        "SELECT stem, title, folder, incoming_links FROM note_index "
        "WHERE incoming_links > 0 "
        "ORDER BY incoming_links DESC LIMIT ?",
        (top_n,),
    )
    return [dict(r) for r in rows]


def collect_by_project(conn: sqlite3.Connection) -> dict:
    """Return note counts per project.

    Args:
        conn: Open DB connection.

    Returns:
        Dict with keys: by_project (list of {project, n}), untagged_n (int).
    """
    rows = fetch_all(
        conn,
        "SELECT project, COUNT(*) AS n FROM note_index "
        "WHERE project != '' "
        "GROUP BY project ORDER BY n DESC",
    )
    untagged = fetch_all(
        conn,
        "SELECT COUNT(*) AS n FROM note_index WHERE project = ''",
    )
    untagged_n = untagged[0]["n"] if untagged else 0
    return {
        "by_project": [dict(r) for r in rows],
        "untagged_n": untagged_n,
    }


def collect_growth(conn: sqlite3.Connection, weeks: int = 8) -> list[dict]:
    """Return notes created per week for the last N weeks.

    Args:
        conn: Open DB connection.
        weeks: Number of weeks to cover.

    Returns:
        List of dicts with keys: label (str), n (int), week_num (int),
        ordered from oldest to most-recent.
    """
    now = time.time()
    week_secs = 7 * 24 * 3600
    cutoff = now - weeks * week_secs

    rows = fetch_all(
        conn,
        "SELECT mtime FROM note_index WHERE mtime >= ? ORDER BY mtime ASC",
        (cutoff,),
    )

    buckets: dict[int, int] = {}
    for row in rows:
        week_num = int((now - row["mtime"]) / week_secs)
        week_num = min(week_num, weeks - 1)
        buckets[week_num] = buckets.get(week_num, 0) + 1

    result = []
    for w in range(weeks - 1, -1, -1):
        n = buckets.get(w, 0)
        label = "this week" if w == 0 else f"{w}w ago"
        result.append({"label": label, "n": n, "week_num": w})
    return result


def collect_tags(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Collect all tags from note_index, returning (tag, count) sorted by count desc.

    The ``tags`` column stores either a comma-separated string
    (``"python, vault, hooks"``) or a JSON array (``["python", "vault"]``).
    Both formats are handled; malformed values are skipped silently.

    Args:
        conn: Open DB connection.

    Returns:
        List of (tag, count) tuples sorted by count descending.
    """
    rows = fetch_all(
        conn, "SELECT tags FROM note_index WHERE tags IS NOT NULL AND tags != ''"
    )
    counts: dict[str, int] = {}
    for row in rows:
        raw = row["tags"]
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


def collect_graph(conn: sqlite3.Connection) -> dict:
    """Return knowledge graph analytics from the note_index.

    Args:
        conn: Open DB connection.

    Returns:
        Dict with keys: total, total_links, avg_links, linked_count,
        unlinked_count, hub_notes (list of dicts), isolated_notes (list of dicts).
    """
    all_rows = fetch_all(
        conn,
        "SELECT stem, title, folder, incoming_links, related FROM note_index",
    )
    if not all_rows:
        return {
            "total": 0,
            "total_links": 0,
            "avg_links": 0.0,
            "linked_count": 0,
            "unlinked_count": 0,
            "hub_notes": [],
            "isolated_notes": [],
        }

    total = len(all_rows)
    total_links = sum(r["incoming_links"] or 0 for r in all_rows)
    avg_links = total_links / total if total else 0.0
    linked_count = sum(1 for r in all_rows if (r["incoming_links"] or 0) > 0)
    unlinked_count = total - linked_count

    hub_rows = sorted(
        [r for r in all_rows if (r["incoming_links"] or 0) >= 5],
        key=lambda r: -(r["incoming_links"] or 0),
    )[:10]

    isolated_rows = [
        r
        for r in all_rows
        if (r["incoming_links"] or 0) == 0 and not (r["related"] or "").strip()
    ]

    return {
        "total": total,
        "total_links": total_links,
        "avg_links": avg_links,
        "linked_count": linked_count,
        "unlinked_count": unlinked_count,
        "hub_notes": [dict(r) for r in hub_rows],
        "isolated_notes": [dict(r) for r in isolated_rows],
    }


def collect_pending(vault: Path | None = None) -> dict:
    """Return a summary of pending_summaries.jsonl queue.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        Dict with keys: exists (bool), total (int), source_counts (dict),
        project_counts (dict), oldest_ts (str|None), token_estimate (int),
        entries (list of dicts).
    """
    vault = vault or vault_common.resolve_vault()
    pending_path = vault / "pending_summaries.jsonl"
    if not pending_path.exists():
        return {
            "exists": False,
            "total": 0,
            "source_counts": {},
            "project_counts": {},
            "oldest_ts": None,
            "token_estimate": 0,
            "entries": [],
        }

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
    except OSError:
        return {
            "exists": True,
            "error": True,
            "total": 0,
            "source_counts": {},
            "project_counts": {},
            "oldest_ts": None,
            "token_estimate": 0,
            "entries": [],
        }

    total = len(entries)
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

    return {
        "exists": True,
        "total": total,
        "source_counts": source_counts,
        "project_counts": project_counts,
        "oldest_ts": oldest_ts,
        "token_estimate": total * 100,
        "entries": entries,
    }


def collect_hooks(last_n: int = 20, vault: Path | None = None) -> dict:
    """Return the last N events from hook_events.log.

    Args:
        last_n: Number of most-recent events to return.
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        Dict with keys: exists (bool), total (int), events (list of dicts).
    """
    vault = vault or vault_common.resolve_vault()
    log_path = vault / "hook_events.log"
    if not log_path.exists():
        return {"exists": False, "total": 0, "events": []}

    all_events: list[dict] = []
    try:
        with open(log_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        all_events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        return {"exists": True, "error": True, "total": 0, "events": []}

    return {
        "exists": True,
        "total": len(all_events),
        "events": all_events[-last_n:],
    }


def collect_timeline(
    conn: sqlite3.Connection | None, days: int = 30, vault: Path | None = None
) -> list[dict]:
    """Return per-day note counts for the last N days.

    Args:
        conn: Open DB connection, or None for file-walk fallback.
        days: Number of days to cover.
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        List of dicts with keys: date (YYYY-MM-DD str), n (int), is_today (bool),
        ordered from oldest to most-recent (index 0 = days-1 ago).
    """
    from datetime import date, timedelta

    today = date.today()
    now_ts = time.time()
    day_secs = 24 * 3600
    cutoff_ts = now_ts - days * day_secs

    day_counts: dict[int, int] = {i: 0 for i in range(days)}

    if conn is not None:
        rows = fetch_all(
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

    result = []
    for d in range(days - 1, -1, -1):
        day_date = today - timedelta(days=d)
        result.append(
            {
                "date": day_date.strftime("%Y-%m-%d"),
                "n": day_counts.get(d, 0),
                "is_today": d == 0,
            }
        )
    return result


def collect_summarizer_progress() -> dict:
    """Return current summarizer progress from ~/.claude/logs/.

    Returns:
        Dict with keys: exists (bool), total, processed, written, skipped,
        errors, current (str), pct (str).
    """
    progress_path = (
        Path.home() / ".claude" / "logs" / "parsidion-summarizer-progress.json"
    )
    if not progress_path.exists():
        return {"exists": False}

    try:
        data = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"exists": True, "error": str(exc)}

    total = data.get("total", 0)
    processed = data.get("processed", 0)
    pct = f"{processed / total * 100:.1f}%" if total else "—"

    return {
        "exists": True,
        "total": total,
        "processed": processed,
        "written": data.get("written", 0),
        "skipped": data.get("skipped", 0),
        "errors": data.get("errors", 0),
        "current": data.get("current", ""),
        "pct": pct,
    }


def collect_no_db_summary(vault: Path | None = None) -> dict:
    """Return a file-walk based note count when DB is absent.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        Dict with keys: vault_exists (bool), total (int),
        by_folder (list of {folder, n} sorted by n desc).
    """
    vault = vault or vault_common.resolve_vault()
    if not vault.exists():
        return {"vault_exists": False, "total": 0, "by_folder": []}

    counts: dict[str, int] = {}
    total = 0
    for md in vault.rglob("*.md"):
        folder = md.parent.name if md.parent != vault else "(root)"
        counts[folder] = counts.get(folder, 0) + 1
        total += 1

    by_folder = sorted(
        [{"folder": f, "n": n} for f, n in counts.items()],
        key=lambda x: -int(x["n"]),
    )
    return {"vault_exists": True, "total": total, "by_folder": by_folder}


def collect_dashboard(conn: sqlite3.Connection) -> dict:
    """Return all data needed for the full analytics dashboard.

    Args:
        conn: Open DB connection.

    Returns:
        Dict combining summary, growth, top_linked, stale, and tags data.
    """
    now = time.time()
    week_secs = 7 * 24 * 3600

    total = fetch_all(conn, "SELECT COUNT(*) AS n FROM note_index")[0]["n"]
    stale_count = fetch_all(
        conn, "SELECT COUNT(*) AS n FROM note_index WHERE is_stale = 1"
    )[0]["n"]
    linked_count_row = fetch_all(
        conn, "SELECT COUNT(*) AS n FROM note_index WHERE incoming_links > 0"
    )[0]["n"]
    folder_rows = fetch_all(
        conn,
        "SELECT folder, COUNT(*) AS n FROM note_index GROUP BY folder ORDER BY n DESC",
    )
    top_linked_rows = fetch_all(
        conn,
        "SELECT stem, title, incoming_links FROM note_index "
        "WHERE incoming_links > 0 ORDER BY incoming_links DESC LIMIT 10",
    )
    stale_rows = fetch_all(
        conn,
        "SELECT stem, folder, mtime FROM note_index WHERE is_stale = 1 ORDER BY mtime ASC LIMIT 10",
    )
    growth_rows = fetch_all(
        conn,
        "SELECT mtime FROM note_index WHERE mtime >= ? ORDER BY mtime ASC",
        (now - 8 * week_secs,),
    )
    tags_data = collect_tags(conn)[:20]

    # Build weekly growth buckets
    buckets: dict[int, int] = {}
    for row in growth_rows:
        w = int((now - row["mtime"]) / week_secs)
        w = min(w, 7)
        buckets[w] = buckets.get(w, 0) + 1

    growth = []
    for w in range(7, -1, -1):
        label = "this week" if w == 0 else f"{w}w ago"
        growth.append({"label": label, "n": buckets.get(w, 0), "week_num": w})

    stale_with_age = []
    for row in stale_rows:
        try:
            age = datetime.fromtimestamp(row["mtime"], tz=UTC).strftime("%Y-%m-%d")
        except (OSError, ValueError):
            age = "unknown"
        stale_with_age.append(
            {"stem": row["stem"], "folder": row["folder"], "age": age}
        )

    return {
        "total": total,
        "stale_count": stale_count,
        "linked_count": linked_count_row,
        "by_folder": [dict(r) for r in folder_rows],
        "top_linked": [dict(r) for r in top_linked_rows],
        "stale": stale_with_age,
        "growth": growth,
        "tags": tags_data,
        "timestamp": datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC"),
    }
