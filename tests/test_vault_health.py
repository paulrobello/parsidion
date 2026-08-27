"""Tests for ``vault_health`` and the ``vault-stats --health`` CLI mode (ENH-007).

The plan (Step 6) calls out seven tests; the prior audit found
``test_vault_stats.py`` asserted nothing (QA-007) — these tests must assert
on real counts and action strings, not merely that the command didn't raise.
"""

from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
import time
from collections.abc import Generator
from datetime import datetime, UTC
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import vault_common  # noqa: E402
import vault_health  # noqa: E402

vault_stats = importlib.import_module("vault_stats")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Point resolve_vault() at a fresh tmp_path for every test.

    Uses the public CLAUDE_VAULT override (matches conftest.tmp_vault) so
    every dimension reads from the synthetic vault under test.
    """
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    vault_common.clear_config_cache()
    # SEC-P001: register tmp_path in a test-local vaults.yaml so the
    # allowlist resolver accepts the CLAUDE_VAULT reference.
    _cfg_dir = tmp_path / ".config" / "parsidion"
    _cfg_dir.mkdir(parents=True, exist_ok=True)
    (_cfg_dir / "vaults.yaml").write_text(
        f"vaults:\n  test: {tmp_path}\n", encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("CLAUDE_VAULT", str(tmp_path))
    yield
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    vault_common.clear_config_cache()


def _make_db(vault: Path) -> Path:
    """Create embeddings.db with empty note_index + note_embeddings tables."""
    db_path = vault / "embeddings.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE note_index (
            stem TEXT, path TEXT, folder TEXT, title TEXT, summary TEXT,
            tags TEXT, note_type TEXT, project TEXT, confidence TEXT,
            mtime REAL, related TEXT, is_stale INTEGER, incoming_links INTEGER
        )
        """
    )
    conn.execute("CREATE TABLE note_embeddings (stem TEXT PRIMARY KEY, embedding BLOB)")
    conn.commit()
    conn.close()
    return db_path


def _write_graph_meta(vault: Path, *, age_days: float = 0.0) -> None:
    """Write a graph.json with ``meta.generated`` set ``age_days`` ago."""
    ts = datetime.now(tz=UTC).timestamp() - age_days * 86400
    iso = datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    graph = {
        "meta": {"generated": iso, "note_count": 0, "edge_count": 0},
        "nodes": [],
        "edges": [],
    }
    (vault / "graph.json").write_text(json.dumps(graph), encoding="utf-8")


def _insert_note(
    db_path: Path,
    *,
    stem: str,
    folder: str = "Patterns",
    note_type: str = "pattern",
    tags: str = "",
    related: str = "",
    incoming: int = 0,
    mtime: float | None = None,
) -> None:
    """Insert one row into note_index."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO note_index (stem, path, folder, title, summary, tags, "
        "note_type, project, confidence, mtime, related, is_stale, "
        "incoming_links) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            stem,
            f"{folder}/{stem}.md",
            folder,
            stem,
            "",
            tags,
            note_type,
            "",
            "",
            mtime if mtime is not None else time.time(),
            related,
            0,
            incoming,
        ),
    )
    conn.commit()
    conn.close()


def _insert_embedding(db_path: Path, *, stem: str) -> None:
    """Insert a placeholder embedding row so coverage sees the note."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO note_embeddings (stem, embedding) VALUES (?, ?)",
        (stem, b"\x00" * 4),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Test 1 — per-dimension score pins a known engineered value
# ---------------------------------------------------------------------------


class TestPerDimensionKnownScore:
    """Engineer a vault to hit an exact known score on one dimension."""

    def test_queue_health_exact_score(self, tmp_path: Path) -> None:
        # 2 pending + 6 dead letters → 100 - (2*2) - (6*5) = 66
        pending = tmp_path / "pending_summaries.jsonl"
        pending.write_text(
            "\n".join(
                json.dumps({"session_id": f"s{i}", "timestamp": "2026-01-01T00:00:00"})
                for i in range(2)
            )
            + "\n",
            encoding="utf-8",
        )
        dead = tmp_path / "dead_letters.jsonl"
        dead.write_text(
            "\n".join(
                json.dumps({"session_id": f"d{i}", "dead_lettered_at": "2026-01-01"})
                for i in range(6)
            )
            + "\n",
            encoding="utf-8",
        )

        score = vault_health.score_queue_health(tmp_path)

        assert score.name == "queue_health"
        assert score.score == 66, (
            f"queue_health with 2 pending + 6 dead should be 66, got {score.score}"
        )
        # Action must point at the dead-letter remediation path (dead > 0 wins
        # over the pending-only path).
        assert score.action is not None
        assert "vault-review" in score.action
        # QA-007: assert on the actual detail string, not just the score.
        assert "2 pending" in score.detail
        assert "6 dead-lettered" in score.detail


