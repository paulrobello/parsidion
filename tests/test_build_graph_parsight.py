"""Tests for build_graph.py's parsight body-link enrichment (Task 2).

Independence is byte-level: parsight disabled/absent/failing must leave
graph.json identical to today's frontmatter-only output (no new meta keys,
no edge changes). ``pytest.importorskip("numpy")`` keeps the core suite
numpy-free — build_graph.py is a `uv run --no-project` script with its own
inline numpy dependency, not a project dependency.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

np = pytest.importorskip("numpy")

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import build_graph  # noqa: E402
import vault_common  # noqa: E402

from tests.fake_parsight import FakeHealth, FakeParsight, fresh_repos_payload  # noqa: E402


def make_embeddings_db(vault: Path, stems_with_folders: list[tuple[str, str]]) -> None:
    """Minimal embeddings.db satisfying build_graph.py's two SELECTs."""
    conn = sqlite3.connect(vault / "embeddings.db")
    conn.execute(
        "CREATE TABLE note_index (stem TEXT, title TEXT, note_type TEXT, folder TEXT,"
        " tags TEXT, incoming_links INTEGER, related TEXT, mtime REAL, path TEXT)"
    )
    conn.execute("CREATE TABLE note_embeddings (stem TEXT, embedding BLOB)")
    rng = np.random.default_rng(42)
    for stem, folder in stems_with_folders:
        path = str(vault / folder / f"{stem}.md")
        conn.execute(
            "INSERT INTO note_index VALUES (?,?,?,?,?,?,?,?,?)",
            (stem, stem.title(), "note", folder, "", 0, "", 0.0, path),
        )
        vec = rng.standard_normal(384).astype(np.float32)
        conn.execute("INSERT INTO note_embeddings VALUES (?,?)", (stem, vec.tobytes()))
    conn.commit()
    conn.close()


def set_related(vault: Path, stem: str, related: str) -> None:
    """Patch a note_index row's ``related`` field after creation."""
    conn = sqlite3.connect(vault / "embeddings.db")
    conn.execute("UPDATE note_index SET related=? WHERE stem=?", (related, stem))
    conn.commit()
    conn.close()


def run_build_graph(
    vault: Path, out: Path, extra_args: list[str] | None = None
) -> dict:
    """Invoke build_graph.main() with patched argv; return parsed graph.json."""
    argv = [
        "build_graph.py",
        "--vault",
        str(vault),
        "--output",
        str(out),
        "--min-threshold",
        "1.01",  # → no semantic edges; wiki-only assertions
        *(extra_args or []),
    ]
    with mock.patch.object(sys, "argv", argv):
        build_graph.main()
    return json.loads(out.read_text(encoding="utf-8"))


def _write_config(vault: Path, text: str) -> None:
    (vault / "config.yaml").write_text(text, encoding="utf-8")
    vault_common.load_config.cache_clear()
    import parsight_backend

    parsight_backend.reset_parsight_cache()


NOTES = [("a", "Debugging"), ("b", "Patterns"), ("c", "Patterns")]


class TestBodyLinksAppended:
    def test_body_links_appended_and_meta_set(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
        tmp_path: Path,
    ) -> None:
        make_embeddings_db(tmp_vault, NOTES)
        fake_parsight.configure(
            repos=fresh_repos_payload(tmp_vault),
            doc_links={
                "links": [
                    {
                        "source_path": "Debugging/a.md",
                        "target_path": "Patterns/b.md",
                        "target_is_doc": True,
                        "count": 2,
                    }
                ],
                "total": 1,
                "truncated": False,
            },
        )
        out = tmp_path / "graph.json"
        graph = run_build_graph(tmp_vault, out)
        assert graph["edges"] == [{"s": "a", "t": "b", "w": 1.0, "kind": "wiki"}]
        assert graph["meta"]["parsight_body_links"] == 1
        assert graph["meta"]["parsight_body_status"] == "fresh"


class TestDedupeAgainstFrontmatter:
    def test_dedupes_against_frontmatter_related(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
        tmp_path: Path,
    ) -> None:
        make_embeddings_db(tmp_vault, NOTES)
        set_related(tmp_vault, "a", "[[b]]")
        fake_parsight.configure(
            repos=fresh_repos_payload(tmp_vault),
            doc_links={
                "links": [
                    {
                        "source_path": "Debugging/a.md",
                        "target_path": "Patterns/b.md",
                        "target_is_doc": True,
                        "count": 2,
                    }
                ],
                "total": 1,
                "truncated": False,
            },
        )
        out = tmp_path / "graph.json"
        graph = run_build_graph(tmp_vault, out)
        # Exact equality (not just an a-b count) proves the parsight duplicate
        # was fully absorbed into the existing frontmatter edge, not merely
        # outnumbered by it.
        assert graph["edges"] == [{"s": "a", "t": "b", "w": 1.0, "kind": "wiki"}]
        assert "parsight_body_links" not in graph["meta"]
        assert graph["meta"]["parsight_body_status"] == "fresh"


