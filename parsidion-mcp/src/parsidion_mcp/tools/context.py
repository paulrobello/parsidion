"""vault_context MCP tool — session-start-style vault context."""

from pathlib import Path

import vault_common


def _build_compact_index(notes: list[Path], max_chars: int = 4000) -> str:
    """Build a compact one-line-per-note index.

    Format matches session_start_hook.build_compact_index():
      - [[stem]] Title (folder) — `tag1` `tag2`

    Args:
        notes: Ordered list of note Paths to include.
        max_chars: Maximum total characters before truncating.

    Returns:
        Formatted compact index string, or a "no notes" message if empty.
    """
    if not notes:
        return "No vault notes available."

    vault_root = vault_common.VAULT_ROOT
    lines: list[str] = []
    total = 0

    for path in notes:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = vault_common.parse_frontmatter(content)
        title = vault_common.extract_title(content, path.stem)
        tags = fm.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        tag_str = " ".join(f"`{t}`" for t in tags) if tags else ""
        folder = path.parent.name if path.parent != vault_root else "root"
        entry = f"- [[{path.stem}]] {title} ({folder})"
        if tag_str:
            entry += f" — {tag_str}"
        total += len(entry) + 1
        if total > max_chars:
            remaining = len(notes) - len(lines)
            lines.append(
                f"- ... ({remaining} more notes, use parsidion-cc skill to browse)"
            )
            break
        lines.append(entry)

    if not lines:
        return "No vault notes available."

    header = (
        "**Available vault notes** (compact index — "
        "use `parsidion-cc` skill to load full content):\n"
    )
    return header + "\n".join(lines)


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

    return _build_compact_index(notes)
