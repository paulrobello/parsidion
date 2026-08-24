"""QA-015: direct tests for ``check_graph_coverage.py``.

The audit found no test file referencing this module. These tests pin the
pure loaders (color groups from graph.json, tag counts from TAGS.md) and
the group suggester against fixture files.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import check_graph_coverage as cgc  # noqa: E402


@pytest.fixture()
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the module's vault paths at a fixture directory."""
    monkeypatch.setattr(cgc, "GRAPH_JSON", tmp_path / ".obsidian" / "graph.json")
    monkeypatch.setattr(cgc, "CLAUDE_MD", tmp_path / "CLAUDE.md")
    return tmp_path


def test_load_graph_tags_extracts_tags_from_color_group_queries(
    vault: Path,
) -> None:
    graph = {
        "colorGroups": [
            {"query": "tag:#python"},
            {"query": "tag:#rust or tag:#zig"},
        ]
    }
    graph_json = vault / ".obsidian" / "graph.json"
    graph_json.parent.mkdir(parents=True, exist_ok=True)
    graph_json.write_text(json.dumps(graph), encoding="utf-8")

    groups = cgc.load_graph_tags()
    assert groups["tag:#python"] == ["python"]
    assert sorted(groups["tag:#rust or tag:#zig"]) == ["rust", "zig"]


def test_load_graph_tags_missing_file_exits(vault: Path) -> None:
    with pytest.raises(SystemExit):
        cgc.load_graph_tags()


def test_load_vault_tag_counts_reads_tag_cloud(vault: Path) -> None:
    (vault / "TAGS.md").write_text(
        "# Tags\n\n## Tag Cloud\n`python` (12) · `vault` (3) · `hook` (7)\n",
        encoding="utf-8",
    )
    counts = cgc.load_vault_tag_counts()
    assert counts["python"] == 12
    assert counts["vault"] == 3
    assert counts["hook"] == 7


def test_load_vault_tags_reads_existing_tags_list(vault: Path) -> None:
    (vault / "TAGS.md").write_text(
        "# Tags\n\n## Existing Tags\n\npython, vault, hook\n\n## Tag Cloud\nx\n",
        encoding="utf-8",
    )
    assert cgc.load_vault_tags() == {"python", "vault", "hook"}


def test_suggest_group_maps_keyword_families() -> None:
    assert cgc._suggest_group("python") == "Languages"
    assert cgc._suggest_group("voxel-world") == "Graphics / 3D"
    assert cgc._suggest_group("vt100") == "Terminal"


def test_main_reports_uncovered_tags(
    vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    graph = {"colorGroups": [{"query": "tag:#python"}]}
    graph_json = vault / ".obsidian" / "graph.json"
    graph_json.parent.mkdir(parents=True, exist_ok=True)
    graph_json.write_text(json.dumps(graph), encoding="utf-8")
    (vault / "TAGS.md").write_text(
        "# Tags\n\n## Existing Tags\n\npython, mysterytag\n\n"
        "## Tag Cloud\n`python` (5) · `mysterytag` (2)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["check_graph_coverage.py"])
    cgc.main()
    out = capsys.readouterr().out
    assert "mysterytag" in out
    assert "python" not in out  # covered tag is not reported as uncovered
