#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "fastembed>=0.6.0,<1.0",
#   "sqlite-vec>=0.1.6,<1.0",
# ]
# ///
"""Unified vault search: semantic (positional query) or metadata (filter flags).

Semantic mode — provide a natural language query:
    vault_search.py "sqlite vector search" --top 5
    vault_search.py "hook patterns" --json
    vault_search.py "qdrant embeddings" --min-score 0.4

Metadata mode — provide one or more filter flags (no positional query):
    vault_search.py --tag python --limit 10
    vault_search.py --folder Patterns
    vault_search.py --type debugging
    vault_search.py --project parsidion-cc
    vault_search.py --recent-days 7
    vault_search.py --tag rust --folder Patterns --text

Both modes output the same JSON structure. Semantic results include a ``score``
field (cosine similarity); metadata results set ``score`` to ``null``.
"""

import argparse
import json
import sqlite3
import struct
import sys
from datetime import datetime, timedelta
from pathlib import Path

# These scripts are not a proper package — sys.path.insert is intentional so
# each script can run standalone via ``uv run`` without requiring pip install.
sys.path.insert(0, str(Path(__file__).parent))
import vault_common  # noqa: E402

_DEFAULT_MODEL: str = vault_common.get_config("embeddings", "model", "BAAI/bge-small-en-v1.5")


# ---------------------------------------------------------------------------
# Semantic search
# ---------------------------------------------------------------------------


def _open_db_semantic(db_path: Path) -> sqlite3.Connection:
    """Open embeddings DB with sqlite-vec extension loaded.

    Args:
        db_path: Path to the SQLite embeddings database.

    Returns:
        An open sqlite3.Connection with sqlite-vec loaded.
    """
    try:
        import sqlite_vec  # type: ignore[import-untyped]
    except ImportError:
        print(
            "sqlite-vec not installed — run: uv tool install --editable '.[tools]'",
            file=sys.stderr,
        )
        sys.exit(1)
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
) -> list[dict[str, object]]:
    """Search the vault for notes semantically similar to *query*.

    Returns an empty list gracefully when embeddings.db does not exist.

    Args:
        query: Natural language query string.
        top: Maximum number of results to return.
        min_score: Minimum cosine similarity threshold (0.0–1.0).
        model_name: fastembed model ID used when the index was built.

    Returns:
        List of result dicts with keys: score, stem, title, folder, tags, path.
        Sorted by score descending.
    """
    db_path = vault_common.get_embeddings_db_path()
    if not db_path.exists():
        return []

    try:
        from fastembed import TextEmbedding  # type: ignore[import-untyped]

        model = TextEmbedding(model_name=model_name)
        query_vec = list(model.embed([query]))[0]
        query_blob = _pack_vector(list(query_vec))
    except Exception:  # noqa: BLE001 — graceful fallback
        return []

    try:
        conn = _open_db_semantic(db_path)
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
                "summary": "",
                "note_type": "",
                "project": "",
                "confidence": "",
                "mtime": None,
                "related": [],
                "is_stale": False,
                "incoming_links": 0,
            }
        )

    return results


# ---------------------------------------------------------------------------
# Metadata search
# ---------------------------------------------------------------------------


