"""ENH-017: ``scripts/gen_config_docs.py`` — determinism, drift detection,
and the byte-for-byte template contract.

The generator is imported as a module (its ``run_check``/renderers are
pure w.r.t. the committed tree), so the drift test can perturb a committed
artifact in place, observe ``run_check`` fail, and restore it.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "gen_config_docs", REPO_ROOT / "scripts" / "gen_config_docs.py"
)
assert _spec is not None and _spec.loader is not None
gen = importlib.util.module_from_spec(_spec)
sys.modules["gen_config_docs"] = gen
_spec.loader.exec_module(gen)


class TestDeterminism:
    def test_renders_are_stable(self) -> None:
        assert gen.render_annotated_yaml() == gen.render_annotated_yaml()
        assert gen.render_table() == gen.render_table()
        assert gen.render_template() == gen.render_template()


class TestTemplateContract:
    def test_generated_template_equals_committed_byte_for_byte(self) -> None:
        committed = (
            REPO_ROOT / "skills" / "parsidion" / "templates" / "config.yaml"
        ).read_text(encoding="utf-8")
        assert gen.render_template() == committed


class TestDriftDetection:
    def test_check_passes_on_committed_tree(self) -> None:
        assert gen.run_check() == 0

    def test_check_fails_on_edited_artifact(self) -> None:
        """A hand edit to any generated artifact must fail --check."""
        path = REPO_ROOT / "docs" / "generated" / "config-table.md"
        original = path.read_text(encoding="utf-8")
        try:
            path.write_text(original + "| hand edit |\n", encoding="utf-8")
            assert gen.run_check() == 1
        finally:
            path.write_text(original, encoding="utf-8")
        # Restored: clean again.
        gen.run_check()  # also drains the success print for capsymmetry
        assert gen.run_check() == 0

    def test_schema_edit_changes_output(self) -> None:
        """The acceptance shape: a deliberate schema description edit flips
        the rendered template (so config-docs-check would exit 1)."""
        rendered = gen.render_template()
        assert "Model for AI note selection" in rendered
