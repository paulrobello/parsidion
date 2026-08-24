"""Note lookup by absolute path or stem name (ARC-005).

Extracted from ``vault_merge.py``. Re-exported by the entry shim so
``vault_merge._find_note`` keeps resolving for tests and other callers.

Stdlib-only at module load.
"""

from __future__ import annotations

from pathlib import Path

import vault_common
from vault_path import is_path_inside_vault


def _find_note(query: str, vault_path: Path) -> Path | None:
    """Locate a vault note by absolute path or stem name.

    If ``query`` is an absolute path that exists, return it directly.
    Otherwise walk all vault notes and return the first whose stem matches
    ``query`` (case-insensitive).

    SEC-011: a resolved candidate that exists but sits outside the vault
    root (absolute path like ``/etc/hosts``, or a ``../`` relative) raises
    :class:`LookupError` — the caller reports "outside vault" and exits
    instead of reading and merging a file the vault does not own.

    Args:
        query: Absolute path string or stem name.
        vault_path: Path to the vault root.

    Returns:
        Matching Path, or None if not found.

    Raises:
        LookupError: If the query resolves to an existing path outside the
            vault root.
    """
    candidate = Path(query)
    if candidate.is_absolute() and candidate.exists():
        if not is_path_inside_vault(candidate, vault_path):
            raise LookupError(f"note path is outside the vault: {query}")
        return candidate

    # Relative path: try relative to vault root
    if not candidate.is_absolute():
        vault_candidate = vault_path / query
        if vault_candidate.exists():
            if not is_path_inside_vault(vault_candidate, vault_path):
                raise LookupError(f"note path is outside the vault: {query}")
            return vault_candidate
        # Add .md if missing
        if not query.endswith(".md"):
            vault_candidate_md = vault_path / (query + ".md")
            if vault_candidate_md.exists():
                if not is_path_inside_vault(vault_candidate_md, vault_path):
                    raise LookupError(f"note path is outside the vault: {query}")
                return vault_candidate_md

    # Stem search across all vault notes
    query_lower = query.lower().removesuffix(".md")
    for path in vault_common.all_vault_notes_walk(vault=vault_path):
        if path.stem.lower() == query_lower:
            return path
    return None
