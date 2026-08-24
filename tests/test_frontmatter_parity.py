"""ARC-005: Python half of the shared frontmatter serialization contract.

Consumes the same fixture as the TypeScript half
(``visualizer/lib/frontmatter.parity.test.ts``) — the ENH-005 pattern — so
the Python emitter (:func:`core.vault_index.serialize_frontmatter`) and the
visualizer's ``frontmatter.ts`` cannot drift on quoting, key order, or list
formatting.

The fixture's ``fields`` is the shared model: ``related`` holds bare stems
(the Python dict wraps them as ``[[wikilinks]]`` before serializing, and the
round-trip assertion unwraps them again); ``project``/``provenance``/
``session_id`` are ``""`` when absent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.vault_index import parse_frontmatter, serialize_frontmatter

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "parity" / "frontmatter.json"

#: The known fields both emitters order canonically (note_schema.FRONTMATTER_FIELD_ORDER).
_CANONICAL_ORDER = (
    "date",
    "type",
    "tags",
    "project",
    "confidence",
    "sources",
    "related",
    "provenance",
    "session_id",
)


def _load_vectors() -> list[dict[str, Any]]:
    with FIXTURE.open(encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["version"] == 1, (
        "bump both parity suites when the fixture format changes"
    )
    return data["vectors"]


def _model_to_fields(model: dict[str, Any]) -> dict[str, Any]:
    """Map the shared fixture model onto the Python emitter's dict shape."""
    fields: dict[str, Any] = {
        "date": model["date"],
        "type": model["type"],
        "tags": list(model["tags"]),
        "confidence": model["confidence"],
        "sources": list(model["sources"]),
        "related": [f"[[{stem}]]" for stem in model["related"]],
    }
    for key in ("project", "provenance", "session_id"):
        if model[key]:
            fields[key] = model[key]
    return fields


@pytest.mark.parametrize(
    "vector", _load_vectors(), ids=[v["name"] for v in _load_vectors()]
)
def test_serialize_matches_expected(vector: dict[str, Any]) -> None:
    fields = _model_to_fields(vector["fields"])
    assert serialize_frontmatter(fields) == vector["expected"]


@pytest.mark.parametrize(
    "vector", _load_vectors(), ids=[v["name"] for v in _load_vectors()]
)
def test_parse_round_trips_expected(vector: dict[str, Any]) -> None:
    parsed = parse_frontmatter(vector["expected"])
    assert parsed == _model_to_fields(vector["fields"])


def test_canonical_key_order_matches_note_schema() -> None:
    from note_schema import FRONTMATTER_FIELD_ORDER

    assert FRONTMATTER_FIELD_ORDER == _CANONICAL_ORDER


def test_emitter_quoting_rules() -> None:
    """Python-only quoting pins beyond the shared fixture surface."""
    # String that looks like a number must be quoted so parse returns a str.
    assert serialize_frontmatter({"a": "3"}) == '---\na: "3"\n---\n'
    # Real int stays bare and parses back to an int.
    assert parse_frontmatter(serialize_frontmatter({"a": 3})) == {"a": 3}
    # Booleans lowercase.
    assert serialize_frontmatter({"a": True, "b": False}) == (
        "---\na: true\nb: false\n---\n"
    )
    # None and empty string are dropped.
    assert serialize_frontmatter({"a": None, "b": "", "c": "x"}) == "---\nc: x\n---\n"
    # Colon-terminating and comment-hazard scalars are quoted.
    assert serialize_frontmatter({"a": "ends:"}) == '---\na: "ends:"\n---\n'
    assert serialize_frontmatter({"a": "x # y"}) == '---\na: "x # y"\n---\n'
    # Mid-string double quotes are valid plain YAML and round-trip bare.
    assert parse_frontmatter(serialize_frontmatter({"a": 'say "hi"'})) == {
        "a": 'say "hi"'
    }
    # A scalar STARTING with a quote is quoted so the parser does not strip it.
    assert serialize_frontmatter({"a": '"quoted" start'}) == (
        "---\na: '\"quoted\" start'\n---\n"
    )
    # Unknown keys follow the canonical ones in insertion order.
    out = serialize_frontmatter({"zebra": 1, "date": "2026-08-23"})
    assert out == "---\ndate: 2026-08-23\nzebra: 1\n---\n"


def test_writers_route_through_shared_emitter() -> None:
    """ARC-005 contract: the four historical writers are thin adapters."""
    import vault_merge
    import vault_new

    out = vault_new._build_frontmatter("pattern", ["python"], "myproj")
    assert out == serialize_frontmatter(
        {
            "date": out.splitlines()[1].split(": ", 1)[1],
            "type": "pattern",
            "tags": ["python"],
            "project": "myproj",
            "confidence": "medium",
            "sources": [],
            "related": ["[[vault-index]]"],
            "provenance": "inferred",
        }
    )
    merged = vault_merge._build_frontmatter(
        {"date": "2026-08-23", "type": "pattern", "tags": ["a"], "sources": None}
    )
    assert merged == "---\ndate: 2026-08-23\ntype: pattern\ntags: [a]\n---\n"
