"""Unit tests for the per-prompt evaluator package (ENH-008, board item #3).

These exercise render / parse / score with NO AI call — the render path goes
through the real ``prompt_templates.render`` (whose strict variable check is the
drift gate), and parse/score are pure functions fed canned model outputs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EVAL_DIR = _REPO_ROOT / "tools" / "eval"
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from evaluators import CaseInput, EVALUATORS, parse_flat_yaml  # noqa: E402
from evaluators.summarize_session import SummarizeSessionEvaluator  # noqa: E402

_TEMPLATES_DIR = _REPO_ROOT / "skills" / "parsidion" / "templates" / "prompts"


# ---------------------------------------------------------------------------
# Flat-YAML parser
# ---------------------------------------------------------------------------


class TestParseFlatYaml:
    def test_scalars_and_lists(self) -> None:
        out = parse_flat_yaml(
            "should_produce_note: true\n"
            "expected_type: debugging\n"
            "count: 3\n"
            "ratio: 0.5\n"
            "expected_tags_include: [sqlite, locking]\n"
            'must_mention: ["WAL", "inode"]\n'
            "empty: []\n"
            "# a comment line\n"
            "blank:\n"
        )
        assert out["should_produce_note"] is True
        assert out["expected_type"] == "debugging"
        assert out["count"] == 3
        assert out["ratio"] == 0.5
        assert out["expected_tags_include"] == ["sqlite", "locking"]
        assert out["must_mention"] == ["WAL", "inode"]
        assert out["empty"] == []
        assert "blank" not in out  # empty values are skipped
        assert "# a comment line" not in out


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_summarize_session_registered(self) -> None:
        assert "summarize-session" in EVALUATORS
        assert isinstance(EVALUATORS["summarize-session"], SummarizeSessionEvaluator)

    def test_every_registered_id_has_a_template(self) -> None:
        # Each evaluator's prompt_id must resolve to a real template file, so the
        # registry cannot drift from templates/prompts/.
        for prompt_id, ev in EVALUATORS.items():
            assert (_TEMPLATES_DIR / f"{prompt_id}.md").is_file(), prompt_id
            assert ev.prompt_id == prompt_id


# ---------------------------------------------------------------------------
# summarize-session evaluator
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def summarizer() -> SummarizeSessionEvaluator:
    return SummarizeSessionEvaluator()


class TestSummarizeSessionLoad:
    def test_loads_eight_cases(self, summarizer: SummarizeSessionEvaluator) -> None:
        cases = summarizer.load_cases()
        assert len(cases) == 8
        assert all("cleaned_transcript" in c.inputs for c in cases)
        assert all(c.inputs["cleaned_transcript"].strip() for c in cases)

    def test_limit(self, summarizer: SummarizeSessionEvaluator) -> None:
        assert len(summarizer.load_cases(limit=3)) == 3


class TestSummarizeSessionRender:
    def test_renders_through_real_template(
        self, summarizer: SummarizeSessionEvaluator
    ) -> None:
        case = summarizer.load_cases(limit=1)[0]
        rendered = summarizer.render(case)
        # The template's SYSTEM line + the inlined transcript both appear, and
        # render() did not raise PromptError (variable contract holds).
        assert "vault-note-writing API" in rendered
        assert "database is locked" in rendered  # from the sqlite transcript


class TestSummarizeSessionScore:
    def _good_note(self) -> str:
        return (
            "---\n"
            "date: 2026-07-31\n"
            "type: debugging\n"
            "tags: [sqlite, locking]\n"
            "confidence: high\n"
            'related: ["[[sqlite]]"]\n'
            "---\n"
            "# SQLite locking fix\n"
            "## Summary\n"
            "Enable WAL mode to avoid database-is-locked errors; the inode is stable.\n"
        )

    def test_good_note_scores_high(self, summarizer: SummarizeSessionEvaluator) -> None:
        case = CaseInput(
            case_id="synthetic",
            inputs={},
            expected={
                "should_produce_note": True,
                "expected_type": "debugging",
                "expected_tags_include": ["sqlite", "locking"],
                "expected_tags_exclude": ["misc"],
                "must_mention": ["WAL", "inode"],
            },
        )
        parsed = summarizer.parse(self._good_note())
        score, checks = summarizer.score(parsed, case)
        assert checks["write_gate"] == 1.0
        assert checks["type"] == 1.0
        assert checks["frontmatter"] == 1.0
        assert checks["must_mention"] == 1.0
        assert score == 100.0

    def test_skip_when_none_expected_scores_full(
        self, summarizer: SummarizeSessionEvaluator
    ) -> None:
        case = CaseInput(
            case_id="synthetic",
            inputs={},
            expected={"should_produce_note": False},
        )
        parsed = summarizer.parse('{"decision": "skip", "reason": "transient run"}')
        score, checks = summarizer.score(parsed, case)
        assert checks["write_gate"] == 1.0
        assert score == 100.0

    def test_wrong_type_fails_type_check(
        self, summarizer: SummarizeSessionEvaluator
    ) -> None:
        case = CaseInput(
            case_id="synthetic",
            inputs={},
            expected={"should_produce_note": True, "expected_type": "debugging"},
        )
        parsed = summarizer.parse(
            self._good_note().replace("type: debugging", "type: pattern")
        )
        _, checks = summarizer.score(parsed, case)
        assert checks["type"] == 0.0
        assert checks["frontmatter"] == 1.0  # 'pattern' is still a valid type
