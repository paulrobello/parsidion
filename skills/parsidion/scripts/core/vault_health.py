"""vault_health — composite vault health score (ENH-007).

Seven scored dimensions (0–100 each) combined into a weighted overall grade.
Each dimension returns a ``DimensionScore`` carrying its own action string —
a score with no concrete next action is just a number, so dimensions that
cannot name a command deliberately leave ``action`` as ``None`` when healthy.

This module is **stdlib-only** (it is imported by ``vault_stats``, the MCP
server, and indirectly by hooks). The display layer (``vault_stats``) may
freely use rich; this scoring layer must not. The constraint is enforced
structurally by ``tests/test_stdlib_only.py``.

Reuse contract: frontmatter validity and broken-wikilink counts come from
``vault_doctor.scan_notes_readonly`` — the *same* ``check_note`` path the
repair pipeline uses. There is no second validator here.

All ``score_*`` functions degrade rather than raise: a missing ``graph.json``,
a missing ``embeddings.db``, or an empty vault produces a low score with a
detail string, never a crash.
"""

from __future__ import annotations

import json
import sqlite3
import stat
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ARC-001: import siblings directly — core/ must not round-trip through the
# deprecated root-shim facade (``import vault_metrics`` here previously
# resolved to the root shim module, not this package's sibling).
from . import vault_metrics
from .vault_index import all_vault_notes_walk, note_index_age
from .vault_path import get_embeddings_db_path, resolve_vault

__all__: list[str] = [
    # Data model
    "DimensionScore",
    "HealthReport",
    "DIMENSION_WEIGHTS",
    # Scoring functions
    "score_index_freshness",
    "score_queue_health",
    "score_graph_connectivity",
    "score_metadata_quality",
    "score_embedding_coverage",
    "score_tag_hygiene",
    "score_file_hygiene",
    # Top-level entry point + serialisation
    "compute_health_report",
    "to_json_dict",
    "to_json",
    "render_report",
    # Private helper re-exported because tests reach into it via the shim
    # (``vault_health._grade_for(score)`` in test_vault_health.py).
    "_grade_for",
]

# ---------------------------------------------------------------------------
# Dimension weights — single source of truth (Step 2 of the plan).
# ---------------------------------------------------------------------------
# Rationale: the read path (index + embeddings + queue) weighs heaviest
# because staleness or a dead-letter backlog silently breaks every other
# feature. Metadata + connectivity weigh next — they bound search and graph
# quality. Tag/file hygiene are real but lower-impact operationally.
# Adjustments are cheap; the rationale matters more than the numbers.
DIMENSION_WEIGHTS: dict[str, int] = {
    "index_freshness": 20,
    "queue_health": 20,
    "graph_connectivity": 15,
    "metadata_quality": 15,
    "embedding_coverage": 10,
    "tag_hygiene": 10,
    "file_hygiene": 10,
    # ENH-019: small weight — latency is an operational risk signal, not a
    # vault-content quality dimension.
    "hook_latency": 5,
}

