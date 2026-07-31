"""vault_health -- compatibility shim (ARC-004 / ENH-007).

Implementation moved to ``core.vault_health``. This shim re-exports the
module's complete non-dunder surface so every existing caller --
``import vault_health``, ``from vault_health import X``, ``vault_health.X``
(``vault_stats``, the MCP server, hooks, tests) -- keeps working unchanged.
The stdlib-only constraint is enforced on ``core.vault_health`` by
``tests/test_stdlib_only.py``.
"""

from core.vault_health import (  # noqa: F401 -- full-surface re-export
    DIMENSION_WEIGHTS,
    DimensionScore,
    HealthReport,
    Path,
    _age_score,
    _build_warnings,
    _grade_for,
    _graph_meta,
    _levenshtein1,
    _parse_iso_z,
    _tag_pairs_near_duplicate,
    asdict,
    compute_health_report,
    field,
    json,
    render_report,
    score_embedding_coverage,
    score_file_hygiene,
    score_graph_connectivity,
    score_index_freshness,
    score_metadata_quality,
    score_queue_health,
    score_tag_hygiene,
    sqlite3,
    stat,
    time,
    to_json,
    to_json_dict,
    vault_common,
    vault_metrics,
)
