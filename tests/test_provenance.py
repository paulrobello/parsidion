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
