"""QA-105: table-driven regression fence for the doctor tag-merge path.

One row per recorded production incident plus every merge rule and both
tags-field representations handled by ``doctor/tags.py``. The decomposition
itself is behavior-preserving; these tests pin that contract so a future
heuristic change has to confront the history it repeats.

Incidents:
- b7931fd (2026-08-14): ``ios`` vs ``io`` mis-merged into ``io`` — the
  "-s" suffix match is a semantic guess and a 10x-dominant "plural" is a
  distinct coexisting tag, not drift. The dominance guard now skips it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import vault_doctor


# ---------------------------------------------------------------------------
# Pair classification + canonical-form selection (table-driven)
# ---------------------------------------------------------------------------

# (id, counts, expected) where expected is a list of (keep, away, reason).
_PAIR_CASES: list[tuple[str, dict[str, int], list[tuple[str, str, str]]]] = [
    # b7931fd incident: 10x-dominant "plural" is a distinct tag — never merge.
    (
        "ios-io-dominance-guard-skips-merge",
        {"io": 1, "ios": 170},
        [],
    ),
    # Exactly at the 10x boundary the guard still fires (>= comparison).
    (
        "exactly-10x-still-guarded",
        {"io": 5, "ios": 50},
        [],
    ),
    # Just under 10x dominance: ordinary plural drift — merge to singular.
    (
        "under-10x-merges-to-singular",
        {"pattern": 10, "patterns": 50},
        [("pattern", "patterns", "plural/singular")],
    ),
    # Plain plural drift regardless of which sorted position each holds.
    (
        "plural-merges-keep-singular",
        {"hook": 5, "hooks": 3},
        [("hook", "hooks", "plural/singular")],
    ),
    # Hyphen vs underscore always keeps the kebab-case form.
    (
        "hyphen-underscore-keeps-kebab",
        {"par-ai-core": 3, "par_ai_core": 2},
        [("par-ai-core", "par_ai_core", "hyphen/underscore")],
    ),
    # Case duplicates fall back to higher-count-wins (no casing convention).
    (
        "case-duplicate-higher-count-wins",
        {"Python": 1, "python": 5},
        [("python", "Python", "case")],
    ),
    # Collapsed hyphens keep the hyphenated (more readable) form.
    (
        "collapsed-hyphen-keeps-hyphenated",
        {"realtime": 9, "real-time": 1},
        [("real-time", "realtime", "hyphenated/collapsed")],
    ),
    # Unrelated tags produce no pairs.
    (
        "unrelated-tags-no-pairs",
        {"python": 5, "rust": 3, "typescript": 2},
        [],
    ),
]


@pytest.mark.parametrize(
    ("case_id", "counts", "expected"),
    _PAIR_CASES,
    ids=[case[0] for case in _PAIR_CASES],
)
def test_find_tag_duplicates_table(
    case_id: str, counts: dict[str, int], expected: list[tuple[str, str, str]]
) -> None:
    assert vault_doctor._find_tag_duplicates(counts) == expected


# ---------------------------------------------------------------------------
# Tags-field replacement across both representations (table-driven)
# ---------------------------------------------------------------------------

# (id, note content, old, new, expected result, expected content)
_REPLACE_CASES: list[tuple[str, str, str, str, bool, str]] = [
    (
        "inline-list",
        "---\ntags: [hooks, python]\nconfidence: high\n---\n\n# Test\n",
        "hooks",
        "hook",
        True,
        "---\ntags: [hook, python]\nconfidence: high\n---\n\n# Test\n",
    ),
    (
        "quoted-inline-list-preserves-style",
        '---\ntags: ["hooks", "python"]\nconfidence: high\n---\n\n# Test\n',
        "hooks",
        "hook",
        True,
        '---\ntags: ["hook", "python"]\nconfidence: high\n---\n\n# Test\n',
    ),
    # Pre-existing quirk, pinned as-is (byte-identical decomposition, QA-105):
    # when the tags line is the LAST frontmatter line, the _TAGS_INLINE_RE
    # ``\s*$`` tail consumes the trailing newline, so the rewritten line
    # glues onto the closing ``---``. Real notes carry further frontmatter
    # fields after tags, which is why this has never surfaced in production.
    (
        "inline-list-as-last-frontmatter-line-swallows-newline",
        "---\ntags: [hooks, python]\n---\n\n# Test\n",
        "hooks",
        "hook",
        True,
        "---\ntags: [hook, python]---\n\n# Test\n",
    ),
    (
        "block-sequence",
        "---\ntags:\n  - hooks\n  - python\n---\n\n# Test\n",
        "hooks",
        "hook",
        True,
        "---\ntags:\n  - hook\n  - python\n---\n\n# Test\n",
    ),
    (
        "block-sequence-keeps-following-fields",
        "---\ntags:\n  - hooks\nsources:\n  - https://example.com/a_b\n---\n\n# Test\n",
        "hooks",
        "hook",
        True,
        "---\ntags:\n  - hook\nsources:\n  - https://example.com/a_b\n---\n\n# Test\n",
    ),
    (
        "inline-replacement-dedupes-onto-existing",
        "---\ntags: [hooks, hook]\nconfidence: high\n---\n\n# Test\n",
        "hooks",
        "hook",
        True,
        "---\ntags: [hook]\nconfidence: high\n---\n\n# Test\n",
    ),
    (
        "tag-absent-returns-false",
        "---\ntags: [python, rust]\nconfidence: high\n---\n\n# Test\n",
        "hooks",
        "hook",
        False,
        "---\ntags: [python, rust]\nconfidence: high\n---\n\n# Test\n",
    ),
]


@pytest.mark.parametrize(
    ("case_id", "content", "old", "new", "expected_result", "expected_content"),
    _REPLACE_CASES,
    ids=[case[0] for case in _REPLACE_CASES],
)
def test_replace_tag_in_note_table(
    tmp_vault: Path,
    case_id: str,
    content: str,
    old: str,
    new: str,
    expected_result: bool,
    expected_content: str,
) -> None:
    note = tmp_vault / "Patterns" / f"{case_id}.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(content, encoding="utf-8")

    assert vault_doctor._replace_tag_in_note(note, old, new) is expected_result
    assert note.read_text(encoding="utf-8") == expected_content
