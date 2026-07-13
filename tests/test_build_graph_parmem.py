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
