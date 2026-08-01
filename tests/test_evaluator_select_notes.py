"""Unit tests for the ``select-notes`` evaluator (ENH-008, board item #3).

Exercises render / parse / score with NO AI call — the render path goes through
the real ``prompt_templates.render`` (whose strict bidirectional variable check
is the drift gate), and parse/score are pure functions fed canned model outputs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EVAL_DIR = _REPO_ROOT / "tools" / "eval"
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from evaluators import CaseInput  # noqa: E402
from evaluators.select_notes import SelectNotesEvaluator  # noqa: E402


@pytest.fixture(scope="module")
def selector() -> SelectNotesEvaluator:
    return SelectNotesEvaluator()


# ---------------------------------------------------------------------------
# load_cases
# ---------------------------------------------------------------------------


class TestSelectNotesLoad:
    def test_loads_two_cases(self, selector: SelectNotesEvaluator) -> None:
        cases = selector.load_cases()
        assert len(cases) == 2
        assert all("candidates_text" in c.inputs for c in cases)
        assert all(c.inputs["candidates_text"].strip() for c in cases)

    def test_limit(self, selector: SelectNotesEvaluator) -> None:
        assert len(selector.load_cases(limit=1)) == 1


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


class TestSelectNotesRender:
    def test_renders_through_real_template(
        self, selector: SelectNotesEvaluator
    ) -> None:
        case = selector.load_cases(limit=1)[0]
        rendered = selector.render(case)
        # The candidate notes are inlined inside the <content> guard, and the
        # render() call did not raise PromptError (the variable contract holds).
        assert "<content>" in rendered
        assert "</content>" in rendered
        assert "acme-cli" in rendered  # project_name substituted into the body
        assert "Patterns/acme-cli-args.md" in rendered  # candidates inlined


# ---------------------------------------------------------------------------
# score
# ---------------------------------------------------------------------------


def _expected_acme() -> dict[str, object]:
    return {
        "project_name": "acme-cli",
        "cwd": "/workspace/acme-cli",
        "output_limit": 1500,
        "must_select": ["acme-cli-args"],
        "must_not_select": ["sourdough"],
    }


class TestSelectNotesScore:
    def _good_output(self) -> str:
        return (
            "### Acme CLI argument parsing (Patterns/acme-cli-args.md)\n"
            "Argparse subcommand dispatcher for the acme-cli tool.\n"
            "Parses --verbose and --config flags centrally.\n"
        )

    def test_good_output_scores_full(self, selector: SelectNotesEvaluator) -> None:
        case = CaseInput(case_id="synthetic", inputs={}, expected=_expected_acme())
        parsed = selector.parse(self._good_output())
        score, checks = selector.score(parsed, case)
        assert checks["must_select_present"] == 1.0
        assert checks["must_not_select_absent"] == 1.0
        assert checks["under_output_limit"] == 1.0
        assert checks["correct_format"] == 1.0
        assert score == 100.0

    def test_bad_output_leaks_sourdough(self, selector: SelectNotesEvaluator) -> None:
        # The forbidden tangent note is included alongside the right one.
        bad = (
            self._good_output() + "\n### Baking sourdough (Knowledge/sourdough.md)\n"
            "Hobby cooking note about bread.\n"
        )
        case = CaseInput(case_id="synthetic", inputs={}, expected=_expected_acme())
        parsed = selector.parse(bad)
        _, checks = selector.score(parsed, case)
        assert checks["must_not_select_absent"] == 0.0
        # The right note is still present and well-formed.
        assert checks["must_select_present"] == 1.0
        assert checks["correct_format"] == 1.0

    def test_bad_output_over_limit(self, selector: SelectNotesEvaluator) -> None:
        # Well-formed selection but the body blows past the 1500-char budget.
        over = (
            "### Acme CLI argument parsing (Patterns/acme-cli-args.md)\n"
            + "detail " * 300  # ~2100 chars
        )
        case = CaseInput(case_id="synthetic", inputs={}, expected=_expected_acme())
        parsed = selector.parse(over)
        _, checks = selector.score(parsed, case)
        assert checks["under_output_limit"] == 0.0
        # Format and selection are still correct; only the budget fails.
        assert checks["correct_format"] == 1.0
        assert checks["must_select_present"] == 1.0

    def test_empty_output_scores_zero_on_selection(
        self, selector: SelectNotesEvaluator
    ) -> None:
        case = CaseInput(case_id="synthetic", inputs={}, expected=_expected_acme())
        parsed = selector.parse("")
        _, checks = selector.score(parsed, case)
        assert checks["must_select_present"] == 0.0
        assert checks["correct_format"] == 0.0
