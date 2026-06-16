"""Tests for vault-conflicts (contradiction detection)."""

from __future__ import annotations

import json  # noqa: F401
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
