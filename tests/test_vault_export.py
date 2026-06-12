"""Tests for vault_export.py — note collection and markdown renderer.

QA-006: vault_export (551 lines) has zero dedicated tests.
These tests cover _collect_notes (filter logic), _md_to_html (renderer),
and the ZIP export helper.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import vault_common  # noqa: E402
import vault_export  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vault_common, "VAULT_ROOT", tmp_path)
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    vault_common.load_config.cache_clear()  # type: ignore[attr-defined]


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    for d in vault_common.VAULT_DIRS:
        (tmp_path / d).mkdir(exist_ok=True)
    return tmp_path


def _note(vault: Path, rel_path: str, frontmatter: str, body: str) -> Path:
    p = vault / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _collect_notes — fallback walk (no DB)
# ---------------------------------------------------------------------------


class TestCollectNotes:
    def test_collects_all_notes_when_no_filters(self, vault: Path) -> None:
        _note(vault, "Patterns/a.md", "tags: [python]", "# A\n")
        _note(vault, "Debugging/b.md", "tags: [rust]", "# B\n")
        results = vault_export._collect_notes(None, None, None, vault)
        stems = [p.stem for p in results]
        assert "a" in stems
        assert "b" in stems

    def test_filters_by_folder(self, vault: Path) -> None:
        _note(vault, "Patterns/pat.md", "tags: []", "# Pat\n")
        _note(vault, "Debugging/dbg.md", "tags: []", "# Dbg\n")
        results = vault_export._collect_notes(None, "Patterns", None, vault)
        stems = [p.stem for p in results]
        assert "pat" in stems
        assert "dbg" not in stems

    def test_filters_by_tag(self, vault: Path) -> None:
        _note(vault, "Patterns/py.md", "tags: [python, vault]", "# Py\n")
        _note(vault, "Patterns/rs.md", "tags: [rust]", "# Rs\n")
        results = vault_export._collect_notes(None, None, "python", vault)
        stems = [p.stem for p in results]
        assert "py" in stems
        assert "rs" not in stems

    def test_filters_by_project(self, vault: Path) -> None:
        _note(vault, "Projects/p1.md", "project: parsidion", "# P1\n")
        _note(vault, "Projects/p2.md", "project: other", "# P2\n")
        results = vault_export._collect_notes("parsidion", None, None, vault)
        stems = [p.stem for p in results]
        assert "p1" in stems
        assert "p2" not in stems

    def test_tag_filter_case_insensitive(self, vault: Path) -> None:
        _note(vault, "Patterns/ci.md", "tags: [Python]", "# CI\n")
        results = vault_export._collect_notes(None, None, "python", vault)
        assert any(p.stem == "ci" for p in results)

    def test_returns_sorted_paths(self, vault: Path) -> None:
        _note(vault, "Patterns/z-note.md", "", "# Z\n")
        _note(vault, "Patterns/a-note.md", "", "# A\n")
        results = vault_export._collect_notes(None, None, None, vault)
        paths = [str(p) for p in results]
        assert paths == sorted(paths)


# ---------------------------------------------------------------------------
# _md_to_html — markdown renderer
# ---------------------------------------------------------------------------


class TestMdToHtml:
    def test_renders_h1(self) -> None:
        out = vault_export._md_to_html("# My Title")
        assert "<h1>" in out
        assert "My Title" in out

    def test_renders_h2(self) -> None:
        out = vault_export._md_to_html("## Section")
        assert "<h2>" in out

    def test_renders_h3(self) -> None:
        out = vault_export._md_to_html("### Sub")
        assert "<h3>" in out

    def test_renders_bold(self) -> None:
        out = vault_export._md_to_html("This is **bold** text.")
        assert "<strong>bold</strong>" in out

    def test_renders_italic(self) -> None:
        out = vault_export._md_to_html("This is *italic* text.")
        assert "<em>italic</em>" in out

    def test_renders_inline_code(self) -> None:
        out = vault_export._md_to_html("Use `vault-search` to search.")
        assert "<code>vault-search</code>" in out

    def test_renders_fenced_code_block(self) -> None:
        md = "```python\nprint('hello')\n```"
        out = vault_export._md_to_html(md)
        assert "<pre>" in out
        assert "print" in out

    def test_renders_wikilink(self) -> None:
        out = vault_export._md_to_html("See [[my-note]] for details.")
        assert 'class="wikilink"' in out
        assert "my-note" in out

    def test_renders_horizontal_rule(self) -> None:
        out = vault_export._md_to_html("---")
        assert "<hr>" in out

    def test_renders_blockquote(self) -> None:
        out = vault_export._md_to_html("> Important note")
        assert "<blockquote>" in out
        assert "Important note" in out

    def test_escapes_html_in_body(self) -> None:
        out = vault_export._md_to_html("Plain <script>alert(1)</script> text.")
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_empty_input(self) -> None:
        out = vault_export._md_to_html("")
        assert isinstance(out, str)

    def test_renders_unordered_list(self) -> None:
        out = vault_export._md_to_html("- item one\n- item two")
        assert "<ul>" in out
        assert "<li>" in out

    def test_renders_ordered_list(self) -> None:
        out = vault_export._md_to_html("1. first\n2. second")
        assert "<ol>" in out
        assert "<li>" in out


# ---------------------------------------------------------------------------
# ZIP export
# ---------------------------------------------------------------------------


class TestZipExport:
    def test_zip_contains_md_files(self, vault: Path, tmp_path: Path) -> None:
        _note(vault, "Patterns/exported.md", "tags: [python]", "# Exported\n")
        zip_path = tmp_path / "out.zip"

        vault_export._cmd_zip(
            output_file=zip_path, project=None, folder=None, tag=None, vault_path=vault
        )

        assert zip_path.exists()
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert any("exported.md" in n for n in names)

    def test_zip_filter_by_folder(self, vault: Path, tmp_path: Path) -> None:
        _note(vault, "Patterns/keep.md", "", "# Keep\n")
        _note(vault, "Debugging/skip.md", "", "# Skip\n")
        zip_path = tmp_path / "filtered.zip"

        vault_export._cmd_zip(
            output_file=zip_path, project=None, folder="Patterns", tag=None, vault_path=vault
        )

        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert any("keep.md" in n for n in names)
        assert not any("skip.md" in n for n in names)
