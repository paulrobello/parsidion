"""ENH-024: tests for ``core/yaml_lite``, the shared YAML subset module.

Two layers:

- **Table-driven unit tests** for every token-level rule the module owns:
  key/value splitting, both quote-stripping dialects, inline-comment
  splitting, scalar coercion, inline arrays (splitting, item parsing,
  escapes), and the dump policy (when a scalar needs quotes, how it is
  quoted, and that emitted values round-trip through the readers).
- **Differential fixture tests**: the committed corpora under
  ``tests/fixtures/yaml_lite/`` are replayed through the three consumers
  (``vault_config._parse_config_yaml``, ``vault_index.parse_frontmatter``,
  ``vault_path.read_vaults_yaml``) and compared against outputs captured
  from the PRE-ENH-024 private parsers (commit f936bc1). This pins the
  consolidation as behavior-preserving: byte-for-byte identical parse
  output over every corpus shape, including the historical doctor
  false-positive forms (TOML-ish lines, code-fence content).
"""

from __future__ import annotations

import contextlib
import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from core import vault_config, vault_path, yaml_lite
from core.vault_index import parse_frontmatter

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "yaml_lite"


def _corpus(name: str) -> list[dict[str, Any]]:
    doc = json.loads((_FIXTURES / f"{name}_corpus.json").read_text(encoding="utf-8"))
    return doc["cases"]


def _expected(name: str) -> dict[str, Any]:
    doc = json.loads((_FIXTURES / f"{name}_expected.json").read_text(encoding="utf-8"))
    return doc["cases"]


def _canon(value: Any) -> str:
    """Canonical JSON form used for byte-for-byte differential comparison."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# split_key_value
# ---------------------------------------------------------------------------


class TestSplitKeyValue:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("key: value", ("key", "value")),
            ("key:value", ("key", "value")),
            ("  indented: v", ("indented", "v")),
            ("key:  spaced  ", ("key", "spaced")),
            ("url: https://example.com/x", ("url", "https://example.com/x")),
            ("title: My: Title", ("title", "My: Title")),
            ("key:", ("key", "")),
            (": value", ("", "value")),
            (":", ("", "")),
            ("no colon here", None),
            ("", None),
        ],
    )
    def test_table(self, text: str, expected: tuple[str, str] | None) -> None:
        assert yaml_lite.split_key_value(text) == expected


# ---------------------------------------------------------------------------
# quote stripping (both dialects)
# ---------------------------------------------------------------------------


class TestStripQuotes:
    """Pair-matching strip: quotes are removed only as a matching pair."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ('"double"', "double"),
            ("'single'", "single"),
            ("\"mixed'", "\"mixed'"),  # unmatched pair survives verbatim
            ("'mixed\"", "'mixed\""),
            ('""', ""),
            ("''", ""),
            ('"', '"'),  # single quote char is not a pair
            ("bare", "bare"),
            ("", ""),
            ('"a\\"b"', 'a\\"b'),  # inner content is untouched here
        ],
    )
    def test_table(self, value: str, expected: str) -> None:
        assert yaml_lite.strip_quotes(value) == expected


class TestStripQuoteEdges:
    """Greedy edge strip -- the historical vaults.yaml reader dialect."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ('"/path"', "/path"),
            ("'/path'", "/path"),
            ("\"'t'\"", "t"),  # strips BOTH quote kinds at the edges
            ("\"unterminated'", "unterminated"),
            ("'x\"", "x"),
            ("plain", "plain"),
            ("'''", ""),
        ],
    )
    def test_table(self, value: str, expected: str) -> None:
        assert yaml_lite.strip_quote_edges(value) == expected


# ---------------------------------------------------------------------------
# inline comments
# ---------------------------------------------------------------------------


class TestStripInlineComment:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("30  # seconds", "30"),
            ("30 # seconds", "30"),
            ("30\t# tab before hash", "30"),
            ("value#nospace", "value#nospace"),  # no space/tab before #
            ("https://example.com/a#anchor", "https://example.com/a#anchor"),
            ('"a # b"', '"a # b"'),  # inside quotes: kept
            ("'a # b'", "'a # b'"),
            ("v # c # d", "v"),  # first comment wins
            ("# leading", "# leading"),  # i == 0 is not a trailing comment
            ("no comment", "no comment"),
            ("trail   # c", "trail"),  # rstrip applies only at the comment cut
            ("no comment  ", "no comment  "),  # no comment: verbatim
        ],
    )
    def test_table(self, value: str, expected: str) -> None:
        assert yaml_lite.strip_inline_comment(value) == expected


# ---------------------------------------------------------------------------
# scalars
# ---------------------------------------------------------------------------


class TestParseScalar:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("true", True),
            ("TRUE", True),
            ("yes", True),
            ("Yes", True),
            ("false", False),
            ("no", False),
            ("null", None),
            ("NULL", None),
            ("~", None),
            ("42", 42),
            ("-7", -7),
            ("+5", 5),
            ("1_000", 1000),
            ("3.14", 3.14),
            ("1e3", 1000.0),
            (".5", 0.5),
            ("0x10", "0x10"),  # int() rejects hex literals without a base
            ("2026-01-15", "2026-01-15"),  # dates stay strings
            ("claude-haiku", "claude-haiku"),
            ('"quoted string"', "quoted string"),
            ("'single quoted'", "single quoted"),
            ('"42"', "42"),  # quoted values are never coerced
            ('"true"', "true"),
            ("''", ""),  # quoted empty is the empty STRING, not null
            ('""', ""),
            ("\"mixed'", "\"mixed'"),  # unmatched quotes: bare-string fallback
        ],
    )
    def test_table(self, value: str, expected: Any) -> None:
        assert yaml_lite.parse_scalar(value) == expected
        assert type(yaml_lite.parse_scalar(value)) is type(expected)


class TestParseListItem:
    """List items are strings only -- never bool/int/float coerced."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("python", "python"),
            ("2026", "2026"),
            ("true", "true"),
            ("3.14", "3.14"),
            ('"quoted"', "quoted"),
            ("'single'", "single"),
            ('"[[wikilink]]"', "[[wikilink]]"),
            # SEC-033(c): double-quoted items unescape \" and \\
            ('"say \\"hi\\""', 'say "hi"'),
            ('"back\\\\slash"', "back\\slash"),
            ("'no # escape'", "no # escape"),  # single quotes: no unescaping
            ('"unterminated', '"unterminated'),
        ],
    )
    def test_table(self, value: str, expected: str) -> None:
        assert yaml_lite.parse_list_item(value) == expected
        assert isinstance(yaml_lite.parse_list_item(value), str)


