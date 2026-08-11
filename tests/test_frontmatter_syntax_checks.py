"""``check_note`` must detect malformed frontmatter the stdlib parser swallows.

``parse_frontmatter`` is a deliberately small YAML subset.  When a note uses a
shape outside that subset it does not raise — it silently produces the wrong
value, and the doctor's scan then reports ``✓ No issues found``.  Five such
shapes were found in the live vault (7951 notes) with ad-hoc scripts:

* nested/indented mapping keys — warned on stderr only, never as a scan issue
* an inline list opened with ``[`` but not closed on the same line
* an orphan ``]`` left as the first body line
* a list-typed field holding a bare scalar (``tags: a b c``), which collapses
  to one string and makes the note unfindable by tag — 56 notes, 45 of them
  ``tags``, so those notes contributed nothing to TAGS.md
* a duplicate top-level key (35 notes), where last-wins silently discards the
  earlier value

Detection is what these tests pin; repair stays manual.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "skills/parsidion/scripts")
)

import vault_doctor  # noqa: E402


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "Patterns").mkdir(parents=True)
    (tmp_path / "Daily" / "2026-08").mkdir(parents=True)
    return tmp_path


def _write(vault: Path, rel: str, content: str) -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _codes(vault: Path, note: Path, extra: list[Path] | None = None) -> list[str]:
    note_map = vault_doctor.build_note_map([note, *(extra or [])])
    return [i.code for i in vault_doctor.check_note(note, note_map, vault)]


# A well-formed frontmatter tail so the shape under test is the only defect.
_TAIL = 'confidence: high\nrelated: ["[[anchor]]"]\n'


def _anchor(vault: Path) -> Path:
    return _write(
        vault,
        "Patterns/anchor.md",
        "---\ndate: 2026-01-01\ntype: pattern\n---\n\n# A\n",
    )


class TestNestedMappingKey:
    def test_indented_sub_mapping_is_reported(self, vault: Path) -> None:
        anchor = _anchor(vault)
        note = _write(
            vault,
            "Patterns/nested.md",
            "---\n"
            "date: 2026-08-02\n"
            "type: pattern\n"
            "sources:\n"
            "  - repo: par-hack\n"
            "    commit: d6a2e00\n"
            "    plan: docs/plan.md\n"
            f"{_TAIL}"
            "---\n\n# Nested\n",
        )
        assert "NESTED_FM_KEY" in _codes(vault, note, [anchor])

    def test_plain_block_sequence_is_not_reported(self, vault: Path) -> None:
        """`- item` lines are supported — only indented `key:` lines are not."""
        anchor = _anchor(vault)
        note = _write(
            vault,
            "Patterns/blockseq.md",
            "---\n"
            "date: 2026-08-02\n"
            "type: pattern\n"
            "sources:\n"
            "  - https://example.com/a\n"
            "  - https://example.com/b\n"
            f"{_TAIL}"
            "---\n\n# Block\n",
        )
        assert "NESTED_FM_KEY" not in _codes(vault, note, [anchor])


class TestUnterminatedInlineList:
    def test_wrapped_inline_list_is_reported(self, vault: Path) -> None:
        anchor = _anchor(vault)
        note = _write(
            vault,
            "Patterns/wrapped.md",
            "---\n"
            "date: 2026-08-02\n"
            "type: pattern\n"
            "sources: [\n"
            "  https://example.com/a,\n"
            "  https://example.com/b\n"
            f"{_TAIL}"
            "---\n\n# Wrapped\n",
        )
        assert "UNTERMINATED_FM_LIST" in _codes(vault, note, [anchor])

    def test_single_line_inline_list_is_not_reported(self, vault: Path) -> None:
        anchor = _anchor(vault)
        note = _write(
            vault,
            "Patterns/inline.md",
            "---\n"
            "date: 2026-08-02\n"
            "type: pattern\n"
            "tags: [a, b, c]\n"
            f"{_TAIL}"
            "---\n\n# Inline\n",
        )
        assert "UNTERMINATED_FM_LIST" not in _codes(vault, note, [anchor])


class TestOrphanBracket:
    def test_orphan_close_bracket_at_body_start_is_reported(self, vault: Path) -> None:
        anchor = _anchor(vault)
        note = _write(
            vault,
            "Patterns/orphan.md",
            "---\n"
            "date: 2026-08-02\n"
            "type: pattern\n"
            "sources: []\n"
            f"{_TAIL}"
            "---\n"
            "]\n"
            "\n# Orphan\n",
        )
        assert "ORPHAN_FM_BRACKET" in _codes(vault, note, [anchor])

    def test_normal_body_is_not_reported(self, vault: Path) -> None:
        anchor = _anchor(vault)
        note = _write(
            vault,
            "Patterns/normal.md",
            "---\n"
            "date: 2026-08-02\n"
            "type: pattern\n"
            "sources: []\n"
            f"{_TAIL}"
            "---\n\n# Normal\n\nText.\n",
        )
        assert "ORPHAN_FM_BRACKET" not in _codes(vault, note, [anchor])


class TestScalarWhereListExpected:
    def test_space_separated_tags_scalar_is_reported(self, vault: Path) -> None:
        anchor = _anchor(vault)
        note = _write(
            vault,
            "Patterns/scalartags.md",
            "---\n"
            "date: 2026-08-02\n"
            "type: pattern\n"
            "tags: security-audit apns subprocess-isolation\n"
            f"{_TAIL}"
            "---\n\n# Scalar tags\n",
        )
        assert "SCALAR_LIST_FIELD" in _codes(vault, note, [anchor])

    def test_scalar_related_is_reported(self, vault: Path) -> None:
        anchor = _anchor(vault)
        note = _write(
            vault,
            "Patterns/scalarrelated.md",
            "---\n"
            "date: 2026-08-02\n"
            "type: pattern\n"
            "tags: [x]\n"
            "confidence: high\n"
            "related: onslaught\n"
            "---\n\n# Scalar related\n",
        )
        assert "SCALAR_LIST_FIELD" in _codes(vault, note, [anchor])

    def test_proper_lists_are_not_reported(self, vault: Path) -> None:
        anchor = _anchor(vault)
        note = _write(
            vault,
            "Patterns/lists.md",
            "---\n"
            "date: 2026-08-02\n"
            "type: pattern\n"
            "tags: [a, b]\n"
            "sources:\n"
            "  - https://example.com/a\n"
            f"{_TAIL}"
            "---\n\n# Lists\n",
        )
        assert "SCALAR_LIST_FIELD" not in _codes(vault, note, [anchor])

    def test_empty_list_is_not_reported(self, vault: Path) -> None:
        anchor = _anchor(vault)
        note = _write(
            vault,
            "Patterns/emptylist.md",
            "---\n"
            "date: 2026-08-02\n"
            "type: pattern\n"
            "tags: []\n"
            "sources: []\n"
            f"{_TAIL}"
            "---\n\n# Empty\n",
        )
        assert "SCALAR_LIST_FIELD" not in _codes(vault, note, [anchor])


class TestDuplicateKey:
    def test_duplicate_top_level_key_is_reported(self, vault: Path) -> None:
        """The 35-note shape: two `related:` keys, silently last-wins."""
        anchor = _anchor(vault)
        note = _write(
            vault,
            "Patterns/dupe.md",
            "---\n"
            "date: 2026-08-02\n"
            "type: pattern\n"
            "tags: [x]\n"
            "related: []\n"
            "confidence: high\n"
            'related: ["[[anchor]]"]\n'
            "---\n\n# Dupe\n",
        )
        assert "DUPLICATE_FM_KEY" in _codes(vault, note, [anchor])

    def test_block_sequence_items_are_not_duplicate_keys(self, vault: Path) -> None:
        anchor = _anchor(vault)
        note = _write(
            vault,
            "Patterns/seq.md",
            "---\n"
            "date: 2026-08-02\n"
            "type: pattern\n"
            "tags:\n"
            "  - a\n"
            "  - b\n"
            f"{_TAIL}"
            "---\n\n# Seq\n",
        )
        assert "DUPLICATE_FM_KEY" not in _codes(vault, note, [anchor])


def test_clean_note_reports_none_of_the_new_codes(vault: Path) -> None:
    anchor = _anchor(vault)
    note = _write(
        vault,
        "Patterns/clean.md",
        "---\n"
        "date: 2026-08-02\n"
        "type: pattern\n"
        "tags: [python, vault]\n"
        "sources:\n"
        "  - https://example.com/a\n"
        f"{_TAIL}"
        "---\n\n# Clean\n\nBody.\n",
    )
    codes = _codes(vault, note, [anchor])
    assert codes == []


def test_notes_without_frontmatter_do_not_crash_the_syntax_checks(
    vault: Path,
) -> None:
    note = _write(vault, "Patterns/plain.md", "Just text, no fences.\n")
    assert "MISSING_FRONTMATTER" in _codes(vault, note)
