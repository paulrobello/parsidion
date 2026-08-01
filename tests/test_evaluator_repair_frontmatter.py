"""Unit tests for the ``repair-frontmatter`` evaluator (ENH-008, board item #3).

Exercises render / parse / score with NO AI call — the render path goes through
the real ``prompt_templates.render`` (whose strict bidirectional variable check
is the drift gate), and parse/score are pure functions fed canned outputs.
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
from evaluators.repair_frontmatter import RepairFrontmatterEvaluator  # noqa: E402


@pytest.fixture(scope="module")
def evaluator() -> RepairFrontmatterEvaluator:
    return RepairFrontmatterEvaluator()


class TestLoad:
    def test_loads_two_cases(self, evaluator: RepairFrontmatterEvaluator) -> None:
        cases = evaluator.load_cases()
        assert len(cases) == 2
        assert all("content" in c.inputs for c in cases)
        assert all(c.inputs["content"].strip() for c in cases)


class TestRender:
    def test_renders_through_real_template(
        self, evaluator: RepairFrontmatterEvaluator
    ) -> None:
        case = evaluator.load_cases(limit=1)[0]
        rendered = evaluator.render(case)
        # The prompt's "Issues to fix:" heading and the BEGIN marker around the
        # inlined note both appear, and render() did not raise PromptError
        # (the variable contract holds).
        assert "Issues to fix" in rendered
        assert "---BEGIN---" in rendered


class TestScore:
    def _good_output(self) -> str:
        # Corrected note: valid type, single FM block, no markers, body preserved.
        return (
            "---\n"
            "date: 2026-07-31\n"
            "type: debugging\n"
            "tags: [sqlite, locking]\n"
            "confidence: high\n"
            'related: ["[[sqlite]]"]\n'
            "sources: []\n"
            "---\n"
            "# SQLite locking fix\n"
            "Enable WAL mode and use a connection-pool to avoid locks.\n"
        )

    def _bad_output(self) -> str:
        # BAD: echoes the BEGIN/END markers AND keeps the invalid type, AND drops
        # one of the must-mention body keywords.
        return (
            "---BEGIN---\n"
            "---\n"
            "date: 2026-07-31\n"
            "type: notetype\n"
            "tags: [sqlite]\n"
            "confidence: high\n"
            'related: ["[[sqlite]]"]\n'
            "---\n"
            "---END---\n"
            "# SQLite locking fix\n"
            "Enable WAL mode.\n"
        )

    def _case(self) -> CaseInput:
        return CaseInput(
            case_id="synthetic",
            inputs={"content": "ignored"},
            expected={
                "issues": ["type"],
                "rel": "Debugging/sqlite-locking.md",
                "expected_type": "debugging",
                "must_mention": ["WAL", "connection-pool"],
            },
        )

    def test_good_output_scores_full(
        self, evaluator: RepairFrontmatterEvaluator
    ) -> None:
        parsed = evaluator.parse(self._good_output())
        score, checks = evaluator.score(parsed, self._case())
        assert checks["issues_fixed"] == 1.0
        assert checks["valid_type"] == 1.0
        assert checks["single_wellformed_fm"] == 1.0
        assert checks["no_BEGIN_END_markers"] == 1.0
        assert checks["body_otherwise_preserved"] == 1.0
        assert score == 100.0

    def test_bad_output_fails_relevant_checks(
        self, evaluator: RepairFrontmatterEvaluator
    ) -> None:
        parsed = evaluator.parse(self._bad_output())
        _, checks = evaluator.score(parsed, self._case())
        # type issue NOT resolved (kept invalid 'notetype') → 0
        assert checks["issues_fixed"] == 0.0
        # 'notetype' is not a valid note type → 0
        assert checks["valid_type"] == 0.0
        # echoed the BEGIN/END markers → 0
        assert checks["no_BEGIN_END_markers"] == 0.0
        # 'connection-pool' dropped from the body → fraction < 1.0
        assert checks["body_otherwise_preserved"] < 1.0
