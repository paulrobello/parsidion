"""vault_search MCP tool — semantic and metadata modes."""

from __future__ import annotations

import json
from pathlib import Path

import parsight_backend
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
    min_score: float = 0.45,
    vault: str | None = None,
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
        vault: Optional vault reference (name from vaults.yaml, or absolute path).
            When None, the resolver's default precedence applies. ARC-021:
            multi-vault users can target a specific vault instead of always
            hitting the default.

    Returns:
        JSON array of note objects.

    Raises:
        ValueError: If the embeddings DB is missing and parsight cannot serve
            the query either (semantic mode).
    """
    # ARC-021: resolve the optional vault reference once and pass it through
    # to both the embeddings DB path check and the underlying search/query.
    resolved_vault: Path | None = None
    if vault is not None:
        resolved_vault = vault_common.resolve_vault(explicit=vault)

    if query is not None:
        db_path = vault_common.get_embeddings_db_path(resolved_vault)
        if not db_path.exists() and not parsight_backend.resolve_parsight_backend():
            # ARC-008: Raise instead of returning a sentinel error string
            raise ValueError(
                "embeddings DB not found and parsight unavailable -- "
                "run rebuild_index first, or install/start parsight"
            )
        results = _vault_search_module.search(
            query, top=top_k, min_score=min_score, vault=resolved_vault
        )
    else:
        results = _vault_search_module.query(
            tag=tag,
            folder=folder,
            note_type=note_type,
            project=project,
            recent_days=recent_days,
            limit=top_k,
            vault=resolved_vault,
        )

    return json.dumps(results, default=str, indent=2)
