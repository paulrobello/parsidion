#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "fastembed>=0.6.0,<1.0",
#   "sqlite-vec>=0.1.6,<1.0",
# ]
# ///
"""Semantic search for Claude Vault using sqlite-vec + fastembed.

Embeds the query with BAAI/bge-small-en-v1.5, then retrieves the top-K most
similar vault notes from embeddings.db using cosine similarity.

Usage:
    uv run vault_search.py "sqlite vector search" --top 5
    uv run vault_search.py "hook patterns" --json | python3 -m json.tool
    uv run vault_search.py "qdrant embeddings" --min-score 0.4
"""

import argparse
import json
import sqlite3
import struct
import sys
from pathlib import Path

import sqlite_vec  # type: ignore[import-untyped]
from fastembed import TextEmbedding  # type: ignore[import-untyped]

# These scripts are not a proper package — sys.path.insert is intentional so
# each script can run standalone via ``uv run`` without requiring pip install.
sys.path.insert(0, str(Path(__file__).parent))
import vault_common  # noqa: E402

_DEFAULT_MODEL: str = "BAAI/bge-small-en-v1.5"
_EMBED_DIM: int = 384


def _open_db(db_path: Path) -> sqlite3.Connection:
    """Open the embeddings database with sqlite-vec loaded.

    Args:
        db_path: Path to the SQLite embeddings database.

    Returns:
        An open sqlite3.Connection.
    """
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def _pack_vector(vec: list[float]) -> bytes:
    """Pack a float32 vector as a BLOB for sqlite-vec query parameter.

    Args:
        vec: List of float values.

    Returns:
        Packed binary representation.
    """
    return struct.pack(f"{len(vec)}f", *vec)


def search(
    query: str,
    top: int = 10,
    min_score: float = 0.0,
    model_name: str = _DEFAULT_MODEL,
    vault_root: Path | None = None,
) -> list[dict[str, object]]:
    """Search the vault for notes semantically similar to *query*.

    Returns an empty list gracefully when embeddings.db does not exist.

    Args:
        query: Natural language query string.
        top: Maximum number of results to return.
        min_score: Minimum cosine similarity threshold (0.0–1.0).
        model_name: fastembed model ID used when the index was built.
        vault_root: Override vault root path (defaults to vault_common.VAULT_ROOT).

    Returns:
        List of result dicts, each containing:
        - score (float): cosine similarity score
        - stem (str): note filename stem
        - title (str): note title
        - folder (str): vault subfolder name
        - tags (list[str]): list of tags
        - path (str): absolute path to the note file
        Sorted by score descending.
    """
    db_path = vault_common.get_embeddings_db_path()
    if not db_path.exists():
        return []

    try:
        model = TextEmbedding(model_name=model_name)
        query_vec = list(model.embed([query]))[0]
        query_blob = _pack_vector(list(query_vec))
    except Exception:  # noqa: BLE001 — graceful fallback
        return []

    try:
        conn = _open_db(db_path)
        cursor = conn.execute(
            """
            SELECT stem, path, folder, title, tags,
                   (1.0 - vec_distance_cosine(embedding, ?)) AS score
            FROM note_embeddings
            ORDER BY score DESC
            LIMIT ?
            """,
            (query_blob, top),
        )
        rows = cursor.fetchall()
        conn.close()
    except Exception:  # noqa: BLE001 — graceful fallback
        return []

    results: list[dict[str, object]] = []
    for stem, path, folder, title, tags_str, score in rows:
        if score < min_score:
            continue
        tags_raw: str = tags_str if isinstance(tags_str, str) else ""
        tags: list[str] = [t.strip() for t in tags_raw.split(",") if t.strip()]
        results.append(
            {
                "score": round(float(score), 4),
                "stem": stem,
                "title": title,
                "folder": folder,
                "tags": tags,
                "path": path,
            }
        )

    return results


def main() -> None:
    """CLI entry point for semantic vault search."""
    parser = argparse.ArgumentParser(
        description="Semantic search for Claude Vault notes.",
    )
    parser.add_argument("query", help="Search query string.")
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        metavar="N",
        help="Maximum number of results (default: 10).",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        metavar="FLOAT",
        help="Minimum cosine similarity threshold 0.0–1.0 (default: 0.0).",
    )
    parser.add_argument(
        "--model",
        default=_DEFAULT_MODEL,
        metavar="MODEL",
        help=f"fastembed model ID (default: {_DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output results as a JSON array (used by hook subprocess calls).",
    )
    args = parser.parse_args()

    db_path = vault_common.get_embeddings_db_path()
    if not db_path.exists():
        print(
            "embeddings.db not found — run build_embeddings.py first",
            file=sys.stderr,
        )
        sys.exit(0)

    results = search(
        query=args.query,
        top=args.top,
        min_score=args.min_score,
        model_name=args.model,
    )

    if args.json:
        print(json.dumps(results))
    else:
        if not results:
            print("No results found.")
            return
        for item in results:
            score = item["score"]
            title = item["title"]
            folder = item["folder"]
            stem = item["stem"]
            tags = item["tags"]
            tag_str = f" [{', '.join(str(t) for t in tags)}]" if tags else ""
            print(f"{score:.4f}  {title}{tag_str}  ({folder}/{stem})")


if __name__ == "__main__":
    main()