# ---------------------------------------------------------------------------
# inline arrays
# ---------------------------------------------------------------------------


class TestSplitListItems:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("a, b, c", ["a", "b", "c"]),
            ("a,b", ["a", "b"]),
            ('"a, b", c', ['"a, b"', "c"]),  # comma inside quotes kept
            ("'x, y', z", ["'x, y'", "z"]),
            ('"say \\"hi\\"", b', ['"say \\"hi\\""', "b"]),  # escaped quote
            ('"end \\"", b', ['"end \\""', "b"]),
            ("  padded  ,  x  ", ["padded", "x"]),
            ("a,,b", ["a", "", "b"]),  # empty segment is an item
            ("a,", ["a"]),  # trailing comma drops the empty tail
            ("", []),
            ("solo", ["solo"]),
        ],
    )
    def test_table(self, text: str, expected: list[str]) -> None:
        assert yaml_lite.split_list_items(text) == expected


class TestParseInlineList:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("[python, rust]", ["python", "rust"]),
            ("[]", []),
            ("[  padded , x ]", ["padded", "x"]),
            ('["[[a]]", "[[b]]"]', ["[[a]]", "[[b]]"]),
            ('["a, b", c]', ["a, b", "c"]),
            ("[2026, 3.14, true]", ["2026", "3.14", "true"]),  # strings only
            ('["say \\"hi\\"", b]', ['say "hi"', "b"]),
            ("[a [b]]", ["a [b]"]),  # inner brackets survive the greedy match
            # Non-lists (None): the caller falls back to scalar parsing.
            ("[a, b", None),  # unterminated
            ("plain", None),
            ("[x] trailing", None),
            ("", None),
        ],
    )
    def test_table(self, value: str, expected: list[str] | None) -> None:
        assert yaml_lite.parse_inline_list(value) == expected


# ---------------------------------------------------------------------------
# dump policy
# ---------------------------------------------------------------------------


class TestScalarNeedsQuotes:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("plain", False),
            ("claude-haiku", False),
            ("2026-01-15", False),
            ("", True),
            (" padded", True),
            ("padded ", True),
            ("- dash", True),  # special first character
            ("# hash", True),
            ("[bracket]", True),
            ("a: b", True),  # colon+space
            ("ends:", True),
            ("a # b", True),  # inline-comment hazard
            ("true", True),  # coerced word
            ("NULL", True),
            ("42", True),  # numeric-looking strings
            ("3.14", True),
        ],
    )
    def test_table(self, text: str, expected: bool) -> None:
        assert yaml_lite.scalar_needs_quotes(text) is expected


class TestQuoteScalar:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("plain", '"plain"'),
            # A value containing a double quote is single-quoted.
            ('has "double"', "'has \"double\"'"),
            # A value containing only a single quote is double-quoted.
            ("has 'single'", "\"has 'single'\""),
            # Both quote characters: double-quoted with backslash escapes.
            ("both \"and '", '"both \\"and \'"'),
            ("back\\slash", '"back\\\\slash"'),
        ],
    )
    def test_table(self, text: str, expected: str) -> None:
        assert yaml_lite.quote_scalar(text) == expected


