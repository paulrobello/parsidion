"""Tests for the optional provenance frontmatter field across templates and serializers."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import vault_new  # noqa: E402

_TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "templates"
)

# Canonical allowed values for the provenance field.
VALID_PROVENANCE: set[str] = {
    "explicit",
    "inferred",
    "corrected",
    "observed",
    "imported",
}

# Daily notes are auto-written by hooks -> observed. Everything else -> inferred.
_EXPECTED_TEMPLATE_PROVENANCE: dict[str, str] = {
    "debugging": "inferred",
    "framework": "inferred",
    "knowledge": "inferred",
    "language": "inferred",
    "pattern": "inferred",
    "project": "inferred",
    "research": "inferred",
    "tool": "inferred",
    "daily": "observed",
}


class TestTemplatesCarryProvenance:
    def test_every_template_has_a_provenance_line(self) -> None:
        missing: list[str] = []
        for tf in sorted(_TEMPLATES_DIR.glob("*.md")):
            text = tf.read_text(encoding="utf-8")
            if "provenance:" not in text:
                missing.append(tf.name)
        assert missing == [], f"Templates missing provenance: {missing}"

    def test_template_provenance_values_are_canonical(self) -> None:
        for name, expected in _EXPECTED_TEMPLATE_PROVENANCE.items():
            text = (_TEMPLATES_DIR / f"{name}.md").read_text(encoding="utf-8")
            line = next(
                (ln for ln in text.splitlines() if ln.startswith("provenance:")),
                None,
            )
            assert line is not None, f"{name}.md has no provenance line"
            value = line.split(":", 1)[1].strip()
            assert value == expected, (
                f"{name}.md provenance={value!r}, want {expected!r}"
            )


class TestVaultNewEmitsProvenance:
    def test_build_frontmatter_contains_provenance(self) -> None:
        fm = vault_new._build_frontmatter("pattern", ["vault"], project=None)
        assert "provenance: inferred" in fm
        # provenance must appear after related (epistemic cluster ordering)
        assert fm.index("related:") < fm.index("provenance:")


import vault_merge  # noqa: E402


class TestVaultMergePreservesProvenance:
    def test_build_frontmatter_emits_provenance(self) -> None:
        fm = vault_merge._build_frontmatter(
            {
                "date": "2026-06-16",
                "type": "pattern",
                "tags": ["x"],
                "related": ["[[a]]"],
                "provenance": "inferred",
            }
        )
        assert "provenance: inferred" in fm
        assert fm.index("related:") < fm.index("provenance:")


import importlib  # noqa: E402
import sys  # noqa: E402
import types  # noqa: E402
from collections.abc import Iterator  # noqa: E402
from typing import cast  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture()
def summarize_sessions() -> Iterator[types.ModuleType]:
    """Import summarize_sessions with anyio stubbed out.

    summarize_sessions.py is a PEP 723 script whose runtime deps (anyio) are
    not installed in the dev environment — tests/test_summarize_sessions.py
    uses the same stubbing pattern. We only exercise the pure-python
    _validate_frontmatter helper, so a no-op anyio namespace is sufficient.
    """
    sys.modules["anyio"] = cast(
        types.ModuleType,
        types.SimpleNamespace(
            Semaphore=object,
            to_thread=types.SimpleNamespace(
                run_sync=lambda func, *a, **k: func(*a, **k)
            ),
            create_task_group=object,
            run=lambda func, *a, **k: func(*a, **k),
        ),
    )
    sys.modules.pop("summarize_sessions", None)
    try:
        yield importlib.import_module("summarize_sessions")
    finally:
        sys.modules.pop("summarize_sessions", None)
        sys.modules.pop("anyio", None)


class TestSummarizerAcceptsProvenance:
    def test_valid_provenance_passes_validation(
        self, summarize_sessions: types.ModuleType
    ) -> None:
        note = (
            "---\n"
            "date: 2026-06-16\n"
            "type: pattern\n"
            "tags: [vault]\n"
            "provenance: inferred\n"
            'related: ["[[x]]"]\n'
            "---\n# Title\nbody\n"
        )
        assert summarize_sessions._validate_frontmatter(note) is None

    def test_invalid_provenance_fails_validation(
        self, summarize_sessions: types.ModuleType
    ) -> None:
        note = (
            "---\n"
            "date: 2026-06-16\n"
            "type: pattern\n"
            "tags: [vault]\n"
            "provenance: guesswork\n"
            'related: ["[[x]]"]\n'
            "---\n# Title\nbody\n"
        )
        err = summarize_sessions._validate_frontmatter(note)
        assert err is not None and "provenance" in err.lower()

    def test_missing_provenance_still_valid(
        self, summarize_sessions: types.ModuleType
    ) -> None:
        # provenance is optional — notes without it must still validate.
        note = (
            "---\n"
            "date: 2026-06-16\n"
            "type: pattern\n"
            "tags: [vault]\n"
            'related: ["[[x]]"]\n'
            "---\n# Title\nbody\n"
        )
        assert summarize_sessions._validate_frontmatter(note) is None
