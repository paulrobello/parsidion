"""vault_context MCP tool — session-start-style vault context."""

from __future__ import annotations

from pathlib import Path

import vault_common


def vault_context(
    project: str | None = None,
    recent_days: int = 3,
    verbose: bool = False,
    vault: str | None = None,
) -> str:
    """Return vault context for injection into a system prompt.

    Mirrors the session_start_hook context format. Compact one-line index by
    default; full summaries when *verbose* is True.

    ARC-021: *vault* threads through to ``resolve_vault(explicit=vault)`` so
    a multi-vault caller can target a specific named vault.

    SEC-032: the vault is resolved once and passed explicitly to the
    ``find_notes_by_project`` / ``find_recent_notes`` / ``build_*`` helpers.
    The previous implementation swapped the module-global ``VAULT_ROOT``
    (and cleared the resolver cache) for the duration of the call — two
    concurrent multi-vault ``vault_context`` calls could interleave the
    swap/restore and read the wrong vault.

    Args:
        project: Project name to prioritize notes for.
        recent_days: Include notes modified within this many days.
        verbose: When True, return full note summaries instead of compact index.
        vault: Optional vault reference (name from vaults.yaml, or absolute path).
            When None, the resolver's default precedence applies.

    Returns:
        Context string ready for system prompt injection.
    """
    vault_root = vault_common.resolve_vault(explicit=vault) if vault else None

    notes: list[Path] = []
    seen: set[Path] = set()

    if project:
        for p in vault_common.find_notes_by_project(project, vault=vault_root):
            if p not in seen:
                notes.append(p)
                seen.add(p)

    for p in vault_common.find_recent_notes(recent_days, vault=vault_root):
        if p not in seen:
            notes.append(p)
            seen.add(p)

    if not notes:
        return "No relevant vault notes found."

    if verbose:
        return vault_common.build_context_block(notes)

    return vault_common.build_compact_index(notes, vault=vault_root)
