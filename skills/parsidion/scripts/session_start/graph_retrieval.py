"""Graph retrieval: Tier 1 neighbour expansion + Tier 2 tag/hubness rerank.

Extracted from ``session_start_hook.py`` (ARC-006).

The vault maintains a bidirectional wikilink graph in the ``related``
frontmatter field (written by ``vault_links.py``); these functions turn it
on at retrieval time.  Tier 1 expands the seed selection with 1-hop
neighbours; Tier 2 stable-re-ranks the result by seed-cluster tag overlap
and hubness (``incoming_links``).  The same primitives back the AI-mode
candidate-pool enrichment.

None of these functions are test-monkeypatched, so they extract cleanly to a
leaf submodule.  The entry shim re-exports every symbol so
``session_start_hook._graph_neighbors`` etc. keep resolving.
"""

from __future__ import annotations

from pathlib import Path

from vault_config import get_config
from vault_index import parse_related_stems
from vault_path import is_path_inside_vault

# Graph retrieval (Tier 1 expansion + Tier 2 rerank).  The note_index wikilink
# graph is maintained by vault_links.py but was historically never traversed at
# retrieval time; these defaults turn it on.  Expansion only ever *adds* notes
# and the output is char-budget capped, so the blast radius is small and both
# disable cleanly via config (session_start_hook.graph_expand / graph_rerank).
_DEFAULT_GRAPH_EXPAND: bool = True
_DEFAULT_GRAPH_EXPAND_MAX: int = 8
_DEFAULT_GRAPH_RERANK: bool = True


def _enrich_with_graph(
    base: list[Path],
    prefix_len: int,
    seed_notes: list[Path],
    graph_meta: dict[str, dict[str, object]] | None,
    vault_path: Path,
    max_add: int,
) -> list[Path]:
    """Splice 1-hop graph neighbours of *seed_notes* into *base* after the
    project-notes prefix (Phase 3 AI-mode enrichment).

    Neighbours already present in *base* are not duplicated.  No-op when graph
    metadata is unavailable or *max_add* is non-positive.
    """
    if not graph_meta or max_add <= 0:
        return base
    neighbours = _graph_neighbors(seed_notes, graph_meta, vault_path, max_add)
    if not neighbours:
        return base
    existing = {str(p) for p in base}
    fresh = [n for n in neighbours if str(n) not in existing]
    if not fresh:
        return base
    return base[:prefix_len] + fresh + base[prefix_len:]


def _graph_neighbors(
    seed_paths: list[Path],
    meta_map: dict[str, dict[str, object]] | None,
    vault_path: Path,
    max_add: int,
) -> list[Path]:
    """Tier 1: return up to *max_add* 1-hop wikilink neighbours of *seed_paths*.

    The vault already maintains a bidirectional wikilink graph in the
    ``related`` frontmatter field (written by ``vault_links.py``); this turns
    it on at retrieval time.  A note is a neighbour when a seed links to it
    (outgoing) or it links to a seed (incoming), so the neighbourhood is
    complete even for notes authored before backlink injection existed.  Seeds
    themselves are excluded.  Resolved neighbour paths must exist and reside
    inside *vault_path* (the SEC-005 path-containment guard).  When the cap is
    binding, the best-connected neighbours (highest ``incoming_links``) survive.

    Args:
        seed_paths: Already-selected note paths (excluded from results).
        meta_map: Output of ``vault_index.load_graph_metadata()``, or ``None``.
        vault_path: Vault root for path-containment validation.
        max_add: Maximum number of neighbour paths to return.

    Returns:
        List of neighbour Paths, possibly empty.
    """
    if not meta_map or max_add <= 0:
        return []

    parse_related = parse_related_stems
    related_sets: dict[str, set[str]] = {
        stem: set(parse_related(str(meta.get("related", ""))))
        for stem, meta in meta_map.items()
    }

    seed_stems = {p.stem for p in seed_paths}
    neighbour_stems: set[str] = set()
    for seed in seed_paths:
        stem = seed.stem
        # Outgoing: stems this seed declares.
        neighbour_stems |= related_sets.get(stem, set())
        # Incoming: notes that declare this seed.
        for other, rels in related_sets.items():
            if stem in rels:
                neighbour_stems.add(other)

    neighbour_stems -= seed_stems

    vault_root = vault_path.resolve()
    candidates: list[tuple[int, str, Path]] = []
    for stem in neighbour_stems:
        meta = meta_map.get(stem)
        if not meta:
            continue
        path = Path(str(meta.get("path", "")))
        try:
            if not path.exists() or not is_path_inside_vault(path, vault_root):
                continue
        except OSError:
            continue
        raw_incoming = meta.get("incoming_links", 0)
        incoming = int(raw_incoming) if isinstance(raw_incoming, (int, float)) else 0
        candidates.append((incoming, stem, path))

    # Best-connected first; deterministic tie-break by stem.
    candidates.sort(key=lambda c: (-c[0], c[1]))
    return [path for _, _, path in candidates[:max_add]]


