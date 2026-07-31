#!/usr/bin/env python3
"""ENH-005: generate and validate cross-language parity fixtures.

This script is the single source of truth for two cross-language contracts
between the Python vault tooling and the TypeScript visualizer:

1. **Graph JSON Schema** (``tests/fixtures/graph.schema.json``) -- DERIVED
   from ``GRAPH_JSON_SCHEMA`` in
   ``skills/parsidion/scripts/build_graph.py``. The fixture is a generated
   artifact; do not hand-edit it. Run ``make parity-fixtures`` to regenerate
   after changing ``GRAPH_JSON_SCHEMA``. CI runs ``make parity-fixtures-check``
   which regenerates to a temp path and diffs against the committed file,
   failing if they differ -- so the fixture cannot drift from the emitter.

2. **Vault-resolution vectors** (``tests/fixtures/parity/vault-resolution.json``)
   -- hand-authored test vectors (they encode intent). This script VALIDATES
   their structure (unique names, version match, every vector has expect OR
   expect_error, applies_to well-formed) but does NOT write them. The vectors
   are consumed by ``tests/test_vault_resolver_parity.py`` (Python) and
   ``visualizer/lib/vaultResolver.parity.test.ts`` (TypeScript).

Stdlib-only -- no third-party imports. ``GRAPH_JSON_SCHEMA`` is extracted from
``build_graph.py`` via :mod:`ast` (so ``numpy``, which ``build_graph.py``
imports, is never pulled into this process). ``build_graph.py`` is therefore
the canonical location for schema edits, and the fixture mirrors it exactly.

Usage::

    uv run python scripts/gen_parity_fixtures.py            # regenerate
    uv run python scripts/gen_parity_fixtures.py --check     # diff vs committed (CI)
    uv run python scripts/gen_parity_fixtures.py --help
"""

from __future__ import annotations

import argparse
import ast
import difflib
import json
import sys
from pathlib import Path
from typing import Any

# Bumped whenever the vault-resolution vector format changes in a way that
# requires both consumers to update. The committed fixture must carry this
# exact value; the generator fails if it does not.
VECTORS_VERSION: int = 1

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BUILD_GRAPH_PY = _REPO_ROOT / "skills" / "parsidion" / "scripts" / "build_graph.py"
_GRAPH_SCHEMA_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "graph.schema.json"
_VECTORS_FIXTURE = (
    _REPO_ROOT / "tests" / "fixtures" / "parity" / "vault-resolution.json"
)

_VALID_APPLIES_TO = frozenset({"python", "typescript"})


# ---------------------------------------------------------------------------
# Graph schema extraction (from build_graph.py, without importing numpy)
# ---------------------------------------------------------------------------


def _extract_graph_schema(build_graph_path: Path = _BUILD_GRAPH_PY) -> dict[str, Any]:
    """Return the ``GRAPH_JSON_SCHEMA`` dict from ``build_graph.py``.

    Uses :mod:`ast` + :func:`ast.literal_eval` so the host process never
    imports ``build_graph`` (which would pull in ``numpy``). The schema dict
    is required to be JSON-literal -- if it ever gains a non-literal
    construct, this function fails loudly, which is the intended signal that
    the schema can no longer be statically extracted and the approach needs
    revisiting.
    """
    source = build_graph_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.iter_child_nodes(tree):
        # Handle both annotated (``X: dict = {...}`` -> AnnAssign) and plain
        # (``X = {...}`` -> Assign) top-level assignments.
        target_id = ""
        value_node: ast.expr | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_id = node.target.id
            value_node = node.value
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    target_id = target.id
                    value_node = node.value
                    break
        if target_id == "GRAPH_JSON_SCHEMA" and value_node is not None:
            try:
                schema = ast.literal_eval(value_node)
            except (ValueError, SyntaxError) as exc:
                raise SystemExit(
                    f"GRAPH_JSON_SCHEMA in {build_graph_path} is no longer a "
                    f"pure literal dict (ast.literal_eval failed: {exc}). "
                    "Revisit scripts/gen_parity_fixtures.py to extract it."
                ) from exc
            if not isinstance(schema, dict):
                raise SystemExit(
                    f"GRAPH_JSON_SCHEMA in {build_graph_path} did not parse to "
                    f"a dict (got {type(schema).__name__})."
                )
            return schema
    raise SystemExit(
        f"Could not find a top-level GRAPH_JSON_SCHEMA assignment in {build_graph_path}."
    )


def _render_graph_schema(schema: dict[str, Any]) -> str:
    """Stable serialisation of the graph schema (sorted keys, indent=2)."""
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# Vault-resolution vector validation
# ---------------------------------------------------------------------------


