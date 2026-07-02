"""Regression tests for parser/index fixes.

Covers:
- Frontmatter list items are kept as strings (no bool/int/float coercion),
  so numeric-looking tags like ``tags: [2026, python]`` survive end-to-end
  through ``update_index.build_index()`` tag collection.
- ``update_index`` defensively coerces non-string tags (from legacy parses)
  with a stderr warning instead of silently dropping them.
- ``_parse_config_yaml`` warns and skips config nesting deeper than 2 levels
  instead of silently flattening it into the 2nd-level dict.
- ``inject_related_links`` only touches the frontmatter ``related`` field,
  never a body line starting with ``related:``.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

import update_index
import vault_common
import vault_links

# ---------------------------------------------------------------------------
# Fix 1a: frontmatter list items stay strings
# ---------------------------------------------------------------------------


class TestListItemsStayStrings:
    """Frontmatter list items must never be coerced to bool/int/float."""

    def test_inline_numeric_tag_stays_string(self) -> None:
        fm = vault_common.parse_frontmatter("---\ntags: [2026, python]\n---\n")
        assert fm["tags"] == ["2026", "python"]

    def test_block_list_items_stay_strings(self) -> None:
        content = "---\ntags:\n  - 2026\n  - true\n  - 3.14\n---\n"
        fm = vault_common.parse_frontmatter(content)
        assert fm["tags"] == ["2026", "true", "3.14"]

    def test_quoted_list_items_still_unquoted(self) -> None:
        fm = vault_common.parse_frontmatter(
            "---\nrelated: [\"[[note-a]]\", '[[note-b]]']\n---\n"
        )
        assert fm["related"] == ["[[note-a]]", "[[note-b]]"]

    def test_scalar_coercion_unchanged(self) -> None:
        """Non-list scalars keep their bool/int/float coercion."""
        fm = vault_common.parse_frontmatter("---\ncount: 42\nflag: true\n---\n")
        assert fm["count"] == 42
        assert fm["flag"] is True


# ---------------------------------------------------------------------------
# Fix 1: numeric-looking tag end-to-end through index tag collection
# ---------------------------------------------------------------------------


class TestNumericTagEndToEnd:
    """A note tagged ``[2026, python]`` must be indexed under both tags."""

    def test_numeric_tag_survives_index_build(self, tmp_vault: Path) -> None:
        notes_dir = tmp_vault / "Patterns"
        notes_dir.mkdir(parents=True, exist_ok=True)
        (notes_dir / "numeric-tag-note.md").write_text(
            "---\n"
            "date: 2026-01-01\n"
            "type: pattern\n"
            "tags: [2026, python]\n"
            'related: ["[[other-note]]"]\n'
            "---\n"
            "\n"
            "# Numeric Tag Note\n"
            "\n"
            "Body.\n",
            encoding="utf-8",
        )

        _, _, _, _, db_rows, tag_counter = update_index.build_index(tmp_vault)

        assert tag_counter["2026"] == 1
        assert tag_counter["python"] == 1
        row = next(r for r in db_rows if r.stem == "numeric-tag-note")
        assert "2026" in row.tags

    def test_nonstring_tag_coerced_with_warning(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Fix 1b: legacy non-string tags are coerced via str(), not dropped."""
        notes_dir = tmp_vault / "Patterns"
        notes_dir.mkdir(parents=True, exist_ok=True)
        (notes_dir / "legacy-int-tag.md").write_text(
            "---\ntags: [placeholder]\n---\n\n# Legacy\n",
            encoding="utf-8",
        )
        # Simulate an older parser that coerced list items to int
        monkeypatch.setattr(
            update_index,
            "parse_frontmatter",
            lambda _content: {"tags": [2026, "python"]},
        )

        _, _, _, _, db_rows, tag_counter = update_index.build_index(tmp_vault)

        assert tag_counter["2026"] == 1
        assert tag_counter["python"] == 1
        assert "coercing non-string tag" in capsys.readouterr().err
        row = next(r for r in db_rows if r.stem == "legacy-int-tag")
        assert "2026" in row.tags


# ---------------------------------------------------------------------------
# Fix 2: config nesting deeper than 2 levels warns and skips
# ---------------------------------------------------------------------------