def _rank_by_graph(
    notes: list[Path],
    seed_paths: list[Path],
    meta_map: dict[str, dict[str, object]] | None,
) -> list[Path]:
    """Tier 2: stable re-rank of *notes* using graph signals.

    Primary key: shares at least one tag with the seed cluster (the tags of
    the pre-expansion selection).  Secondary key: ``incoming_links`` (hubness).
    Python's ``sorted`` is stable, so notes with no graph signal keep their
    prior relative order.  Tag-overlap-primary means the intentionally
    selected seeds stay near the top and char-budget truncation cuts low-signal
    expansion neighbours rather than displacing relevant notes.

    The cluster is derived from *seed_paths* (the pre-expansion snapshot) so
    expansion neighbours cannot vote themselves up via their own tags.

    Args:
        notes: Candidate note paths to re-rank.
        seed_paths: The pre-expansion seed notes defining the tag cluster.
        meta_map: Output of ``vault_index.load_graph_metadata()``, or ``None``.

    Returns:
        Re-ranked list of the same paths.
    """
    if not meta_map or not notes:
        return notes

    def _tags(stem: str) -> set[str]:
        meta = meta_map.get(stem)
        if not meta:
            return set()
        raw = str(meta.get("tags", ""))
        return {t.strip() for t in raw.split(",") if t.strip()} if raw else set()

    cluster_tags: set[str] = set()
    for seed in seed_paths:
        cluster_tags |= _tags(seed.stem)

    def _key(note: Path) -> tuple[int, int]:
        meta = meta_map.get(note.stem)
        if not meta:
            return (0, 0)
        shares = 1 if (cluster_tags & _tags(note.stem)) else 0
        raw_incoming = meta.get("incoming_links", 0)
        incoming = int(raw_incoming) if isinstance(raw_incoming, (int, float)) else 0
        return (shares, incoming)

    return sorted(notes, key=_key, reverse=True)


def _apply_graph_retrieval(
    all_notes: list[Path],
    seen: set[Path],
    graph_meta: dict[str, dict[str, object]] | None,
    vault_path: Path,
    adaptive_enabled: bool,
) -> list[Path]:
    """Tier 1: expand the seed set with 1-hop wikilink neighbours.

    Tier 2: re-rank by seed-cluster tag overlap + hubness. Then an adaptive
    usefulness rerank when enabled. The seed snapshot is captured BEFORE
    expansion so the Tier 2 cluster reflects the intentional selection, not
    the added neighbours. ``seen`` is mutated in place so neighbour dedup
    shares the seed set's resolved-path index.
    """
    seed_snapshot: list[Path] = list(all_notes)

    if (
        get_config("session_start_hook", "graph_expand", _DEFAULT_GRAPH_EXPAND)
        and graph_meta is not None
    ):
        max_add = get_config(
            "session_start_hook", "graph_expand_max", _DEFAULT_GRAPH_EXPAND_MAX
        )
        for note in _graph_neighbors(seed_snapshot, graph_meta, vault_path, max_add):
            resolved = note.resolve()
            if resolved not in seen:
                seen.add(resolved)
                all_notes.append(note)

    if (
        get_config("session_start_hook", "graph_rerank", _DEFAULT_GRAPH_RERANK)
        and graph_meta is not None
        and all_notes
    ):
        all_notes = _rank_by_graph(all_notes, seed_snapshot, graph_meta)

    if adaptive_enabled and all_notes:
        # Late import: seed_selection imports graph_retrieval at top level for
        # _enrich_with_graph, so importing it at module top here would create
        # a circular load. By the time _apply_graph_retrieval runs, every
        # submodule is fully loaded.
        from .seed_selection import _rank_by_usefulness

        all_notes = _rank_by_usefulness(all_notes)

    return all_notes