class TestUnmappedAndSelfLinksDropped:
    def test_unmapped_and_self_links_dropped(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
        tmp_path: Path,
    ) -> None:
        make_embeddings_db(tmp_vault, NOTES)
        fake_parsight.configure(
            repos=fresh_repos_payload(tmp_vault),
            doc_links={
                "links": [
                    {
                        "source_path": "MANIFEST.md",
                        "target_path": "Patterns/b.md",
                        "target_is_doc": True,
                        "count": 1,
                    },
                    {
                        "source_path": "Patterns/b.md",
                        "target_path": "Patterns/b.md",
                        "target_is_doc": True,
                        "count": 1,
                    },
                ],
                "total": 2,
                "truncated": False,
            },
        )
        out = tmp_path / "graph.json"
        graph = run_build_graph(tmp_vault, out)
        assert graph["edges"] == []
        assert "parsight_body_links" not in graph["meta"]
        assert graph["meta"]["parsight_body_status"] == "fresh"


class TestNoParsightFlag:
    def test_no_parsight_flag_skips_call(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
        tmp_path: Path,
    ) -> None:
        make_embeddings_db(tmp_vault, NOTES)
        fake_parsight.configure(
            doc_links={
                "links": [
                    {
                        "source_path": "Debugging/a.md",
                        "target_path": "Patterns/b.md",
                        "target_is_doc": True,
                        "count": 2,
                    }
                ],
                "total": 1,
                "truncated": False,
            }
        )
        out = tmp_path / "graph.json"
        graph = run_build_graph(tmp_vault, out, extra_args=["--no-parsight"])
        fake_parsight.assert_no_call("doc-links", settle=0.1)
        assert graph["edges"] == []
        assert "parsight_body_links" not in graph["meta"]


