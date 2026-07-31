"""vault_metrics -- compatibility shim (ARC-004).

Implementation moved to ``core.vault_metrics``. This shim re-exports the
module's complete non-dunder surface so every existing caller --
``import vault_metrics``, ``from vault_metrics import X``, ``vault_metrics.X`` (hooks, CLIs,
tests, parsidion-mcp, the installer) -- keeps working unchanged,
including imported constants and test monkeypatch targets. The
stdlib-only constraint is enforced on ``core.vault_metrics`` by
``tests/test_stdlib_only.py``.
"""

from core.vault_metrics import (  # noqa: F401 -- full-surface re-export
    Path,
    UTC,
    annotations,
    collect_by_project,
    collect_dashboard,
    collect_dead_letters,
    collect_graph,
    collect_growth,
    collect_hooks,
    collect_no_db_summary,
    collect_pending,
    collect_stale,
    collect_summarizer_progress,
    collect_summary,
    collect_tags,
    collect_timeline,
    collect_top_linked,
    datetime,
    fetch_all,
    json,
    open_db,
    sqlite3,
    time,
    vault_common,
)
