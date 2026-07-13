"""code_search MCP tool — par-mem code-memory bridge (optional backend)."""

import json
from pathlib import Path

# Resolvable via the editable `parsidion` dependency, the same mechanism
# tools/search.py uses for vault_common / vault_search.
import parmem_backend


def code_search(
    query: str,
    repo_path: str | None = None,
    top_k: int = 10,
) -> str:
    """Search a par-mem-indexed repository's code graph by natural language.

    Delegates to parsidion's optional par-mem backend (external Rust CLI +
    local daemon). With *repo_path*, the raw par-mem code hits (repo-relative
    ``file_path`` + RRF ``score``) are returned verbatim for that repository.
    Without it, the resolved vault is searched and results are parsidion
    note objects (identical to ``vault_search``'s semantic results).

    Unlike the hook/CLI surfaces, this tool raises instead of degrading
    silently — MCP callers can choose another tool.

    Args:
        query: Natural language query (error strings work well).
        repo_path: Optional absolute path to a repository par-mem has indexed.
        top_k: Maximum number of results.

    Returns:
        JSON array of result objects.

    Raises:
        ValueError: par-mem unavailable (not installed, daemon down, or
            disabled via config), nonexistent *repo_path*, or a failed query.
    """
    if not parmem_backend.resolve_parmem_backend():
        raise ValueError(
            "par-mem unavailable -- install par-mem and start its daemon (see docs/PAR-MEM.md)"
        )
    if repo_path is None:
        results = parmem_backend.parmem_search(query, top_k=top_k)
    else:
        repo = Path(repo_path).expanduser()
        if not repo.is_dir():
            raise ValueError(f"repo_path does not exist: {repo_path}")
        results = parmem_backend.find_code_raw(query, top_k=top_k, cwd=repo)
    if results is None:
        raise ValueError(
            "par-mem query failed -- check `par-mem repos --json` and the daemon log"
        )
    return json.dumps(results, default=str, indent=2)
