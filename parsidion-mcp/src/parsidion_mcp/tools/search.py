"""vault_search MCP tool — semantic and metadata modes."""

import json

import vault_common

# vault_search.py mutates sys.path at import time (adds its own directory).
# This is an intentional design of the standalone script; the side effect is
# benign here — it ensures vault_common remains resolvable at runtime.
import vault_search as _vault_search_module


def vault_search(
    query: str | None = None,
    tag: str | None = None,
    folder: str | None = None,
    note_type: str | None = None,
    project: str | None = None,
    recent_days: int | None = None,
    top_k: int = 10,
    min_score: float = 0.35,
) -> str:
    """Search vault notes using semantic or metadata mode.

    Semantic mode is used when *query* is provided; metadata mode otherwise.

    Args:
        query: Natural language query (enables semantic search).
        tag: Filter by exact tag token.
        folder: Filter by folder name.
        note_type: Filter by note type.
        project: Filter by project name.
        recent_days: Only notes modified within this many days.
        top_k: Maximum number of results.
        min_score: Minimum cosine similarity threshold (semantic mode only).

    Returns:
        JSON array of note objects, or an ERROR string on failure.
    """
    if query is not None:
        db_path = vault_common.get_embeddings_db_path()
        if not db_path.exists():
            return "ERROR: embeddings DB not found — run rebuild_index first"
        results = _vault_search_module.search(query, top=top_k, min_score=min_score)
    else:
        results = _vault_search_module.query(
            tag=tag,
            folder=folder,
            note_type=note_type,
            project=project,
            recent_days=recent_days,
            limit=top_k,
        )

    return json.dumps(results, default=str, indent=2)
