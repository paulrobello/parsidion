"""Unit tests for the ``merge-notes`` prompt evaluator (ENH-008, board item #3).

Render goes through the real ``prompt_templates.render`` (its strict variable
check is the drift gate); parse/score are pure functions fed canned bodies, with
NO AI call.
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
from evaluators.merge_notes import MergeNotesEvaluator  # noqa: E402


@pytest.fixture(scope="module")
def merger() -> MergeNotesEvaluator:
    return MergeNotesEvaluator()


# ---------------------------------------------------------------------------
# load_cases
# ---------------------------------------------------------------------------


class TestMergeNotesLoad:
    def test_loads_two_cases(self, merger: MergeNotesEvaluator) -> None:
        cases = merger.load_cases()
        assert len(cases) == 2
        assert all("body_a" in c.inputs and "body_b" in c.inputs for c in cases)
        assert all(
            c.inputs["body_a"].strip() and c.inputs["body_b"].strip() for c in cases
        )

    def test_limit(self, merger: MergeNotesEvaluator) -> None:
        assert len(merger.load_cases(limit=1)) == 1


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


class TestMergeNotesRender:
    def test_renders_through_real_template(self, merger: MergeNotesEvaluator) -> None:
        case = merger.load_cases(limit=1)[0]
        rendered = merger.render(case)
        # Both note wrappers and the topic title appear in the rendered prompt,
        # and render() did not raise PromptError (variable contract holds).
        assert "<note_a>" in rendered
        assert "<note_b>" in rendered
        assert str(case.expected.get("title")) in rendered


# ---------------------------------------------------------------------------
# parse + score
# ---------------------------------------------------------------------------


def _expected() -> dict[str, object]:
    return {
        "title": "worker-pool tuning",
        "must_mention_a": ["WAL"],
        "must_mention_b": ["backpressure"],
        "must_not_duplicate": ["single writer"],
    }


class TestMergeNotesScore:
    def _good_merged(self) -> str:
        return (
            "## Summary\n"
            "Worker-pool tuning for the ingestion pipeline. Enable WAL mode on "
            "SQLite to keep readers unblocked, and apply backpressure so a slow "
            "writer cannot exhaust memory.\n\n"
            "## Key Learnings\n"
            "- Use a single writer thread to serialise writes and avoid lock "
            "contention\n"
            "- Bounded queue with backpressure signalling\n"
            "- Batch inserts in groups of 500\n"
        )

    def test_good_merged_scores_full(self, merger: MergeNotesEvaluator) -> None:
        case = CaseInput(case_id="synthetic", inputs={}, expected=_expected())
        parsed = merger.parse(self._good_merged())
        score, checks = merger.score(parsed, case)
        assert checks["facts_a_present"] == 1.0
        assert checks["facts_b_present"] == 1.0
        assert checks["no_duplication"] == 1.0
        assert checks["has_headings_no_fence_no_fm"] == 1.0
        assert score == 100.0

    def test_missing_body_b_fact_zeroes_facts_b(
        self, merger: MergeNotesEvaluator
    ) -> None:
        # Drop every mention of "backpressure" — body_b's fact disappears.
        body = self._good_merged().replace("backpressure", "flow control")
        case = CaseInput(case_id="synthetic", inputs={}, expected=_expected())
        parsed = merger.parse(body)
        score, checks = merger.score(parsed, case)
        assert checks["facts_b_present"] == 0.0
        assert checks["facts_a_present"] == 1.0  # WAL still present
        assert score == 70.0

    def test_wrapped_in_fence_zeroes_format(self, merger: MergeNotesEvaluator) -> None:
        body = "```\n" + self._good_merged() + "\n```"
        case = CaseInput(case_id="synthetic", inputs={}, expected=_expected())
        parsed = merger.parse(body)
        score, checks = merger.score(parsed, case)
        assert checks["has_headings_no_fence_no_fm"] == 0.0
        assert parsed["has_fence"] is True
        assert score == 80.0  # only the 20-point format check fails

    def test_leading_frontmatter_zeroes_format(
        self, merger: MergeNotesEvaluator
    ) -> None:
        body = "---\ntype: pattern\n---\n" + self._good_merged()
        case = CaseInput(case_id="synthetic", inputs={}, expected=_expected())
        parsed = merger.parse(body)
        _, checks = merger.score(parsed, case)
        assert parsed["has_frontmatter"] is True
        assert checks["has_headings_no_fence_no_fm"] == 0.0

    def test_repeated_phrase_zeroes_no_duplication(
        self, merger: MergeNotesEvaluator
    ) -> None:
        # Echo the "single writer" line twice → the dedup target appears >1x.
        body = self._good_merged().replace(
            "## Key Learnings",
            "- Use a single writer thread for ordering\n## Key Learnings",
            1,
        )
        case = CaseInput(case_id="synthetic", inputs={}, expected=_expected())
        parsed = merger.parse(body)
        _, checks = merger.score(parsed, case)
        assert checks["no_duplication"] == 0.0
