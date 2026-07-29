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
    a multi-vault caller can target a specific named vault. The underlying
    ``find_notes_by_project`` / ``find_recent_notes`` / ``build_*`` helpers
    in vault_index.py read the module-level VAULT_ROOT, so we swap it for
    the duration of this call (and restore it on exit — MCP servers are
    long-lived and one tool call must not poison the next).

    Args:
        project: Project name to prioritize notes for.
        recent_days: Include notes modified within this many days.
        verbose: When True, return full note summaries instead of compact index.
        vault: Optional vault reference (name from vaults.yaml, or absolute path).
            When None, the resolver's default precedence applies.

    Returns:
        Context string ready for system prompt injection.
    """
    notes: list[Path] = []
    seen: set[Path] = set()

    # ARC-021: temporarily override VAULT_ROOT so the vault_index helpers
    # (which read it as a module global) see the caller's explicit vault.
    # save/restore on exit keeps the long-lived MCP server's globals stable.
    saved_root = vault_common.VAULT_ROOT
    if vault is not None:
        try:
            vault_common.VAULT_ROOT = vault_common.resolve_vault(explicit=vault)
            # Resolve_vault is cached; clear the cache so the new value
            # propagates to any helper that calls resolve_vault() internally.
            vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
        except Exception:
            vault_common.VAULT_ROOT = saved_root
            raise
    try:
        if project:
            for p in vault_common.find_notes_by_project(project):
                if p not in seen:
                    notes.append(p)
                    seen.add(p)

        for p in vault_common.find_recent_notes(recent_days):
            if p not in seen:
                notes.append(p)
                seen.add(p)

        if not notes:
            return "No relevant vault notes found."

        if verbose:
            return vault_common.build_context_block(notes)

        return vault_common.build_compact_index(notes)
    finally:
        if vault is not None:
            vault_common.VAULT_ROOT = saved_root
            vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
