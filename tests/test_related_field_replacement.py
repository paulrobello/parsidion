"""``inject_related_links`` must REPLACE the ``related`` field, never append a second one.

The original ``_RELATED_FIELD_RE`` required the ``related:`` line to end right
after an optional ``[...]``.  Two shapes present in the live vault defeated it —
the daily-note template placeholder (which carries a trailing ``#`` comment) and
a scalar value — so the writer fell through to its append branch and emitted a
*second* ``related:`` key.  35 notes accumulated duplicate top-level keys that
way; ``parse_frontmatter`` is last-wins, so the damage was silent.

Every shape below must end with exactly one ``related:`` key in the frontmatter.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "skills/parsidion/scripts")
)

import vault_common  # noqa: E402
import vault_links  # noqa: E402


def _fm_block(content: str) -> str:
    """Return the raw frontmatter text (between the fences)."""
    _, _, rest = content.partition("---\n")
    block, _, _ = rest.partition("\n---\n")
    return block


def _count_related_keys(content: str) -> int:
    """Count top-level ``related:`` keys inside the frontmatter block only."""
    return sum(
        1
        for line in _fm_block(content).splitlines()
        if line.startswith("related:") or line.rstrip() == "related:"
    )


DAILY_PLACEHOLDER = (
    'related: []  # inline quoted array: ["[[note-one]]", "[[note-two]]"]'
    " -- no orphan notes allowed"
)


@pytest.mark.parametrize(
    ("shape", "related_lines", "expected"),
    [
        (
            "clean-inline",
            ['related: ["[[old-note]]"]'],
            ["[[old-note]]", "[[new-note]]"],
        ),
        # The daily-note template placeholder — the generator of 20 of the 35
        # duplicate-key notes in the live vault.
        ("trailing-comment", [DAILY_PLACEHOLDER], ["[[new-note]]"]),
        # A bare scalar (e.g. Debugging/fixed-slot-grid-flawed-for-dense-crowds.md
        # carried `related: "onslaught"`). Not a wikilink, so it is discarded.
        ("scalar", ['related: "onslaught"'], ["[[new-note]]"]),
        (
            "block-list",
            ["related:", '  - "[[old-note]]"', '  - "[[other-note]]"'],
            ["[[old-note]]", "[[other-note]]", "[[new-note]]"],
        ),
        ("empty-inline", ["related: []"], ["[[new-note]]"]),
        ("bare-key", ["related:"], ["[[new-note]]"]),
        # An inline list opened but wrapped across lines — parse_frontmatter only
        # accepts single-line `[a, b]`, so this was silently stored as the scalar
        # "[" and its continuation lines were mis-parsed as their own keys.
        (
            "wrapped-inline",
            ["related: [", '  "[[old-note]]",', '  "[[other-note]]"]'],
            ["[[new-note]]"],
        ),
        (
            "inline-with-indented-continuation",
            ['related: ["[[old-note]]"]', '  - "stray-tag"'],
            ["[[old-note]]", "[[new-note]]"],
        ),
    ],
)
def test_related_field_is_replaced_not_appended(
    tmp_path: Path, shape: str, related_lines: list[str], expected: list[str]
) -> None:
    note = tmp_path / f"{shape}.md"
    note.write_text(
        "---\n"
        "date: 2026-01-01\n"
        "type: pattern\n" + "\n".join(related_lines) + "\n"
        "provenance: observed\n"
        "---\n"
        "\n"
        f"# {shape}\n",
        encoding="utf-8",
    )

    vault_links.inject_related_links(note, ["[[new-note]]"])

    content = note.read_text(encoding="utf-8")
    assert _count_related_keys(content) == 1, (
        f"{shape}: expected exactly one related: key, frontmatter was:\n"
        f"{_fm_block(content)}"
    )
    fm = vault_common.parse_frontmatter(content)
    assert fm["related"] == expected
    # Fields on either side of `related` must survive the replacement.
    assert fm["type"] == "pattern"
    assert fm["provenance"] == "observed"


def test_existing_duplicate_related_keys_are_collapsed(tmp_path: Path) -> None:
    """A note already carrying two `related:` keys is healed, not grown to three."""
    note = tmp_path / "already-duplicated.md"
    note.write_text(
        "---\n"
        "date: 2026-01-01\n"
        "type: daily\n"
        f"{DAILY_PLACEHOLDER}\n"
        "provenance: observed\n"
        'related: ["[[real-one]]"]\n'
        "---\n"
        "\n"
        "## Sessions\n",
        encoding="utf-8",
    )

    vault_links.inject_related_links(note, ["[[new-note]]"])

    content = note.read_text(encoding="utf-8")
    assert _count_related_keys(content) == 1
    fm = vault_common.parse_frontmatter(content)
    # parse_frontmatter is last-wins, so the real list is what gets merged.
    assert fm["related"] == ["[[real-one]]", "[[new-note]]"]
    assert fm["provenance"] == "observed"


def test_body_related_line_still_untouched(tmp_path: Path) -> None:
    """Regression: the frontmatter-scoped replacement must not reach the body."""
    body_line = 'related: ["[[should-stay-in-body]]"]'
    note = tmp_path / "schema-note.md"
    note.write_text(
        "---\n"
        "date: 2026-01-01\n"
        f"{DAILY_PLACEHOLDER}\n"
        "---\n"
        "\n"
        "# Schema Example\n"
        "\n"
        f"{body_line}\n",
        encoding="utf-8",
    )

    vault_links.inject_related_links(note, ["[[new-note]]"])

    content = note.read_text(encoding="utf-8")
    assert body_line in content
    assert _count_related_keys(content) == 1
    assert vault_common.parse_frontmatter(content)["related"] == ["[[new-note]]"]


def test_daily_template_placeholder_has_no_trailing_comment() -> None:
    """The template must not ship the comment that defeated the match."""
    template = (
        Path(__file__).resolve().parents[1] / "skills/parsidion/templates/daily.md"
    )
    related = [
        ln
        for ln in template.read_text(encoding="utf-8").splitlines()
        if ln.startswith("related:")
    ]
    assert related == ["related: []"]
