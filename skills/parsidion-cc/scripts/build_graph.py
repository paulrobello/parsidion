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
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


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
        help="Override VAULT_ROOT (default: $VAULT_ROOT env var or ~/ClaudeVault)",
    )
    return parser.parse_args()


def get_vault_root(args: argparse.Namespace) -> Path:
    """Resolve the vault root path."""
    if args.vault is not None:
        return args.vault.expanduser().resolve()
    env_vault = os.environ.get("VAULT_ROOT", "")
    if env_vault:
        return Path(env_vault).expanduser().resolve()
    return Path("~/ClaudeVault").expanduser().resolve()


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
) -> list[dict]:
    """Compute pairwise cosine similarity and return edges above threshold."""
    # L2-normalize each row
    norms = np.linalg.norm(embeddings_matrix, axis=1, keepdims=True)
    # Avoid division by zero
    norms = np.where(norms == 0, 1.0, norms)
    normalized = embeddings_matrix / norms

    # Compute full similarity matrix
    sim = normalized @ normalized.T  # shape (N, N)

    n = len(stems)
    edges = []
    # Extract upper triangle (i < j)
    for i in range(n):
        for j in range(i + 1, n):
            w = float(sim[i, j])
            if w >= min_threshold:
                edges.append(
                    {
                        "s": stems[i],
                        "t": stems[j],
                        "w": round(w, 4),
                        "kind": "semantic",
                    }
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
        stems_ordered, embeddings_matrix, args.min_threshold
    )
    print(f"  → {len(semantic_edges)} pairs", file=sys.stderr)

    print("Extracting wiki edges...", end="", file=sys.stderr, flush=True)
    wiki_edges = build_wiki_edges(filtered_notes, valid_stems)
    print(f"  → {len(wiki_edges)} pairs", file=sys.stderr)

    all_edges = semantic_edges + wiki_edges
    total_edges = len(all_edges)

    # Build nodes list
    vault_root_str = str(vault_root) + "/"
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
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "note_count": len(nodes),
            "edge_count": total_edges,
            "min_semantic_threshold": args.min_threshold,
        },
        "nodes": nodes,
        "edges": all_edges,
    }

    # Ensure output directory exists
    output_path = args.output if args.output is not None else vault_root / "graph.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("Writing graph.json...", file=sys.stderr)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, separators=(",", ":"))

    print(
        f"Done: {len(nodes)} nodes, {total_edges} edges → {output_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
