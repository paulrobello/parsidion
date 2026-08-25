"""CLI entry point for ``vault-stats`` (``main()`` + QA-002 dispatch table).

Extracted from ``vault_stats.py`` (ARC-005). The argparse block, the
``_MODE_FLAGS`` selection table, and the ``_MODES`` dispatch table are
lifted unchanged from the QA-002 refactor — each mode is declared exactly
once with its runner, whether it requires a live DB connection, and what
to call instead when the DB is missing. Adding a new mode requires editing
``_build_parser`` plus the ``_MODES`` table; there is no second if/elif
chain to keep in sync.

Stdlib-only at module load.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

import vault_common

from cli.stats._common import _open_db, _get_console

from cli.stats.dashboard import run_dashboard
from cli.stats.graph import run_graph
from cli.stats.health import run_health
from cli.stats.operations import (
    run_hooks,
    run_pending,
    run_summarizer_progress,
    run_timeline,
)
from cli.stats.overview import (
    run_by_project,
    run_growth,
    run_stale,
    run_tags,
    run_top_linked,
)
from cli.stats.rollups import run_monthly, run_weekly
from cli.stats.summary import run_no_db_summary, run_summary


# ---------------------------------------------------------------------------
# QA-002: argparse + dispatch-table refactor of main()
# ---------------------------------------------------------------------------
#
# The ``--help`` epilog. Lifted verbatim from the original ``vault_stats.py``
# module docstring so ``vault-stats --help`` output is byte-identical after
# the ARC-005 package split (the parser used ``epilog=__doc__`` previously).
_HELP_EPILOG = """\
vault-stats — analytics over the Parsidion vault note_index database.

Modes (mutually exclusive; default is --health):
    --health               Composite vault health score (default mode)
    --summary              Count notes by folder and type
    --stale                List stale notes (is_stale = 1)
    --top-linked N         Top N most-linked notes (default: 10)
    --by-project           Count notes per project
    --growth N             Notes created per week for the last N weeks (default: 8)
    --tags                 Show tag cloud (top 30 most-used tags)
    --dashboard            Full-page analytics dashboard (combines all views)
    --pending              Show pending_summaries.jsonl queue stats
    --graph                Knowledge graph analytics (hubs, isolated, ratios)
    --hooks N              Show last N hook events from hook_events.log (default: 20)
    --weekly               Generate/preview weekly rollup note for current ISO week
    --monthly              Generate/preview monthly rollup note for current month
    --timeline N           Bar chart of notes created per day for last N days (default: 30)
    --summarizer-progress  Show current summarizer progress from ~/.claude/logs

``--json`` emits machine-readable output (currently applies to ``--health``;
other modes keep their native Rich/text rendering).

