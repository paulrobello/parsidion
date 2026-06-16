"""Tests for vault-conflicts (contradiction detection)."""

from __future__ import annotations

import json  # noqa: F401
import struct
import sys
from pathlib import Path

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import vault_conflicts  # noqa: E402


class TestParseJsonArray:
    def test_plain_array(self) -> None:
        text = '[{"type": "contradiction", "a": "x", "b": "y"}]'
        assert vault_conflicts._parse_json_array(text) == [
            {"type": "contradiction", "a": "x", "b": "y"}
        ]

    def test_strips_markdown_fence(self) -> None:
        text = "```json\n[]\n```"
        assert vault_conflicts._parse_json_array(text) == []

    def test_extracts_array_from_prose(self) -> None:
        text = 'Here are the conflicts:\n[{"a": "1", "b": "2"}]\nDone.'
        assert vault_conflicts._parse_json_array(text) == [{"a": "1", "b": "2"}]

    def test_empty_for_unparseable(self) -> None:
        assert vault_conflicts._parse_json_array("no json here") == []


class TestCosine:
    def test_identical_vectors_score_one(self) -> None:
        assert vault_conflicts._cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0

    def test_orthogonal_vectors_score_zero(self) -> None:
        assert vault_conflicts._cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_zero_vector_returns_zero(self) -> None:
        assert vault_conflicts._cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


class TestGroupClusters:
    def test_union_find_merges_transitively(self) -> None:
        # pairs: 0~1, 1~2  ->  {0,1,2}; 3~4 -> {3,4}
        pairs = [(0, 1), (1, 2), (3, 4)]
        clusters = vault_conflicts._group_clusters(5, pairs)
        clusters_sorted = sorted(sorted(c) for c in clusters)
        assert clusters_sorted == [[0, 1, 2], [3, 4]]

    def test_singletons_excluded(self) -> None:
        # Index 2 is isolated (no pairs) -> must NOT appear. Contract:
        # _group_clusters returns ONLY clusters with >= 2 members.
        assert vault_conflicts._group_clusters(3, [(0, 1)]) == [[0, 1]]


class TestModuleImports:
    def test_main_exists(self) -> None:
        assert callable(vault_conflicts.main)


def _seed_embeddings_db(vault: Path, rows: list[tuple[str, list[float], str]]) -> None:
    """Create a minimal note_embeddings table with hand-crafted vectors."""
    import sqlite3

    db = vault / "embeddings.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE note_embeddings ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, stem TEXT, path TEXT, folder TEXT, "
        "title TEXT, tags TEXT, mtime REAL, embedding BLOB)"
    )
    for stem, vec, title in rows:
        blob = struct.pack(f"{len(vec)}f", *vec)
        path = str(vault / "Patterns" / f"{stem}.md")
        (vault / "Patterns").mkdir(parents=True, exist_ok=True)
        (vault / "Patterns" / f"{stem}.md").write_text(
            f"---\ntype: pattern\n---\n# {title}\nbody\n", encoding="utf-8"
        )
        conn.execute(
            "INSERT INTO note_embeddings (stem, path, folder, title, tags, mtime, embedding) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (stem, path, "Patterns", title, "", 1000.0, blob),
        )
    conn.commit()
    conn.close()


class TestFindCandidateClusters:
    def test_clusters_similar_notes_excludes_daily(self, tmp_vault: Path) -> None:
        # a,b near-identical (same direction); c orthogonal.
        _seed_embeddings_db(
            tmp_vault,
            [
                ("note-a", [1.0, 0.0, 0.0], "A"),
                ("note-b", [0.99, 0.01, 0.0], "B"),
                ("note-c", [0.0, 0.0, 1.0], "C"),
            ],
        )
        clusters = vault_conflicts.find_candidate_clusters(
            tmp_vault, threshold=0.75, top=50
        )
        stems_per_cluster = [{rec["stem"] for rec in cluster} for cluster in clusters]
        assert any({"note-a", "note-b"} == s for s in stems_per_cluster)
        assert not any("note-c" in s for s in stems_per_cluster)