class TestParsightDisabledOutputIdentical:
    def test_parsight_disabled_output_identical(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        make_embeddings_db(tmp_vault, NOTES)
        fake_parsight.configure(
            doc_links={
                "links": [
                    {
                        "source_path": "Debugging/a.md",
                        "target_path": "Patterns/b.md",
                        "target_is_doc": True,
                        "count": 2,
                    }
                ],
                "total": 1,
                "truncated": False,
            }
        )
        # Run 1: parsight is fully available (binary + health) but disabled
        # via config — build_parsight_body_edges must never even probe it.
        _write_config(tmp_vault, "parsight:\n  enabled: false\n")
        out_disabled = tmp_path / "graph-disabled.json"
        graph_disabled = run_build_graph(tmp_vault, out_disabled)
        fake_parsight.assert_no_call("doc-links", settle=0.1)
        # Freshness gate never even runs: config availability fails first, so
        # the `repos` probe is never issued either.
        fake_parsight.assert_no_call("repos", settle=0.1)

        # Run 2 (control): binary entirely absent from PATH, config default
        # (enabled) — the pre-integration behavior.
        (tmp_vault / "config.yaml").unlink()
        vault_common.load_config.cache_clear()
        import parsight_backend

        parsight_backend.reset_parsight_cache()
        empty_bin = tmp_path / "empty-bin"
        empty_bin.mkdir()
        monkeypatch.setenv("PATH", str(empty_bin))
        out_absent = tmp_path / "graph-absent.json"
        graph_absent = run_build_graph(tmp_vault, out_absent)

        del graph_disabled["meta"]["generated"]
        del graph_absent["meta"]["generated"]
        assert json.dumps(graph_disabled, sort_keys=True) == json.dumps(
            graph_absent, sort_keys=True
        )


class TestParsightFailureGraphStillWritten:
    def test_parsight_failure_graph_still_written(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
        tmp_path: Path,
    ) -> None:
        make_embeddings_db(tmp_vault, NOTES)
        fake_parsight.configure(
            repos=fresh_repos_payload(tmp_vault), exit_codes={"doc-links": 1}
        )
        out = tmp_path / "graph.json"
        graph = run_build_graph(tmp_vault, out)
        assert out.exists()
        assert graph["edges"] == []
        assert "parsight_body_links" not in graph["meta"]
        # Fresh index, but the doc-links fetch failed — recorded as an error,
        # distinct from "ran cleanly, found nothing".
        assert graph["meta"]["parsight_body_status"] == "error"


class TestFreshnessGate:
    """build_parsight_body_edges must skip enrichment when the index is not fresh.

    A stale / mid-catch-up index returns a partial, run-to-run-variable link
    set; trusting it made two builds over identical input diverge. The gate
    skips the nondeterministic ``doc-links`` fetch entirely and records the
    reason in ``meta.parsight_body_status``.
    """

    _A_TO_B = {
        "links": [
            {
                "source_path": "Debugging/a.md",
                "target_path": "Patterns/b.md",
                "target_is_doc": True,
                "count": 2,
            }
        ],
        "total": 1,
        "truncated": False,
    }

    def test_stale_index_skips_enrichment_and_fetch(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
        tmp_path: Path,
    ) -> None:
        make_embeddings_db(tmp_vault, NOTES)
        fake_parsight.configure(
            repos=fresh_repos_payload(tmp_vault, stale=True), doc_links=self._A_TO_B
        )
        out = tmp_path / "graph.json"
        graph = run_build_graph(tmp_vault, out)
        assert graph["meta"]["parsight_body_status"] == "skipped:index-stale"
        assert "parsight_body_links" not in graph["meta"]
        assert graph["edges"] == []
        # The nondeterministic fetch is gated off — doc-links never runs...
        fake_parsight.assert_no_call("doc-links", settle=0.1)
        # ...but the freshness probe itself did.
        fake_parsight.wait_for_call("repos")

    def test_absent_index_skips_enrichment(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
        tmp_path: Path,
    ) -> None:
        make_embeddings_db(tmp_vault, NOTES)
        # Default repos payload is empty -> vault classified "absent".
        fake_parsight.configure(doc_links=self._A_TO_B)
        out = tmp_path / "graph.json"
        graph = run_build_graph(tmp_vault, out)
        assert graph["meta"]["parsight_body_status"] == "skipped:index-absent"
        assert "parsight_body_links" not in graph["meta"]
        fake_parsight.assert_no_call("doc-links", settle=0.1)

    def test_stale_index_is_deterministic(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
        tmp_path: Path,
    ) -> None:
        make_embeddings_db(tmp_vault, NOTES)
        fake_parsight.configure(
            repos=fresh_repos_payload(tmp_vault, stale=True), doc_links=self._A_TO_B
        )
        g1 = run_build_graph(tmp_vault, tmp_path / "g1.json")
        g2 = run_build_graph(tmp_vault, tmp_path / "g2.json")
        del g1["meta"]["generated"]
        del g2["meta"]["generated"]
        assert g1 == g2
        assert g1["meta"]["parsight_body_status"] == "skipped:index-stale"


class TestArc038GraphSchemaValidation:
    """ARC-038: a generated graph.json must conform to the committed schema.

    Reuses the stdlib validator defined in ``tests/test_graph_schema.py`` so
    there is one interpretation of the schema (no ``jsonschema`` dependency).
    Exercises nodes, wiki edges (via the ``related`` frontmatter field), and
    meta -- including referential integrity (edge endpoints must reference
    existing node ids), which the JSON Schema cannot express on its own.
    """

    def test_generated_graph_conforms_to_schema(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        from tests.test_graph_schema import validate_graph

        schema_path = (
            Path(__file__).resolve().parent.parent
            / "tests"
            / "fixtures"
            / "graph.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        make_embeddings_db(tmp_vault, NOTES)
        # Wire a wiki edge a -> b so the graph has both nodes and an edge to
        # validate. Override the shared helper's --min-threshold 1.01 with 1.0
        # (the schema caps min_semantic_threshold at 1.0 -- cosine similarity
        # cannot exceed it; 1.01 is a test-only trick that is out-of-contract).
        # Distinct random 384-dim vectors never reach cosine 1.0, so 1.0 still
        # suppresses semantic edges while staying inside the schema.
        set_related(tmp_vault, "a", "[[b]]")

        out = tmp_path / "graph-schema-check.json"
        graph = run_build_graph(
            tmp_vault, out, extra_args=["--min-threshold", "1.0", "--no-parsight"]
        )

        # Should have produced 3 nodes and at least the a-b wiki edge.
        assert len(graph["nodes"]) == 3
        assert any(e["kind"] == "wiki" for e in graph["edges"]), (
            "expected at least one wiki edge from the a->b related link"
        )
        # Full schema + referential-integrity validation (raises on failure).
        validate_graph(graph, schema)

    def test_generated_graph_rejects_dangling_edge_via_negative_check(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        """Sanity-check the validator: a graph with a dangling edge must fail.

        Guards against the validator being a no-op. If this passes, the schema
        gate is not actually enforcing referential integrity.
        """
        from tests.test_graph_schema import validate_graph

        schema_path = (
            Path(__file__).resolve().parent.parent
            / "tests"
            / "fixtures"
            / "graph.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        make_embeddings_db(tmp_vault, NOTES)
        out = tmp_path / "graph-dangling.json"
        graph = run_build_graph(tmp_vault, out, extra_args=["--no-parsight"])
        # Inject a dangling edge that references a non-existent node id.
        graph["edges"].append({"s": "a", "t": "ghost-node", "w": 1.0, "kind": "wiki"})
        with pytest.raises(AssertionError, match="does not reference a node id"):
            validate_graph(graph, schema)


class TestSemanticEdgeCap:
    """ENH-001: top-K-per-node semantic edge policy.

    Each note keeps its strongest ``max_neighbors`` neighbours above
    ``min_threshold``; a pair selected by either endpoint is kept once.
    """

    def _stems(self, n: int) -> list[str]:
        return [f"note{i}" for i in range(n)]

    def test_cap_honoured(self) -> None:
        # 50 nodes whose embeddings all point the same way → every pair clears
        # the threshold, so without the cap all C(50,2)=1225 pairs would be
        # kept. With max_neighbors=5 each node contributes at most 5 edges, so
        # the total is bounded by n * max_neighbors = 250 (far below 1225).
        # Per-node degree is NOT bounded by 2*max_neighbors here: in-degree is
        # unbounded under the union policy (a hub can be selected by many other
        # nodes), which is deliberate -- it is what keeps sparse notes connected.
        rng = np.random.default_rng(7)
        base = rng.standard_normal(384).astype(np.float32)
        matrix = np.stack(
            [
                base + 0.01 * rng.standard_normal(384).astype(np.float32)
                for _ in range(50)
            ]
        )
        edges = build_graph.build_semantic_edges(
            self._stems(50), matrix, min_threshold=0.5, max_neighbors=5
        )
        assert len(edges) <= 50 * 5  # hard cap: each node contributes ≤ max_neighbors
        assert len(edges) < 1225  # and far below the all-pairs count

    def test_threshold_still_floors(self) -> None:
        # max_neighbors >= n disables the cap; only pairs above the floor survive.
        rng = np.random.default_rng(11)
        matrix = rng.standard_normal((50, 384)).astype(np.float32)
        # Random 384-dim vectors sit near cosine 0; force exactly 3 identical
        # pairs (cosine 1.0) above the 0.70 floor.
        matrix[1] = matrix[0]
        matrix[3] = matrix[2]
        matrix[5] = matrix[4]
        edges = build_graph.build_semantic_edges(
            self._stems(50), matrix, min_threshold=0.70, max_neighbors=50
        )
        assert len(edges) == 3

    def test_sparse_note_stays_connected(self) -> None:
        # Node 0 sits at cosine ~0.71 to a dense 49-node cluster (>0.95
        # internally). The cluster's top-5 lists never include node 0, but
        # node 0 lists the cluster — the union rule must keep the edge.
        rng = np.random.default_rng(23)
        cluster = rng.standard_normal(384).astype(np.float32)
        dense = np.stack(
            [
                cluster + 0.001 * rng.standard_normal(384).astype(np.float32)
                for _ in range(49)
            ]
        )
        unit = cluster / np.linalg.norm(cluster)
        ortho = rng.standard_normal(384).astype(np.float32)
        ortho -= float(unit @ ortho) * unit  # make orthogonal to the cluster dir
        ortho /= np.linalg.norm(ortho)
        cos_theta = 0.71
        sin_theta = (1.0 - cos_theta * cos_theta) ** 0.5
        sparse = cos_theta * unit + sin_theta * ortho
        matrix = np.vstack([sparse.astype(np.float32)[None, :], dense])
        edges = build_graph.build_semantic_edges(
            self._stems(50), matrix, min_threshold=0.70, max_neighbors=5
        )
        assert any(e["s"] == "note0" or e["t"] == "note0" for e in edges), (
            "sparse node 0 lost all edges under the cap"
        )

    def test_wiki_edges_identical_with_cap_on_and_off(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
        tmp_path: Path,
    ) -> None:
        # The cap affects only semantic edges; the wiki edge set must be
        # identical whether the cap is on or off.
        make_embeddings_db(tmp_vault, NOTES)
        set_related(tmp_vault, "a", "[[b]]")
        set_related(tmp_vault, "b", "[[c]]")
        out_on = tmp_path / "on.json"
        out_off = tmp_path / "off.json"
        # Low threshold so semantic edges would flood without the cap; cap vs
        # no-cap must still produce the same wiki edges. --no-parsight isolates
        # this from body-link enrichment.
        graph_on = run_build_graph(
            tmp_vault,
            out_on,
            extra_args=[
                "--min-threshold",
                "0.0",
                "--max-neighbors",
                "1",
                "--no-parsight",
            ],
        )
        graph_off = run_build_graph(
            tmp_vault,
            out_off,
            extra_args=[
                "--min-threshold",
                "0.0",
                "--max-neighbors",
                "0",
                "--no-parsight",
            ],
        )
        wiki_on = {
            tuple(sorted((e["s"], e["t"])))
            for e in graph_on["edges"]
            if e["kind"] == "wiki"
        }
        wiki_off = {
            tuple(sorted((e["s"], e["t"])))
            for e in graph_off["edges"]
            if e["kind"] == "wiki"
        }
        assert wiki_on == wiki_off
        assert wiki_on  # non-empty: a-b and b-c

    def test_max_neighbors_zero_matches_all_pairs(self) -> None:
        # max_neighbors=0 disables the cap and must reproduce the pre-change
        # upper-triangle walk exactly (same pairs, same weights).
        rng = np.random.default_rng(5)
        stems = self._stems(30)
        matrix = rng.standard_normal((30, 384)).astype(np.float32)
        matrix[2] = matrix[0]
        matrix[5] = matrix[3]
        edges = build_graph.build_semantic_edges(
            stems, matrix, min_threshold=0.5, max_neighbors=0
        )
        # Reference: manual upper-triangle computation (pre-ENH-001 behaviour).
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        normed = matrix / norms
        sim = normed @ normed.T
        expected = []
        for i in range(30):
            for j in range(i + 1, 30):
                w = float(sim[i, j])
                if w >= 0.5:
                    expected.append((stems[i], stems[j], round(w, 4)))
        got = sorted((e["s"], e["t"], e["w"]) for e in edges)
        expected.sort()
        assert got == expected

    def test_no_self_edges(self) -> None:
        # Identical rows would tempt self-selection if the diagonal guard
        # (np.fill_diagonal(sim, -1.0)) were missing.
        rng = np.random.default_rng(9)
        matrix = rng.standard_normal((20, 384)).astype(np.float32)
        matrix[1] = matrix[0]
        edges = build_graph.build_semantic_edges(
            self._stems(20), matrix, min_threshold=0.0, max_neighbors=3
        )
        assert edges  # something was emitted
        assert all(e["s"] != e["t"] for e in edges)


# ---------------------------------------------------------------------------
# ENH-002: incremental graph generation
# ---------------------------------------------------------------------------


def _vec_at_angle(angle: float, dim: int = 384):
    """Unit vector at ``angle`` in the first 2 dims, zeros elsewhere.

    Lets the eviction test express exact cosine relationships (cosine between
    two such vectors = cos(|Δangle|)) without relying on RNG or fragile
    near-duplicate noise.
    """
    v = np.zeros(dim, dtype=np.float32)
    v[0] = float(np.cos(angle))
    v[1] = float(np.sin(angle))
    return v


def _make_db_with_vectors(
    vault: Path, items: list[tuple[str, str, Any, float]]
) -> None:
    """Build embeddings.db with explicit (stem, folder, vector, mtime) rows."""
    conn = sqlite3.connect(vault / "embeddings.db")
    conn.execute(
        "CREATE TABLE note_index (stem TEXT, title TEXT, note_type TEXT, folder TEXT,"
        " tags TEXT, incoming_links INTEGER, related TEXT, mtime REAL, path TEXT)"
    )
    conn.execute("CREATE TABLE note_embeddings (stem TEXT, embedding BLOB)")
    for stem, folder, vec, mtime in items:
        path = str(vault / folder / f"{stem}.md")
        conn.execute(
            "INSERT INTO note_index VALUES (?,?,?,?,?,?,?,?,?)",
            (stem, stem.title(), "note", folder, "", 0, "", mtime, path),
        )
        conn.execute(
            "INSERT INTO note_embeddings VALUES (?,?)",
            (stem, vec.astype(np.float32).tobytes()),
        )
    conn.commit()
    conn.close()


def _bump_mtime(vault: Path, stem: str, mtime: float) -> None:
    conn = sqlite3.connect(vault / "embeddings.db")
    conn.execute("UPDATE note_index SET mtime=? WHERE stem=?", (mtime, stem))
    conn.commit()
    conn.close()


def _update_embedding(vault: Path, stem: str, vec: Any) -> None:
    conn = sqlite3.connect(vault / "embeddings.db")
    conn.execute(
        "UPDATE note_embeddings SET embedding=? WHERE stem=?",
        (vec.astype(np.float32).tobytes(), stem),
    )
    conn.commit()
    conn.close()


def _add_note(vault: Path, stem: str, folder: str, vec: Any, mtime: float) -> None:
    conn = sqlite3.connect(vault / "embeddings.db")
    path = str(vault / folder / f"{stem}.md")
    conn.execute(
        "INSERT INTO note_index VALUES (?,?,?,?,?,?,?,?,?)",
        (stem, stem.title(), "note", folder, "", 0, "", mtime, path),
    )
    conn.execute(
        "INSERT INTO note_embeddings VALUES (?,?)",
        (stem, vec.astype(np.float32).tobytes()),
    )
    conn.commit()
    conn.close()


def _delete_note(vault: Path, stem: str) -> None:
    conn = sqlite3.connect(vault / "embeddings.db")
    conn.execute("DELETE FROM note_index WHERE stem=?", (stem,))
    conn.execute("DELETE FROM note_embeddings WHERE stem=?", (stem,))
    conn.commit()
    conn.close()


def _run_build(vault: Path, out: Path, *extra: str) -> dict:
    """Invoke build_graph.main() with explicit argv control (no forced threshold)."""
    argv = [
        "build_graph.py",
        "--vault",
        str(vault),
        "--output",
        str(out),
        "--no-parsight",  # ENH-002 tests isolate from parsight nondeterminism
        *extra,
    ]
    with mock.patch.object(sys, "argv", argv):
        build_graph.main()
    return json.loads(out.read_text(encoding="utf-8"))


def _edge_set(graph: dict) -> set[tuple[str, str, str]]:
    return {(e["s"], e["t"], e["kind"]) for e in graph["edges"]}


class TestIncrementalEquivalence:
    """ENH-002 test 1 — the non-negotiable gate.

    Incremental and full-rebuild edge sets must be IDENTICAL (set equality on
    (s, t, kind)), not merely the same count. A timing win with a divergent
    edge set is a failure.
    """

    def test_incremental_matches_full_after_modifications(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        rng = np.random.default_rng(123)
        items = [
            (f"note{i}", "Patterns", rng.standard_normal(384).astype(np.float32), 0.0)
            for i in range(12)
        ]
        _make_db_with_vectors(tmp_vault, items)

        out1 = tmp_path / "g-full-1.json"
        _run_build(tmp_vault, out1, "--min-threshold", "0.40", "--max-neighbors", "4")
        # Bump two notes' mtimes well past graph_full_1's `generated` so the
        # incremental loader sees them as changed; also re-embed them so the
        # similarity structure actually changes (mtime-only changes would make
        # this test trivially pass — the recompute would emit identical edges).
        import time

        changed_mtime = time.time() + 1000.0
        _bump_mtime(tmp_vault, "note0", changed_mtime)
        _bump_mtime(tmp_vault, "note5", changed_mtime)
        _update_embedding(
            tmp_vault, "note0", rng.standard_normal(384).astype(np.float32)
        )
        _update_embedding(
            tmp_vault, "note5", rng.standard_normal(384).astype(np.float32)
        )

        # Incremental reuses the previous graph at the SAME output path — that
        # is the contract load_previous_graph checks. Writing to a different
        # path would correctly fall back to a full rebuild (test 6 covers that).
        graph_inc = _run_build(
            tmp_vault,
            out1,
            "--min-threshold",
            "0.40",
            "--max-neighbors",
            "4",
            "--incremental",
        )
        # Sanity: the incremental flag actually fired (meta.incremental set).
        assert graph_inc["meta"].get("incremental") is True

        out2 = tmp_path / "g-full-2.json"
        graph_full_2 = _run_build(
            tmp_vault, out2, "--min-threshold", "0.40", "--max-neighbors", "4"
        )

        inc_set = _edge_set(graph_inc)
        full_set = _edge_set(graph_full_2)
        assert inc_set == full_set, (
            f"incremental diverged from full rebuild:\n"
            f"  incremental {len(inc_set)} edges, full {len(full_set)} edges\n"
            f"  only-incremental: {len(inc_set - full_set)} "
            f"{sorted(inc_set - full_set)[:5]}\n"
            f"  only-full: {len(full_set - inc_set)} "
            f"{sorted(full_set - inc_set)[:5]}"
        )


class TestIncrementalParameterChangeForcesFull:
    """ENH-002 test 2 — a parameter change must not reuse anything."""

    def test_max_neighbors_change_forces_full_rebuild(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        rng = np.random.default_rng(7)
        items = [
            (f"n{i}", "Patterns", rng.standard_normal(384).astype(np.float32), 0.0)
            for i in range(8)
        ]
        _make_db_with_vectors(tmp_vault, items)

        out1 = tmp_path / "g-15.json"
        _run_build(tmp_vault, out1, "--min-threshold", "0.0", "--max-neighbors", "15")
        # Reuse out1 as the previous graph; the incremental run must reject it
        # because max_neighbors differs (15 → 5).
        graph_inc = _run_build(
            tmp_vault,
            out1,
            "--min-threshold",
            "0.0",
            "--max-neighbors",
            "5",
            "--incremental",
        )
        # Full rebuild happened: no incremental flag, max_neighbors is the new
        # value, and schema_version is current.
        assert "incremental" not in graph_inc["meta"]
        assert graph_inc["meta"]["max_neighbors"] == 5
        assert graph_inc["meta"]["schema_version"] == build_graph.GRAPH_SCHEMA_VERSION

    def test_load_previous_graph_rejects_threshold_mismatch(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        rng = np.random.default_rng(3)
        items = [
            (f"n{i}", "Patterns", rng.standard_normal(384).astype(np.float32), 0.0)
            for i in range(4)
        ]
        _make_db_with_vectors(tmp_vault, items)
        out = tmp_path / "g.json"
        _run_build(tmp_vault, out, "--min-threshold", "0.50")
        # Same args → reusable.
        argv_ok = _ns(threshold=0.50, max_neighbors=15, include_daily=True)
        assert build_graph.load_previous_graph(out, argv_ok) is not None
        # Different threshold → rejected.
        argv_bad = _ns(threshold=0.90, max_neighbors=15, include_daily=True)
        assert build_graph.load_previous_graph(out, argv_bad) is None


class TestIncrementalSchemaVersionMismatch:
    """ENH-002 test 3 — a schema_version mismatch forces a full rebuild."""

    def test_old_schema_version_forces_full_rebuild(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        rng = np.random.default_rng(11)
        items = [
            (f"n{i}", "Patterns", rng.standard_normal(384).astype(np.float32), 0.0)
            for i in range(4)
        ]
        _make_db_with_vectors(tmp_vault, items)
        out = tmp_path / "g.json"
        prev = _run_build(tmp_vault, out, "--min-threshold", "0.0")
        # Tamper with schema_version to simulate a v1 graph.
        prev["meta"]["schema_version"] = 1
        out.write_text(json.dumps(prev), encoding="utf-8")

        graph = _run_build(
            tmp_vault,
            out,
            "--min-threshold",
            "0.0",
            "--incremental",
        )
        # Full rebuild: incremental flag absent, schema_version restored to current.
        assert "incremental" not in graph["meta"]
        assert graph["meta"]["schema_version"] == build_graph.GRAPH_SCHEMA_VERSION


class TestIncrementalDeletedNoteVanishes:
    """ENH-002 test 4 — a deleted note's node and edges disappear."""

    def test_deleted_note_node_and_edges_absent(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        # Three notes on a line: a-b-c with b similar to both, so deleting b
        # removes edges (a,b) and (b,c).
        items = [
            ("a", "Patterns", _vec_at_angle(0.0), 0.0),
            ("b", "Patterns", _vec_at_angle(0.4), 0.0),
            ("c", "Patterns", _vec_at_angle(0.8), 0.0),
        ]
        _make_db_with_vectors(tmp_vault, items)
        out = tmp_path / "g.json"
        _run_build(tmp_vault, out, "--min-threshold", "0.40", "--max-neighbors", "5")

        import time

        _delete_note(tmp_vault, "b")
        # Bump a and c so the incremental loader sees a reason to recompute
        # (otherwise the changed set is empty and only the node list updates).
        now = time.time() + 1000.0
        _bump_mtime(tmp_vault, "a", now)
        _bump_mtime(tmp_vault, "c", now)

        graph = _run_build(
            tmp_vault,
            out,
            "--min-threshold",
            "0.40",
            "--max-neighbors",
            "5",
            "--incremental",
        )
        node_ids = {n["id"] for n in graph["nodes"]}
        assert "b" not in node_ids
        for e in graph["edges"]:
            assert "b" not in (e["s"], e["t"]), (
                f"deleted note 'b' still referenced by edge {e}"
            )


class TestIncrementalAddedNoteConnected:
    """ENH-002 test 5 — an added note appears and is connected."""

    def test_added_note_appears_and_connects(
        self, tmp_vault: Path, tmp_path: Path
    ) -> None:
        items = [
            ("a", "Patterns", _vec_at_angle(0.0), 0.0),
            ("b", "Patterns", _vec_at_angle(0.3), 0.0),
        ]
        _make_db_with_vectors(tmp_vault, items)
        out = tmp_path / "g.json"
        _run_build(tmp_vault, out, "--min-threshold", "0.50", "--max-neighbors", "3")

        import time

        now = time.time() + 1000.0
        # c very close to a (cosine ~0.99) — well above the 0.50 floor.
        _add_note(tmp_vault, "c", "Patterns", _vec_at_angle(0.05), now)

        graph = _run_build(
            tmp_vault,
            out,
            "--min-threshold",
            "0.50",
            "--max-neighbors",
            "3",
            "--incremental",
        )
        node_ids = {n["id"] for n in graph["nodes"]}
        assert "c" in node_ids
        # c must have at least one edge to a (its strongest neighbour).
        ac_edges = [
            e
            for e in graph["edges"]
            if "c" in (e["s"], e["t"]) and "a" in (e["s"], e["t"])
        ]
        assert ac_edges, "added note c has no edge to its nearest neighbour a"


class TestIncrementalMissingPreviousGraph:
    """ENH-002 test 6 — a missing previous graph falls back cleanly."""

    def test_no_previous_graph_no_crash(self, tmp_vault: Path, tmp_path: Path) -> None:
        rng = np.random.default_rng(5)
        items = [
            (f"n{i}", "Patterns", rng.standard_normal(384).astype(np.float32), 0.0)
            for i in range(4)
        ]
        _make_db_with_vectors(tmp_vault, items)
        # Point output at a path that doesn't exist yet; --incremental must
        # collapse to a full rebuild rather than raise.
        out = tmp_path / "fresh.json"
        graph = _run_build(
            tmp_vault,
            out,
            "--min-threshold",
            "0.0",
            "--incremental",
        )
        assert out.exists()
        assert "incremental" not in graph["meta"]
        assert len(graph["nodes"]) == 4


class TestIncrementalEvictionHandled:
    """ENH-002 test 7 — the closure handles top-K eviction by a new note.

    Add a note strongly similar to an existing note whose single edge to a
    third note exists only from its own side. The new note evicts that edge.
    Without the similarity-based closure (recomputing only the new note) the
    incremental graph would keep the evicted edge and diverge from the full
    rebuild; the closure recomputes the affected neighbour and matches.
    """

    def test_evicted_edge_is_gone(self, tmp_vault: Path, tmp_path: Path) -> None:
        # Layout (angles in radians; cosine = cos(|Δ|)):
        #   M @ 0.0, O @ -0.1, N @ 0.45
        # cos(N,M) = cos(0.45) ≈ 0.900  → N's top-1 = M (M > O for N: cos(N,O)=cos(0.55)≈0.852)
        # cos(M,O) = cos(0.10) ≈ 0.995  → M's top-1 = O (so M does NOT pick N)
        # cos(O,M) = cos(0.10) ≈ 0.995  → O's top-1 = M
        # So edge (N,M) exists solely because N picks M.
        items = [
            ("m", "Patterns", _vec_at_angle(0.0), 0.0),
            ("o", "Patterns", _vec_at_angle(-0.10), 0.0),
            ("n", "Patterns", _vec_at_angle(0.45), 0.0),
        ]
        _make_db_with_vectors(tmp_vault, items)
        out = tmp_path / "g.json"
        graph_before = _run_build(
            tmp_vault, out, "--min-threshold", "0.80", "--max-neighbors", "1"
        )
        before = _edge_set(graph_before)
        # Sanity: (m,n) is present (N picks M, union dedup keeps it once).
        assert ("m", "n", "semantic") in before or ("n", "m", "semantic") in before, (
            f"precondition failed: (m,n) edge not in before-graph {before}"
        )

        import time

        now = time.time() + 1000.0
        # X @ 0.50: cos(X,N)=cos(0.05)≈0.998 > cos(N,M)=0.900 → X enters N's
        # top-1, evicting M. cos(X,M)=cos(0.50)≈0.878 still clears the 0.80
        # floor but does not displace O from M's top-1 (cos(M,O)=0.995).
        _add_note(tmp_vault, "x", "Patterns", _vec_at_angle(0.50), now)

        graph_inc = _run_build(
            tmp_vault,
            out,
            "--min-threshold",
            "0.80",
            "--max-neighbors",
            "1",
            "--incremental",
        )
        out_full = tmp_path / "g-full.json"
        graph_full = _run_build(
            tmp_vault, out_full, "--min-threshold", "0.80", "--max-neighbors", "1"
        )

        inc_set = _edge_set(graph_inc)
        full_set = _edge_set(graph_full)
        # The gate: incremental must match full.
        assert inc_set == full_set, (
            f"incremental diverged from full on eviction:\n"
            f"  only-incremental: {sorted(inc_set - full_set)}\n"
            f"  only-full: {sorted(full_set - inc_set)}"
        )
        # And the evicted edge (m,n) is gone in both (the closure did work):
        for e in inc_set:
            assert not ({e[0], e[1]} == {"m", "n"} and e[2] == "semantic"), (
                f"evicted edge (m,n) survived in incremental graph: {e}"
            )
        # The new edge (n,x) is present in both.
        assert any({e[0], e[1]} == {"n", "x"} for e in inc_set), (
            f"new edge (n,x) missing from incremental graph: {inc_set}"
        )


class TestIncrementalClosureUnit:
    """Unit coverage for the helpers that underpin the incremental merge."""

    def test_load_previous_graph_missing_returns_none(self, tmp_path: Path) -> None:
        args = _ns(threshold=0.7, max_neighbors=15, include_daily=True)
        assert build_graph.load_previous_graph(tmp_path / "absent.json", args) is None

    def test_load_previous_graph_corrupt_returns_none(self, tmp_path: Path) -> None:
        bad = tmp_path / "corrupt.json"
        bad.write_text("{not json", encoding="utf-8")
        args = _ns(threshold=0.7, max_neighbors=15, include_daily=True)
        assert build_graph.load_previous_graph(bad, args) is None

    def test_expand_recompute_set_adds_prev_neighbours(self) -> None:
        prev_edges = [
            {"s": "a", "t": "b", "kind": "semantic"},
            {"s": "b", "t": "c", "kind": "semantic"},
            {"s": "x", "t": "y", "kind": "semantic"},  # neither in changed
            {"s": "a", "t": "z", "kind": "wiki"},  # wiki, skipped
        ]
        out = build_graph.expand_recompute_set({"a"}, prev_edges)
        assert out == {"a", "b"}

    def test_extend_recompute_closure_catches_forward_neighbours(self) -> None:
        # X (new) is similar to N; closure must pull N in via similarity even
        # though the prev-edge seed ({X} alone) would not.
        stems = ["x", "n", "m"]
        vecs = np.stack([_vec_at_angle(0.50), _vec_at_angle(0.45), _vec_at_angle(0.0)])
        normalized = build_graph._normalize_rows(vecs)
        closure = build_graph.extend_recompute_closure(
            stems,
            normalized,
            {"x"},
            [],  # no prev semantic edges (X is brand-new)
            min_threshold=0.80,
            max_neighbors=1,
        )
        assert "n" in closure, f"closure missed forward neighbour N: {closure}"

    def test_extend_recompute_closure_includes_prev_edge_partners(self) -> None:
        # Y is in the seed; (Y, Z) is a prev semantic edge. Z must be added so
        # the merge recomputes both endpoints (otherwise the edge is dropped
        # and only re-emitted from Y's perspective, silently losing it when Z
        # was the selecting endpoint).
        stems = ["y", "z", "w"]
        vecs = np.stack([_vec_at_angle(0.0), _vec_at_angle(0.3), _vec_at_angle(0.9)])
        normalized = build_graph._normalize_rows(vecs)
        prev_semantic = [{"s": "y", "t": "z", "kind": "semantic"}]
        closure = build_graph.extend_recompute_closure(
            stems,
            normalized,
            {"y"},
            prev_semantic,
            min_threshold=0.80,
            max_neighbors=1,
        )
        assert "z" in closure, f"closure missed prev-edge partner Z: {closure}"


def _ns(
    *, threshold: float, max_neighbors: int, include_daily: bool
) -> argparse.Namespace:
    """Build a minimal argparse.Namespace for load_previous_graph."""
    return argparse.Namespace(
        min_threshold=threshold,
        max_neighbors=max_neighbors,
        include_daily=include_daily,
    )
