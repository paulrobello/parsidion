"""Shared backlink helpers for Parsidion vault note cross-referencing.

This module provides utilities for finding related vault notes (by tag overlap
or semantic similarity) and injecting bidirectional wikilinks into note
frontmatter.  It is stdlib-only and can be imported by any vault script
without introducing third-party dependencies.

Extracted from ``summarize_sessions.py`` to enable reuse by both the
summarizer and ``parsidion-mcp``.
"""

import re
import subprocess
from pathlib import Path

import vault_common

__all__ = [
    "find_related_by_tags",
    "find_related_by_semantic",
    "inject_related_links",
    "add_backlinks_to_existing",
]


def find_related_by_tags(
    new_note_path: Path,
    new_tags: list[str],
    max_links: int = 5,
    vault_notes: list[Path] | None = None,
    vault: Path | None = None,
) -> list[str]:
    """Find existing vault notes that share tags with a new note.

    Args:
        new_note_path: Path to the newly written note (excluded from results).
        new_tags: Tags from the new note's frontmatter.
        max_links: Maximum number of related note wikilinks to return.
        vault_notes: Pre-collected list of vault note paths.  When ``None``
            (default), calls ``vault_common.all_vault_notes()``.  Callers
            that already have the list should pass it to avoid a redundant
            vault walk.  See ARC-010.
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        List of ``"[[stem]]"`` wikilink strings for the top matching notes,
        sorted by tag-overlap score descending.
    """
    if not new_tags:
        return []

    new_tag_set = set(new_tags)
    candidates: list[tuple[int, Path]] = []
    notes = (
        vault_notes if vault_notes is not None else vault_common.all_vault_notes(vault)
    )

    for note_path in notes:
        # Skip the note itself and daily notes
        if note_path == new_note_path:
            continue
        if note_path.parts and "Daily" in note_path.parts:
            continue

        try:
            content = note_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        fm = vault_common.parse_frontmatter(content)
        existing_tags = fm.get("tags")
        if not isinstance(existing_tags, list):
            continue

        overlap = len(new_tag_set & {str(t) for t in existing_tags})
        if overlap >= 1:
            candidates.append((overlap, note_path))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [f"[[{p.stem}]]" for _, p in candidates[:max_links]]


