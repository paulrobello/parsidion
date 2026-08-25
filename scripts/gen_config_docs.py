#!/usr/bin/env python3
"""ENH-017: generate the config reference from ``core/vault_schema.py``.

Single source of truth for the three hand-maintained config documents —
the CLAUDE.md config table, the ``docs/ARCHITECTURE.md`` reference block,
and ``skills/parsidion/templates/config.yaml`` — is the typed schema in
``skills/parsidion/scripts/core/vault_schema.py`` (field metadata:
``doc``, ``read_by``, optional ``example`` / ``reserved``). This script
renders all three from the schema plus standalone copies under
``docs/generated/``, so the documents cannot drift from the keys the code
reads. CI runs ``make config-docs-check`` which regenerates in-memory and
diffs against the committed files, failing on drift — the same contract
``gen_parity_fixtures.py`` established for the graph fixtures.

Stdlib-only. Usage::

    uv run python scripts/gen_config_docs.py            # regenerate
    uv run python scripts/gen_config_docs.py --check     # diff vs committed (CI)
    uv run python scripts/gen_config_docs.py --help
"""

from __future__ import annotations

import argparse
import difflib
import sys
import textwrap
from dataclasses import fields
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "skills" / "parsidion" / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from core import vault_schema  # noqa: E402

_TEMPLATE = _REPO_ROOT / "skills" / "parsidion" / "templates" / "config.yaml"
_TEMPLATE_HEADER = (
    _REPO_ROOT / "skills" / "parsidion" / "templates" / "config.header.yaml"
)
_CLAUDE_MD = _REPO_ROOT / "CLAUDE.md"
_ARCHITECTURE_MD = _REPO_ROOT / "docs" / "ARCHITECTURE.md"
_GENERATED_DIR = _REPO_ROOT / "docs" / "generated"
_GENERATED_TABLE = _GENERATED_DIR / "config-table.md"
_GENERATED_REFERENCE = _GENERATED_DIR / "config-reference.md"

_TABLE_START = "<!-- config-table:start -->"
_TABLE_END = "<!-- config-table:end -->"
_REFERENCE_START = "<!-- config-reference:start -->"
_REFERENCE_END = "<!-- config-reference:end -->"

# Docstring comment lines are wrapped to this width (the "#" prefix + one
# space count toward it).
_WRAP_WIDTH = 78


# ---------------------------------------------------------------------------
# Schema traversal
# ---------------------------------------------------------------------------


def _sections() -> list[tuple[str, type, list[Any]]]:
    """Yield (section_name, section_class, field_list) in schema order."""
    out: list[tuple[str, type, list[Any]]] = []
    for f in fields(vault_schema.VaultAppConfig):
        ann = _section_class_for(f.name)
        if ann is None:
            continue
        out.append((f.name, ann, list(fields(ann))))
    return out


def _section_class_for(name: str) -> type | None:
    """Return the dataclass behind aggregate field *name* (or None)."""
    import typing

    hints = typing.get_type_hints(vault_schema.VaultAppConfig)
    tp = hints.get(name)
    args = getattr(tp, "__args__", None)
    cls = tp
    if args is not None:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            cls = non_none[0]
    if isinstance(cls, type) and hasattr(cls, "__dataclass_fields__"):
        return cls  # type: ignore[return-value]
    return None


def _meta(f: Any) -> dict[str, Any]:
    """Field metadata with doc/read_by defaults."""
    m = dict(f.metadata or {})
    m.setdefault("doc", "")
    m.setdefault("read_by", "")
    return m


