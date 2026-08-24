# /// script
# dependencies = ["numpy"]
# ///
"""Pre-compute similarity between vault notes and output a graph.json file.

Usage:
    uv run --no-project scripts/build_graph.py [OPTIONS]

This script reads note metadata and embeddings from the vault's embeddings.db,
computes pairwise cosine similarity, extracts wiki edges from related fields,
and writes a graph.json file for use by the vault visualizer.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import datetime
from pathlib import Path
from typing import IO

import numpy as np


# ENH-002: bump whenever the on-disk graph.json shape changes in a way that
# invalidates previously-computed edges. The incremental loader compares this
# against ``meta.schema_version`` and falls back to a full rebuild on mismatch.
# Reusing edges computed under different parameters is the single most likely
# way to ship a silently-wrong graph, so every such change must bump this.
GRAPH_SCHEMA_VERSION: int = 2


# ARC-038: machine-readable contract for the graph.json this script emits.
# Mirrors the canonical fixture tests/fixtures/graph.schema.json and the
# TypeScript GraphData interface (visualizer/lib/graph.ts). Kept as a plain
# stdlib dict (draft 2020-12) so no jsonschema dependency is required.
GRAPH_JSON_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://parsidion.local/schemas/graph.schema.json",
    "title": "GraphData",
    "description": (
        "Schema for vault graph.json emitted by build_graph.py and consumed "
        "by the TypeScript visualizer (visualizer/lib/graph.ts GraphData)."
    ),
    "type": "object",
    "additionalProperties": False,
    "required": ["meta", "nodes", "edges"],
    "properties": {
        "meta": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "generated",
                "note_count",
                "edge_count",
                "min_semantic_threshold",
                "schema_version",
                "include_daily",
                "max_neighbors",
            ],
            "properties": {
                "generated": {
                    "type": "string",
                    "description": "ISO-8601 UTC timestamp the graph was built.",
                },
                "note_count": {"type": "integer", "minimum": 0},
                "edge_count": {"type": "integer", "minimum": 0},
                "min_semantic_threshold": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
                "schema_version": {
                    "type": "integer",
                    "minimum": 1,
                    "description": (
                        "On-disk graph.json shape version (GRAPH_SCHEMA_VERSION "
                        "in build_graph.py). The incremental loader compares this "
                        "against its own constant and full-rebuilds on mismatch."
                    ),
                },
                "include_daily": {
                    "type": "boolean",
                    "description": (
                        "Whether Daily-folder notes are included in the node set. "
                        "Participates in the incremental compatibility check "
                        "because it changes which nodes exist."
                    ),
                },
                "max_neighbors": {
                    "type": "integer",
                    "minimum": 0,
                    "description": (
                        "Maximum semantic edges kept per note, strongest first "
                        "(ENH-001). 0 disables the cap and emits every pair "
                        "above min_semantic_threshold."
                    ),
                },
                "incremental": {
                    "type": "boolean",
                    "description": (
                        "True when this graph was produced by an incremental "
                        "rebuild (ENH-002). Absent on full-rebuild graphs."
                    ),
                },
                "parmem_body_links": {
                    "type": "integer",
                    "minimum": 0,
                    "description": (
                        "Wiki edges contributed by par-mem body-link enrichment. "
                        "Absent when enrichment was skipped or added nothing."
                    ),
                },
                "parmem_body_status": {
                    "type": "string",
                    "description": (
                        "Outcome of par-mem body-link enrichment when it was "
                        "attempted (present whenever --no-parmem was not passed). "
                        "'fresh' = index current, enrichment ran (count in "
                        "parmem_body_links when >=1); 'skipped:index-stale' / "
                        "'skipped:index-absent' / 'skipped:index-invalid' = a "
                        "non-fresh index made enrichment non-deterministic so it "
                        "was skipped; 'unavailable' / 'error' = backend failure."
                    ),
                },
            },
        },
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id",
                    "title",
                    "type",
                    "folder",
                    "path",
                    "tags",
                    "incoming_links",
                    "mtime",
                ],
                "properties": {
                    "id": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Note stem — the unique node identity.",
                    },
                    "title": {"type": "string"},
                    "type": {"type": "string"},
                    "folder": {"type": "string"},
                    "path": {
                        "type": "string",
                        "description": "Vault-relative path to the note file.",
                    },
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "incoming_links": {"type": "integer", "minimum": 0},
                    "mtime": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Note modification time as a unix timestamp.",
                    },
                },
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["s", "t", "w", "kind"],
                "properties": {
                    "s": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Source node id (stem).",
                    },
                    "t": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Target node id (stem).",
                    },
                    "w": {
                        "type": "number",
                        "description": (
                            "Edge weight. Semantic edges: cosine similarity in "
                            "[-1, 1] (writer emits >= min_semantic_threshold). "
                            "Wiki edges: 1.0."
                        ),
                    },
                    "kind": {"type": "string", "enum": ["semantic", "wiki"]},
                },
            },
        },
    },
}


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Pre-compute vault note similarity and output graph.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--include-daily",
        dest="include_daily",
        action="store_true",
        help="Include notes from the Daily folder (default; kept for backward compatibility)",
    )
    parser.add_argument(
        "--no-daily",
        dest="include_daily",
        action="store_false",
        help="Exclude notes from the Daily folder",
    )
    parser.set_defaults(include_daily=True)
    parser.add_argument(
        "--min-threshold",
        type=float,
        default=0.70,
        metavar="FLOAT",
        help="Minimum cosine similarity threshold for semantic edges (default: 0.70)",
    )
    parser.add_argument(
        "--max-neighbors",
        type=int,
        default=15,
        metavar="INT",
        help=(
            "Maximum semantic edges kept per note, strongest first (default: 15). "
            "Pass 0 to disable the cap and emit every pair above --min-threshold."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        metavar="PATH",
        help="Output path for graph.json (default: graph.json inside the vault root)",
    )
    parser.add_argument(
        "--vault",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Override VAULT_ROOT (default: $VAULT_ROOT env var, ~/ParsidionVault, "
            "or legacy ~/ClaudeVault if it exists)"
        ),
    )
    parser.add_argument(
        "--no-parmem",
        dest="use_parmem",
        action="store_false",
        help="Skip par-mem body-link enrichment of wiki edges",
    )
    parser.set_defaults(use_parmem=True)
    parser.add_argument(
        "--no-schema",
        dest="emit_schema",
        action="store_false",
        help="Skip writing graph.schema.json alongside graph.json",
    )
    parser.set_defaults(emit_schema=True)
    parser.add_argument(
        "--incremental",
        action="store_true",
        help=(
            "Reuse the previous graph.json and recompute only notes whose mtime "
            "changed since meta.generated. Falls back to a full rebuild if the "
            "previous graph is missing, unreadable, or was built with different "
            "parameters (schema_version, min_semantic_threshold, max_neighbors, "
            "or include_daily)."
        ),
    )
    return parser.parse_args()


def _default_vault_root() -> Path:
    current = Path.home() / "ParsidionVault"
    legacy = Path.home() / "ClaudeVault"
    if legacy.exists() and not current.exists():
        return legacy
    return current


def get_vault_root(args: argparse.Namespace) -> Path:
    """Resolve the vault root path."""
    if args.vault is not None:
        return args.vault.expanduser().resolve()
    env_vault = os.environ.get("VAULT_ROOT", "")
    if env_vault:
        return Path(env_vault).expanduser().resolve()
    return _default_vault_root().resolve()


def load_note_metadata(conn: sqlite3.Connection, include_daily: bool) -> list[dict]:
    """Load all rows from note_index table."""
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT stem, title, note_type, folder, tags, incoming_links, related, mtime, path
        FROM note_index
        """
    )
    rows = cursor.fetchall()
    notes = []
    for row in rows:
        stem, title, note_type, folder, tags, incoming_links, related, mtime, path = row
        if not include_daily and folder == "Daily":
            continue
        notes.append(
            {
                "stem": stem,
                "title": title or "",
                "type": note_type or "",
                "folder": folder or "",
                "tags": tags or "",
                "incoming_links": incoming_links or 0,
                "related": related or "",
                "mtime": mtime or 0,
                "path": path or "",
            }
        )
    return notes


