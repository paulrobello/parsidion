"""Tests for vault_merge.py — backlink rewriting and merge logic.

QA-006: vault_merge has zero test coverage; these tests focus on the
wikilink-rewriting logic (can silently corrupt vault-wide) and the
merge helper functions.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import vault_common  # noqa: E402
import vault_merge  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point vault_common at a temporary vault and clear caches."""
    monkeypatch.setattr(vault_common, "VAULT_ROOT", tmp_path)
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    vault_common.load_config.cache_clear()  # type: ignore[attr-defined]


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    """Create standard vault directories and return vault root."""
    for d in vault_common.VAULT_DIRS:
        (tmp_path / d).mkdir(exist_ok=True)
    return tmp_path


def _note(vault: Path, rel_path: str, content: str) -> Path:
    """Write a note and return its path."""
    p = vault / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _update_wikilinks_in_vault
# ---------------------------------------------------------------------------


class TestUpdateWikilinks:
    """Tests for the wikilink-rewriting core logic."""

    def test_rewrites_simple_wikilink(self, vault: Path) -> None:
        note = _note(
            vault,
            "Patterns/other-note.md",
            "# Other\nSee [[old-note]] for details.\n",
        )
        updated = vault_merge._update_wikilinks_in_vault("old-note", "new-note", vault)
        assert updated == 1
        content = note.read_text(encoding="utf-8")
        assert "[[new-note]]" in content
        assert "[[old-note]]" not in content

    def test_rewrites_wikilink_with_alias(self, vault: Path) -> None:
        note = _note(
            vault,
            "Patterns/alias-note.md",
            "[[old-note|Display Text]] is useful.\n",
        )
        vault_merge._update_wikilinks_in_vault("old-note", "new-note", vault)
        content = note.read_text(encoding="utf-8")
        assert "[[new-note|Display Text]]" in content

    def test_rewrites_wikilink_with_fragment(self, vault: Path) -> None:
        note = _note(
            vault,
            "Patterns/frag-note.md",
            "[[old-note#section]] is referenced here.\n",
        )
        vault_merge._update_wikilinks_in_vault("old-note", "new-note", vault)
        content = note.read_text(encoding="utf-8")
        assert "[[new-note#section]]" in content

    def test_rewrites_multiple_occurrences(self, vault: Path) -> None:
        note = _note(
            vault,
            "Patterns/multi.md",
            "[[old-note]] and also [[old-note]] again.\n",
        )
        vault_merge._update_wikilinks_in_vault("old-note", "new-note", vault)
        content = note.read_text(encoding="utf-8")
        assert content.count("[[new-note]]") == 2
        assert "[[old-note]]" not in content

    def test_does_not_touch_files_without_old_link(self, vault: Path) -> None:
        note = _note(
            vault,
            "Patterns/unrelated.md",
            "# Unrelated\nNo links here at all.\n",
        )
        updated = vault_merge._update_wikilinks_in_vault("old-note", "new-note", vault)
        assert updated == 0
        content = note.read_text(encoding="utf-8")
        assert "[[new-note]]" not in content

    def test_returns_count_of_updated_files(self, vault: Path) -> None:
        _note(vault, "Patterns/a.md", "[[old-note]]\n")
        _note(vault, "Patterns/b.md", "[[old-note]]\n")
        _note(vault, "Patterns/c.md", "[[other-note]]\n")
        count = vault_merge._update_wikilinks_in_vault("old-note", "new-note", vault)
        assert count == 2

    def test_case_insensitive_match(self, vault: Path) -> None:
        note = _note(vault, "Patterns/case.md", "[[Old-Note]] is linked.\n")
        vault_merge._update_wikilinks_in_vault("old-note", "new-note", vault)
        content = note.read_text(encoding="utf-8")
        assert "[[new-note]]" in content


# ---------------------------------------------------------------------------
# _parse_related_list / _parse_tags_list
# ---------------------------------------------------------------------------


class TestParseFrontmatterHelpers:
    """Tests for _parse_related_list and _parse_tags_list."""

    def test_parse_related_list_from_list(self) -> None:
        fm = {"related": ["[[note-a]]", "[[note-b]]"]}
        result = vault_merge._parse_related_list(fm)
        assert result == ["[[note-a]]", "[[note-b]]"]

    def test_parse_related_list_empty(self) -> None:
        assert vault_merge._parse_related_list({}) == []

    def test_parse_related_list_inline_string(self) -> None:
        fm = {"related": '["[[note-a]]", "[[note-b]]"]'}
        result = vault_merge._parse_related_list(fm)
        assert "[[note-a]]" in result
        assert "[[note-b]]" in result

    def test_parse_tags_list_from_list(self) -> None:
        fm = {"tags": ["python", "vault"]}
        assert vault_merge._parse_tags_list(fm) == ["python", "vault"]

    def test_parse_tags_list_from_string(self) -> None:
        fm = {"tags": "python, vault"}
        result = vault_merge._parse_tags_list(fm)
        assert "python" in result
        assert "vault" in result

    def test_parse_tags_list_empty(self) -> None:
        assert vault_merge._parse_tags_list({}) == []


