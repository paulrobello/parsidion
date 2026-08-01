"""stats — focused submodules extracted from vault_stats.py (ARC-005).

The original 1,341-line ``vault_stats.py`` God module exposed 18 display
modes plus a QA-002 dispatch table. ARC-005 decomposes it into a package of
focused submodules behind the same proven ``doctor/`` layout, while the
``vault_stats.py`` script remains as a thin re-export shim and CLI entry
point (``vault-stats = "vault_stats:main"`` in ``pyproject.toml``) so every
``import vault_stats`` consumer and test attribute access
(``vault_stats._open_db``, ``vault_stats.run_pending``,
``vault_stats.vault_metrics``, …) keeps working byte-for-byte.

Submodule layout:
    _common     — shared console + DB helpers (_get_console, _open_db,
                  _fetch_all, _collect_tags).
    summary     — run_summary + run_no_db_summary (the DB / no-DB fallback
                  pair for the same logical view).
    overview    — run_stale, run_top_linked, run_by_project, run_growth,
                  run_tags (table-style vault overviews).
    dashboard   — run_dashboard (composite full-page view).
    graph       — run_graph (knowledge-graph analytics).
    operations  — run_pending, run_hooks, run_timeline,
                  run_summarizer_progress (operational / activity views).
    rollups     — run_weekly, run_monthly (note generation; shared logic).
    health      — run_health (delegates to the vault_health scoring module).
    cli         — _build_parser, _MODE_FLAGS, _MODES, _selected_mode, main
                  (QA-002 dispatch table, lifted unchanged).

Behaviour is identical to the original — this is a pure structural move.
The ``rich`` dependency stays lazy-imported inside each display function so
the package remains importable without ``rich`` installed.
"""

from __future__ import annotations