def load_embeddings(conn: sqlite3.Connection, stems: set[str]) -> dict[str, np.ndarray]:
    """Load embeddings from note_embeddings table for the given stems."""
    cursor = conn.cursor()
    cursor.execute("SELECT stem, embedding FROM note_embeddings")
    rows = cursor.fetchall()

    stem_to_embedding: dict[str, np.ndarray] = {}
    for stem, blob in rows:
        if stem not in stems:
            continue
        if not blob:
            continue
        try:
            vec = np.frombuffer(blob, dtype=np.float32)
            if vec.shape[0] not in (384, 768):
                continue
            stem_to_embedding[stem] = vec
        except (ValueError, TypeError):
            continue
    return stem_to_embedding


def parse_tags(tags_str: str) -> list[str]:
    """Parse comma-separated tags string into a list."""
    if not tags_str:
        return []
    return [t.strip() for t in tags_str.split(",") if t.strip()]


def parse_related_stems(related_str: str) -> list[str]:
    """Extract stems from the related field (comma-separated stems or [[wikilinks]])."""
    if not related_str:
        return []
    # Support both wikilink format [[stem]] and bare comma-separated stems
    if "[[" in related_str:
        return re.findall(r"\[\[([^\]]+)\]\]", related_str)
    return [s.strip() for s in related_str.split(",") if s.strip()]


