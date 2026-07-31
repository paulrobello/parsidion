"""Tests for build_graph.py's par-mem body-link enrichment (Task 2).

Independence is byte-level: par-mem disabled/absent/failing must leave
graph.json identical to today's frontmatter-only output (no new meta keys,
no edge changes). ``pytest.importorskip("numpy")`` keeps the core suite
numpy-free — build_graph.py is a `uv run --no-project` script with its own
inline numpy dependency, not a project dependency.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
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

from tests.fake_parmem import FakeHealth, FakeParMem  # noqa: E402


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
    import parmem_backend

    parmem_backend.reset_parmem_cache()


NOTES = [("a", "Debugging"), ("b", "Patterns"), ("c", "Patterns")]


class TestBodyLinksAppended:
    def test_body_links_appended_and_meta_set(
        self,
        tmp_vault: Path,
        fake_parmem: FakeParMem,
        fake_parmem_health: FakeHealth,
        tmp_path: Path,
    ) -> None:
        make_embeddings_db(tmp_vault, NOTES)
        fake_parmem.configure(
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
        graph = run_build_graph(tmp_vault, out)
        assert graph["edges"] == [{"s": "a", "t": "b", "w": 1.0, "kind": "wiki"}]
        assert graph["meta"]["parmem_body_links"] == 1


class TestDedupeAgainstFrontmatter:
    def test_dedupes_against_frontmatter_related(
        self,
        tmp_vault: Path,
        fake_parmem: FakeParMem,
        fake_parmem_health: FakeHealth,
        tmp_path: Path,
    ) -> None:
        make_embeddings_db(tmp_vault, NOTES)
        set_related(tmp_vault, "a", "[[b]]")
        fake_parmem.configure(
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
        graph = run_build_graph(tmp_vault, out)
        # Exact equality (not just an a-b count) proves the par-mem duplicate
        # was fully absorbed into the existing frontmatter edge, not merely
        # outnumbered by it.
        assert graph["edges"] == [{"s": "a", "t": "b", "w": 1.0, "kind": "wiki"}]
        assert "parmem_body_links" not in graph["meta"]


class TestUnmappedAndSelfLinksDropped:
    def test_unmapped_and_self_links_dropped(
        self,
        tmp_vault: Path,
        fake_parmem: FakeParMem,
        fake_parmem_health: FakeHealth,
        tmp_path: Path,
    ) -> None:
        make_embeddings_db(tmp_vault, NOTES)
        fake_parmem.configure(
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
            }
        )
        out = tmp_path / "graph.json"
        graph = run_build_graph(tmp_vault, out)
        assert graph["edges"] == []
        assert "parmem_body_links" not in graph["meta"]


class TestNoParmemFlag:
    def test_no_parmem_flag_skips_call(
        self,
        tmp_vault: Path,
        fake_parmem: FakeParMem,
        fake_parmem_health: FakeHealth,
        tmp_path: Path,
    ) -> None:
        make_embeddings_db(tmp_vault, NOTES)
        fake_parmem.configure(
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
        graph = run_build_graph(tmp_vault, out, extra_args=["--no-parmem"])
        fake_parmem.assert_no_call("doc-links", settle=0.1)
        assert graph["edges"] == []
        assert "parmem_body_links" not in graph["meta"]


class TestParmemDisabledOutputIdentical:
    def test_parmem_disabled_output_identical(
        self,
        tmp_vault: Path,
        fake_parmem: FakeParMem,
        fake_parmem_health: FakeHealth,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        make_embeddings_db(tmp_vault, NOTES)
        fake_parmem.configure(
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
        # Run 1: par-mem is fully available (binary + health) but disabled
        # via config — build_parmem_body_edges must never even probe it.
        _write_config(tmp_vault, "par_mem:\n  enabled: false\n")
        out_disabled = tmp_path / "graph-disabled.json"
        graph_disabled = run_build_graph(tmp_vault, out_disabled)
        fake_parmem.assert_no_call("doc-links", settle=0.1)

        # Run 2 (control): binary entirely absent from PATH, config default
        # (enabled) — the pre-integration behavior.
        (tmp_vault / "config.yaml").unlink()
        vault_common.load_config.cache_clear()
        import parmem_backend

        parmem_backend.reset_parmem_cache()
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


class TestParmemFailureGraphStillWritten:
    def test_parmem_failure_graph_still_written(
        self,
        tmp_vault: Path,
        fake_parmem: FakeParMem,
        fake_parmem_health: FakeHealth,
        tmp_path: Path,
    ) -> None:
        make_embeddings_db(tmp_vault, NOTES)
        fake_parmem.configure(exit_codes={"doc-links": 1})
        out = tmp_path / "graph.json"
        graph = run_build_graph(tmp_vault, out)
        assert out.exists()
        assert graph["edges"] == []
        assert "parmem_body_links" not in graph["meta"]


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
            tmp_vault, out, extra_args=["--min-threshold", "1.0", "--no-parmem"]
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
        graph = run_build_graph(tmp_vault, out, extra_args=["--no-parmem"])
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
        fake_parmem: FakeParMem,
        fake_parmem_health: FakeHealth,
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
        # no-cap must still produce the same wiki edges. --no-parmem isolates
        # this from body-link enrichment.
        graph_on = run_build_graph(
            tmp_vault,
            out_on,
            extra_args=[
                "--min-threshold",
                "0.0",
                "--max-neighbors",
                "1",
                "--no-parmem",
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
                "--no-parmem",
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
