"""vault_context MCP tool — session-start-style vault context."""

from pathlib import Path

import vault_common


def vault_context(
    project: str | None = None,
    recent_days: int = 3,
    verbose: bool = False,
) -> str:
    """Return vault context for injection into a system prompt.

    Mirrors the session_start_hook context format. Compact one-line index by
    default; full summaries when *verbose* is True.

    Args:
        project: Project name to prioritize notes for.
        recent_days: Include notes modified within this many days.
        verbose: When True, return full note summaries instead of compact index.

    Returns:
        Context string ready for system prompt injection.
    """
    notes: list[Path] = []
    seen: set[Path] = set()

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
