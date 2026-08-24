"""Shared helpers for the cli.stats subpackage (ARC-005).

Holds the lazy Rich console accessor and the thin DB-helper wrappers that
``vault_stats.py`` re-exports for backwards-compat with test attribute
access (``vault_stats._open_db``, ``vault_stats._collect_tags``).

Stdlib-only at module load (``rich`` is lazy-imported inside
``_get_console``); delegates the real work to ``vault_metrics``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import vault_metrics


# ---------------------------------------------------------------------------
# Lazy rich accessor — keeps the module importable without rich installed
# ---------------------------------------------------------------------------


def _get_console():  # type: ignore[return]
    """Return the shared Rich Console instance, importing rich lazily."""
    from rich.console import Console  # noqa: PLC0415

    if not hasattr(_get_console, "_instance"):
        _get_console._instance = Console()  # type: ignore[attr-defined]
    return _get_console._instance  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# DB helpers (thin wrappers; real implementation in vault_metrics)
# ---------------------------------------------------------------------------


def _open_db(vault: Path | None = None) -> sqlite3.Connection | None:
    """Open the embeddings.db in read-only mode.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        An open connection, or None if the DB is absent or unreadable.
    """
    return vault_metrics.open_db(vault)


def _collect_tags(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Collect all tags from note_index; delegate to vault_metrics.

    The ``tags`` column stores either a comma-separated string or a JSON
    array; both formats are handled.  Kept as a thin wrapper so that
    existing callers (including test_vault_stats.py) continue to work
    without change.

    Args:
        conn: Open DB connection.

    Returns:
        List of (tag, count) tuples sorted by count descending.
    """
    return vault_metrics.collect_tags(conn)
