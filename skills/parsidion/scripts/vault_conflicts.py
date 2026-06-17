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
import sys
from collections import defaultdict
from pathlib import Path
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


def _python_pairwise(
    vectors: list[list[float]], threshold: float
) -> list[tuple[int, int]]:
    """Pure-Python O(n^2) pairwise cosine — fallback when sqlite-vec is absent.

    Correct but slow at scale (~minutes for thousands of notes); used in the
    dev/test environment and minimal installs without the ``[tools]`` extras.
    """
    pairs: list[tuple[int, int]] = []
    n = len(vectors)
    for i in range(n):
        vi = vectors[i]
        for j in range(i + 1, n):
            if _cosine_similarity(vi, vectors[j]) >= threshold:
                pairs.append((i, j))
    return pairs


def _sqlite_pairwise(
    vault: Path, stem_to_idx: dict[str, int], threshold: float
) -> list[tuple[int, int]] | None:
    """Fast pairwise cosine via sqlite-vec's C-level ``vec_distance_cosine``.

    Returns ``None`` when sqlite-vec is unavailable or the query fails, so the
    caller falls back to pure Python. ``cosine similarity >= threshold`` is
    equivalent to ``vec_distance_cosine <= (1 - threshold)``.
    """
    try:
        import sqlite3

        import sqlite_vec  # type: ignore[import-untyped]
    except ImportError:
        return None
    db_path = vault_common.get_embeddings_db_path(vault)
    if not db_path.exists():
        return None
    max_dist = 1.0 - threshold
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        # Upper-triangle self-join: each pair once, no self-pairs.
        rows = conn.execute(
            """
            SELECT a.stem, b.stem
            FROM note_embeddings a
            JOIN note_embeddings b ON a.rowid < b.rowid
            WHERE vec_distance_cosine(a.embedding, b.embedding) <= ?
            """,
            (max_dist,),
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        if conn is not None:
            conn.close()
    pairs: list[tuple[int, int]] = []
    for a, b in rows:
        ia = stem_to_idx.get(a)
        ib = stem_to_idx.get(b)
        if ia is not None and ib is not None:
            pairs.append((ia, ib))
    return pairs


def find_candidate_clusters(
    vault: Path, threshold: float = _DEFAULT_TOPIC_THRESHOLD, top: int = _DEFAULT_TOP
) -> list[list[dict[str, str]]]:
    """Cluster semantically-similar notes; return clusters with >= 2 members.

    Uses sqlite-vec's C-level cosine when available (fast at scale); falls back
    to a pure-Python O(n^2) scan otherwise.
    """
    records, vectors = _load_embeddings(vault)
    n = len(records)
    if n < 2:
        return []
    stem_to_idx = {rec["stem"]: i for i, rec in enumerate(records)}
    pairs = _sqlite_pairwise(vault, stem_to_idx, threshold)
    if pairs is None:
        pairs = _python_pairwise(vectors, threshold)
    clusters: list[list[dict[str, str]]] = []
    for member_indices in _group_clusters(n, pairs):
        cluster = [records[idx] for idx in member_indices[:top]]
        if len(cluster) >= 2:
            clusters.append(cluster)
    return clusters


def _read_body(path: str) -> str:
    """Read a note's body (after frontmatter), truncated for prompt size."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""
    # Drop frontmatter block if present.
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    return text.strip()[:2000]


def _build_prompt(records: list[dict[str, str]]) -> str:
    """Build the contradiction-detection prompt with note bodies inlined."""
    blocks: list[str] = []
    for rec in records:
        body = _read_body(rec["path"])
        blocks.append(f"### {rec['stem']}\n{rec['path']}\n{body}")
    note_block = "\n\n".join(blocks)
    return (
        f"You are a knowledge-vault consistency auditor. Below are {len(records)} "
        "notes that are semantically similar and may overlap.\n\n"
        f"NOTES:\n{note_block}\n\n"
        "Identify CONTRADICTIONS ONLY — pairs of notes making conflicting, "
        "mutually-exclusive claims about the same subject. Do NOT flag near-duplicates, "
        "complements, or unrelated notes sharing keywords.\n\n"
        "Respond with ONLY a JSON array (no prose). Each element:\n"
        '{"type":"contradiction","a":"<stem A>","b":"<stem B>",'
        '"a_says":"<one-line claim>","b_says":"<one-line claim>",'
        '"recommendation":"keep_a|keep_b|merge|needs_review"}\n\n'
        "If there are no contradictions, respond with: []"
    )


def _detect_contradictions(
    records: list[dict[str, str]], vault: Path, no_ai: bool = False
) -> list[dict[str, Any]]:
    """Ask the AI backend to find contradictions within a cluster of notes.

    Returns a list of conflict dicts (parsed from the AI's JSON array).
    """
    if no_ai or len(records) < 2:
        return []
    import ai_backend

    prompt = _build_prompt(records)
    raw = ai_backend.run_ai_prompt(
        prompt,
        model_tier="large",
        timeout=_DEFAULT_AI_TIMEOUT,
        purpose="vault-conflicts",
        vault=vault,
    )
    if not raw:
        return []
    conflicts = _parse_json_array(raw)
    # Keep only entries that name two distinct known stems in this cluster.
    known = {rec["stem"] for rec in records}
    return [
        c
        for c in conflicts
        if c.get("a") in known and c.get("b") in known and c.get("a") != c.get("b")
    ]


def _report_path(vault: Path) -> Path:
    return vault / _CONFLICTS_DIRNAME / _CONFLICTS_FILENAME


def write_conflict_report(conflicts: list[dict[str, Any]], vault: Path) -> None:
    """Atomically write the conflict report (flock-protected)."""
    dest = _report_path(vault)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        vault_common.flock_exclusive(fh)
        fh.write(json.dumps(conflicts, indent=2) + "\n")
    tmp.replace(dest)


def read_conflict_report(vault: Path) -> list[dict[str, Any]]:
    """Read the conflict report; return [] if absent/unparseable."""
    path = _report_path(vault)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _apply_resolution(conflict: dict[str, Any], choice: str) -> str:
    """Return a short description of the applied resolution.

    The TUI (Task 4.6) collects *choice* ∈ {keep_a, keep_b, merge, skip} and
    calls this. Actual note mutation (deleting/merging files) is intentionally
    NOT done here — this version records the decision; users perform the edit
    in their editor or via vault-merge. (Keeps vault-conflicts read-only-safe.)
    """
    a = conflict.get("a", "?")
    b = conflict.get("b", "?")
    if choice == "keep_a":
        return f"keep {a}; review {b} for staleness"
    if choice == "keep_b":
        return f"keep {b}; review {a} for staleness"
    if choice == "merge":
        return f"merge {a} + {b} via vault-merge"
    return "skipped"


def _run_scan(
    vault: Path, threshold: float, top: int, no_ai: bool = False
) -> list[dict[str, Any]]:
    """Run the full detect pipeline and persist the report. Returns conflicts."""
    clusters = find_candidate_clusters(vault, threshold=threshold, top=top)
    all_conflicts: list[dict[str, Any]] = []
    for cluster in clusters:
        members = cluster[:_DEFAULT_MAX_CLUSTER]
        all_conflicts.extend(_detect_contradictions(members, vault, no_ai=no_ai))
    write_conflict_report(all_conflicts, vault)
    return all_conflicts


def _run_tui(conflicts: list[dict[str, Any]], vault: Path) -> None:  # pragma: no cover
    """Interactive curses walkthrough (mirrors vault_review._show_popup).

    For each conflict: show a_says vs b_says, collect a choice
    (a=keep_a, b=keep_b, m=merge, s=skip, q=quit). Decisions are collected
    during the curses loop and printed AFTER the wrapper returns (curses owns
    the screen while active, so stdout is not visible mid-loop).
    """
    import curses

    decisions: list[str] = []

    def _loop(stdscr: Any) -> None:
        curses.curs_set(0)
        stdscr.keypad(True)
        mapping = {
            ord("a"): "keep_a",
            ord("b"): "keep_b",
            ord("m"): "merge",
            ord("s"): "skip",
        }
        idx = 0
        while idx < len(conflicts):
            c = conflicts[idx]
            stdscr.clear()
            stdscr.addstr(0, 2, f"Conflict {idx + 1}/{len(conflicts)}")
            stdscr.addstr(2, 2, f"[A] {c.get('a')}: {c.get('a_says', '')}")
            stdscr.addstr(3, 2, f"[B] {c.get('b')}: {c.get('b_says', '')}")
            stdscr.addstr(5, 2, "a=keep A  b=keep B  m=merge  s=skip  q=quit")
            stdscr.refresh()
            key = stdscr.getch()
            if key == ord("q"):
                break
            choice = mapping.get(key)
            if choice:
                decisions.append(_apply_resolution(c, choice))
                idx += 1

    try:
        curses.wrapper(_loop)
    except Exception:  # noqa: BLE001
        print(
            "Terminal does not support curses; use --scan-only or --json.",
            file=sys.stderr,
        )
        return

    for line in decisions:
        print(line)


def main() -> None:
    """CLI entry: detect contradictions, then optionally review interactively."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="vault-conflicts",
        description="Detect contradictions between semantically-similar vault notes.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=_DEFAULT_TOPIC_THRESHOLD,
        help=f"Topic-similarity threshold for clustering (default {_DEFAULT_TOPIC_THRESHOLD}).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=_DEFAULT_TOP,
        help=f"Max pairs considered (default {_DEFAULT_TOP}).",
    )
    parser.add_argument(
        "--vault", "-V", metavar="PATH", default=None, help="Vault root."
    )
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Write the conflict report and exit (no interactive TUI).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the conflict report as JSON and exit.",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Run clustering only; do not call the AI backend (useful for dry runs).",
    )
    args = parser.parse_args()

    vault_path = vault_common.resolve_vault(explicit=args.vault, cwd=str(Path.cwd()))

    # QA-001/ARC-001: swap VAULT_ROOT so lru-cached resolvers observe the new root.
    original_vault_root = vault_common.VAULT_ROOT
    vault_common.VAULT_ROOT = vault_path
    vault_common.apply_configured_env_defaults(vault=vault_path)
    vault_common.load_config.cache_clear()  # type: ignore[attr-defined]
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    try:
        conflicts = _run_scan(vault_path, args.threshold, args.top, no_ai=args.no_ai)
    finally:
        vault_common.VAULT_ROOT = original_vault_root
        vault_common.load_config.cache_clear()  # type: ignore[attr-defined]
        vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]

    if args.json:
        print(json.dumps(conflicts, indent=2))
        return

    if not conflicts:
        print("No contradictions detected.")
        return

    print(f"Detected {len(conflicts)} potential contradiction(s).")
    print(f"Report written to {vault_path / 'conflicts' / 'report.json'}")
    if not args.scan_only:
        _run_tui(conflicts, vault_path)


if __name__ == "__main__":
    main()
