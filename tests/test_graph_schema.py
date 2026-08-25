"""ARC-038: graph.json schema contract and cross-language parity.

``tests/fixtures/graph.schema.json`` is the canonical shape of the vault
graph.json emitted by ``build_graph.py`` and consumed by the TypeScript
visualizer (``visualizer/lib/graph.ts`` ``GraphData`` interface).  Both
sides must agree; this module enforces that.

Why a stdlib mini-validator instead of ``jsonschema``:
    The core test suite runs ``uv run pytest`` with no third-party deps
    (``pyproject.toml`` declares ``dependencies = []``; only the
    ``search``/``tools``/``eval`` extras pull fastembed & friends).
    ``jsonschema`` is not in any extra, so depending on it would make the
    schema gate silently skip in every default environment.  The subset of
    JSON Schema we actually use (type / required / properties / items /
    additionalProperties / enum / minimum / maximum / minLength) is small
    enough to interpret with ~50 lines of stdlib, and the validator is
    reused by ``tests/test_build_graph_parsight.py`` to check a generated
    graph.json.  The committed schema file is still a genuine JSON Schema
    (draft 2020-12) document, so any external tooling that speaks JSON
    Schema can validate against it too.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "skills" / "parsidion" / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_SCHEMA_PATH = _REPO_ROOT / "tests" / "fixtures" / "graph.schema.json"
_SAMPLE_PATH = (
    _REPO_ROOT / "visualizer" / "lib" / "__fixtures__" / "graph" / "sample.json"
)
_GRAPH_TS = _REPO_ROOT / "visualizer" / "lib" / "graph.ts"


# ---------------------------------------------------------------------------
# stdlib JSON-Schema-subset validator
# ---------------------------------------------------------------------------


def _type_ok(value: Any, type_name: str) -> bool:
    """Check a JSON value against a JSON Schema type name."""
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        # Reject bools (bool is an int subclass in Python) and floats.
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True  # unknown type keyword -- be permissive


def validate_against_schema(
    instance: Any, schema: dict[str, Any], *, path: str = "<root>"
) -> list[str]:
    """Validate *instance* against *schema* using the supported subset.

    Returns a list of human-readable error strings.  An empty list means the
    instance conforms.  Supported keywords: ``type``, ``required``,
    ``properties``, ``items``, ``additionalProperties`` (bool only),
    ``enum``, ``minimum``, ``maximum``, ``minLength``.
    """
    errors: list[str] = []

    if "type" in schema and not _type_ok(instance, schema["type"]):
        errors.append(
            f"{path}: expected type {schema['type']!r}, got {type(instance).__name__}"
        )
        return errors  # deeper checks are meaningless if the type is wrong

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in enum {schema['enum']}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        props = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            if key in props:
                errors.extend(
                    validate_against_schema(value, props[key], path=f"{path}.{key}")
                )
            elif additional is False:
                errors.append(f"{path}: additional property {key!r} not allowed")

    if isinstance(instance, list) and "items" in schema:
        item_schema = schema["items"]
        for idx, value in enumerate(instance):
            errors.extend(
                validate_against_schema(value, item_schema, path=f"{path}[{idx}]")
            )

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} > maximum {schema['maximum']}")

    if isinstance(instance, str) and "minLength" in schema:
        if len(instance) < schema["minLength"]:
            errors.append(
                f"{path}: string shorter than minLength {schema['minLength']}"
            )

    return errors


def validate_graph(graph: dict[str, Any], schema: dict[str, Any]) -> None:
    """Assert *graph* conforms to *schema*, raising AssertionError on failure.

    Also enforces the graph invariant the schema cannot express: every edge
    endpoint must reference a node id that exists.
    """
    errors = validate_against_schema(graph, schema)
    # Referential integrity (graph invariant, not expressible in JSON Schema).
    node_ids = {n["id"] for n in graph.get("nodes", []) if isinstance(n, dict)}
    for i, edge in enumerate(graph.get("edges", [])):
        if not isinstance(edge, dict):
            continue
        for endpoint in ("s", "t"):
            value = edge.get(endpoint)
            if isinstance(value, str) and value not in node_ids:
                errors.append(
                    f"edges[{i}].{endpoint}={value!r} does not reference a node id"
                )
    assert not errors, "graph.json schema validation failed:\n  " + "\n  ".join(errors)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def schema() -> dict[str, Any]:
    """Load the canonical graph schema fixture."""
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def sample_graph() -> dict[str, Any]:
    """Load the visualizer's sample graph fixture."""
    return json.loads(_SAMPLE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Schema self-consistency
# ---------------------------------------------------------------------------


class TestSchemaSelfConsistency:
    """The schema fixture must itself be a well-formed JSON Schema document."""

    def test_schema_file_exists(self) -> None:
        assert _SCHEMA_PATH.exists(), f"schema not found at {_SCHEMA_PATH}"

    def test_schema_is_json_schema_draft(self, schema: dict[str, Any]) -> None:
        assert schema.get("$schema", "").startswith("https://json-schema.org/"), (
            "fixture must declare a JSON Schema $schema"
        )
        assert schema.get("type") == "object"
        assert schema.get("title") == "GraphData"

    def test_schema_top_level_required(self, schema: dict[str, Any]) -> None:
        assert schema["required"] == ["meta", "nodes", "edges"]

    def test_schema_edge_kind_is_closed_enum(self, schema: dict[str, Any]) -> None:
        """Edge kind must be the closed set {semantic, wiki}.

        Matches the TypeScript ``kind: 'semantic' | 'wiki'`` union and the
        three writers in build_graph.py (semantic, wiki, parsight body wiki).
        """
        kind = schema["properties"]["edges"]["items"]["properties"]["kind"]
        assert kind["enum"] == ["semantic", "wiki"]

    def test_schema_node_required_fields(self, schema: dict[str, Any]) -> None:
        node_required = schema["properties"]["nodes"]["items"]["required"]
        assert node_required == [
            "id",
            "title",
            "type",
            "folder",
            "path",
            "tags",
            "incoming_links",
            "mtime",
        ]

    def test_schema_parsight_body_links_optional(self, schema: dict[str, Any]) -> None:
        """parsight_body_links is optional (absent when enrichment added nothing)."""
        meta_required = schema["properties"]["meta"]["required"]
        assert "parsight_body_links" not in meta_required
        assert "parsight_body_links" in schema["properties"]["meta"]["properties"]


# ---------------------------------------------------------------------------
# Visualizer sample.json validates against the schema
# ---------------------------------------------------------------------------


class TestSampleValidates:
    """The visualizer's committed sample graph must conform to the schema.

    This is the agreement that the TypeScript ``GraphData`` interface and the
    Python writer produce the same shape.
    """

    def test_sample_file_exists(self) -> None:
        assert _SAMPLE_PATH.exists(), f"sample fixture not found at {_SAMPLE_PATH}"

    def test_sample_conforms_to_schema(
        self, schema: dict[str, Any], sample_graph: dict[str, Any]
    ) -> None:
        validate_graph(sample_graph, schema)

    def test_sample_has_both_edge_kinds(self, sample_graph: dict[str, Any]) -> None:
        """The fixture should exercise both enum values so the enum is real."""
        kinds = {e["kind"] for e in sample_graph["edges"]}
        assert kinds == {"semantic", "wiki"}


# ---------------------------------------------------------------------------
# TypeScript interface parity (visualizer/lib/graph.ts)
# ---------------------------------------------------------------------------


def _parse_ts_interface(source: str, name: str) -> dict[str, str]:
    """Extract ``field: type`` pairs from a TS interface body by name.

    Handles optional fields (``field?: type``) -- the trailing ``?`` is stripped
    from the field name.  Type is the raw text up to the line's terminator
    (``\\n`` or ``;``), trimmed.  Nested object bodies (e.g. ``GraphData.meta``'s
    inline block) are tracked via brace depth and their inner fields are NOT
    collected here -- only the interface's own (depth-0) fields are returned,
    so ``GraphData`` yields ``{meta, nodes, edges}`` rather than swallowing the
    meta block's fields. Use :func:`_parse_ts_meta_block` for the meta fields.
    """
    m = re.search(
        rf"export\s+interface\s+{re.escape(name)}\s*(?:<[^>]*>)?\s*\{{(.*?)\n\}}",
        source,
        re.DOTALL,
    )
    assert m, f"could not find interface {name} in graph.ts"
    body = m.group(1)
    fields: dict[str, str] = {}
    depth = 0
    for raw in body.splitlines():
        # A field is ours when it sits at the interface's own nesting level.
        # Evaluate at start-of-line depth so a field that opens a nested
        # block on the same line (``meta: {``) is still captured as ours.
        start_depth = depth
        depth += raw.count("{") - raw.count("}")
        if start_depth != 0:
            continue  # inside a nested block -- not one of our fields
        line = raw.strip()
        if not line or line.startswith("//") or line.startswith("/**"):
            continue
        fm = re.match(r"(\w+)\??:\s*(.+?)(?:;|$)", line)
        if fm:
            fields[fm.group(1)] = fm.group(2).strip()
    return fields


def _parse_ts_meta_block(source: str) -> dict[str, str]:
    """Extract the inline ``meta: { ... }`` block fields from GraphData."""
    m = re.search(r"meta:\s*\{(.*?)\n\s*\}", source, re.DOTALL)
    assert m, "could not find GraphData.meta inline block in graph.ts"
    body = m.group(1)
    fields: dict[str, str] = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("/**"):
            continue
        fm = re.match(r"(\w+)\??:\s*(.+?)(?:;|$)", line)
        if fm:
            fields[fm.group(1)] = fm.group(2).strip()
    return fields


class TestTypeScriptInterfaceParity:
    """The TypeScript ``GraphData`` interfaces must agree with the schema.

    Drift in either direction (a field added on one side but not the other,
    a renamed field, a kind enum that widens) fails here.  Same philosophy as
    ``test_vault_resolver_parity.py``: text-level parse of the TS source, no
    Node/Bun runtime needed.
    """

    def test_graph_ts_exists(self) -> None:
        assert _GRAPH_TS.exists(), f"graph.ts not found at {_GRAPH_TS}"

    def test_note_node_fields_match(self) -> None:
        ts = _parse_ts_interface(_GRAPH_TS.read_text(encoding="utf-8"), "NoteNode")
        assert set(ts) == {
            "id",
            "title",
            "type",
            "folder",
            "path",
            "tags",
            "incoming_links",
            "mtime",
        }

    def test_graph_edge_fields_and_kind_union(self) -> None:
        ts = _parse_ts_interface(_GRAPH_TS.read_text(encoding="utf-8"), "GraphEdge")
        assert set(ts) == {"s", "t", "w", "kind"}
        # The TS kind union must stay the closed set the schema enum pins.
        assert ts["kind"] == "'semantic' | 'wiki'"

    def test_graph_data_meta_fields_match(self, schema: dict[str, Any]) -> None:
        ts_meta = _parse_ts_meta_block(_GRAPH_TS.read_text(encoding="utf-8"))
        schema_meta_props = set(schema["properties"]["meta"]["properties"])
        assert set(ts_meta) == schema_meta_props, (
            f"GraphData.meta fields drifted: ts={set(ts_meta)} schema={schema_meta_props}"
        )

    def test_graph_data_top_level_fields(self) -> None:
        ts = _parse_ts_interface(_GRAPH_TS.read_text(encoding="utf-8"), "GraphData")
        assert set(ts) == {"meta", "nodes", "edges"}
