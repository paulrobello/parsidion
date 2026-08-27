"""Embedding-based near-duplicate scan across the whole vault (ARC-005).

Extracted from ``vault_merge.py``. Re-exported by the entry shim so
``vault_merge._scan_duplicates``, ``vault_merge._is_excluded_from_scan``,
``vault_merge._DEFAULT_SCAN_THRESHOLD``, and
``vault_merge._DEFAULT_SCAN_TOP`` keep resolving for tests and other
callers.

``sqlite_vec`` is lazy-imported inside ``_scan_duplicates`` so the module
remains importable without the ``tools``/``search`` extras installed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from core.vault_path import get_embeddings_db_path

_DEFAULT_SCAN_THRESHOLD = 0.92
_DEFAULT_SCAN_TOP = 50


class MergeScanError(Exception):
    """Raised when the duplicate scan cannot run (missing DB/deps/read errors).

    QA-016: library code raises; the CLI entrypoint catches this and exits 1.
    """


def _is_excluded_from_scan(path: str) -> bool:
    """Return True for notes excluded from duplicate scanning.

    Daily notes share a templated session-list structure and the ``NN-probello``
    slug pattern, so they hit the cosine threshold against each other despite
    being semantically distinct (different days). Merging them destroys the
    per-day structure, so they are excluded entirely.

    The embeddings store the path as absolute (``/…/Daily/YYYY-MM/…``) and the
    folder as the month (``YYYY-MM``), so match the ``Daily/`` segment anywhere
    in the normalized path (covers absolute and relative forms).
    """
    norm = str(path).replace("\\", "/")
    return "/Daily/" in norm or norm.lstrip("./").startswith("Daily/")


def _scan_duplicates(
    threshold: float = _DEFAULT_SCAN_THRESHOLD,
    top: int = _DEFAULT_SCAN_TOP,
    vault_path: Path | None = None,
) -> None:
    """Scan all vault notes for near-duplicate pairs using embedding similarity.

    Loads all embeddings from the DB, computes pairwise cosine similarity,
    and prints pairs above *threshold* sorted by score descending.

    Args:
        threshold: Minimum similarity score to report (0.0–1.0).
        top: Maximum number of pairs to report.
        vault_path: Path to the vault root.
    """
    db_path = get_embeddings_db_path(vault=vault_path)
    if not db_path.exists():
        raise MergeScanError(
            "No embeddings database found. Run build_embeddings.py first."
        )

    try:
        import sqlite_vec  # type: ignore[import-untyped]
    except ImportError:
        raise MergeScanError(
            "sqlite-vec not installed — run: uv tool install --editable '.[tools]'"
        )

    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    try:
        rows = conn.execute(
            "SELECT stem, path, folder, title, tags, embedding FROM note_embeddings"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        conn.close()
        raise MergeScanError(f"Error reading embeddings: {exc}") from exc

    rows = [r for r in rows if not _is_excluded_from_scan(str(r[1]))]

    if not rows:
        conn.close()
        print("No embeddings found in database.")
        return

    stems = [r[0] for r in rows]
    folders = [r[2] for r in rows]
    titles = [r[3] for r in rows]
    tags_list = [r[4] for r in rows]
    stem_to_idx = {s: i for i, s in enumerate(stems)}

    # Pairwise cosine via sqlite-vec's C-level vec_distance_cosine (fast at
    # scale; the pure-Python O(n^2) scan took minutes on thousands of notes).
    # similarity >= threshold  <=>  vec_distance_cosine <= (1 - threshold).
    # Restrict to non-excluded stems via a temp table so _is_excluded_from_scan
    # stays the single source of truth for the Daily-note filter.
    max_dist = 1.0 - threshold
    try:
        conn.execute("CREATE TEMP TABLE _kept (stem TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO _kept (stem) VALUES (?)", [(s,) for s in stems])
        pair_rows = conn.execute(
            """
            SELECT a.stem, b.stem,
                   (1.0 - vec_distance_cosine(a.embedding, b.embedding)) AS score
            FROM note_embeddings a
            JOIN note_embeddings b ON a.rowid < b.rowid
            WHERE a.stem IN (SELECT stem FROM _kept)
              AND b.stem IN (SELECT stem FROM _kept)
              AND vec_distance_cosine(a.embedding, b.embedding) <= ?
            ORDER BY score DESC
            LIMIT ?
            """,
            (max_dist, top),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        conn.close()
        raise MergeScanError(f"Error computing similarities: {exc}") from exc
    conn.close()

    pairs: list[tuple[float, int, int]] = []
    for a, b, score in pair_rows:
        ia = stem_to_idx.get(a)
        ib = stem_to_idx.get(b)
        if ia is not None and ib is not None:
            pairs.append((score, ia, ib))

    if not pairs:
        print(f"No note pairs found above similarity threshold {threshold:.2f}.")
        return

    pairs.sort(key=lambda x: x[0], reverse=True)
    pairs = pairs[:top]

    print(f"Found {len(pairs)} near-duplicate pair(s) (threshold={threshold:.2f}):\n")
    for rank, (score, i, j) in enumerate(pairs, 1):
        label_a = f"{folders[i] or '.'}/{stems[i]}"
        label_b = f"{folders[j] or '.'}/{stems[j]}"

        # ARC-011: Enhancement - session_id matching logic
        match_note = ""
        tags_a = [t.strip() for t in tags_list[i].split(",") if t.strip()]
        tags_b = [t.strip() for t in tags_list[j].split(",") if t.strip()]

        sid_a = next(
            (
                t
                for t in tags_a
                if len(t) == 16 and all(c in "0123456789abcdef" for c in t.lower())
            ),
            None,
        )
        sid_b = next(
            (
                t
                for t in tags_b
                if len(t) == 16 and all(c in "0123456789abcdef" for c in t.lower())
            ),
            None,
        )

        if sid_a and sid_b and sid_a == sid_b:
            match_note = f" [SAME SESSION: {sid_a}]"

        print(f"  {rank:>3}.  [{score:.4f}]  {label_a}")
        print(f"              {label_b}{match_note}")
        print(f"         A: {titles[i]}")
        print(f"         B: {titles[j]}")
        print(f"         → vault-merge {stems[i]} {stems[j]}")
        print()