def _emit_top_k_edges(
    stems: list[str],
    sim_rows: np.ndarray,
    row_global_indices: list[int],
    candidate_cols: list[np.ndarray],
    min_threshold: float,
    seen: set[tuple[int, int]] | None = None,
) -> list[dict]:
    """Walk per-row candidate columns; emit semantic edges above threshold.

    Single shared walker so full and incremental modes cannot diverge — that
    divergence is exactly the bug class this repo already has in its two
    ``findNote`` copies and its two vault resolvers. ``sim_rows[i]`` is the
    similarity row for the stem at ``row_global_indices[i]``;
    ``candidate_cols[i]`` are the column indices to consider for that row.
    Pairs are deduped unordered on ``(min(gi, gj), max(gi, gj))`` global
    indices, so an edge selected by either endpoint is kept once.
    """
    own_seen = seen is None
    if own_seen:
        seen = set()
    edges: list[dict] = []
    for local_i, global_i in enumerate(row_global_indices):
        row = sim_rows[local_i]
        for j in candidate_cols[local_i]:
            j = int(j)
            if j == global_i:
                continue
            w = float(row[j])
            if w < min_threshold:
                continue
            a, b = (global_i, j) if global_i < j else (j, global_i)
            if (a, b) in seen:
                continue
            seen.add((a, b))
            edges.append(
                {"s": stems[a], "t": stems[b], "w": round(w, 4), "kind": "semantic"}
            )
    return edges


