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

import time
from pathlib import Path

from core.vault_adaptive import (
    decay_factor,
    effective_score,
    load_usefulness_scores,
)
from core.vault_config import get_config
from core.vault_index import (
    SessionIndexSnapshot,
    all_vault_notes,
    parse_frontmatter,
    query_note_index,
)

from .graph_retrieval import _graph_neighbors

# Default cap on the AI-selector candidate pool. The prompt embeds 6-line
# summaries under an 8000-char budget; on large vaults the unranked pool
# (project + recent + neighbours) can reach hundreds of notes, so without a
# cap the selector only ever saw an arbitrary prefix.
_DEFAULT_AI_CANDIDATES_MAX = 48

_PROJECT_MATCH_SCORE = 30.0
_GRAPH_NEIGHBOUR_SCORE = 15.0
_RECENCY_WINDOW_DAYS = 10.0
_RECENCY_MAX_SCORE = 10.0
_USEFULNESS_MAX_SCORE = 10
_HUBNESS_STEP = 5
_HUBNESS_MAX_SCORE = 5


def _build_candidates(
    project_name: str,
    vault_path: Path,
    graph_meta: dict[str, dict[str, object]] | None = None,
    graph_expand_max: int = 0,
    max_candidates: int | None = None,
    snapshot: SessionIndexSnapshot | None = None,
) -> list[Path]:
    """Collect, rank, and prune the AI-mode candidate pool.

    Collection: project notes first (SQLite ``note_index`` when available,
    filesystem walk otherwise), then 1-hop graph neighbours of the project
    seeds spliced in after the project prefix, then other recent notes by
    mtime.

    Ranking: candidates are scored Python-side so the selector's prompt
    carries the *best* subset rather than an arbitrary prefix — on large
    vaults the raw pool reaches hundreds of notes while the prompt budget
    holds only ~50 six-line summaries. Signals, strongest first: project
    match, graph adjacency to the project seeds, adaptive usefulness
    (hit/miss history), recency (10-day linear window), and hubness
    (``incoming_links`` from graph metadata, when present). Ties break to
    newer mtime, then path, so the order is deterministic.

    Pruning: *max_candidates* caps the ranked pool (0 = keep all ranked
    notes; ``None`` falls back to ``_DEFAULT_AI_CANDIDATES_MAX``). The
    ``session_start_hook.ai_candidates_max`` config key feeds this.

    Args:
        project_name: The current project name (used to prioritize notes).
        vault_path: The vault root path.
        graph_meta: Optional output of ``vault_index.load_graph_metadata()``.
        graph_expand_max: Max graph neighbours to splice in (0 = disabled).
        max_candidates: Cap on the ranked pool (0 = unlimited, None = default).
        snapshot: PRF-104 -- the run's shared ``note_index`` snapshot. When
            given, both pool queries are served from it instead of opening two
            more read-only connections.

    Returns:
        Ranked, pruned list of note paths.
    """
    if not isinstance(max_candidates, int) or max_candidates < 0:
        max_candidates = _DEFAULT_AI_CANDIDATES_MAX

    # ARC-011: Try SQLite first for project notes (O(1) index lookup)
    if snapshot is not None:
        db_project_notes: list[Path] | None = snapshot.paths_where(
            project=project_name, limit=500
        )
        db_recent_notes: list[Path] | None = snapshot.paths_where(
            recent_days=30, limit=500
        )
    else:
        db_project_notes = query_note_index(
            project=project_name, limit=500, vault=vault_path
        )
        db_recent_notes = query_note_index(recent_days=30, limit=500, vault=vault_path)

    if db_project_notes is not None and db_recent_notes is not None:
        # SQLite path: fast, no file reads needed for candidate list
        project_set = set(str(p) for p in db_project_notes)
        other_notes = [p for p in db_recent_notes if str(p) not in project_set]
        base = db_project_notes + other_notes
        prefix_len = len(db_project_notes)
        seeds = db_project_notes
    else:
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
        project_set = set(str(p) for p in project_notes)
        base = project_notes + [p for _, p in other_notes_with_mtime]
        prefix_len = len(project_notes)
        seeds = project_notes

    # Phase 3 splice: 1-hop neighbours of the project seeds, after the
    # project prefix. Computed once here so the same set feeds the scorer.
    neighbours: list[Path] = []
    if graph_meta and graph_expand_max > 0 and seeds:
        neighbours = _graph_neighbors(
            seeds, graph_meta, vault_path, graph_expand_max, snapshot=snapshot
        )
    existing = {str(p) for p in base}
    fresh = [n for n in neighbours if str(n) not in existing]
    enriched = base[:prefix_len] + fresh + base[prefix_len:]

    return _rank_candidates(
        enriched,
        project_set,
        {str(n) for n in neighbours},
        graph_meta,
        max_candidates,
        vault=vault_path,
    )