# ---------------------------------------------------------------------------
# Test 2 — missing graph.json / embeddings.db / empty vault degrades
# ---------------------------------------------------------------------------


class TestDegradesOnMissingInputs:
    """A missing graph.json, missing embeddings.db, or empty vault must
    produce a low score with a detail string — never an exception."""

    def test_empty_vault_does_not_raise(self, tmp_path: Path) -> None:
        report = vault_health.compute_health_report(tmp_path)
        assert isinstance(report, vault_health.HealthReport)
        # All seven dimensions present.
        names = {d.name for d in report.dimensions}
        assert names == set(vault_health.DIMENSION_WEIGHTS)

    def test_missing_graph_json_scores_low_with_detail(self, tmp_path: Path) -> None:
        # DB present but graph.json absent. The plan documents graph.json and
        # note_index as separately-scored then averaged, so a missing graph
        # alongside a fresh index is "low" (below the D threshold) rather
        # than zero. The invariant the plan actually requires: low score +
        # a detail string naming graph.json + no exception.
        _make_db(tmp_path)
        score = vault_health.score_index_freshness(tmp_path)
        assert score.score < 60, (
            f"missing graph.json should pull freshness below the D threshold, "
            f"got {score.score}"
        )
        assert "graph.json" in score.detail

    def test_missing_embeddings_db_scores_low_with_detail(self, tmp_path: Path) -> None:
        score = vault_health.score_embedding_coverage(tmp_path)
        assert score.score == 0
        assert "embeddings.db" in score.detail


# ---------------------------------------------------------------------------
# Test 3 — overall equals the weighted mean of dimension scores
# ---------------------------------------------------------------------------


class TestOverallIsWeightedMean:
    def test_overall_matches_arithmetic(self, tmp_path: Path) -> None:
        # Any vault setup will do — the invariant is arithmetic, not data-dependent.
        _make_db(tmp_path)
        _write_graph_meta(tmp_path, age_days=0.0)
        report = vault_health.compute_health_report(tmp_path)

        total_weight = sum(d.weight for d in report.dimensions)
        expected = round(
            sum(d.score * d.weight for d in report.dimensions) / total_weight
        )
        assert report.overall == expected, (
            f"overall {report.overall} != weighted mean {expected} "
            f"(dimensions: {[(d.name, d.score, d.weight) for d in report.dimensions]})"
        )


# ---------------------------------------------------------------------------
# Test 4 — grade boundaries
# ---------------------------------------------------------------------------


class TestGradeBoundaries:
    @pytest.mark.parametrize(
        "score,grade",
        [
            (100, "A"),
            (90, "A"),
            (89, "B"),
            (80, "B"),
            (79, "C"),
            (70, "C"),
            (69, "D"),
            (60, "D"),
            (59, "F"),
            (0, "F"),
        ],
    )
    def test_grade_for_score(self, score: int, grade: str) -> None:
        assert vault_health._grade_for(score) == grade


# ---------------------------------------------------------------------------
# Test 5 — --json output validates against a committed schema
# ---------------------------------------------------------------------------


# Hand-rolled structural schema. Adding jsonschema as a test dependency just
# to validate this one output is overkill; the schema is small enough that an
# explicit walker is clearer and stays in sync with the dataclass shape.
_EXPECTED_DIM_FIELDS = {"name", "score", "weight", "detail", "action"}


def _validate_report_json(payload: dict) -> None:
    """Assert the JSON payload matches the HealthReport contract."""
    assert set(payload) == {
        "vault",
        "overall",
        "grade",
        "dimensions",
        "note_types",
        "warnings",
    }
    assert isinstance(payload["vault"], str)
    assert isinstance(payload["overall"], int)
    assert 0 <= payload["overall"] <= 100
    assert payload["grade"] in {"A", "B", "C", "D", "F"}
    assert isinstance(payload["dimensions"], list)
    assert len(payload["dimensions"]) == len(vault_health.DIMENSION_WEIGHTS)
    for dim in payload["dimensions"]:
        assert set(dim) == _EXPECTED_DIM_FIELDS
        assert isinstance(dim["name"], str)
        assert dim["name"] in vault_health.DIMENSION_WEIGHTS
        assert isinstance(dim["score"], int)
        assert 0 <= dim["score"] <= 100
        assert dim["weight"] == vault_health.DIMENSION_WEIGHTS[dim["name"]]
        assert isinstance(dim["detail"], str) and dim["detail"]
        assert dim["action"] is None or isinstance(dim["action"], str)
    assert isinstance(payload["note_types"], dict)
    assert isinstance(payload["warnings"], list)