def find_related_by_semantic(
    new_note_path: Path,
    vault_root: Path | None = None,
    max_links: int = 5,
    tag_strs: list[str] | None = None,
    vault: Path | None = None,
) -> list[str]:
    """Find related vault notes using semantic search via vault_search.py subprocess.

    Returns an empty list when vault_search.py or embeddings.db is missing,
    or when the subprocess fails for any reason.

    Args:
        new_note_path: Path to the newly written note (excluded from results).
        vault_root: Deprecated alias for vault. Use vault instead.
        max_links: Maximum number of related note wikilinks to return.
        tag_strs: Already-parsed tag strings from the note's frontmatter. When
            provided, avoids re-reading the note file.
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        List of ``"[[stem]]"`` wikilink strings, sorted by semantic similarity.
    """
    import json as _json

    # Support legacy vault_root parameter
    vault = vault or vault_root or vault_common.resolve_vault()

    vault_search_script = Path(__file__).parent / "vault_search.py"
    if not vault_search_script.exists():
        return []

    db_path = vault_common.get_embeddings_db_path(vault)
    if not db_path.exists():
        return []

    # Build query from stem and tags of the new note.
    # Use caller-supplied tag_strs when available to avoid re-reading the file.
    if tag_strs is None:
        try:
            content = new_note_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
        fm = vault_common.parse_frontmatter(content)
        note_tags = fm.get("tags") or []
        if not isinstance(note_tags, list):
            note_tags = []
        tag_strs = [str(t) for t in note_tags]

    tag_part = " ".join(tag_strs)
    query = f"{new_note_path.stem.replace('-', ' ')} {tag_part}".strip()

    try:
        result = subprocess.run(
            [
                "uv",
                "run",
                "--no-project",
                str(vault_search_script),
                query,
                "--top",
                str(max_links + 1),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            env=vault_common.env_without_claudecode(),
        )
        if result.returncode != 0:
            return []
        items: list[dict[str, object]] = _json.loads(result.stdout)
    except (
        subprocess.TimeoutExpired,
        FileNotFoundError,
        OSError,
        _json.JSONDecodeError,
    ):
        return []

    links: list[str] = []
    for item in items:
        stem = str(item.get("stem", ""))
        if not stem or stem == new_note_path.stem:
            continue
        links.append(f"[[{stem}]]")
        if len(links) >= max_links:
            break

    return links


# Regex that matches the entire `related` field including block-style entries.
# Matches:
#   Inline:  related: ["[[a]]", "[[b]]"]
#   Block:   related:\n  - "[[a]]"\n  - "[[b]]"
# Also handles mixed inline+block (the corruption this fix prevents).
_RELATED_FIELD_RE = re.compile(
    r"^related:\s*(?:\[.*?\])?\s*$(?:\n^[ \t]+-[ \t].*$)*",
    re.MULTILINE,
)


def inject_related_links(note_path: Path, new_links: list[str]) -> None:
    """Merge new wikilinks into the ``related`` frontmatter field of a note.

    Replaces the entire ``related`` field (inline or block-style) with a clean
    inline quoted array: ``related: ["[[a]]", "[[b]]"]``.

    Self-referencing wikilinks (links back to the note itself) are filtered out.

    Args:
        note_path: Path to the note to update.
        new_links: Wikilinks to add (e.g. ``["[[note-a]]", "[[note-b]]"]``).
    """
    try:
        content = note_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return

    fm = vault_common.parse_frontmatter(content)
    existing_related = fm.get("related") or []
    if not isinstance(existing_related, list):
        existing_related = []
    # Normalise existing entries to strings
    existing_strs: list[str] = [str(r) for r in existing_related]

    # Deduplicate existing + new, preserving order
    merged = list(dict.fromkeys(existing_strs + new_links))

    # Remove self-references
    self_ref = f"[[{note_path.stem}]]"
    merged = [m for m in merged if m != self_ref]

    if merged == existing_strs:
        # Nothing new to add
        return

    # Build the replacement line using inline quoted array format
    quoted_items = ", ".join(f'"{lnk}"' for lnk in merged)
    new_related_line = f"related: [{quoted_items}]"

    # Replace the entire related field (inline or block-style)
    if _RELATED_FIELD_RE.search(content):
        updated = _RELATED_FIELD_RE.sub(new_related_line, content, count=1)
    else:
        # Insert before the closing --- of frontmatter
        updated = content.replace("\n---\n", f"\n{new_related_line}\n---\n", 1)

    try:
        note_path.write_text(updated, encoding="utf-8")
    except OSError:
        pass


def add_backlinks_to_existing(
    new_note_path: Path,
    related_notes: list[str],
    vault_notes: list[Path] | None = None,
    vault: Path | None = None,
) -> list[Path]:
    """Add a backlink to ``new_note_path`` in each of the ``related_notes``.

    For each wikilink in ``related_notes``, locates the corresponding note
    file in the vault and calls ``inject_related_links()`` to add a
    back-reference to ``new_note_path``.

    Args:
        new_note_path: Path to the newly written note.
        related_notes: List of ``"[[stem]]"`` wikilinks for existing notes.
        vault_notes: Pre-collected list of vault note paths.  When ``None``
            (default), calls ``vault_common.all_vault_notes()``.  Callers
            that already have the list should pass it to avoid a redundant
            vault walk.  See ARC-010.
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        List of Paths that were modified.
    """
    new_link = f"[[{new_note_path.stem}]]"
    modified: list[Path] = []

    # Build a stem -> path index from all vault notes once
    notes = (
        vault_notes if vault_notes is not None else vault_common.all_vault_notes(vault)
    )
    stem_index: dict[str, Path] = {}
    for note_path in notes:
        stem_index[note_path.stem] = note_path

    for wikilink in related_notes:
        # Extract stem from [[stem]]
        stem_match = re.match(r"^\[\[(.+)\]\]$", wikilink)
        if not stem_match:
            continue
        stem = stem_match.group(1)
        target_path = stem_index.get(stem)
        if target_path is None or target_path == new_note_path:
            continue
        inject_related_links(target_path, [new_link])
        modified.append(target_path)

    return modified