def _rank_candidates(
    candidates: list[Path],
    project_paths: set[str],
    neighbour_paths: set[str],
    graph_meta: dict[str, dict[str, object]] | None,
    max_candidates: int,
    vault: Path | None = None,
) -> list[Path]:
    """Score, de-duplicate, order, and cap the AI-mode candidate pool.

    Scoring is deliberately cheap (no embeddings, no AI): one usefulness JSON
    load, one ``stat`` per note, and graph metadata lookups when the index
    exists. See :func:`_build_candidates` for the signal weights.

    Args:
        vault: Vault root the candidates belong to (ARC-101); used for the
            ``adaptive_context.decay_days`` config lookup.
    """
    usefulness = load_usefulness_scores()
    now = time.time()
    decay_days = get_config("adaptive_context", "decay_days", 30, vault=vault)

    def score_and_mtime(note: Path) -> tuple[float, float]:
        """Compute the (ranking score, mtime) pair for a candidate note."""
        key = str(note)
        score = _PROJECT_MATCH_SCORE if key in project_paths else 0.0
        if key in neighbour_paths:
            score += _GRAPH_NEIGHBOUR_SCORE
        stats = usefulness.get(note.stem)
        if isinstance(stats, dict):
            net = int(stats.get("hits", 0) or 0) - int(stats.get("misses", 0) or 0)
            net = float(max(0, min(net, _USEFULNESS_MAX_SCORE)))
            # ENH-016: decay the net-usefulness term by time since the
            # note's last positive use.
            score += net * decay_factor(stats, now, decay_days)
        try:
            mtime = note.stat().st_mtime
        except OSError:
            mtime = 0.0
        age_days = max(0.0, (now - mtime) / 86400.0)
        if age_days < _RECENCY_WINDOW_DAYS:
            score += _RECENCY_MAX_SCORE * (1.0 - age_days / _RECENCY_WINDOW_DAYS)
        meta = graph_meta.get(note.stem) if graph_meta else None
        if meta:
            raw = meta.get("incoming_links", 0)
            if isinstance(raw, (int, float)):
                score += min(int(raw) // _HUBNESS_STEP, _HUBNESS_MAX_SCORE)
        return score, mtime

    scored: list[tuple[float, float, str, Path]] = []
    seen: set[str] = set()
    for note in candidates:
        key = str(note)
        if key in seen:
            continue
        seen.add(key)
        score, mtime = score_and_mtime(note)
        scored.append((-score, -mtime, key, note))
    scored.sort(key=lambda item: (item[0], item[1], item[2]))
    ranked = [item[3] for item in scored]
    return ranked[:max_candidates] if max_candidates > 0 else ranked


def _rank_by_usefulness(notes: list[Path], vault: Path | None = None) -> list[Path]:
    """Re-rank *notes* by decayed usefulness score (adaptive context #17).

    Notes with a positive hit/miss ratio float to the top; notes that were
    repeatedly injected but never referenced sink toward the bottom — and,
    since ENH-016, keep sinking the longer they go unused: the Laplace
    ratio is multiplied by a half-life decay over ``adaptive_context.decay_days``
    (default 30; ``0`` disables it and reproduces the pre-decay order).
    A hit resets the clock, so usefulness recovers on use. Notes with no
    recorded stats keep their original relative order (stable sort).

    Args:
        notes: Candidate note paths in their current order.
        vault: Vault root the notes belong to (ARC-101); used for the
            ``adaptive_context.decay_days`` config lookup.

    Returns:
        Re-ranked list of the same paths.
    """
    scores = load_usefulness_scores()
    now = time.time()
    decay_days = get_config("adaptive_context", "decay_days", 30, vault=vault)

    def _score(path: Path) -> float:
        """Decayed Laplace-smoothed usefulness score in [0, 1] for *path*."""
        entry = scores.get(path.stem)
        if not entry:
            return 0.5  # Neutral score for new notes
        return effective_score(entry, now, decay_days)

    return sorted(notes, key=_score, reverse=True)