def query(
    *,
    tag: str | None = None,
    folder: str | None = None,
    note_type: str | None = None,
    project: str | None = None,
    recent_days: int | None = None,
    limit: int = 50,
) -> list[dict[str, object]]:
    """Query the note_index table for metadata-filtered results.

    Returns an empty list (not None) if the DB is absent or table missing.

    Args:
        tag: Exact tag token to match.
        folder: Exact folder name to match.
        note_type: Exact note_type to match.
        project: Exact project name to match.
        recent_days: Notes modified within this many days.
        limit: Maximum result count.

    Returns:
        List of result dicts with score set to null, sorted by mtime descending.
    """
    db_path = vault_common.get_embeddings_db_path()
    if not db_path.exists():
        return []

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError:
        return []

    try:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='note_index'"
        ).fetchone() is None:
            return []

        conditions: list[str] = []
        params: list[object] = []

        if tag is not None:
            conditions.append(
                "(tags = ? OR tags LIKE ? OR tags LIKE ? OR tags LIKE ?)"
            )
            params.extend([tag, f"{tag},%", f"%, {tag}", f"%, {tag},%"])

        if folder is not None:
            conditions.append("folder = ?")
            params.append(folder)

        if note_type is not None:
            conditions.append("note_type = ?")
            params.append(note_type)

        if project is not None:
            conditions.append("project = ?")
            params.append(project)

        if recent_days is not None:
            cutoff = (datetime.now() - timedelta(days=recent_days)).timestamp()
            conditions.append("mtime >= ?")
            params.append(cutoff)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = (
            f"SELECT stem, path, folder, title, summary, tags, note_type, "
            f"project, confidence, mtime, related, is_stale, incoming_links "
            f"FROM note_index {where} ORDER BY mtime DESC LIMIT ?"
        )
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    results: list[dict[str, object]] = []
    for row in rows:
        d = dict(row)
        tags_str: str = d.get("tags", "") or ""
        related_str: str = d.get("related", "") or ""
        results.append(
            {
                "score": None,
                "stem": d.get("stem", ""),
                "title": d.get("title", ""),
                "folder": d.get("folder", ""),
                "tags": [t.strip() for t in tags_str.split(",") if t.strip()],
                "path": d.get("path", ""),
                "summary": d.get("summary", ""),
                "note_type": d.get("note_type", ""),
                "project": d.get("project", ""),
                "confidence": d.get("confidence", ""),
                "mtime": d.get("mtime"),
                "related": [r.strip() for r in related_str.split(",") if r.strip()],
                "is_stale": bool(d.get("is_stale", 0)),
                "incoming_links": d.get("incoming_links", 0),
            }
        )
    return results


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _format_text(results: list[dict[str, object]]) -> str:
    """Format results as human-readable one-line-per-note text.

    Args:
        results: List of result dicts.

    Returns:
        Newline-separated string.
    """
    lines: list[str] = []
    for r in results:
        score = r.get("score")
        tags = r.get("tags", [])
        tags_str = ", ".join(str(t) for t in tags) if tags else ""
        stale = " [STALE]" if r.get("is_stale") else ""
        tags_label = f" [{tags_str}]" if tags_str else ""
        score_label = f"{float(score):.4f}  " if score is not None else ""
        lines.append(
            f"{score_label}{r['folder'] or '.'}/{r['stem']}{tags_label}{stale} — {r['title']}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point: semantic search or metadata filter depending on args."""
    parser = argparse.ArgumentParser(
        prog="vault-search",
        description=(
            "Search Claude Vault notes by meaning (semantic) or by metadata filters.\n\n"
            "Semantic mode: provide a QUERY string.\n"
            "Metadata mode: provide one or more filter flags (--tag, --folder, etc.).\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Positional — optional; triggers semantic mode when present
    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="Natural language query for semantic search. Omit to use metadata filters.",
    )

    # Semantic-only flags
    _cfg_top_k: int = vault_common.get_config("embeddings", "top_k", 10)
    _cfg_min_score: float = vault_common.get_config("embeddings", "min_score", 0.0)
    parser.add_argument(
        "--top",
        type=int,
        default=_cfg_top_k,
        metavar="N",
        help=f"Semantic: max results (default {_cfg_top_k}).",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=_cfg_min_score,
        metavar="FLOAT",
        help=f"Semantic: minimum cosine similarity 0.0–1.0 (default {_cfg_min_score}).",
    )
    parser.add_argument(
        "--model",
        default=_DEFAULT_MODEL,
        metavar="MODEL",
        help=f"Semantic: fastembed model ID (default: {_DEFAULT_MODEL}).",
    )

    # Metadata filter flags
    parser.add_argument("--tag", metavar="TAG", help="Metadata: filter by exact tag token.")
    parser.add_argument("--folder", metavar="FOLDER", help="Metadata: filter by exact folder name.")
    parser.add_argument(
        "--type",
        metavar="TYPE",
        dest="note_type",
        help="Metadata: filter by note type.",
    )
    parser.add_argument("--project", metavar="PROJECT", help="Metadata: filter by project name.")
    parser.add_argument(
        "--recent-days",
        metavar="N",
        type=int,
        help="Metadata: notes modified within the last N days.",
    )
    parser.add_argument(
        "--limit",
        metavar="N",
        type=int,
        default=50,
        help="Metadata: maximum number of results (default: 50).",
    )

    # Output format
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--json",
        dest="output_format",
        action="store_const",
        const="json",
        default="json",
        help="JSON array output (default).",
    )
    output_group.add_argument(
        "--text",
        dest="output_format",
        action="store_const",
        const="text",
        help="Human-readable one-line-per-note output.",
    )

    args = parser.parse_args()

    _filter_flags = (args.tag, args.folder, args.note_type, args.project, args.recent_days)
    has_query = args.query is not None
    has_filters = any(f is not None for f in _filter_flags)

    if not has_query and not has_filters:
        parser.error(
            "Provide a search QUERY for semantic search, or at least one filter flag "
            "(--tag, --folder, --type, --project, --recent-days) for metadata search."
        )

    if has_query and has_filters:
        parser.error(
            "Semantic search (QUERY) and metadata filters are mutually exclusive. "
            "Use one mode at a time."
        )

    if has_query:
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
    else:
        results = query(
            tag=args.tag,
            folder=args.folder,
            note_type=args.note_type,
            project=args.project,
            recent_days=args.recent_days,
            limit=args.limit,
        )

    if args.output_format == "text":
        print(_format_text(results))
    else:
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