All modes read from the resolved vault's embeddings.db (note_index table).
Falls back to a plain-text walk when the DB is absent.
"""
# Previously ``main`` carried a ~120-line argparse block, a duplicated
# 14-flag ``no_mode`` enumeration, and two parallel if/elif chains over the
# same mode set (one for the conn-None branch, one for the conn-set branch).
# Adding a new mode meant editing all three places. The table below is a
# pure reorganisation: each mode is declared exactly once with its runner,
# whether it requires a live DB connection, and what to call instead when
# the DB is missing. Behaviour is identical to the prior inline dispatch.

# Maps mode name -> the argparse attribute that selects it. ``True`` flags
# are booleans (``store_true``); the rest are ``nargs="?"`` integers, so a
# mode is "selected" when the attribute is either True (a set store_true
# flag — an unset one is False and does NOT count) or a non-None non-bool
# value (an explicitly passed or const-defaulted integer).
_MODE_FLAGS: dict[str, str] = {
    "summary": "summary",
    "stale": "stale",
    "top_linked": "top_linked",
    "by_project": "by_project",
    "growth": "growth",
    "tags": "tags",
    "dashboard": "dashboard",
    "pending": "pending",
    "graph": "graph",
    "hooks": "hooks",
    "weekly": "weekly",
    "monthly": "monthly",
    "timeline": "timeline",
    "summarizer_progress": "summarizer_progress",
}


def _build_parser() -> argparse.ArgumentParser:
    """Construct the vault-stats CLI parser.

    Extracted from :func:`main` (QA-002) so the parser can be inspected and
    tested independently of argv side effects. Adding a new mode requires
    editing this function plus the ``_MODES`` table below — there is no
    second if/elif chain to keep in sync.
    """
    parser = argparse.ArgumentParser(
        prog="vault-stats",
        description="Vault analytics from the note_index database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_HELP_EPILOG,
    )

    parser.add_argument(
        "--vault",
        "-V",
        metavar="PATH|NAME",
        default=None,
        help="Vault path or named vault (default: ~/ParsidionVault, or legacy ~/ClaudeVault if it exists)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--health",
        action="store_true",
        default=False,
        help="Composite vault health score — the default mode when no flag is given (ENH-007)",
    )
    mode.add_argument(
        "--summary",
        "-s",
        action="store_true",
        default=False,
        help="Count notes by folder and type",
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
    mode.add_argument(
        "--pending",
        action="store_true",
        default=False,
        help="Show pending_summaries.jsonl queue stats",
    )
    mode.add_argument(
        "--graph",
        action="store_true",
        default=False,
        help="Knowledge graph analytics (hubs, isolated notes, linked ratio)",
    )
    mode.add_argument(
        "--hooks",
        metavar="N",
        nargs="?",
        const=20,
        type=int,
        help="Show last N hook events from hook_events.log (default: 20)",
    )
    mode.add_argument(
        "--weekly",
        action="store_true",
        default=False,
        help="Generate (or preview with --dry-run) weekly rollup note for current ISO week",
    )
    mode.add_argument(
        "--monthly",
        action="store_true",
        default=False,
        help="Generate (or preview with --dry-run) monthly rollup note for current month",
    )
    mode.add_argument(
        "--timeline",
        metavar="N",
        nargs="?",
        const=30,
        type=int,
        help="Bar chart of notes created per day for last N days (default: 30)",
    )
    mode.add_argument(
        "--summarizer-progress",
        action="store_true",
        default=False,
        help="Show current summarizer progress from /tmp",
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        default=False,
        help="Preview output without writing files (applies to --weekly and --monthly)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit JSON (currently applies to --health; other modes keep their native output)",
    )
    parser.add_argument(
        "--hooks-window",
        metavar="DAYS",
        type=int,
        default=7,
        help=("Aggregation window (days) for the --hooks latency table (default: 7)"),
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        default=False,
        help="Skip the metadata-quality scan in --health (faster on large vaults)",
    )
    return parser


# Each entry: (runner, strict_db, no_db_fallback).
# * runner(conn, args, vault) — always invoked with the resolved conn (which
#   may be None for db-optional modes; see ``strict_db``).
# * strict_db — True when the mode requires a live DB connection. When the
#   DB is absent, ``no_db_fallback`` (if any) is called instead; if there
#   is no fallback, main() exits with the canonical "DB not found" error.
# * no_db_fallback(vault) — called in lieu of runner when strict_db is True
#   and the DB is missing. Only summary/dashboard have one (run_no_db_summary).
RunnerFn = Callable[[sqlite3.Connection | None, argparse.Namespace, Path], None]
FallbackFn = Callable[[Path], None]
_ModeEntry = tuple[RunnerFn, bool, FallbackFn | None]

_MODES: dict[str, _ModeEntry] = {
    "summary": (
        lambda conn, args, vault: run_summary(conn),  # type: ignore[arg-type]
        True,
        run_no_db_summary,
    ),
    "dashboard": (
        lambda conn, args, vault: run_dashboard(conn),  # type: ignore[arg-type]
        True,
        run_no_db_summary,
    ),
    "stale": (
        lambda conn, args, vault: run_stale(conn),  # type: ignore[arg-type]
        True,
        None,
    ),
    "top_linked": (
        lambda conn, args, vault: run_top_linked(conn, args.top_linked),  # type: ignore[arg-type]
        True,
        None,
    ),
    "by_project": (
        lambda conn, args, vault: run_by_project(conn),  # type: ignore[arg-type]
        True,
        None,
    ),
    "growth": (
        lambda conn, args, vault: run_growth(conn, args.growth),  # type: ignore[arg-type]
        True,
        None,
    ),
    "tags": (
        lambda conn, args, vault: run_tags(conn, args.tags),  # type: ignore[arg-type]
        True,
        None,
    ),
    "graph": (
        lambda conn, args, vault: run_graph(conn),  # type: ignore[arg-type]
        True,
        None,
    ),
    # db-optional modes — runner accepts conn=None.
    "timeline": (
        lambda conn, args, vault: run_timeline(conn, args.timeline, vault),
        False,
        None,
    ),
    "weekly": (
        lambda conn, args, vault: run_weekly(dry_run=args.dry_run, vault=vault),
        False,
        None,
    ),
    "monthly": (
        lambda conn, args, vault: run_monthly(dry_run=args.dry_run, vault=vault),
        False,
        None,
    ),
    # No-DB modes — runner ignores conn entirely.
    "pending": (
        lambda conn, args, vault: run_pending(vault),
        False,
        None,
    ),
    "hooks": (
        lambda conn, args, vault: run_hooks(
            args.hooks, vault, window_days=args.hooks_window
        ),
        False,
        None,
    ),
    "summarizer_progress": (
        lambda conn, args, vault: run_summarizer_progress(),
        False,
        None,
    ),
}


def _selected_mode(args: argparse.Namespace) -> str | None:
    """Return the name of the mode requested on *args*, or ``None``.

    A mode is "selected" when its argparse attribute is either True (a set
    ``store_true`` flag — an unset flag is False and must NOT count as a
    selection) or a non-None non-bool value (the ``nargs="?"`` integer
    modes, whether passed explicitly or defaulted via ``const``).
    ``--health`` is intentionally NOT in this table: it is the
    bare-invocation default and is handled separately in :func:`main`.
    """
    for mode_name, attr in _MODE_FLAGS.items():
        value = getattr(args, attr)
        if value is True or (value is not None and not isinstance(value, bool)):
            return mode_name
    return None


def main() -> None:
    """CLI entry point for vault-stats."""
    parser = _build_parser()
    args = parser.parse_args()

    vault_path = vault_common.resolve_vault(explicit=args.vault, cwd=os.getcwd())
    conn = _open_db(vault_path)

    selected = _selected_mode(args)

    # --health is the bare-invocation default (ENH-007); a bare ``vault-stats``
    # call renders the health report rather than the summary table. ``--health``
    # is also explicitly selectable (it sits in the same mutually-exclusive
    # group as the other modes, so argparse guarantees at most one is set).
    if args.health or selected is None:
        run_health(vault_path, as_json=args.json, fast=args.fast)
        return

    runner, strict_db, no_db_fallback = _MODES[selected]

    if conn is None:
        if strict_db:
            if no_db_fallback is not None:
                no_db_fallback(vault_path)
                return
            _get_console().print(
                "[yellow]note_index DB not found — run update_index.py first.[/yellow]"
            )
            sys.exit(1)
        # db-optional mode with the DB absent: pass conn=None to the runner.
        runner(None, args, vault_path)
        return

    try:
        runner(conn, args, vault_path)
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover — entry shim re-exports main
    main()
