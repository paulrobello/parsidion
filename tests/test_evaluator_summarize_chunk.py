"""Unit tests for the summarize-chunk evaluator (ENH-008, board item #3).

Exercises render / parse / score with NO AI call — render goes through the real
``prompt_templates.render`` (whose strict variable check is the drift gate),
and parse/score are pure functions fed canned model outputs.
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
from evaluators.summarize_chunk import SummarizeChunkEvaluator  # noqa: E402


@pytest.fixture(scope="module")
def evaluator() -> SummarizeChunkEvaluator:
    return SummarizeChunkEvaluator()


class TestSummarizeChunkLoad:
    def test_loads_two_cases(self, evaluator: SummarizeChunkEvaluator) -> None:
        cases = evaluator.load_cases()
        assert len(cases) == 2
        assert all("chunk_text" in c.inputs for c in cases)
        assert all(c.inputs["chunk_text"].strip() for c in cases)

    def test_limit(self, evaluator: SummarizeChunkEvaluator) -> None:
        assert len(evaluator.load_cases(limit=1)) == 1


class TestSummarizeChunkRender:
    def test_renders_through_real_template(
        self, evaluator: SummarizeChunkEvaluator
    ) -> None:
        case = evaluator.load_cases(limit=1)[0]
        rendered = evaluator.render(case)
        # The template's body both names "Transcript:" and inlines the chunk;
        # render() did not raise PromptError (variable contract holds).
        assert "Transcript:" in rendered
        assert "deadlock" in rendered  # chunk_text content survives


class TestSummarizeChunkScore:
    def _expected(self) -> dict[str, object]:
        return {
            "must_mention": ["deadlock", "mutex"],
            "sentence_min": 3,
            "sentence_max": 6,
        }

    def _good_summary(self) -> str:
        return (
            "The worker pool deadlocked because the dispatcher held the mutex "
            "across a blocking channel send. Shrinking the lock scope to just "
            "the counter increment removes the cyclic wait. A race-enabled "
            "load test now guards the lock ordering in CI."
        )

    def test_good_summary_scores_high(self, evaluator: SummarizeChunkEvaluator) -> None:
        case = CaseInput(case_id="synthetic", inputs={}, expected=self._expected())
        parsed = evaluator.parse(self._good_summary())
        score, checks = evaluator.score(parsed, case)
        assert checks["sentence_count_in_range"] == 1.0
        assert checks["must_mention"] == 1.0
        assert checks["no_preamble_no_fence"] == 1.0
        assert score == 100.0

    def test_too_few_sentences_fails_count(
        self, evaluator: SummarizeChunkEvaluator
    ) -> None:
        case = CaseInput(case_id="synthetic", inputs={}, expected=self._expected())
        parsed = evaluator.parse("The deadlock was a mutex scoping bug.")
        _, checks = evaluator.score(parsed, case)
        assert checks["sentence_count_in_range"] == 0.0

    def test_fenced_output_fails_no_preamble_no_fence(
        self, evaluator: SummarizeChunkEvaluator
    ) -> None:
        case = CaseInput(case_id="synthetic", inputs={}, expected=self._expected())
        fenced = "```\n" + self._good_summary() + "\n```"
        parsed = evaluator.parse(fenced)
        _, checks = evaluator.score(parsed, case)
        assert checks["no_preamble_no_fence"] == 0.0
