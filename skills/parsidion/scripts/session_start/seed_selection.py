"""Candidate building and adaptive usefulness re-ranking for seed selection.

Extracted from ``session_start_hook.py`` (ARC-006).

``_build_candidates`` collects the AI-mode candidate pool (project notes first,
then graph-spliced neighbours, then other recent notes by mtime).  It tries
the SQLite ``note_index`` first (O(1) project lookup) and falls back to a full
filesystem walk when the database is absent.

``_rank_by_usefulness`` is the adaptive-context #17 rerank: notes with a
positive hit/miss ratio float to the top; repeatedly-injected-but-never-used
notes sink toward the bottom.  Both are leaf helpers — none of their callees
are test-monkeypatched, so they extract cleanly.
"""

from __future__ import annotations

from pathlib import Path

from vault_adaptive import load_usefulness_scores
from vault_index import all_vault_notes, parse_frontmatter, query_note_index

from .graph_retrieval import _enrich_with_graph


def _build_candidates(
    project_name: str,
    vault_path: Path,
    graph_meta: dict[str, dict[str, object]] | None = None,
    graph_expand_max: int = 0,
) -> list[Path]:
    """Collect candidate vault notes for AI selection.

    Returns project-specific notes first, then all other notes sorted by
    most recently modified.

    ARC-011: Uses ``query_note_index()`` (SQLite) first for fast project
    matching without reading every file.  Falls back to the full filesystem
    walk when the database is absent or the table is missing.

    When *graph_meta* is supplied with a positive *graph_expand_max*, 1-hop
    wikilink neighbours of the project notes are spliced in immediately after
    the project-notes prefix (Phase 3).  This is the AI-mode equivalent of
    Tier 1 expansion: it widens the pool the selector reads so graph-related
    prior art lands inside the prompt's character window.  Tier 2 rerank does
    NOT apply here -- the selector ranks the pool itself.

    Args:
        project_name: The current project name (used to prioritize notes).
        vault_path: The vault root path.
        graph_meta: Optional output of ``vault_index.load_graph_metadata()``.
        graph_expand_max: Max graph neighbours to splice in (0 = disabled).

    Returns:
        Ordered list of note paths; project notes first, then graph neighbours,
        then other notes by mtime.
    """
    # ARC-011: Try SQLite first for project notes (O(1) index lookup)
    db_project_notes = query_note_index(project=project_name, limit=500)
    db_recent_notes = query_note_index(recent_days=30, limit=500)

    if db_project_notes is not None and db_recent_notes is not None:
        # SQLite path: fast, no file reads needed for candidate list
        project_set = set(str(p) for p in db_project_notes)
        other_notes = [p for p in db_recent_notes if str(p) not in project_set]
        return _enrich_with_graph(
            db_project_notes + other_notes,
            len(db_project_notes),
            db_project_notes,
            graph_meta,
            vault_path,
            graph_expand_max,
        )

    # Fallback: full filesystem walk (when embeddings.db is absent)
    all_notes = all_vault_notes(vault=vault_path)
    project_lower = project_name.lower()

    project_notes: list[Path] = []
    other_notes_with_mtime: list[tuple[float, Path]] = []

    for note_path in all_notes:
        try:
            content = note_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = parse_frontmatter(content)
        proj_val = fm.get("project")
        if isinstance(proj_val, str) and proj_val.lower() == project_lower:
            project_notes.append(note_path)
        else:
            try:
                mtime = note_path.stat().st_mtime
            except OSError:
                mtime = 0.0
            other_notes_with_mtime.append((mtime, note_path))

    other_notes_with_mtime.sort(key=lambda x: x[0], reverse=True)
    return _enrich_with_graph(
        project_notes + [p for _, p in other_notes_with_mtime],
        len(project_notes),
        project_notes,
        graph_meta,
        vault_path,
        graph_expand_max,
    )


def _rank_by_usefulness(notes: list[Path]) -> list[Path]:
    """Re-rank *notes* by usefulness score (adaptive context #17).

    Notes with a positive hit/miss ratio float to the top; notes that were
    repeatedly injected but never referenced sink toward the bottom.  Notes
    with no recorded stats keep their original relative order (stable sort).

    Args:
        notes: Candidate note paths in their current order.

    Returns:
        Re-ranked list of the same paths.
    """
    scores = load_usefulness_scores()

    def _score(path: Path) -> float:
        """Return a Laplace-smoothed usefulness score in [0, 1] for *path*.

        Looks up the note stem in the loaded usefulness scores and computes
        ``(hits + 1) / (total + 2)``.  Returns 0.5 (neutral) for notes with
        no recorded history.
        """
        entry = scores.get(path.stem)
        if not entry:
            return 0.5  # Neutral score for new notes
        hits: int = entry.get("hits", 0)
        misses: int = entry.get("misses", 0)
        total = hits + misses
        if total == 0:
            return 0.5
        # Simple Laplace-smoothed ratio: (hits+1) / (total+2)
        return (hits + 1) / (total + 2)

    return sorted(notes, key=_score, reverse=True)
