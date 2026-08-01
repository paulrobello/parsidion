"""Unit tests for the detect-conflicts evaluator (ENH-008, board item #3).

Exercises render / parse / score with NO AI call — render goes through the real
``prompt_templates.render`` (whose strict variable check is the drift gate),
and parse/score are pure functions fed canned model outputs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EVAL_DIR = _REPO_ROOT / "tools" / "eval"
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from evaluators import CaseInput  # noqa: E402
from evaluators.detect_conflicts import DetectConflictsEvaluator  # noqa: E402


@pytest.fixture(scope="module")
def evaluator() -> DetectConflictsEvaluator:
    return DetectConflictsEvaluator()


# ---------------------------------------------------------------------------
# load_cases
# ---------------------------------------------------------------------------


class TestLoadCases:
    def test_loads_two_cases(self, evaluator: DetectConflictsEvaluator) -> None:
        cases = evaluator.load_cases()
        assert len(cases) == 2
        assert all("note_block" in c.inputs for c in cases)
        assert all(c.inputs["note_block"].strip() for c in cases)

    def test_limit(self, evaluator: DetectConflictsEvaluator) -> None:
        assert len(evaluator.load_cases(limit=1)) == 1


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


class TestRender:
    def test_renders_through_real_template(
        self, evaluator: DetectConflictsEvaluator
    ) -> None:
        case = evaluator.load_cases(limit=1)[0]
        # render() did not raise PromptError — the variable contract holds.
        rendered = evaluator.render(case)
        assert "NOTES:" in rendered
        # The inlined note_block carries the per-note headings through.
        assert "### note-" in rendered


# ---------------------------------------------------------------------------
# parse + score
# ---------------------------------------------------------------------------


class TestParse:
    def test_parses_json_array(self, evaluator: DetectConflictsEvaluator) -> None:
        parsed = evaluator.parse(json.dumps([{"a": "note-a", "b": "note-b"}]))
        assert parsed["valid"] is True
        assert parsed["elements_valid"] is True
        assert len(parsed["array"]) == 1

    def test_invalid_json(self, evaluator: DetectConflictsEvaluator) -> None:
        parsed = evaluator.parse("not json")
        assert parsed["valid"] is False
        assert parsed["array"] == []

    def test_non_list_json(self, evaluator: DetectConflictsEvaluator) -> None:
        parsed = evaluator.parse('{"a": "note-a"}')
        assert parsed["valid"] is False
        assert parsed["array"] == []


class TestScore:
    def test_good_detection_scores_full(
        self, evaluator: DetectConflictsEvaluator
    ) -> None:
        case = CaseInput(
            case_id="synthetic",
            inputs={"note_block": ""},
            expected={
                "note_count": 3,
                "expected_conflicts": ["note-a", "note-b"],
            },
        )
        good = json.dumps(
            [
                {
                    "type": "contradiction",
                    "a": "note-a",
                    "b": "note-b",
                    "a_says": "use WAL",
                    "b_says": "never use WAL",
                    "recommendation": "needs_review",
                }
            ]
        )
        parsed = evaluator.parse(good)
        score, checks = evaluator.score(parsed, case)
        assert checks["valid_json_array"] == 1.0
        assert checks["element_schema"] == 1.0
        assert checks["contradiction_recall"] == 1.0
        assert checks["contradiction_precision"] == 1.0
        assert score == 100.0

    def test_empty_array_when_conflict_expected_fails_recall(
        self, evaluator: DetectConflictsEvaluator
    ) -> None:
        case = CaseInput(
            case_id="synthetic",
            inputs={"note_block": ""},
            expected={
                "note_count": 3,
                "expected_conflicts": ["note-a", "note-b"],
            },
        )
        parsed = evaluator.parse("[]")
        score, checks = evaluator.score(parsed, case)
        # Valid JSON array, but nothing flagged → recall collapses.
        assert checks["valid_json_array"] == 1.0
        assert checks["contradiction_recall"] == 0.0
        assert score < 100.0

    def test_invalid_json_fails_valid_check(
        self, evaluator: DetectConflictsEvaluator
    ) -> None:
        case = CaseInput(
            case_id="synthetic",
            inputs={"note_block": ""},
            expected={
                "note_count": 3,
                "expected_conflicts": ["note-a", "note-b"],
            },
        )
        parsed = evaluator.parse("not json at all")
        _, checks = evaluator.score(parsed, case)
        assert checks["valid_json_array"] == 0.0
        assert checks["contradiction_recall"] == 0.0

    def test_expect_empty_array_scores_full(
        self, evaluator: DetectConflictsEvaluator
    ) -> None:
        case = CaseInput(
            case_id="synthetic",
            inputs={"note_block": ""},
            expected={"note_count": 3, "expect_empty": True},
        )
        parsed = evaluator.parse("[]")
        score, checks = evaluator.score(parsed, case)
        assert checks["contradiction_recall"] == 1.0
        assert checks["contradiction_precision"] == 1.0
        assert score == 100.0

    def test_expect_empty_but_flagged_collapses(
        self, evaluator: DetectConflictsEvaluator
    ) -> None:
        case = CaseInput(
            case_id="synthetic",
            inputs={"note_block": ""},
            expected={"note_count": 3, "expect_empty": True},
        )
        parsed = evaluator.parse(json.dumps([{"a": "note-a", "b": "note-b"}]))
        _, checks = evaluator.score(parsed, case)
        assert checks["contradiction_recall"] == 0.0
        assert checks["contradiction_precision"] == 0.0