class TestConfigDeepNesting:
    """3rd-level config keys must be skipped with a warning, not flattened."""

    def test_three_level_nesting_warns_and_skips(self) -> None:
        text = (
            "ai_models:\n"
            "  codex:\n"
            "    small: gpt-small\n"
            "    deeper:\n"
            "      leaf: nope\n"
            "  claude:\n"
            "    small: haiku\n"
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = vault_common._parse_config_yaml(text)

        # 2nd level is intact and NOT corrupted by the deeper keys
        assert result["ai_models"]["codex"] == {"small": "gpt-small"}
        assert result["ai_models"]["claude"] == {"small": "haiku"}

        warnings = stderr.getvalue()
        assert "deeper than 2 levels" in warnings
        assert "'deeper'" in warnings
        assert "'leaf'" in warnings

    def test_two_level_nesting_still_parses(self) -> None:
        text = "ai_models:\n  codex:\n    small: gpt-small\n    large: gpt-large\n"
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = vault_common._parse_config_yaml(text)
        assert result["ai_models"]["codex"] == {
            "small": "gpt-small",
            "large": "gpt-large",
        }
        assert stderr.getvalue() == ""


# ---------------------------------------------------------------------------
# Fix 3: inject_related_links scoped to the frontmatter block
# ---------------------------------------------------------------------------


class TestInjectRelatedLinks:
    """`related:` lines in the note body must never be clobbered."""

    BODY_LINE = 'related: ["[[should-stay-in-body]]"]'

    def test_body_line_untouched_when_frontmatter_lacks_related(
        self, tmp_path: Path
    ) -> None:
        # The original bug: no `related` in frontmatter, so the MULTILINE
        # regex matched the body line quoting the schema and clobbered it.
        note = tmp_path / "schema-note.md"
        note.write_text(
            "---\n"
            "date: 2026-01-01\n"
            "type: knowledge\n"
            "---\n"
            "\n"
            "# Schema Example\n"
            "\n"
            "Frontmatter must include:\n"
            f"{self.BODY_LINE}\n",
            encoding="utf-8",
        )

        vault_links.inject_related_links(note, ["[[new-note]]"])

        content = note.read_text(encoding="utf-8")
        assert self.BODY_LINE in content
        fm = vault_common.parse_frontmatter(content)
        assert fm["related"] == ["[[new-note]]"]
        # One occurrence in frontmatter, one (untouched) in the body
        assert content.count("related:") == 2

    def test_frontmatter_related_field_still_updated(self, tmp_path: Path) -> None:
        note = tmp_path / "normal-note.md"
        note.write_text(
            "---\n"
            "date: 2026-01-01\n"
            'related: ["[[old-note]]"]\n'
            "---\n"
            "\n"
            "# Normal Note\n"
            "\n"
            f"{self.BODY_LINE}\n",
            encoding="utf-8",
        )

        vault_links.inject_related_links(note, ["[[new-note]]"])

        content = note.read_text(encoding="utf-8")
        assert self.BODY_LINE in content
        fm = vault_common.parse_frontmatter(content)
        assert fm["related"] == ["[[old-note]]", "[[new-note]]"]

    def test_block_style_related_replaced(self, tmp_path: Path) -> None:
        note = tmp_path / "block-note.md"
        note.write_text(
            '---\ndate: 2026-01-01\nrelated:\n  - "[[old-note]]"\n---\n\nBody.\n',
            encoding="utf-8",
        )

        vault_links.inject_related_links(note, ["[[new-note]]"])

        fm = vault_common.parse_frontmatter(note.read_text(encoding="utf-8"))
        assert fm["related"] == ["[[old-note]]", "[[new-note]]"]

    def test_no_frontmatter_leaves_file_untouched(self, tmp_path: Path) -> None:
        note = tmp_path / "plain.md"
        original = f"# No frontmatter\n\n{self.BODY_LINE}\n"
        note.write_text(original, encoding="utf-8")

        vault_links.inject_related_links(note, ["[[new-note]]"])

        assert note.read_text(encoding="utf-8") == original

    def test_no_tmp_file_left_behind(self, tmp_path: Path) -> None:
        note = tmp_path / "clean-note.md"
        note.write_text(
            '---\nrelated: ["[[old-note]]"]\n---\nBody.\n', encoding="utf-8"
        )

        vault_links.inject_related_links(note, ["[[new-note]]"])

        leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert leftovers == []
