"""ARC-207: emit -> parse round-trip over the config template's scalar shapes.

The single YAML emitter is ``parsidion.core.yaml_lite.dump_scalar`` (ENH-024
subset); ARC-207 made ``scripts/gen_config_docs.py`` consume it instead of a
private ``_yaml_scalar`` copy, killing a second quoting/coercion rule before
it can drift. This suite pins the emitter contract end to end: every scalar
shape the generated config template can contain round-trips exactly through
the stdlib reader in the same module.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = str(_REPO_ROOT / "skills" / "parsidion" / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from parsidion.core.yaml_lite import (  # noqa: E402
    dump_list_item,
    dump_scalar,
    parse_inline_list,
    parse_list_item,
    parse_scalar,
)


# Scalar shapes that appear in the generated config template (bare strings,
# ints, floats, bools, nulls, quoted strings) plus the quoting-forcing
# variants the emitter must survive.
SCALAR_CASES = [
    pytest.param("claude-cli", "claude-cli", id="bare-string"),
    pytest.param(0, 0, id="int-zero"),
    pytest.param(42, 42, id="int"),
    pytest.param(-7, -7, id="int-negative"),
    pytest.param(3.14, 3.14, id="float"),
    pytest.param(True, True, id="bool-true"),
    pytest.param(False, False, id="bool-false"),
    pytest.param(None, None, id="null"),
    pytest.param("", "", id="empty-string"),
    pytest.param("3", "3", id="numeric-string"),
    pytest.param("true", "true", id="bool-word-string"),
    pytest.param("null", "null", id="null-word-string"),
    pytest.param("a: b", "a: b", id="colon-space"),
    pytest.param("has #hash", "has #hash", id="hash"),
    pytest.param(" padded ", " padded ", id="edge-spaces"),
    pytest.param("-dash", "-dash", id="special-prefix"),
    pytest.param('say "hi"', 'say "hi"', id="embedded-quotes"),
    pytest.param("ends with colon:", "ends with colon:", id="trailing-colon"),
]


@pytest.mark.parametrize(("value", "expected"), SCALAR_CASES)
def test_dump_scalar_roundtrip(value: object, expected: object) -> None:
    emitted = dump_scalar(value)
    assert parse_scalar(emitted) == expected


@pytest.mark.parametrize(("value", "expected"), SCALAR_CASES)
def test_dump_scalar_no_none_regression(value: object, expected: object) -> None:
    """ARC-207 regression: None must emit 'null', never the string 'None'."""
    emitted = dump_scalar(value)
    assert emitted != "None" or value == "None"
    if value is None:
        assert emitted == "null"


# Inline-list shapes (vault frontmatter / config arrays).
LIST_CASES = [
    pytest.param(["sqlite", "prompt-cache"], id="plain-items"),
    pytest.param(["a, b", "c"], id="comma-item"),
    pytest.param(["key: value", "x"], id="colon-item"),
    pytest.param(["-1"], id="dash-item"),
    pytest.param([""], id="empty-item"),
]


@pytest.mark.parametrize(("items",), LIST_CASES)
def test_dump_list_item_roundtrip(items: list[str]) -> None:
    rendered = "[" + ", ".join(dump_list_item(i) for i in items) + "]"
    parsed = parse_inline_list(rendered)
    assert parsed == [parse_list_item(dump_list_item(i)) for i in items]
