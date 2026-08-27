"""QA-104: render_vaults_yaml preservation matrix.

``render_vaults_yaml`` is the single ``vaults.yaml`` writer body (QA-004).
QA-104 rewrites it as parse -> mutate -> serialize over a structured model;
these tests pin the preservation contract that rewrite must uphold:

- the named entry and the ``default:`` line are the ONLY lines rewritten,
- comments, blank lines, unknown top-level keys, and the ``vaults:``
  section structure survive verbatim and in order,
- the output round-trips through ``read_vaults_yaml`` unchanged,
- re-rendering the output is a byte-identical no-op (idempotence),
- a fresh minimal template is emitted when the original has no
  column-0 ``vaults:`` section.

Two behaviors here are fixes pinned ahead of the rewrite (the line-oriented
implementation failed them):

- the entry must be inserted exactly once when the vault name also appears
  as a top-level key outside the section (the old section-exit insert and
  post-loop fallback could both fire, duplicating the entry), and
- the caller's ``vaults`` dict must not be mutated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import vault_path  # noqa: E402 -- conftest puts scripts/ on sys.path

# Exact bytes of the fresh template (no column-0 ``vaults:`` section).
_TEMPLATE = (
    "# Named vaults for parsidion\n"
    "# Populated by `install.py --vault` (ARC-019).\n"
    "\n"
    "vaults:\n"
    "  wv: /w\n"
    "\n"
    "default: /w\n"
)


def _render(
    original: str,
    *,
    name: str = "wv",
    path: str = "/w",
    vaults: dict[str, str] | None = None,
    default: str = "",
) -> str:
    return vault_path.render_vaults_yaml(
        dict(vaults or {}), default, name, path, original
    )


def _round_trip(tmp_path: Path, content: str) -> tuple[dict[str, str], str | None]:
    cfg = tmp_path / "vaults.yaml"
    cfg.write_text(content, encoding="utf-8")
    return vault_path.read_vaults_yaml(cfg)


class TestEntryPreservation:
    """(a)/(b): the named entry is the only entry line rewritten."""

    def test_existing_entry_replaced_in_place_with_comments_untouched(self) -> None:
        original = (
            "# top comment\n"
            "vaults:\n"
            "  # about alpha\n"
            "  alpha: /old-alpha\n"
            "  beta: /b\n"
            "\n"
            "default: /old-alpha\n"
        )
        assert _render(original, name="alpha", path="/new-alpha") == (
            "# top comment\n"
            "vaults:\n"
            "  # about alpha\n"
            "  alpha: /new-alpha\n"
            "  beta: /b\n"
            "\n"
            "default: /new-alpha\n"
        )

    def test_absent_entry_inserted_directly_after_section_header(self) -> None:
        # Current placement semantics: a new name lands at the head of the
        # section, before the existing entries.
        original = "vaults:\n  alpha: /a\n  beta: /b\n\ndefault: /a\n"
        assert _render(original, name="new", path="/n") == (
            "vaults:\n  new: /n\n  alpha: /a\n  beta: /b\n\ndefault: /n\n"
        )

    def test_absent_entry_lands_before_comment_under_header(self) -> None:
        original = "vaults:\n  # section comment\n  alpha: /a\n"
        assert _render(original, name="new", path="/n") == (
            "vaults:\n  new: /n\n  # section comment\n  alpha: /a\ndefault: /n\n"
        )

    def test_entry_normalized_to_two_space_indent(self) -> None:
        original = "vaults:\n    alpha: /a\n"
        assert _render(original, name="alpha", path="/new") == (
            "vaults:\n  alpha: /new\ndefault: /new\n"
        )

    def test_name_shadowed_at_top_level_inserted_exactly_once(self) -> None:
        # QA-104 fix: the old renderer's section-exit insert and post-loop
        # fallback both fired here, writing the entry twice.
        original = "myvault: /elsewhere\nvaults:\n  a: /a\nz: 1\n"
        rendered = _render(original, name="myvault", path="/p")
        assert rendered.count("  myvault: /p") == 1, rendered
        assert rendered == (
            "myvault: /elsewhere\nvaults:\n  a: /a\n  myvault: /p\nz: 1\ndefault: /p\n"
        )

    def test_name_shadowed_at_top_level_no_double_insert_around_blank(self) -> None:
        original = "myvault: /elsewhere\nvaults:\n  a: /a\n\nz: 1\n"
        rendered = _render(original, name="myvault", path="/p")
        assert rendered.count("  myvault: /p") == 1, rendered
        assert rendered == (
            "myvault: /elsewhere\nvaults:\n  a: /a\n  myvault: /p\n\nz: 1\ndefault: /p\n"
        )

    def test_caller_vaults_dict_not_mutated(self) -> None:
        # QA-104 fix: the old renderer assigned into the caller's dict.
        # Call the real function with the caller's dict directly -- the
        # _render helper copies its input, which would mask the mutation.
        caller_vaults = {"x": "/x"}
        vault_path.render_vaults_yaml(
            caller_vaults, "", "new", "/n", "vaults:\n  x: /x\n"
        )
        assert caller_vaults == {"x": "/x"}


class TestDefaultPreservation:
    """(c)/(d): the default line is replaced in place or appended once."""

    def test_default_before_section_replaced_in_place(self) -> None:
        original = "default: /old\n\nvaults:\n  alpha: /a\n"
        assert _render(original, name="alpha", path="/new") == (
            "default: /new\n\nvaults:\n  alpha: /new\n"
        )

    def test_default_after_section_replaced_in_place_not_duplicated(self) -> None:
        original = "vaults:\n  alpha: /a\n\ndefault: /old\nother: y\n"
        rendered = _render(original, name="alpha", path="/new")
        assert rendered.count("default:") == 1, rendered
        assert rendered == ("vaults:\n  alpha: /new\n\ndefault: /new\nother: y\n")

    def test_absent_default_appended_at_end_of_file(self) -> None:
        # Current semantics: the default goes at the end of the file, after
        # any unknown top-level keys that follow the section.
        original = "vaults:\n  alpha: /a\nsomething: x\n"
        assert _render(original, name="alpha", path="/new") == (
            "vaults:\n  alpha: /new\nsomething: x\ndefault: /new\n"
        )

    def test_absent_default_appended_after_section_only_file(self) -> None:
        original = "vaults:\n  alpha: /a\n"
        assert _render(original, name="alpha", path="/new") == (
            "vaults:\n  alpha: /new\ndefault: /new\n"
        )


class TestStructurePreservation:
    """(e)/(f): comments and unknown top-level keys survive verbatim."""

    def test_comments_between_entries_and_above_keys_verbatim(self) -> None:
        original = (
            "# header comment\n"
            "\n"
            "# vault section below\n"
            "vaults:\n"
            "  # alpha note\n"
            "  alpha: /a\n"
            "  # between\n"
            "  beta: /b\n"
            "\n"
            "# trailing note\n"
            "other: y\n"
        )
        assert _render(original, name="alpha", path="/new") == (
            "# header comment\n"
            "\n"
            "# vault section below\n"
            "vaults:\n"
            "  # alpha note\n"
            "  alpha: /new\n"
            "  # between\n"
            "  beta: /b\n"
            "\n"
            "# trailing note\n"
            "other: y\n"
            "default: /new\n"
        )

    def test_unknown_top_level_key_before_section_preserved(self) -> None:
        original = "something: x\n\nvaults:\n  alpha: /a\n"
        assert _render(original, name="alpha", path="/new") == (
            "something: x\n\nvaults:\n  alpha: /new\ndefault: /new\n"
        )

    def test_original_without_trailing_newline_gains_exactly_one(self) -> None:
        original = "vaults:\n  alpha: /a"
        assert _render(original, name="alpha", path="/new") == (
            "vaults:\n  alpha: /new\ndefault: /new\n"
        )


class TestFreshTemplate:
    """(i): no column-0 ``vaults:`` section -> exact minimal template."""

    def test_empty_original_pins_exact_template_bytes(self) -> None:
        assert _render("", name="wv", path="/w") == _TEMPLATE

    def test_original_without_section_discarded_for_template(self) -> None:
        assert _render("# my config\nother: x\n", name="wv", path="/w") == _TEMPLATE

    def test_indented_vaults_header_counts_as_no_section(self) -> None:
        # Section detection is column-0 only; an indented header is not a
        # recognised section and the file is replaced by the template.
        assert _render("  vaults:\n  a: /a\n", name="wv", path="/w") == _TEMPLATE

    def test_template_includes_caller_entries_before_new_name(self) -> None:
        rendered = _render("", name="wv", path="/w", vaults={"other": "/o"})
        assert rendered == (
            "# Named vaults for parsidion\n"
            "# Populated by `install.py --vault` (ARC-019).\n"
            "\n"
            "vaults:\n"
            "  other: /o\n"
            "  wv: /w\n"
            "\n"
            "default: /w\n"
        )


class TestIdempotenceAndRoundTrip:
    """(g)/(h): stable under re-render; parses back to what was written."""

    CASES: list[tuple[str, str, str]] = [
        (
            "entry-replaced",
            "# c\nvaults:\n  # note\n  alpha: /old\n  beta: /b\n\ndefault: /old\n",
            "alpha",
        ),
        ("entry-inserted", "vaults:\n  alpha: /a\n  beta: /b\n\ndefault: /a\n", "new"),
        ("default-mid-file", "default: /old\n\nvaults:\n  alpha: /a\n", "alpha"),
        ("default-appended", "vaults:\n  alpha: /a\nsomething: x\n", "alpha"),
        (
            "comments-and-unknown-keys",
            "something: x\n\nvaults:\n  # note\n  alpha: /a\n\nother: y\n",
            "alpha",
        ),
        ("shadowed-name", "myvault: /elsewhere\nvaults:\n  a: /a\nz: 1\n", "myvault"),
        ("fresh-template", "", "wv"),
    ]

    @pytest.mark.parametrize(
        ("label", "original", "name"), CASES, ids=[c[0] for c in CASES]
    )
    def test_idempotent(self, label: str, original: str, name: str) -> None:
        once = _render(original, name=name, path="/p")
        assert _render(once, name=name, path="/p") == once, once

    @pytest.mark.parametrize(
        ("label", "original", "name"), CASES, ids=[c[0] for c in CASES]
    )
    def test_round_trips_through_read(
        self, label: str, original: str, name: str, tmp_path: Path
    ) -> None:
        rendered = _render(original, name=name, path="/p")
        vaults, default = _round_trip(tmp_path, rendered)
        assert vaults.get(name) == "/p", rendered
        assert default == "/p", rendered

    def test_round_trip_preserves_unrelated_entries(self, tmp_path: Path) -> None:
        rendered = _render(
            "vaults:\n  alpha: /a\n  beta: /b\n\ndefault: /a\n",
            name="alpha",
            path="/new",
        )
        vaults, default = _round_trip(tmp_path, rendered)
        assert vaults == {"alpha": "/new", "beta": "/b"}
        assert default == "/new"
