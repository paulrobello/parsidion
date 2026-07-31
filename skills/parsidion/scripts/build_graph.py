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

import numpy as np


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
            ],
            "properties": {
                "generated": {"type": "string"},
                "note_count": {"type": "integer", "minimum": 0},
                "edge_count": {"type": "integer", "minimum": 0},
                "min_semantic_threshold": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
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
                "parmem_body_links": {
                    "type": "integer",
                    "minimum": 0,
                    "description": (
                        "Wiki edges contributed by par-mem body-link enrichment. "
                        "Absent when enrichment was skipped or added nothing."
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
                    "id": {"type": "string", "minLength": 1},
                    "title": {"type": "string"},
                    "type": {"type": "string"},
                    "folder": {"type": "string"},
                    "path": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "incoming_links": {"type": "integer", "minimum": 0},
                    "mtime": {"type": "number", "minimum": 0},
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
                    "s": {"type": "string", "minLength": 1},
                    "t": {"type": "string", "minLength": 1},
                    "w": {"type": "number"},
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
    # L2-normalize each row
    norms = np.linalg.norm(embeddings_matrix, axis=1, keepdims=True)
    # Avoid division by zero
    norms = np.where(norms == 0, 1.0, norms)
    normalized = embeddings_matrix / norms

    n = len(stems)
    if n == 0:
        return []

    # Compute full similarity matrix
    sim = normalized @ normalized.T  # shape (N, N)
    np.fill_diagonal(sim, -1.0)  # never select self

    if max_neighbors <= 0 or max_neighbors >= n:
        candidate_cols = [np.arange(n)] * n
    else:
        # argpartition is O(n) per row vs O(n log n) for a full sort.
        top_idx = np.argpartition(-sim, max_neighbors - 1, axis=1)[:, :max_neighbors]
        candidate_cols = list(top_idx)

    seen: set[tuple[int, int]] = set()
    edges: list[dict] = []
    for i in range(n):
        for j in candidate_cols[i]:
            j = int(j)
            if j == i:
                continue
            w = float(sim[i, j])
            if w < min_threshold:
                continue
            a, b = (i, j) if i < j else (j, i)
            if (a, b) in seen:
                continue
            seen.add((a, b))
            edges.append(
                {"s": stems[a], "t": stems[b], "w": round(w, 4), "kind": "semantic"}
            )
    return edges


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
) -> list[dict]:
    """Wiki edges from par-mem's in-body doc links (frontmatter pass can't see them).

    Never raises and returns [] whenever par-mem is unavailable or fails, so the
    graph build is byte-identical to the pre-integration output in every
    degraded case.
    """
    try:
        import parmem_backend
    except ImportError:
        return []
    try:
        if not parmem_backend.resolve_parmem_backend(vault_root):
            return []
        links = parmem_backend.doc_links_raw(cwd=vault_root, vault=vault_root)
        if not links:
            return []
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
        return edges
    except Exception:  # noqa: BLE001 — enrichment must never break the build
        return []


def write_graph_json(graph: dict, output_path: Path) -> None:
    """Write graph.json via tmp + atomic replace.

    The visualizer live-reads this file, so a direct write could expose
    truncated JSON mid-write.
    """
    tmp_path = output_path.parent / (output_path.name + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
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
    with open(tmp_path, "w", encoding="utf-8") as f:
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

    # Build embedding matrix
    stems_ordered = [n["stem"] for n in filtered_notes]
    print(f"Loading {len(stems_ordered)} embeddings...", file=sys.stderr)
    embeddings_matrix = np.stack(
        [stem_to_embedding[s] for s in stems_ordered], axis=0
    ).astype(np.float32)

    # Compute similarity matrix
    n = len(stems_ordered)
    print(f"Computing {n}×{n} similarity matrix...", file=sys.stderr)

    print(
        f"Extracting semantic edges (threshold={args.min_threshold})...",
        end="",
        file=sys.stderr,
        flush=True,
    )
    semantic_edges = build_semantic_edges(
        stems_ordered, embeddings_matrix, args.min_threshold, args.max_neighbors
    )
    print(f"  → {len(semantic_edges)} pairs", file=sys.stderr)

    print("Extracting wiki edges...", end="", file=sys.stderr, flush=True)
    wiki_edges = build_wiki_edges(filtered_notes, valid_stems)
    print(f"  → {len(wiki_edges)} pairs", file=sys.stderr)

    vault_root_str = str(vault_root) + "/"

    body_edges: list[dict] = []
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
        body_edges = build_parmem_body_edges(
            vault_root, rel_path_to_stem, existing_keys
        )
        print(f"  → {len(body_edges)} pairs", file=sys.stderr)

    all_edges = semantic_edges + wiki_edges + body_edges
    total_edges = len(all_edges)

    # Build nodes list
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

    # Build output
    graph = {
        "meta": {
            "generated": datetime.datetime.now(datetime.UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "note_count": len(nodes),
            "edge_count": total_edges,
            "min_semantic_threshold": args.min_threshold,
            "max_neighbors": args.max_neighbors,
            **({"parmem_body_links": len(body_edges)} if body_edges else {}),
        },
        "nodes": nodes,
        "edges": all_edges,
    }

    # Ensure output directory exists
    output_path = args.output if args.output is not None else vault_root / "graph.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

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