# Grade bands (Step 2). Applied to the weighted overall score.
_GRADE_BANDS: tuple[tuple[int, str], ...] = (
    (90, "A"),
    (80, "B"),
    (70, "C"),
    (60, "D"),
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DimensionScore:
    """One scored dimension.

    Attributes:
        name: Canonical dimension key (matches a ``DIMENSION_WEIGHTS`` entry).
        score: 0–100 integer.
        weight: Weight (matches ``DIMENSION_WEIGHTS[name]``); denormalised
            onto each score so a serialized report is self-describing.
        detail: Human-readable finding (what the score observed).
        action: Concrete command to run when unhealthy, or ``None`` when the
            dimension is healthy. The plan mandates: a dimension that cannot
            name an action should not score; here that maps to ``action=None``
            rather than withholding the score.
    """

    name: str
    score: int
    weight: int
    detail: str
    action: str | None = None


@dataclass(frozen=True)
class HealthReport:
    """Composite health report.

    Attributes:
        vault: Path the report was generated against.
        dimensions: One ``DimensionScore`` per dimension, in
            ``DIMENSION_WEIGHTS`` order.
        overall: Weighted mean of dimension scores, rounded to int.
        grade: Letter grade derived from ``overall``.
        note_types: Note counts by ``type`` frontmatter value (empty when
            the index DB is unavailable).
        warnings: Free-form strings flagging structural issues the dimensions
            don't directly score (e.g. type-distribution underrepresentation).
    """

    vault: Path
    dimensions: list[DimensionScore]
    overall: int
    grade: str
    note_types: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Scoring curves (Step 2)
# ---------------------------------------------------------------------------


def _age_score(age_days: float, *, fresh: float = 1.0, stale: float = 30.0) -> int:
    """Linear 100→0 between ``fresh`` and ``stale`` days.

    100 at ``age ≤ fresh``, 0 at ``age ≥ stale``, clamped to [0, 100].
    """
    if age_days <= fresh:
        return 100
    if age_days >= stale:
        return 0
    span = stale - fresh
    return max(0, min(100, int(round(100 * (1 - (age_days - fresh) / span)))))


def _grade_for(score: int) -> str:
    """Return the letter grade for an overall score."""
    for threshold, letter in _GRADE_BANDS:
        if score >= threshold:
            return letter
    return "F"


# ---------------------------------------------------------------------------
# Per-dimension scorers — each degrades rather than raises
# ---------------------------------------------------------------------------


def _graph_meta(vault: Path) -> dict | None:
    """Return the parsed ``graph.json`` ``meta`` block, or None if absent."""
    graph_path = vault / "graph.json"
    try:
        raw = graph_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return json.loads(raw).get("meta") or None
    except json.JSONDecodeError:
        return None


def _parse_iso_z(ts: str) -> float | None:
    """Parse an ISO-8601 ``Z`` timestamp into epoch seconds, or None."""
    if not ts:
        return None
    # Accept "...Z"; fromisoformat handles offsets in 3.11+ but not bare Z
    # until 3.11 — normalise first.
    cleaned = ts.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        from datetime import datetime

        return datetime.fromisoformat(cleaned).timestamp()
    except ValueError:
        return None


def score_index_freshness(vault: Path) -> DimensionScore:
    """Index freshness: graph.json age + note_index age, averaged."""
    weight = DIMENSION_WEIGHTS["index_freshness"]
    graph_meta = _graph_meta(vault)
    now = time.time()

    # --- graph.json component ---
    if graph_meta is None:
        graph_score = 0
        graph_detail = "graph.json missing or unreadable"
        graph_age_days: float | None = None
    else:
        generated = graph_meta.get("generated", "")
        gen_ts = _parse_iso_z(str(generated))
        if gen_ts is None:
            graph_score = 0
            graph_detail = "graph.json meta.generated is unparseable"
            graph_age_days = None
        else:
            graph_age_days = max(0.0, (now - gen_ts) / 86400)
            graph_score = _age_score(graph_age_days)
            graph_detail = f"graph.json is {int(graph_age_days)} day(s) old"

    # --- note_index component ---
    try:
        index_age_s = note_index_age(vault)
    except Exception:  # noqa: BLE001 — note_index_age reads files; never raise
        index_age_s = 0.0
    # Treat as days of staleness, capped at the curve ceiling so a vault that
    # has never been indexed scores 0 rather than 100 (age==0 would be fresh).
    if index_age_s <= 0:
        index_score = 100
        index_detail = "note_index is current"
    else:
        index_age_days = index_age_s / 86400
        index_score = _age_score(index_age_days)
        index_detail = f"note_index is {int(index_age_days)} day(s) behind disk"

    overall = (graph_score + index_score) // 2
    detail = (
        f"{graph_detail}; {index_detail}" if graph_meta is not None else graph_detail
    )
    action = (
        "uv run install.py --schedule-summarizer --rebuild-graph"
        if overall < 90
        else None
    )
    return DimensionScore(
        name="index_freshness",
        score=overall,
        weight=weight,
        detail=detail,
        action=action,
    )


def score_queue_health(vault: Path) -> DimensionScore:
    """Queue health: ``100 - pending*2 - dead_letters*5``, floored at 0.

    Dead letters weigh more because they represent *permanently* lost
    summaries (repeated failures), not merely delayed ones.
    """
    weight = DIMENSION_WEIGHTS["queue_health"]
    pending = vault_metrics.collect_pending(vault)
    dead = vault_metrics.collect_dead_letters(vault)
    pending_n = pending.get("total", 0) if pending.get("exists") else 0
    dead_n = dead.get("total", 0) if dead.get("exists") else 0

    raw = 100 - (pending_n * 2) - (dead_n * 5)
    score = max(0, min(100, raw))

    bits: list[str] = []
    if pending_n:
        bits.append(f"{pending_n} pending")
    if dead_n:
        bits.append(f"{dead_n} dead-lettered")
    if not bits:
        detail = "queue is empty"
        action: str | None = None
    else:
        detail = ", ".join(bits)
        # Prefer dead-letter remediation when present — that's the silent loss.
        if dead_n:
            action = "vault-review     # inspect dead letters, then re-queue or clear"
        else:
            action = "env -u CLAUDECODE uv run --no-project ~/.claude/skills/parsidion/scripts/summarize_sessions.py"
    return DimensionScore(
        name="queue_health",
        score=score,
        weight=weight,
        detail=detail,
        action=action,
    )


def score_graph_connectivity(vault: Path) -> DimensionScore:
    """Graph connectivity: ``100 × (1 - orphans / total)`` plus a cluster penalty.

    Reuses ``vault_metrics.collect_graph`` so the definition of "orphan"
    (no ``related`` AND no incoming links) matches what ``--graph`` reports.
    """
    weight = DIMENSION_WEIGHTS["graph_connectivity"]
    conn = vault_metrics.open_db(vault)
    if conn is None:
        # No index → we can't measure connectivity. Score 0 with a clear detail
        # rather than guessing from a filesystem walk (which has no link data).
        return DimensionScore(
            name="graph_connectivity",
            score=0,
            weight=weight,
            detail="note_index DB absent — run update_index.py",
            action="uv run --no-project ~/.claude/skills/parsidion/scripts/update_index.py",
        )
    try:
        data = vault_metrics.collect_graph(conn)
    finally:
        conn.close()

    total = data.get("total", 0)
    if total == 0:
        return DimensionScore(
            name="graph_connectivity",
            score=0,
            weight=weight,
            detail="vault is empty (no indexed notes)",
            action=None,
        )
    orphans = len(data.get("isolated_notes", []))
    orphan_ratio = orphans / total
    # Isolated-cluster penalty: dangling_targets/total_targets indicates how
    # many wikilinks point at nothing. Cap the penalty so an otherwise-good
    # graph isn't dragged below 50 by a few stale links.
    total_targets = data.get("total_targets", 0)
    dangling = data.get("dangling_targets", 0)
    dangling_ratio = (dangling / total_targets) if total_targets else 0.0
    penalty = min(dangling_ratio * 25, 20)
    raw = 100 * (1 - orphan_ratio) - penalty
    score = max(0, min(100, int(round(raw))))

    bits: list[str] = []
    if orphans:
        bits.append(f"{orphans} orphan note(s)")
    if dangling:
        bits.append(f"{dangling} dangling wikilink(s)")
    detail = "; ".join(bits) if bits else "well-connected"
    action = "vault-doctor --fix-all" if orphans or dangling else None
    return DimensionScore(
        name="graph_connectivity",
        score=score,
        weight=weight,
        detail=detail,
        action=action,
    )


def score_metadata_quality(vault: Path, *, scan=None) -> DimensionScore:
    """Metadata quality: ``100 × (1 - notes_with_issues / total)``.

    Reuses ``vault_doctor.scan_notes_readonly`` — the *same* ``check_note``
    path the repair pipeline uses — so there is exactly one validator.

    The optional ``scan`` kwarg lets tests inject a precomputed
    ``ScanSummary`` to avoid re-walking a large vault per dimension.
    """
    weight = DIMENSION_WEIGHTS["metadata_quality"]
    if scan is None:
        try:
            from doctor.scan import scan_notes_readonly

            scan = scan_notes_readonly(vault)
        except Exception:  # noqa: BLE001 — scan must not crash the report
            return DimensionScore(
                name="metadata_quality",
                score=0,
                weight=weight,
                detail="metadata scan failed (see vault-doctor --dry-run)",
                action="uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --dry-run",
            )
    total = scan.total_notes
    if total == 0:
        return DimensionScore(
            name="metadata_quality",
            score=0,
            weight=weight,
            detail="vault is empty (no notes scanned)",
            action=None,
        )
    affected = scan.notes_with_issues
    raw = 100 * (1 - affected / total)
    score = max(0, min(100, int(round(raw))))

    if affected == 0:
        detail = "all frontmatter valid"
        action: str | None = None
    else:
        top_codes = sorted(scan.by_code.items(), key=lambda kv: -kv[1])[:3]
        code_str = ", ".join(f"{c}={n}" for c, n in top_codes)
        detail = f"{affected}/{total} note(s) with issues ({code_str})"
        action = "uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --dry-run"
    return DimensionScore(
        name="metadata_quality",
        score=score,
        weight=weight,
        detail=detail,
        action=action,
    )


def score_embedding_coverage(vault: Path) -> DimensionScore:
    """Embedding coverage: notes-with-embeddings ÷ total notes."""
    weight = DIMENSION_WEIGHTS["embedding_coverage"]
    db_path = get_embeddings_db_path(vault)
    if not db_path.exists():
        return DimensionScore(
            name="embedding_coverage",
            score=0,
            weight=weight,
            detail="embeddings.db absent — semantic search is unavailable",
            action="uv run --no-project ~/.claude/skills/parsidion/scripts/build_embeddings.py",
        )
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return DimensionScore(
            name="embedding_coverage",
            score=0,
            weight=weight,
            detail="embeddings.db unreadable",
            action="uv run --no-project ~/.claude/skills/parsidion/scripts/build_embeddings.py",
        )
    try:
        emb_row = conn.execute("SELECT COUNT(*) FROM note_embeddings").fetchone()
        idx_row = conn.execute("SELECT COUNT(*) FROM note_index").fetchone()
    except sqlite3.Error:
        conn.close()
        return DimensionScore(
            name="embedding_coverage",
            score=0,
            weight=weight,
            detail="embeddings.db schema is incomplete",
            action="uv run --no-project ~/.claude/skills/parsidion/scripts/build_embeddings.py",
        )
    conn.close()

    total = int(idx_row[0]) if idx_row else 0
    embedded = int(emb_row[0]) if emb_row else 0
    if total == 0:
        return DimensionScore(
            name="embedding_coverage",
            score=0,
            weight=weight,
            detail="no notes indexed",
            action=None,
        )
    missing = max(0, total - embedded)
    score = int(round(100 * embedded / total))
    if missing == 0:
        detail = "all notes embedded"
        action: str | None = None
    else:
        detail = f"{missing} of {total} note(s) unembedded"
        action = (
            "uv run --no-project ~/.claude/skills/parsidion/scripts/update_index.py"
        )
    return DimensionScore(
        name="embedding_coverage",
        score=score,
        weight=weight,
        detail=detail,
        action=action,
    )


def _tag_pairs_near_duplicate(tags: list[str]) -> list[tuple[str, str]]:
    """Detect near-duplicate tags: singular↔plural and Levenshtein-1 pairs.

    Operates on lowercase forms. ``hook`` vs ``hooks`` is the canonical
    false pair this catches; the audit also called out underscores but
    those are a separate dimension (they fail the project's tag-convention
    rule rather than duplicate an existing tag).
    """
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    sorted_tags = sorted(set(tags))
    for a in sorted_tags:
        if a in seen:
            continue
        for b in sorted_tags:
            if a >= b:
                continue
            if _levenshtein1(a, b):
                pairs.append((a, b))
                seen.add(a)
                seen.add(b)
                break
    return pairs


def _levenshtein1(a: str, b: str) -> bool:
    """True iff strings are edit-distance exactly 1 (single insert/delete/sub)."""
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(1 for x, y in zip(a, b, strict=True) if x != y) == 1
    # Ensure a is the shorter one
    if la > lb:
        a, b = b, a
        la, lb = lb, la
    # Walk both; allow one insertion in b
    i = j = edits = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
        else:
            edits += 1
            if edits > 1:
                return False
            j += 1  # skip one char in the longer string
    return True


def score_tag_hygiene(vault: Path) -> DimensionScore:
    """Tag hygiene: penalise singleton ratio, near-duplicates, underscores."""
    weight = DIMENSION_WEIGHTS["tag_hygiene"]
    conn = vault_metrics.open_db(vault)
    if conn is None:
        return DimensionScore(
            name="tag_hygiene",
            score=0,
            weight=weight,
            detail="note_index DB absent",
            action="uv run --no-project ~/.claude/skills/parsidion/scripts/update_index.py",
        )
    try:
        tags = vault_metrics.collect_tags(conn)
    finally:
        conn.close()
    if not tags:
        return DimensionScore(
            name="tag_hygiene",
            score=100,
            weight=weight,
            detail="no tags indexed",
            action=None,
        )

    singletons = [t for t, n in tags if n == 1]
    total_tags = len(tags)
    singleton_ratio = len(singletons) / total_tags
    near_dup_pairs = _tag_pairs_near_duplicate([t for t, _ in tags])
    underscore_tags = [t for t, _ in tags if "_" in t]

    # Singleton ratio penalty (capped at 40 — having niche tags isn't fatal),
    # 8 per near-duplicate pair (capped at 40), 10 per underscore violation
    # (capped at 30). Floor at 0.
    raw = (
        100
        - min(40, int(round(singleton_ratio * 100)))
        - min(40, len(near_dup_pairs) * 8)
        - min(30, len(underscore_tags) * 10)
    )
    score = max(0, min(100, raw))

    bits: list[str] = []
    if singletons:
        bits.append(f"{len(singletons)} singleton tag(s)")
    if near_dup_pairs:
        bits.append(f"{len(near_dup_pairs)} near-duplicate pair(s)")
    if underscore_tags:
        bits.append(f"{len(underscore_tags)} underscore tag(s)")
    detail = "; ".join(bits) if bits else "tags are clean"
    action = "vault-doctor --fix-tags" if (near_dup_pairs or underscore_tags) else None
    return DimensionScore(
        name="tag_hygiene",
        score=score,
        weight=weight,
        detail=detail,
        action=action,
    )


def score_file_hygiene(vault: Path) -> DimensionScore:
    """File hygiene: unexpected modes + gitignored-but-tracked files."""
    weight = DIMENSION_WEIGHTS["file_hygiene"]
    # Mode check: vault .md files should be regular files (not symlinks to
    # outside the vault, not world-writable). Group-writable is fine (vaults
    # are often shared), but other-write or sticky is a misconfiguration.
    bad_mode_files: list[str] = []
    try:
        for md in all_vault_notes_walk(vault):
            try:
                st = md.stat()
            except OSError:
                continue
            mode = st.st_mode
            if stat.S_ISLNK(mode):
                # Symlinks inside the vault (e.g. Templates/) are legitimate;
                # only flag if the link target escapes the vault root.
                target = md.resolve(strict=False)
                try:
                    target.relative_to(vault.resolve())
                except ValueError:
                    bad_mode_files.append(str(md.relative_to(vault)))
            elif mode & stat.S_IWOTH:
                bad_mode_files.append(str(md.relative_to(vault)))
    except Exception as exc:  # noqa: BLE001 — file walk must not crash the report
        print(f"vault health file walk failed: {exc}", file=sys.stderr)
        pass

    # Tracked-but-gitignored check
    tracked_gitignored: list[str] = []
    try:
        from doctor.scan import _git_tracked_gitignored

        tracked_gitignored = _git_tracked_gitignored(vault)
    except Exception as exc:  # noqa: BLE001
        print(f"tracked-but-gitignored scan failed: {exc}", file=sys.stderr)
        pass

    # Deductions: 5 per finding class member, capped per class so a vault with
    # 200 bad files still surfaces a number rather than pinning at 0 silently.
    raw = 100 - min(50, len(bad_mode_files) * 5) - min(40, len(tracked_gitignored) * 5)
    score = max(0, min(100, raw))

    bits: list[str] = []
    if bad_mode_files:
        bits.append(f"{len(bad_mode_files)} file(s) with unexpected modes")
    if tracked_gitignored:
        bits.append(f"{len(tracked_gitignored)} tracked file(s) match .gitignore")
    detail = "; ".join(bits) if bits else "file modes and git tracking are clean"
    if tracked_gitignored:
        action: str | None = "git -C <vault> rm --cached <files>"
    elif bad_mode_files:
        action = "chmod u+rwX,go-w <files>"
    else:
        action = None
    return DimensionScore(
        name="file_hygiene",
        score=score,
        weight=weight,
        detail=detail,
        action=action,
    )


# ---------------------------------------------------------------------------
# Aggregator + warnings
# ---------------------------------------------------------------------------


def _note_type_distribution(vault: Path) -> dict[str, int]:
    """Return ``{note_type: count}`` from the index, or empty on failure."""
    conn = vault_metrics.open_db(vault)
    if conn is None:
        return {}
    try:
        rows = vault_metrics.fetch_all(
            conn,
            "SELECT note_type, COUNT(*) AS n FROM note_index "
            "GROUP BY note_type ORDER BY n DESC",
        )
    finally:
        conn.close()
    out: dict[str, int] = {}
    for row in rows:
        t = row["note_type"] or "(unset)"
        out[t] = int(row["n"])
    return out


def _build_warnings(note_types: dict[str, int]) -> list[str]:
    """Surface structural issues the dimensions don't directly score.

    Today: type-distribution underrepresentation. The audit found the
    ``knowledge`` type was structurally absent because the summarizer
    couldn't emit it; a type-distribution check would have flagged this
    months earlier. Threshold: any of the nine valid types below 0.5% of
    total notes (with a 100-note floor to avoid noisy small-vault warnings).
    """
    if not note_types:
        return []
    total = sum(note_types.values())
    if total < 100:
        return []
    warnings: list[str] = []
    # The nine types defined in doctor._state.VALID_TYPES — names hardwired
    # here so this module stays stdlib-only and does not import doctor (which
    # would pull vault_links and a heavier import graph).
    valid_types = (
        "pattern",
        "debugging",
        "research",
        "project",
        "daily",
        "tool",
        "language",
        "framework",
        "knowledge",
    )
    for t in valid_types:
        n = note_types.get(t, 0)
        if n == 0:
            continue
        ratio = n / total
        if ratio < 0.005:  # under 0.5% of total
            warnings.append(
                f"'{t}' is underrepresented ({n} note(s), "
                f"{ratio * 100:.2f}% of {total}) — the summarizer may not be routing to it"
            )
    return warnings


def score_hook_latency(vault: Path) -> DimensionScore:
    """Score the SessionStart p95 latency against its registered timeout.

    ENH-019: reads hook_events.log through vault_metrics, reuses the stats
    aggregation, and scores ``100 * (1 - p95/timeout)`` clamped to [0, 100]
    — a hook at 10% of budget scores 90, at or over budget scores 0. No
    log / no SessionStart events is neutral (100, no action) so a fresh
    vault is not penalized. Never raises.
    """
    weight = DIMENSION_WEIGHTS["hook_latency"]
    try:
        from core import vault_metrics

        data = vault_metrics.collect_hooks(500, vault)
        if not data.get("exists") or data.get("error"):
            return DimensionScore(
                name="hook_latency",
                score=100,
                weight=weight,
                detail="no hook_events.log",
                action=None,
            )
        aggregate = _hook_latency_aggregate(data["events"])
        agg = aggregate.get("SessionStart")
        if not agg or not agg["count"]:
            return DimensionScore(
                name="hook_latency",
                score=100,
                weight=weight,
                detail="no SessionStart events yet",
                action=None,
            )
        from .vault_constants import HOOK_TIMEOUTS_MS

        timeout_ms = HOOK_TIMEOUTS_MS.get("SessionStart", 60_000)
        ratio = float(agg["p95_ms"]) / float(timeout_ms)
        score = max(0, min(100, int(round(100 * (1 - ratio)))))
        detail = (
            f"SessionStart p95 {int(agg['p95_ms']):,} ms of "
            f"{timeout_ms // 1000}s timeout ({int(round(ratio * 100))}% used), "
            f"{agg['timeouts']} timeout(s)"
        )
        action = None
        if ratio > 0.70:
            action = (
                "SessionStart p95 is approaching its timeout — the runtime "
                "will cancel the hook. Check the AI selector latency or a "
                "cold code-memory daemon."
            )
        return DimensionScore(
            name="hook_latency",
            score=score,
            weight=weight,
            detail=detail,
            action=action,
        )
    except Exception as exc:  # noqa: BLE001 — degrade, never raise
        return DimensionScore(
            name="hook_latency",
            score=50,
            weight=weight,
            detail=f"scan failed: {exc}",
            action=None,
        )


def _hook_latency_aggregate(events: list[dict]) -> dict[str, dict]:
    """summarize_hook_latency over a raw event list (lazy import, no cycle)."""
    from cli.stats.operations import summarize_hook_latency

    return summarize_hook_latency(events, window_days=7)


def compute_health_report(
    vault: Path | str | None = None, *, skip_metadata: bool = False
) -> HealthReport:
    """Compute the full vault health report.

    Args:
        vault: Optional vault path or name. Defaults to ``resolve_vault()``.
        skip_metadata: When True, the metadata-quality scan is skipped (the
            most expensive dimension on a large vault). The dimension is
            reported with a neutral score and ``detail='skipped (--fast)'``
            so the overall is not silently inflated. Used by ``vault-stats
            --fast``.

    Returns:
        A populated ``HealthReport``. Never raises — per-dimension failures
        degrade to low scores with detail strings (and metadata-scan failure
        is wrapped so a broken ``check_note`` cannot abort the whole report).
    """
    resolved = resolve_vault(explicit=vault if isinstance(vault, str) else None)
    # Pre-compute the metadata scan once so the dimension reuses it without
    # walking the vault twice. Skipped under --fast so the report renders in
    # well under a second on a 5k-note vault.
    scan_summary = None
    if not skip_metadata:
        try:
            from doctor.scan import scan_notes_readonly

            scan_summary = scan_notes_readonly(resolved)
        except Exception:  # noqa: BLE001 — degrade; dimension reports scan failed
            scan_summary = None

    if skip_metadata:
        # Neutral score: include the dimension in the report (so the table
        # still lists it) without letting it move the overall either way.
        # The detail makes the skip visible so the user can re-run without
        # --fast if they want the real number.
        metadata_score = DimensionScore(
            name="metadata_quality",
            score=100,
            weight=DIMENSION_WEIGHTS["metadata_quality"],
            detail="skipped (--fast)",
            action=None,
        )
    else:
        metadata_score = score_metadata_quality(resolved, scan=scan_summary)

    dimensions = [
        score_index_freshness(resolved),
        score_queue_health(resolved),
        score_graph_connectivity(resolved),
        metadata_score,
        score_embedding_coverage(resolved),
        score_tag_hygiene(resolved),
        score_file_hygiene(resolved),
        score_hook_latency(resolved),
    ]

    total_weight = sum(d.weight for d in dimensions)
    weighted = sum(d.score * d.weight for d in dimensions)
    overall = int(round(weighted / total_weight)) if total_weight else 0

    note_types = _note_type_distribution(resolved)
    warnings = _build_warnings(note_types)

    return HealthReport(
        vault=resolved,
        dimensions=dimensions,
        overall=overall,
        grade=_grade_for(overall),
        note_types=note_types,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Serialisation + plain-text rendering (stdlib-only; no rich dependency)
# ---------------------------------------------------------------------------


def to_json_dict(report: HealthReport) -> dict:
    """Return a JSON-serialisable dict for the ``--json`` output and MCP tool."""
    return {
        "vault": str(report.vault),
        "overall": report.overall,
        "grade": report.grade,
        "dimensions": [asdict(d) for d in report.dimensions],
        "note_types": dict(report.note_types),
        "warnings": list(report.warnings),
    }


def to_json(report: HealthReport) -> str:
    """Return the report as a JSON string (compact, sorted keys for stability)."""
    return json.dumps(to_json_dict(report), sort_keys=True)


_BAR_FULL = 10


def _bar(score: int) -> str:
    """Render a 10-char textual bar: ``██████░░░░`` style."""
    filled = max(0, min(_BAR_FULL, round(score / 10)))
    return "█" * filled + "░" * (_BAR_FULL - filled)


def render_report(report: HealthReport) -> str:
    """Render the report as plain text suitable for stdout.

    No ANSI escapes — this is read by both terminals and the MCP tool, so it
    must render identically anywhere. ``vault_stats`` may wrap this in Rich
    markup at the CLI boundary if desired; this function is the source.
    """
    lines: list[str] = []
    lines.append(
        f"Vault Health — {report.vault}                                    "
        f"{report.grade}  ({report.overall}/100)"
    )
    lines.append("")
    for d in report.dimensions:
        lines.append(f"  {d.name:<20} {_bar(d.score)} {d.score:>3}   {d.detail}")
        if d.action:
            lines.append(f"{'':>26}→ {d.action}")
    if report.note_types:
        types_str = " · ".join(f"{t} {n}" for t, n in report.note_types.items())
        lines.append("")
        lines.append(f"  Note types: {types_str}")
    for w in report.warnings:
        lines.append(f"  ⚠  {w}")
    return "\n".join(lines)