class TestDumpScalar:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (True, "true"),
            (False, "false"),
            (3, "3"),
            (3.14, "3.14"),
            ("plain", "plain"),
            ("3", '"3"'),  # numeric string must be quoted to stay a string
            ("true", '"true"'),
            ("ends:", '"ends:"'),
            (None, "None"),  # serialize_frontmatter filters None before dump
        ],
    )
    def test_table(self, value: Any, expected: str) -> None:
        assert yaml_lite.dump_scalar(value) == expected

    @pytest.mark.parametrize(
        "value",
        ["plain", "3", "true", "null", "a: b", "x # y", "ends:", "2026-01-15"],
    )
    def test_string_round_trip(self, value: str) -> None:
        assert yaml_lite.parse_scalar(yaml_lite.dump_scalar(value)) == value

    def test_numeric_round_trip(self) -> None:
        assert yaml_lite.parse_scalar(yaml_lite.dump_scalar(42)) == 42
        assert yaml_lite.parse_scalar(yaml_lite.dump_scalar(3.14)) == 3.14
        assert yaml_lite.parse_scalar(yaml_lite.dump_scalar(True)) is True


class TestDumpListItem:
    @pytest.mark.parametrize(
        ("item", "always_quote", "expected"),
        [
            ("python", False, "python"),
            ("2026", False, "2026"),  # list items are never coerced
            ("true", False, "true"),
            ("a, b", False, '"a, b"'),  # structural comma
            ("[x]", False, '"[x]"'),
            ('say "hi"', False, "'say \"hi\"'"),
            ("a: b", False, '"a: b"'),
            (" plain", False, '" plain"'),
            ("", False, '""'),
            ("plain", True, '"plain"'),  # always_quote forces it
            ("[[wikilink]]", True, '"[[wikilink]]"'),
        ],
    )
    def test_table(self, item: Any, always_quote: bool, expected: str) -> None:
        assert yaml_lite.dump_list_item(item, always_quote=always_quote) == expected

    @pytest.mark.parametrize(
        "items",
        [
            ["python", "rust"],
            ["2026", "3.14", "true"],
            ["a, b", "plain"],
            ["[[wikilink one]]", "[[wikilink two]]"],
            ['say "hi"'],
        ],
    )
    def test_inline_list_round_trip(self, items: list[str]) -> None:
        emitted = "[" + ", ".join(yaml_lite.dump_list_item(i) for i in items) + "]"
        assert yaml_lite.parse_inline_list(emitted) == items


# ---------------------------------------------------------------------------
# legacy aliases (vault_common / vault_config back-compat surface)
# ---------------------------------------------------------------------------


class TestLegacyAliases:
    def test_vault_config_private_names_are_yaml_lite(self) -> None:
        assert vault_config._parse_scalar is yaml_lite.parse_scalar
        assert vault_config._parse_list_item is yaml_lite.parse_list_item
        assert vault_config._split_list_items is yaml_lite.split_list_items
        assert vault_config._strip_inline_comment is yaml_lite.strip_inline_comment

    def test_vault_common_still_reexports_them(self) -> None:
        import vault_common

        assert vault_common._parse_scalar is yaml_lite.parse_scalar
        assert vault_common._split_list_items is yaml_lite.split_list_items
        assert vault_common._strip_inline_comment is yaml_lite.strip_inline_comment


# ---------------------------------------------------------------------------
# Differential fixtures: new parsers vs pre-ENH-024 parser output
# ---------------------------------------------------------------------------


def _quiet(fn: Callable[[str], Any], text: str) -> Any:
    """Run a parser with stderr suppressed (config parser warns by design)."""
    with contextlib.redirect_stderr(io.StringIO()):
        return fn(text)


class TestDifferentialConfig:
    """_parse_config_yaml output is identical to the pre-ENH-024 parser."""

    @pytest.mark.parametrize("case", _corpus("config"), ids=lambda c: c["name"])
    def test_output_matches_pre_consolidation(self, case: dict[str, Any]) -> None:
        got = _quiet(vault_config._parse_config_yaml, case["text"])
        assert _canon(got) == _canon(_expected("config")[case["name"]])


class TestDifferentialFrontmatter:
    """parse_frontmatter output is identical to the pre-ENH-024 parser."""

    @pytest.mark.parametrize("case", _corpus("frontmatter"), ids=lambda c: c["name"])
    def test_output_matches_pre_consolidation(self, case: dict[str, Any]) -> None:
        got = _quiet(parse_frontmatter, case["text"])
        assert _canon(got) == _canon(_expected("frontmatter")[case["name"]])


class TestDifferentialVaults:
    """read_vaults_yaml output is identical to the pre-ENH-024 reader."""

    @pytest.mark.parametrize("case", _corpus("vaults"), ids=lambda c: c["name"])
    def test_output_matches_pre_consolidation(
        self, case: dict[str, Any], tmp_path: Path
    ) -> None:
        cfg = tmp_path / "vaults.yaml"
        cfg.write_text(case["text"], encoding="utf-8")
        vaults, default = vault_path.read_vaults_yaml(cfg)
        got: dict[str, Any] = {"vaults": vaults, "default": default}
        assert _canon(got) == _canon(_expected("vaults")[case["name"]])
