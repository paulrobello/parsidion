"""ENH-017: schema-vs-code coverage — every config key the code reads exists
in the schema, and every schema key is actually read (or explicitly reserved).

Two evidence lanes feed the "is this key read?" check:

1. Exact: ``get_config("section", "key", ...)`` literal calls (AST).
2. Broad: the dotted path ``section.key`` appearing anywhere in a script
   (typed-config attribute access such as ``cfg.summarizer.model``) — a
   floor, not a proof.

A key with no evidence in either lane must carry ``metadata={"reserved":
True}`` (documented but not yet implemented) or ``metadata={"section_read":
True}`` (consumed as part of its whole section, keys forwarded verbatim —
e.g. ``anthropic_env`` is merged into the subprocess env as a dict).
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import fields
from pathlib import Path

SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core import vault_schema  # noqa: E402


def _collect_sources() -> dict[Path, str]:
    """Every .py under scripts/ (recursive) — readers live in flat scripts,
    the core/ package, and the cli/ subpackages alike."""
    sources: dict[Path, str] = {}
    for py in sorted(SCRIPTS_DIR.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        sources[py] = py.read_text(encoding="utf-8")
    return sources


_READER_FUNC_RE = re.compile(r"^(get_config|_resolve$|_config_)")


def _get_config_calls(source: str) -> set[tuple[str, str]]:
    """(section, key) pairs from config-reader calls in *source*.

    Readers appear as bare ``get_config``, module-qualified
    ``vault_common.get_config``, and thin wrappers (``_config_bool`` /
    ``_config_str`` in ai_backend, ``_resolve`` in the summarizer, whose
    section/key sit at args 1-2 behind a CLI-flag override). For each
    matching call the first ADJACENT pair of string-literal arguments is
    taken as (section, key).
    """
    pairs: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else (func.attr if isinstance(func, ast.Attribute) else "")
        )
        if not _READER_FUNC_RE.match(name):
            continue
        args = node.args
        for a, b in zip(args, args[1:], strict=False):
            if (
                isinstance(a, ast.Constant)
                and isinstance(a.value, str)
                and isinstance(b, ast.Constant)
                and isinstance(b.value, str)
            ):
                pairs.add((a.value, b.value))
                break
    return pairs


def find_violations(
    sources: dict[Path, str],
) -> tuple[set[tuple[str, str]], list[tuple[str, str]]]:
    """Return (unknown_keys_read, unread_schema_keys).

    Args:
        sources: mapping of script path -> source text to scan.

    Returns:
        A pair of violation sets: ``(section, key)`` pairs the code reads
        that are absent from the schema, and schema keys with no reader
        evidence that are not marked reserved/section_read.
    """
    schema = vault_schema.schema_dict()
    all_text = "\n".join(sources.values())

    read_pairs: set[tuple[str, str]] = set()
    for text in sources.values():
        read_pairs |= _get_config_calls(text)

    unknown = {p for p in read_pairs if p[0] not in schema or p[1] not in schema[p[0]]}

    unread: list[tuple[str, str]] = []
    for section, section_cls in _section_classes().items():
        for f in fields(section_cls):
            m = f.metadata or {}
            if m.get("reserved") or m.get("section_read"):
                continue
            if (section, f.name) in read_pairs:
                continue
            if re.search(rf"\b{re.escape(section)}\.{re.escape(f.name)}\b", all_text):
                continue
            unread.append((section, f.name))
    return unknown, unread


def _section_classes() -> dict[str, type]:
    import typing

    out: dict[str, type] = {}
    hints = typing.get_type_hints(vault_schema.VaultAppConfig)
    for f in fields(vault_schema.VaultAppConfig):
        tp = hints[f.name]
        args = getattr(tp, "__args__", None)
        cls = tp
        if args is not None:
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) == 1:
                cls = non_none[0]
        if isinstance(cls, type) and hasattr(cls, "__dataclass_fields__"):
            out[f.name] = cls  # type: ignore[assignment]
    return out


class TestSchemaCoversCodeReads:
    def test_no_get_config_reads_outside_schema(self) -> None:
        unknown, _ = find_violations(_collect_sources())
        assert not unknown, f"get_config reads missing from schema: {sorted(unknown)}"

    def test_every_schema_key_has_a_reader(self) -> None:
        _, unread = find_violations(_collect_sources())
        assert not unread, (
            "Schema keys with no reader and no reserved/section_read marker "
            f"(mark metadata reserved=True only if truly unread, or fix the "
            f"reader): {unread}"
        )


class TestViolationDetection:
    def test_fake_get_config_call_is_flagged(self) -> None:
        fake = "x = get_config('nope', 'x', 1)\n"
        sources = {Path("fake.py"): fake}
        unknown, _ = find_violations(sources)
        assert ("nope", "x") in unknown
        # Same shape through a wrapper still reads as a config call.
        fake2 = "y = _config_bool('nope', 'y', True)\n"
        unknown2, _ = find_violations({Path("fake2.py"): fake2})
        assert ("nope", "y") in unknown2

    def test_fake_unread_key_is_flagged(self) -> None:
        """A schema key whose dotted path appears nowhere is reported."""
        _, unread = find_violations({Path("fake.py"): "pass\n"})
        # Every non-reserved, non-section_read key with no reader evidence
        # shows up when the corpus is empty.
        assert ("ai", "backend") in unread