def _normalize_rows(embeddings_matrix: np.ndarray) -> np.ndarray:
    """L2-normalize each row; zero-rows are left as zero (not NaN)."""
    norms = np.linalg.norm(embeddings_matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return embeddings_matrix / norms


def build_semantic_edges(
    stems: list[str],
    embeddings_matrix: np.ndarray,
    min_threshold: float,
    max_neighbors: int = 15,
) -> list[dict]:
    """Semantic edges: each note keeps its strongest ``max_neighbors`` neighbours.

    A fixed similarity floor alone produces a degree distribution that scales
    with topical density, so densely-clustered note types dominate the edge
    count while sparse notes stay under-connected. Capping per node keeps both
    ends sane. Edges are undirected; a pair selected by either endpoint is
    kept once.
    """
    n = len(stems)
    if n == 0:
        return []

    normalized = _normalize_rows(embeddings_matrix)
    sim = normalized @ normalized.T  # shape (N, N)
    np.fill_diagonal(sim, -1.0)  # never select self

    if max_neighbors <= 0 or max_neighbors >= n:
        candidate_cols = [np.arange(n)] * n
    else:
        # argpartition is O(n) per row vs O(n log n) for a full sort.
        top_idx = np.argpartition(-sim, max_neighbors - 1, axis=1)[:, :max_neighbors]
        candidate_cols = list(top_idx)

    return _emit_top_k_edges(stems, sim, list(range(n)), candidate_cols, min_threshold)


# ---------------------------------------------------------------------------
# ENH-002: incremental rebuild
# ---------------------------------------------------------------------------


def load_previous_graph(path: Path, args: argparse.Namespace) -> dict | None:
    """Return the previous graph iff it is safely reusable, else None.

    Returning None means "do a full rebuild" — every failure mode collapses
    to that, because a wrong graph is worse than a slow one. The check covers
    schema_version, min_semantic_threshold, max_neighbors, and include_daily
    (the last changes which nodes exist, so it must participate).
    """
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(prev, dict):
        return None
    meta = prev.get("meta", {})
    if not isinstance(meta, dict):
        return None
    if meta.get("schema_version") != GRAPH_SCHEMA_VERSION:
        return None
    if meta.get("min_semantic_threshold") != args.min_threshold:
        return None
    if meta.get("max_neighbors") != args.max_neighbors:
        return None
    if meta.get("include_daily") != args.include_daily:
        return None
    if not meta.get("generated"):
        return None
    return prev


def compute_changed_stems(
    notes: list[dict], prev: dict
) -> tuple[set[str], set[str], set[str]]:
    """Return ``(changed, added, removed)`` stem sets relative to prev.

    ``changed`` is the recompute seed: notes whose mtime is after
    ``meta.generated`` (minus a 2 s safety margin for clock granularity
    between the indexer writing mtime and the graph writing ``generated``)
    plus newly-added notes. Caller extends this via :func:`expand_recompute_set`
    and :func:`extend_recompute_closure`.

    Node identity in prev is the ``id`` field (whose value is the stem — see
    main()'s node writer); the fresh-loaded ``notes`` use ``stem``.
    """
    prev_stems = {n["id"] for n in prev.get("nodes", [])}
    cur_stems = {n["stem"] for n in notes}
    added = cur_stems - prev_stems
    removed = prev_stems - cur_stems

    generated_str = prev.get("meta", {}).get("generated", "")
    cutoff = 0.0
    try:
        # ``generated`` is formatted "%Y-%m-%dT%H:%M:%SZ"; fromisoformat needs
        # the trailing Z as +00:00. Older graphs may carry other shapes — any
        # parse failure collapses to cutoff=0, which treats every note as
        # changed (a safe over-approximation that still produces a correct
        # graph, just at full-rebuild cost for the recompute set).
        generated = datetime.datetime.fromisoformat(
            generated_str.replace("Z", "+00:00")
        )
        cutoff = generated.timestamp() - 2.0  # 2 s safety margin
    except (ValueError, TypeError):
        cutoff = 0.0

    modified = {
        n["stem"]
        for n in notes
        if n["stem"] in prev_stems and float(n["mtime"] or 0) > cutoff
    }
    return modified | added, added, removed


def expand_recompute_set(changed: set[str], prev_edges: list[dict]) -> set[str]:
    """Seed the recompute set with every note sharing a semantic edge with a changed note.

    Top-K selection is relative: a new strong neighbour can evict an existing
    edge between two otherwise-unchanged notes. For *modified* notes this
    prev-edge closure is the cheap, correct seed — recompute them and their
    existing neighbours. Brand-new notes have no prev edges, so their forward
    neighbours are caught separately by :func:`extend_recompute_closure`.
    """
    out = set(changed)
    for e in prev_edges:
        if e.get("kind") != "semantic":
            continue
        s, t = e.get("s", ""), e.get("t", "")
        if s in changed:
            out.add(t)
        elif t in changed:
            out.add(s)
    return out


def extend_recompute_closure(
    stems: list[str],
    normalized: np.ndarray,
    seed: set[str],
    prev_semantic: list[dict],
    min_threshold: float,
    max_neighbors: int,
) -> set[str]:
    """Extend ``seed`` to the full recompute closure, iterated to a fixpoint.

    Two closure rules, applied to every newly-added member until no more
    notes appear:

    1. **Similarity forward** — for each member, compute its similarity row
       and add its top-K above ``min_threshold``. Catches the new-note
       eviction case :func:`expand_recompute_set` misses (a brand-new note
       has no prev edges, but its forward top-K must be recomputed so its
       neighbours' top-K lists — which it enters — are recomputed too).

    2. **Prev-edge** — for each member, add every note sharing a *previous*
       semantic edge with it. The merge in :func:`main` drops any prev edge
       whose endpoints are in the recompute set and re-emits via the
       recompute; for that to be correct **both** endpoints of every affected
       prev edge must be recomputed, otherwise an edge that exists solely
       because the non-recomputed endpoint selected the recomputed one is
       silently dropped. Without this rule a dense vault loses ~1% of its
       edges on every incremental rebuild — a silent divergence, exactly the
       bug class this enhancement exists to prevent.

    The two rules feed each other: similarity adds new members whose prev-edge
    partners must in turn be added, and those partners' similarity rows may
    add still more. Converges because the note set is finite; bounded by the
    prev-edge + top-K connected component reachable from ``seed``. On a dense
    vault (one giant component) this is ~all notes and the incremental
    degrades to full-rebuild cost — correct, just no longer cheaper. On a
    sparse vault, or a small change set in a topical cluster, the closure
    stays small and the |closure| × N saving over the N × N matrix is real.
    """
    n = len(stems)
    if n == 0 or not seed:
        return set(seed)
    idx_of = {s: i for i, s in enumerate(stems)}

    # Prev-edge adjacency (undirected). Used by rule 2.
    prev_adj: dict[str, set[str]] = {}
    for e in prev_semantic:
        s, t = e.get("s", ""), e.get("t", "")
        if not s or not t:
            continue
        prev_adj.setdefault(s, set()).add(t)
        prev_adj.setdefault(t, set()).add(s)

    closure = set(seed)
    pending = {s for s in seed if s in idx_of}
    while pending:
        current_stems = {s for s in pending if s in idx_of}
        pending.clear()

        # Rule 1: similarity forward closure for the pending members.
        current_rows = [idx_of[s] for s in current_stems]
        if current_rows:
            block = normalized[current_rows] @ normalized.T  # (|current|, N)
            for local_i, global_i in enumerate(current_rows):
                block[local_i, global_i] = -1.0  # never select self
            for local_i, global_i in enumerate(current_rows):
                row = block[local_i]
                if max_neighbors <= 0 or max_neighbors >= n:
                    cols = range(n)
                else:
                    cols = np.argpartition(-row, max_neighbors - 1)[:max_neighbors]
                for j in cols:
                    j = int(j)
                    if j == global_i:
                        continue
                    if float(row[j]) < min_threshold:
                        continue
                    neighbour = stems[j]
                    if neighbour not in closure:
                        closure.add(neighbour)
                        pending.add(neighbour)

        # Rule 2: prev-edge closure for the pending members. Ensures both
        # endpoints of every prev edge touching the closure are recomputed.
        for s in current_stems:
            for neighbour in prev_adj.get(s, ()):
                if neighbour in idx_of and neighbour not in closure:
                    closure.add(neighbour)
                    pending.add(neighbour)
    return closure


def build_semantic_edges_incremental(
    stems: list[str],
    normalized: np.ndarray,
    recompute: set[str],
    min_threshold: float,
    max_neighbors: int,
) -> tuple[list[dict], set[str]]:
    """Compute top-K semantic edges for ``recompute`` stems only.

    Returns ``(new_edges, recomputed_stems)``. Peak extra memory is
    ``|recompute| x N``, never ``N x N`` — the whole point of ENH-002. Walks
    the same shared :func:`_emit_top_k_edges` walker as the full build so the
    two modes cannot diverge.
    """
    n = len(stems)
    if n == 0 or not recompute:
        return [], set()
    idx_of = {s: i for i, s in enumerate(stems)}
    rows = [idx_of[s] for s in recompute if s in idx_of]
    if not rows:
        return [], set()

    sub = normalized[rows] @ normalized.T  # shape (|recompute|, N)
    for local_i, global_i in enumerate(rows):
        sub[local_i, global_i] = -1.0  # never select self

    if max_neighbors <= 0 or max_neighbors >= n:
        candidate_cols = [np.arange(n)] * len(rows)
    else:
        top_idx = np.argpartition(-sub, max_neighbors - 1, axis=1)[:, :max_neighbors]
        candidate_cols = list(top_idx)

    edges = _emit_top_k_edges(stems, sub, rows, candidate_cols, min_threshold)
    return edges, {stems[i] for i in rows}


def build_wiki_edges(notes: list[dict], valid_stems: set[str]) -> list[dict]:
    """Extract wiki edges from the related field of each note."""
    edges = []
    for note in notes:
        stem = note["stem"]
        targets = parse_related_stems(note["related"])
        for target in targets:
            if target == stem:
                continue
            if target not in valid_stems:
                continue
            # Normalize ordering: use lexicographic order so (a,b) == (b,a)
            s, t = (stem, target) if stem < target else (target, stem)
            edges.append({"s": s, "t": t, "w": 1.0, "kind": "wiki"})
    # Deduplicate identical wiki edges
    seen: set[tuple[str, str]] = set()
    deduped = []
    for edge in edges:
        key = (edge["s"], edge["t"])
        if key not in seen:
            seen.add(key)
            deduped.append(edge)
    return deduped


def build_parmem_body_edges(
    vault_root: Path,
    rel_path_to_stem: dict[str, str],
    existing_keys: set[tuple[str, str]],
) -> tuple[list[dict], str]:
    """Wiki edges from par-mem's in-body doc links + an outcome status.

    The index must be FRESH before its body links are trusted: a stale or
    mid-catch-up index returns a partial, run-to-run-variable link set, which
    would make two builds over identical input diverge. When the index is not
    fresh the nondeterministic ``doc_links_raw`` fetch is skipped entirely.

    Returns ``(edges, status)``. ``status`` is ``"fresh"`` when enrichment ran
    cleanly (edges may still be empty if par-mem found no new in-body links);
    otherwise a reason: ``skipped:index-stale`` / ``skipped:index-absent`` /
    ``skipped:index-invalid`` (non-fresh index), ``unavailable`` (backend off /
    unreachable), or ``error`` (doc-links fetch failed). Never raises and never
    breaks the build — on any failure the graph is byte-identical to the
    pre-integration output apart from the recorded ``status``.
    """
    try:
        import parmem_backend
    except ImportError:
        return [], "unavailable"
    try:
        if not parmem_backend.resolve_parmem_backend(vault_root):
            return [], "unavailable"
        is_fresh, state = parmem_backend.vault_index_fresh(vault_root)
        if not is_fresh:
            if state in ("stale", "absent", "invalid"):
                return [], f"skipped:index-{state}"
            return [], state  # "unavailable" / "error"
        links = parmem_backend.doc_links_raw(cwd=vault_root, vault=vault_root)
        if links is None:
            # Fresh index, but the fetch itself failed — already logged inside
            # doc_links_raw. Distinct from "ran cleanly, found nothing".
            return [], "error"
        edges = []
        seen = set(existing_keys)
        for link in links:
            src = rel_path_to_stem.get(link.get("source_path", ""))
            dst = rel_path_to_stem.get(link.get("target_path", ""))
            if not src or not dst or src == dst:
                continue
            s, t = (src, dst) if src < dst else (dst, src)
            if (s, t) in seen:
                continue
            seen.add((s, t))
            edges.append({"s": s, "t": t, "w": 1.0, "kind": "wiki"})
        return edges, "fresh"
    except Exception:  # noqa: BLE001 — enrichment must never break the build
        return [], "error"


def _open_tmp_exclusive(tmp_path: Path) -> IO[str]:
    """Open *tmp_path* for writing, creating it exclusively (SEC-005).

    ``O_EXCL`` refuses any existing entry — including a planted
    ``graph.json.tmp`` symlink, which a plain ``open(..., "w")`` would follow
    and clobber through the rename. A stale tmp from a crashed write is
    unlinked (unlink never follows a symlink) and the open retried once;
    a second failure means something is racing us and propagates. Mirrors
    ``core/vault_fs.atomic_write_text``.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(tmp_path, flags, 0o666)
    except FileExistsError:
        tmp_path.unlink()
        fd = os.open(tmp_path, flags, 0o666)
    return os.fdopen(fd, "w", encoding="utf-8")


def write_graph_json(graph: dict, output_path: Path) -> None:
    """Write graph.json via tmp + atomic replace.

    The visualizer live-reads this file, so a direct write could expose
    truncated JSON mid-write.
    """
    tmp_path = output_path.parent / (output_path.name + ".tmp")
    with _open_tmp_exclusive(tmp_path) as f:
        json.dump(graph, f, separators=(",", ":"))
    tmp_path.replace(output_path)


def _schema_path_for(graph_output: Path) -> Path:
    """Sibling graph.schema.json path for a given graph.json output path."""
    if graph_output.suffix == ".json":
        return graph_output.with_suffix(".schema.json")
    return graph_output.parent / "graph.schema.json"


def write_graph_schema(schema: dict, output_path: Path) -> None:
    """Write graph.schema.json via tmp + atomic replace (same pattern as graph.json)."""
    tmp_path = output_path.parent / (output_path.name + ".tmp")
    with _open_tmp_exclusive(tmp_path) as f:
        json.dump(schema, f, indent=2)
        f.write("\n")
    tmp_path.replace(output_path)


def main() -> None:
    """Main entry point."""
    args = parse_args()
    vault_root = get_vault_root(args)
    db_path = vault_root / "embeddings.db"

    if not db_path.exists():
        print(f"Error: embeddings.db not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    print("Loading note metadata...", end="", file=sys.stderr)
    conn = sqlite3.connect(str(db_path))
    try:
        notes = load_note_metadata(conn, args.include_daily)
    except sqlite3.OperationalError as e:
        print(f"\nError reading note_index: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"  ({len(notes)} notes in index)", file=sys.stderr)

    # Build set of stems from note_index
    index_stems = {n["stem"] for n in notes}

    # Load embeddings — only for stems in note_index
    print("Loading embeddings...", end="", file=sys.stderr, flush=True)
    try:
        stem_to_embedding = load_embeddings(conn, index_stems)
    except sqlite3.OperationalError as e:
        print(f"\nError reading note_embeddings: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()

    # Filter notes to those with embeddings
    filtered_notes = [n for n in notes if n["stem"] in stem_to_embedding]
    print(
        f"\nFiltering to {len(filtered_notes)} notes with embeddings...",
        file=sys.stderr,
    )

    if not filtered_notes:
        print("Error: no notes with valid embeddings found.", file=sys.stderr)
        sys.exit(1)

    valid_stems = {n["stem"] for n in filtered_notes}

    # Resolve output path before building matrices: the incremental path needs
    # to know where the previous graph lives, and the full path uses the same
    # path for writing.
    output_path = args.output if args.output is not None else vault_root / "graph.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build embedding matrix
    stems_ordered = [n["stem"] for n in filtered_notes]
    print(f"Loading {len(stems_ordered)} embeddings...", file=sys.stderr)
    embeddings_matrix = np.stack(
        [stem_to_embedding[s] for s in stems_ordered], axis=0
    ).astype(np.float32)
    # ENH-002: normalized rows are needed by both the full path (inside
    # build_semantic_edges) and the incremental path (for the closure expansion
    # and the |recompute| x N sub-matrix). Compute once, reuse.
    normalized = _normalize_rows(embeddings_matrix)

    n = len(stems_ordered)
    prev = load_previous_graph(output_path, args) if args.incremental else None
    incremental_meta: dict[str, object] = {}
    reused_semantic = 0
    if prev is not None:
        changed, added, removed = compute_changed_stems(filtered_notes, prev)
        prev_semantic = [
            e for e in prev.get("edges", []) if e.get("kind") == "semantic"
        ]
        seed = expand_recompute_set(changed, prev_semantic)
        recompute = extend_recompute_closure(
            stems_ordered,
            normalized,
            seed,
            prev_semantic,
            args.min_threshold,
            args.max_neighbors,
        )
        new_semantic, _ = build_semantic_edges_incremental(
            stems_ordered,
            normalized,
            recompute,
            args.min_threshold,
            args.max_neighbors,
        )
        # Keep previous semantic edges whose endpoints are both unchanged AND
        # neither was deleted. Recomputed pairs are dropped here and replaced
        # by ``new_semantic``; the union (kept ∪ new) is the new edge set.
        kept: list[dict] = []
        for e in prev_semantic:
            s, t = e.get("s", ""), e.get("t", "")
            if s in recompute or t in recompute:
                continue
            if s in removed or t in removed:
                continue
            kept.append(e)
        reused_semantic = len(kept)
        semantic_edges = kept + new_semantic
        incremental_meta = {"incremental": True}
        print(
            f"incremental: {len(changed)} changed ({len(added)} added, "
            f"{len(removed)} removed), {len(recompute)} recomputed, "
            f"{reused_semantic} edges reused, {len(new_semantic)} recomputed "
            f"[full vault: {n} notes]",
            file=sys.stderr,
        )
        # Drop the embeddings_matrix reference; the incremental path never
        # forms the N×N matrix, which is the whole memory/CPU win.
        del embeddings_matrix
    else:
        # Full rebuild (the default, or the fallback when the previous graph
        # is missing/unreadable/built under different parameters).
        if args.incremental:
            print(
                "incremental: previous graph not reusable — full rebuild",
                file=sys.stderr,
            )
        print(f"Computing {n}×{n} similarity matrix...", file=sys.stderr)
        print(
            f"Extracting semantic edges (threshold={args.min_threshold})...",
            end="",
            file=sys.stderr,
            flush=True,
        )
        semantic_edges = build_semantic_edges(
            stems_ordered,
            embeddings_matrix,
            args.min_threshold,
            args.max_neighbors,
        )
        print(f"  → {len(semantic_edges)} pairs", file=sys.stderr)

    # Wiki edges are always rebuilt from scratch — a frontmatter scan is cheap
    # (no matrix) and their correctness depends on the whole ``related`` graph,
    # so an incremental merge would only add risk for no measurable saving.
    print("Extracting wiki edges...", end="", file=sys.stderr, flush=True)
    wiki_edges = build_wiki_edges(filtered_notes, valid_stems)
    print(f"  → {len(wiki_edges)} pairs", file=sys.stderr)

    vault_root_str = str(vault_root) + "/"

    body_edges: list[dict] = []
    parmem_status = ""
    if args.use_parmem:
        print(
            "Enriching with par-mem body links...", end="", file=sys.stderr, flush=True
        )
        rel_path_to_stem = {}
        for note in filtered_notes:
            rel = note["path"]
            if rel.startswith(vault_root_str):
                rel = rel[len(vault_root_str) :]
            rel_path_to_stem[rel] = note["stem"]
        existing_keys = {(e["s"], e["t"]) for e in wiki_edges}
        body_edges, parmem_status = build_parmem_body_edges(
            vault_root, rel_path_to_stem, existing_keys
        )
        detail = (
            f"{len(body_edges)} pairs"
            if parmem_status == "fresh"
            else f"skipped ({parmem_status})"
        )
        print(f"  → {detail}", file=sys.stderr)

    all_edges = semantic_edges + wiki_edges + body_edges
    total_edges = len(all_edges)

    # Build nodes list (always from current note_index rows so removed notes
    # disappear naturally; the merge above already dropped their edges).
    nodes = []
    for note in filtered_notes:
        rel_path = note["path"]
        if rel_path.startswith(vault_root_str):
            rel_path = rel_path[len(vault_root_str) :]
        nodes.append(
            {
                "id": note["stem"],
                "title": note["title"],
                "type": note["type"],
                "folder": note["folder"],
                "path": rel_path,
                "tags": parse_tags(note["tags"]),
                "incoming_links": note["incoming_links"],
                "mtime": note["mtime"],
            }
        )

    # Build output. meta carries schema_version/include_daily/max_neighbors so
    # the next incremental run can validate compatibility (and any mismatch
    # collapses to a full rebuild — see load_previous_graph).
    graph = {
        "meta": {
            "generated": datetime.datetime.now(datetime.UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "note_count": len(nodes),
            "edge_count": total_edges,
            "min_semantic_threshold": args.min_threshold,
            "schema_version": GRAPH_SCHEMA_VERSION,
            "include_daily": args.include_daily,
            "max_neighbors": args.max_neighbors,
            **incremental_meta,
            **({"parmem_body_links": len(body_edges)} if body_edges else {}),
            **({"parmem_body_status": parmem_status} if parmem_status else {}),
        },
        "nodes": nodes,
        "edges": all_edges,
    }

    print("Writing graph.json...", file=sys.stderr)
    write_graph_json(graph, output_path)

    if args.emit_schema:
        schema_path = _schema_path_for(output_path)
        print("Writing graph.schema.json...", file=sys.stderr)
        write_graph_schema(GRAPH_JSON_SCHEMA, schema_path)

    print(
        f"Done: {len(nodes)} nodes, {total_edges} edges → {output_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
