"""vault-conflicts: detect contradictions between semantically-similar vault notes.

Companion to vault-merge (which merges near-duplicates). Clusters notes by
embedding similarity, then asks the configured prompt AI backend whether any
pair in a cluster makes mutually-exclusive claims. Conflicts can be reviewed
interactively (curses TUI) or emitted as JSON.
"""

from __future__ import annotations

import json
import re
import struct
import sys  # noqa: F401
from collections import defaultdict
from pathlib import Path  # noqa: F401
from typing import Any

import vault_common

_DEFAULT_TOPIC_THRESHOLD = 0.75
_DEFAULT_MAX_CLUSTER = 8
_DEFAULT_TOP = 50
_DEFAULT_AI_TIMEOUT = 90

_CONFLICTS_DIRNAME = "conflicts"
_CONFLICTS_FILENAME = "report.json"


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    """Extract the first JSON array from *text*; return [] if none/unparseable.

    Tolerates markdown fences and surrounding prose from an LLM.
    """
    if not text:
        return []
    stripped = text.strip()
    if stripped.startswith("```"):
        # Remove a leading ```json / ``` fence and trailing fence.
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    start = stripped.find("[")
    end = stripped.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length float vectors; 0.0 for zero vectors."""
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / ((na**0.5) * (nb**0.5))


def _group_clusters(n: int, pairs: list[tuple[int, int]]) -> list[list[int]]:
    """Union-find over *pairs*; return ONLY clusters with >= 2 members."""
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in pairs:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    groups: dict[int, list[int]] = defaultdict(list)
    for idx in range(n):
        groups[find(idx)].append(idx)
    return [sorted(members) for members in groups.values() if len(members) >= 2]


def _is_excluded(path: str) -> bool:
    """Exclude Daily notes (they are auto-captured, not curated knowledge)."""
    norm = str(path).replace("\\", "/")
    return "/Daily/" in norm or norm.lstrip("./").startswith("Daily/")


def _load_embeddings(
    vault: Path,
) -> tuple[list[dict[str, str]], list[list[float]]]:
    """Load note_embeddings rows + unpacked float32 vectors.

    Returns parallel lists of (records, vectors). Empty if the DB or table is
    missing (the caller should print a helpful message).
    """
    import sqlite3

    db_path = vault_common.get_embeddings_db_path(vault)
    if not db_path.exists():
        return [], []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT stem, path, folder, title, tags, embedding FROM note_embeddings"
        ).fetchall()
    except sqlite3.Error:
        return [], []
    finally:
        conn.close()

    records: list[dict[str, str]] = []
    vectors: list[list[float]] = []
    blobs = []
    for stem, path, folder, title, tags, blob in rows:
        if _is_excluded(path):
            continue
        records.append(
            {
                "stem": stem,
                "path": path,
                "folder": folder,
                "title": title,
                "tags": tags,
            }
        )
        blobs.append(blob)
    if not blobs:
        return [], []
    dim = len(blobs[0]) // 4
    vectors = [list(struct.unpack(f"{dim}f", b)) for b in blobs]
    return records, vectors


def find_candidate_clusters(
    vault: Path, threshold: float = _DEFAULT_TOPIC_THRESHOLD, top: int = _DEFAULT_TOP
) -> list[list[dict[str, str]]]:
    """Cluster semantically-similar notes; return clusters with >= 2 members."""
    records, vectors = _load_embeddings(vault)
    n = len(records)
    if n < 2:
        return []
    pairs: list[tuple[int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            if _cosine_similarity(vectors[i], vectors[j]) >= threshold:
                pairs.append((i, j))
    clusters: list[list[dict[str, str]]] = []
    for member_indices in _group_clusters(n, pairs):
        cluster = [records[idx] for idx in member_indices[:top]]
        if len(cluster) >= 2:
            clusters.append(cluster)
    return clusters


def main() -> None:  # pragma: no cover - wired in Task 4.6
    """CLI entry point (implemented in Task 4.6)."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