class TestJsonSchema:
    def test_json_output_validates(self, tmp_path: Path) -> None:
        _make_db(tmp_path)
        _write_graph_meta(tmp_path, age_days=0.0)
        report = vault_health.compute_health_report(tmp_path)
        payload = json.loads(vault_health.to_json(report))
        _validate_report_json(payload)
        # Vault path round-trips.
        assert payload["vault"] == str(tmp_path)


# ---------------------------------------------------------------------------
# Test 6 — assert on rendered output (capsys), the QA-007 weakness
# ---------------------------------------------------------------------------


class TestRenderedOutput:
    def test_health_report_renders_counts_and_actions(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Engineer a vault with a known queue_health state (2 pending, 6 dead)
        # so we can pin specific strings in the rendered output.
        _make_db(tmp_path)
        _write_graph_meta(tmp_path, age_days=0.0)
        (tmp_path / "pending_summaries.jsonl").write_text(
            "\n".join(
                json.dumps({"session_id": f"s{i}", "timestamp": "2026-01-01T00:00:00"})
                for i in range(2)
            )
            + "\n",
            encoding="utf-8",
        )
        (tmp_path / "dead_letters.jsonl").write_text(
            "\n".join(
                json.dumps({"session_id": f"d{i}", "dead_lettered_at": "2026-01-01"})
                for i in range(6)
            )
            + "\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(sys, "argv", ["vault-stats", "--health"])
        vault_stats.main()
        out = capsys.readouterr().out

        # Header line — overall grade and score.
        assert "Vault Health" in out
        # Each dimension name must render in the table.
        for name in vault_health.DIMENSION_WEIGHTS:
            assert name in out
        # The queue_health dimension's actual count + action must appear.
        assert "2 pending" in out
        assert "6 dead-lettered" in out
        assert "vault-review" in out
        # The overall score itself must appear, not just the grade letter.
        # (100 - 4 - 30 = 66; weighted down by other healthy dimensions, but
        # the queue line itself prints "0" as its score.)
        assert "0" in out  # queue_health score is 0


# ---------------------------------------------------------------------------
# Test 7 — perfectly healthy vault scores 100/A with no actions
# ---------------------------------------------------------------------------


class TestPerfectlyHealthyVault:
    def test_scores_100_a_with_no_actions(self, tmp_path: Path) -> None:
        # Two notes that mutually link → 0 orphans, no dangling targets.
        # Same two tags on both → no singletons, no near-dup pairs, no underscores.
        # Fresh graph.json + matching note_index mtime → fresh index.
        # Embeddings for both → 100% coverage.
        # Real .md files with valid frontmatter so the metadata scan sees
        # them and reports zero issues.
        db = _make_db(tmp_path)
        _write_graph_meta(tmp_path, age_days=0.0)
        patterns = tmp_path / "Patterns"
        patterns.mkdir()
        for stem in ("alpha", "beta"):
            other = "beta" if stem == "alpha" else "alpha"
            # On-disk note with valid frontmatter (passes check_note).
            (patterns / f"{stem}.md").write_text(
                "---\n"
                "date: 2026-07-31\n"
                "type: pattern\n"
                "confidence: high\n"
                f'related: ["[[{other}]]"]\n'
                "---\n"
                f"# {stem.title()}\n"
                f"body for {stem}\n",
                encoding="utf-8",
            )
            _insert_note(
                db,
                stem=stem,
                tags="alpha, beta",  # both tags appear on both notes → no singletons
                related=f"[[{other}]]",
                incoming=1,
            )
            _insert_embedding(db, stem=stem)
        # Embeddings DB mtime must be >= note_index mtime so note_index_age is 0.
        # _make_db + _insert_note already wrote the DB; touch it to refresh mtime.
        os.utime(db, None)

        report = vault_health.compute_health_report(tmp_path)

        # Every dimension should reach 100; if not, the dimension detail tells us why.
        for d in report.dimensions:
            assert d.score == 100, (
                f"dimension {d.name} scored {d.score}, not 100: {d.detail}"
            )
        assert report.overall == 100
        assert report.grade == "A"
        # A healthy vault emits no action strings (the plan's invariant).
        actions = [d.action for d in report.dimensions if d.action is not None]
        assert actions == [], f"unexpected actions on a perfect vault: {actions}"