def _read_by_union(fl: list[Any]) -> str:
    """Deduped, order-stable module list across a section's fields."""
    seen: list[str] = []
    for f in fl:
        for mod in _meta(f)["read_by"].split(","):
            mod = mod.strip()
            if mod and mod not in seen and not mod.startswith("("):
                seen.append(mod)
    return ", ".join(seen)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def _yaml_scalar(value: Any) -> str:
    """Render *value* as a YAML scalar (bare when unambiguous)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    s = str(value)
    low = s.lower()
    if (
        not s
        or s != s.strip()
        or "#" in s
        or ":" in s
        or low in {"true", "false", "null", "yes", "no", "on", "off"}
        or s.startswith(("[", "{", "'", '"', "&", "*", "!", "|", ">", "%", "@", "`"))
    ):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _docstring_comment(cls: type) -> list[str]:
    """The section docstring as '# ' comment lines, wrapped."""
    doc = (cls.__doc__ or "").strip()
    if not doc:
        return []
    lines: list[str] = []
    for paragraph in doc.split("\n\n"):
        if lines:
            lines.append("#")
        wrapped = textwrap.wrap(
            paragraph.replace("\n", " "),
            width=_WRAP_WIDTH,
            initial_indent="# ",
            subsequent_indent="# ",
        )
        lines.extend(wrapped)
    return lines


def render_annotated_yaml() -> str:
    """The canonical annotated YAML: template body == ARCHITECTURE block."""
    out: list[str] = []
    for name, cls, fl in _sections():
        out.extend(_docstring_comment(cls))
        out.append(f"{name}:")
        for f in fl:
            m = _meta(f)
            value = m.get("example", f.default)
            if isinstance(value, dict):
                # Dict-valued keys nest one level deeper than the config
                # parser's inline-comment tolerance on section lines — the
                # doc goes on its own comment line and the key line stays
                # bare (`  claude:`), exactly like the hand-maintained
                # template.
                out.append(f"  # {m['doc']}")
                out.append(f"  {f.name}:")
                for k, v in value.items():
                    out.append(f"    {k}: {_yaml_scalar(v)}")
            else:
                out.append(f"  {f.name}: {_yaml_scalar(value)}  # {m['doc']}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_template() -> str:
    """templates/config.yaml = hand-maintained header prose + generated body."""
    header = _TEMPLATE_HEADER.read_text(encoding="utf-8")
    if not header.endswith("\n"):
        header += "\n"
    return header + "\n" + render_annotated_yaml()


def render_table() -> str:
    """The CLAUDE.md config-sections table (Section | Keys | Used by)."""
    rows: list[str] = ["| Section | Keys | Used by |", "|---|---|---|"]
    for name, _cls, fl in _sections():
        keys: list[str] = []
        for f in fl:
            m = _meta(f)
            key = f"`{f.name}`"
            if m.get("reserved"):
                key += " (reserved)"
            keys.append(key)
        rows.append(f"| `{name}` | {', '.join(keys)} | {_read_by_union(fl)} |")
    return "\n".join(rows) + "\n"


def render_reference_doc() -> str:
    """docs/generated/config-reference.md — the annotated YAML in a fence."""
    return (
        "<!-- Generated by scripts/gen_config_docs.py from\n"
        "     skills/parsidion/scripts/core/vault_schema.py — do not edit\n"
        "     by hand; run `make config-docs` after changing the schema. -->\n"
        "\n```yaml\n" + render_annotated_yaml().rstrip() + "\n```\n"
    )


def render_table_doc() -> str:
    """docs/generated/config-table.md — the table with a generated header."""
    return (
        "<!-- Generated by scripts/gen_config_docs.py from\n"
        "     skills/parsidion/scripts/core/vault_schema.py — do not edit\n"
        "     by hand; run `make config-docs` after changing the schema. -->\n"
        "\n" + render_table()
    )


# ---------------------------------------------------------------------------
# Marker-region rewriting
# ---------------------------------------------------------------------------


def _replace_region(text: str, start: str, end: str, content: str) -> str:
    """Replace the region between *start*/*end* markers with *content*."""
    i = text.find(start)
    j = text.find(end)
    if i == -1 or j == -1 or j < i:
        raise SystemExit(
            f"gen_config_docs: markers {start!r}/{end!r} missing or out of order"
        )
    return text[: i + len(start)] + "\n" + content.rstrip() + "\n" + text[j:]


def _rewrite_claude_md(text: str) -> str:
    return _replace_region(text, _TABLE_START, _TABLE_END, render_table())


def _rewrite_architecture_md(text: str) -> str:
    fenced = "```yaml\n" + render_annotated_yaml().rstrip() + "\n```"
    return _replace_region(text, _REFERENCE_START, _REFERENCE_END, fenced)


# ---------------------------------------------------------------------------
# Check / write drivers
# ---------------------------------------------------------------------------


def _collect_outputs() -> dict[Path, str]:
    """Every generated artifact as (path, rendered content)."""
    claude = _rewrite_claude_md(_CLAUDE_MD.read_text(encoding="utf-8"))
    arch = _rewrite_architecture_md(_ARCHITECTURE_MD.read_text(encoding="utf-8"))
    return {
        _TEMPLATE: render_template(),
        _GENERATED_TABLE: render_table_doc(),
        _GENERATED_REFERENCE: render_reference_doc(),
        _CLAUDE_MD: claude,
        _ARCHITECTURE_MD: arch,
    }


def run_check() -> int:
    """Diff every artifact against its committed copy; 1 on any drift."""
    drifted = False
    for path, rendered in _collect_outputs().items():
        if not path.exists():
            print(f"gen_config_docs: MISSING {path.relative_to(_REPO_ROOT)}")
            drifted = True
            continue
        committed = path.read_text(encoding="utf-8")
        if committed == rendered:
            continue
        drifted = True
        rel = path.relative_to(_REPO_ROOT)
        print(f"gen_config_docs: DRIFT in {rel}")
        diff = difflib.unified_diff(
            committed.splitlines(keepends=True),
            rendered.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
        sys.stdout.writelines(diff)
    if drifted:
        print(
            "\nRun `make config-docs` (uv run python scripts/gen_config_docs.py) "
            "and commit the result."
        )
        return 1
    print("gen_config_docs: all config documents match the schema.")
    return 0


def run_write() -> None:
    """(Re)write every artifact; creates docs/generated/ on first run."""
    _GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    for path, rendered in _collect_outputs().items():
        path.write_text(rendered, encoding="utf-8")
        print(f"gen_config_docs: wrote {path.relative_to(_REPO_ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            __doc__ or "Generate the config reference from vault_schema.py."
        ).splitlines()[0]
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Regenerate in memory and diff against the committed files; exit 1 on drift.",
    )
    args = parser.parse_args()
    sys.exit(run_check() if args.check else (run_write() or 0))


if __name__ == "__main__":
    main()