def _validate_vectors(data: dict[str, Any], vectors_path: Path) -> None:
    """Structurally validate the vault-resolution vectors.

    Raises AssertionError on any malformation. This is a lint, not a
    behaviour check: the two resolver test suites assert the actual
    ``resolve_vault`` / ``resolveVault`` behaviour against each vector.
    """
    errors: list[str] = []

    if data.get("version") != VECTORS_VERSION:
        errors.append(
            f"version is {data.get('version')!r}, expected {VECTORS_VERSION}. "
            "Bump VECTORS_VERSION in gen_parity_fixtures.py AND update both "
            "consumers if the vector format changed."
        )

    vectors = data.get("vectors")
    if not isinstance(vectors, list) or not vectors:
        errors.append("'vectors' must be a non-empty list")
        assert not errors, f"{vectors_path}: " + "\n  ".join(errors)
        return  # for type checkers

    seen_names: set[str] = set()
    for i, vec in enumerate(vectors):
        prefix = f"vectors[{i}]"
        if not isinstance(vec, dict):
            errors.append(f"{prefix}: not an object")
            continue
        name = vec.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"{prefix}: missing/empty 'name'")
            continue
        if name in seen_names:
            errors.append(f"{prefix}: duplicate name {name!r}")
        seen_names.add(name)

        has_expect = "expect" in vec
        has_error = "expect_error" in vec
        if has_expect and has_error:
            errors.append(f"{prefix} ({name}): has both expect and expect_error")
        elif not has_expect and not has_error:
            errors.append(f"{prefix} ({name}): needs expect or expect_error")

        if has_error and not isinstance(vec["expect_error"], str):
            errors.append(f"{prefix} ({name}): expect_error must be a label string")

        applies = vec.get("applies_to")
        if applies is None:
            pass  # applies to both languages
        elif (
            isinstance(applies, list)
            and applies
            and all(isinstance(x, str) and x in _VALID_APPLIES_TO for x in applies)
        ):
            pass
        else:
            errors.append(
                f"{prefix} ({name}): applies_to must be a non-empty subset of "
                f"{sorted(_VALID_APPLIES_TO)}"
            )

    assert not errors, f"{vectors_path}:\n  " + "\n  ".join(errors)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_regenerate() -> None:
    """Regenerate graph.schema.json from build_graph.py and validate vectors."""
    schema = _extract_graph_schema()
    rendered = _render_graph_schema(schema)
    _GRAPH_SCHEMA_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    _GRAPH_SCHEMA_FIXTURE.write_text(rendered, encoding="utf-8")
    print(f"wrote {_GRAPH_SCHEMA_FIXTURE} ({len(rendered)} bytes)")

    vectors_data = json.loads(_VECTORS_FIXTURE.read_text(encoding="utf-8"))
    _validate_vectors(vectors_data, _VECTORS_FIXTURE)
    n = len(vectors_data["vectors"])
    print(
        f"validated {_VECTORS_FIXTURE} ({n} vectors, version {vectors_data['version']})"
    )


def cmd_check() -> int:
    """Regenerate to a temp tree, diff against committed fixtures.

    Returns 0 if everything matches, 1 otherwise. Used by CI
    (``make parity-fixtures-check``) so a schema/vector that drifted from the
    generator fails the build rather than silently regenerating.
    """
    failures: list[str] = []

    # --- Graph schema drift ---
    schema = _extract_graph_schema()
    rendered = _render_graph_schema(schema)
    committed = (
        _GRAPH_SCHEMA_FIXTURE.read_text(encoding="utf-8")
        if _GRAPH_SCHEMA_FIXTURE.exists()
        else ""
    )
    if rendered != committed:
        diff = difflib.unified_diff(
            committed.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=str(_GRAPH_SCHEMA_FIXTURE) + " (committed)",
            tofile=str(_GRAPH_SCHEMA_FIXTURE) + " (generated)",
            n=3,
        )
        failures.append(
            "graph.schema.json is out of date vs GRAPH_JSON_SCHEMA in build_graph.py.\n"
            "Run `make parity-fixtures` to regenerate.\n--- diff ---\n" + "".join(diff)
        )

    # --- Vectors structural validation ---
    try:
        vectors_data = json.loads(_VECTORS_FIXTURE.read_text(encoding="utf-8"))
        _validate_vectors(vectors_data, _VECTORS_FIXTURE)
    except AssertionError as exc:
        failures.append(str(exc))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"{_VECTORS_FIXTURE}: could not parse: {exc}")

    if failures:
        for f in failures:
            print("FAIL: " + f, file=sys.stderr)
        return 1

    print(
        f"OK: {_GRAPH_SCHEMA_FIXTURE.name} matches GRAPH_JSON_SCHEMA; "
        f"{_VECTORS_FIXTURE.name} structurally valid."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate/validate ENH-005 parity fixtures.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit code 0 in --check mode means the committed fixtures match "
            "what the generator would produce. Non-zero means drift."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Diff committed fixtures against freshly generated output; exit 1 on drift.",
    )
    args = parser.parse_args(argv)

    if args.check:
        return cmd_check()
    cmd_regenerate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