# ---------------------------------------------------------------------------
# _merge_notes (no-AI path)
# ---------------------------------------------------------------------------


class TestMergeNotesNoAI:
    """Tests for _merge_notes with no_ai=True."""

    def _make_note_content(
        self,
        title: str,
        tags: list[str],
        related: list[str],
        body: str,
        project: str = "",
    ) -> str:
        tags_str = ", ".join(f'"{t}"' for t in tags)
        related_str = ", ".join(f'"{r}"' for r in related)
        front = (
            f"---\ndate: 2026-01-01\ntype: pattern\ntags: [{tags_str}]\n"
            f"project: {project}\nconfidence: medium\nsources: []\n"
            f"related: [{related_str}]\n---\n"
        )
        return front + f"# {title}\n\n{body}\n"

    def test_merges_tags_union(self, vault: Path) -> None:
        path_a = vault / "Patterns" / "note-a.md"
        path_b = vault / "Patterns" / "note-b.md"
        content_a = self._make_note_content(
            "A", ["python", "vault"], ["[[note-b]]"], "Body A"
        )
        content_b = self._make_note_content(
            "B", ["python", "hook"], ["[[note-a]]"], "Body B"
        )
        path_a.write_text(content_a, encoding="utf-8")
        path_b.write_text(content_b, encoding="utf-8")

        merged = vault_merge._merge_notes(
            path_a, content_a, path_b, content_b, no_ai=True
        )
        fm = vault_common.parse_frontmatter(merged)
        tags = fm.get("tags", [])
        assert "python" in tags
        assert "vault" in tags
        assert "hook" in tags

    def test_merges_related_union_without_duplicates(self, vault: Path) -> None:
        path_a = vault / "Patterns" / "note-a.md"
        path_b = vault / "Patterns" / "note-b.md"
        content_a = self._make_note_content(
            "A", [], ["[[shared]]", "[[note-a]]"], "Body A"
        )
        content_b = self._make_note_content(
            "B", [], ["[[shared]]", "[[note-b]]"], "Body B"
        )
        path_a.write_text(content_a, encoding="utf-8")
        path_b.write_text(content_b, encoding="utf-8")

        merged = vault_merge._merge_notes(
            path_a, content_a, path_b, content_b, no_ai=True
        )
        # [[shared]] should appear only once in related
        assert merged.count("[[shared]]") == 1

    def test_fallback_body_includes_both_bodies(self, vault: Path) -> None:
        path_a = vault / "Patterns" / "note-a.md"
        path_b = vault / "Patterns" / "note-b.md"
        content_a = self._make_note_content("A", [], [], "Unique content from A.")
        content_b = self._make_note_content("B", [], [], "Unique content from B.")
        path_a.write_text(content_a, encoding="utf-8")
        path_b.write_text(content_b, encoding="utf-8")

        merged = vault_merge._merge_notes(
            path_a, content_a, path_b, content_b, no_ai=True
        )
        assert "Unique content from A." in merged
        assert "Unique content from B." in merged

    def test_project_falls_back_to_note_b(self, vault: Path) -> None:
        path_a = vault / "Patterns" / "note-a.md"
        path_b = vault / "Patterns" / "note-b.md"
        content_a = self._make_note_content("A", [], [], "Body A", project="")
        content_b = self._make_note_content("B", [], [], "Body B", project="my-project")
        path_a.write_text(content_a, encoding="utf-8")
        path_b.write_text(content_b, encoding="utf-8")

        merged = vault_merge._merge_notes(
            path_a, content_a, path_b, content_b, no_ai=True
        )
        fm = vault_common.parse_frontmatter(merged)
        assert fm.get("project") == "my-project"


# ---------------------------------------------------------------------------
# _find_note
# ---------------------------------------------------------------------------


class TestFindNote:
    def test_finds_by_absolute_path(self, vault: Path) -> None:
        note = _note(vault, "Patterns/target.md", "# Target\n")
        result = vault_merge._find_note(str(note), vault)
        assert result == note

    def test_finds_by_stem(self, vault: Path) -> None:
        note = _note(vault, "Patterns/my-note.md", "# My Note\n")
        result = vault_merge._find_note("my-note", vault)
        assert result == note

    def test_returns_none_when_not_found(self, vault: Path) -> None:
        result = vault_merge._find_note("nonexistent-stem", vault)
        assert result is None

    def test_finds_by_stem_case_insensitive(self, vault: Path) -> None:
        note = _note(vault, "Patterns/My-Note.md", "# My Note\n")
        result = vault_merge._find_note("my-note", vault)
        assert result == note
