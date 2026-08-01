#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "rich>=13.0",
# ]
# ///
"""vault-stats — analytics over the Parsidion vault note_index database.

Thin re-export shim over the ``cli.stats`` package (ARC-005). The original
1,341-line God module was decomposed into focused submodules (one per mode
group) behind the proven ``doctor/`` layout; every public + private symbol
the original exposed remains importable from ``vault_stats`` so existing
``import vault_stats`` consumers, test attribute access
(``vault_stats._open_db``, ``vault_stats.run_pending``,
``vault_stats.vault_metrics``), and the ``vault-stats`` console-script entry
point keep working byte-for-byte.

Stdlib-only at module load (``rich`` is lazy-imported inside the display
functions). Run with:
    uv run --no-project ~/.claude/skills/parsidion/scripts/vault_stats.py
    vault-stats --summary       # via the installed console script
    vault-stats --health        # default mode
    vault-stats --dashboard
    vault-stats --timeline 30
"""

# Standard-library imports are re-exported so tests that monkeypatch the
# shared module objects keep working (mirrors the vault_doctor.py shim).
import argparse  # noqa: F401 — re-exported for tests
import os  # noqa: F401 — re-exported for tests
import sqlite3  # noqa: F401 — re-exported for tests
import sys  # noqa: F401 — re-exported for tests
from collections.abc import Callable  # noqa: F401 — re-exported for tests
from pathlib import Path  # noqa: F401 — re-exported for tests

import vault_common  # noqa: F401 — re-exported (vault_stats.vault_common)
import vault_metrics  # noqa: F401 — re-exported (vault_stats.vault_metrics)

# ---------------------------------------------------------------------------
# Shared console + DB helpers — cli.stats._common
# ---------------------------------------------------------------------------
from cli.stats._common import (  # noqa: F401 — re-exports
    _collect_tags,
    _fetch_all,
    _get_console,
    _open_db,
)

# ---------------------------------------------------------------------------
# Mode runners — one re-export line per cli.stats submodule, grouped to
# mirror the package layout. Every run_* the original exposed is here.
# ---------------------------------------------------------------------------
from cli.stats.summary import run_no_db_summary, run_summary  # noqa: F401 — re-exports
from cli.stats.overview import (  # noqa: F401 — re-exports
    run_by_project,
    run_growth,
    run_stale,
    run_tags,
    run_top_linked,
)
from cli.stats.dashboard import run_dashboard  # noqa: F401 — re-export
from cli.stats.graph import run_graph  # noqa: F401 — re-export
from cli.stats.operations import (  # noqa: F401 — re-exports
    run_hooks,
    run_pending,
    run_summarizer_progress,
    run_timeline,
)
from cli.stats.rollups import run_monthly, run_weekly  # noqa: F401 — re-exports
from cli.stats.health import run_health  # noqa: F401 — re-export

# ---------------------------------------------------------------------------
# QA-002 dispatch table + parser — cli.stats.cli
# ---------------------------------------------------------------------------
from cli.stats.cli import (  # noqa: F401 — re-exports
    _MODES,
    _MODE_FLAGS,
    _build_parser,
    _selected_mode,
)

# CLI entry point — imported last so the submodule graph is fully populated
# before main() can be invoked. ``if __name__ == "__main__": main()`` below
# keeps this file invocable as ``uv run --no-project vault_stats.py …``.
from cli.stats.cli import main  # noqa: E402,F401 — script entry point


if __name__ == "__main__":
    main()
