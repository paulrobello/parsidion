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

    def test_every_template_is_registered(self) -> None:
        # Reverse parity: a prompt template cannot ship without an evaluator.
        template_ids = {p.stem for p in _TEMPLATES_DIR.glob("*.md")}
        assert template_ids == set(EVALUATORS), (
            f"template/registry drift: "
            f"templates={sorted(template_ids)} registry={sorted(EVALUATORS)}"
        )


# ---------------------------------------------------------------------------
# summarize-session evaluator
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def summarizer() -> SummarizeSessionEvaluator:
    return SummarizeSessionEvaluator()


class TestSummarizeSessionLoad:
    def test_loads_nine_cases(self, summarizer: SummarizeSessionEvaluator) -> None:
        cases = summarizer.load_cases()
        assert len(cases) == 9
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
        assert checks["related_links"] == 1.0
        assert score == 100.0

    def test_block_style_frontmatter_lists_parsed(self, summarizer):
        raw = (
            "---\n"
            "type: debugging\n"
            "tags:\n"
            "  - sqlite\n"
            "  - locking\n"
            "related:\n"
            '  - "[[sqlite]]"\n'
            '  - "[[wal]]"\n'
            "---\n"
            "# note\n"
        )
        parsed = summarizer.parse(raw)
        assert parsed["tags"] == ["sqlite", "locking"]
        assert parsed["related_count"] == 2

    def test_related_links_min_enforced(
        self, summarizer: SummarizeSessionEvaluator
    ) -> None:
        case = CaseInput(
            case_id="synthetic",
            inputs={},
            expected={
                "should_produce_note": True,
                "expected_type": "debugging",
                "related_links_min": 2,
            },
        )
        parsed = summarizer.parse(self._good_note())  # has exactly 1 wikilink
        score, checks = summarizer.score(parsed, case)
        assert checks["related_links"] == 0.0
        assert score == 90.0  # only the 10-point related-links check fails

    def test_frontmatter_valid_mismatch_fails(
        self, summarizer: SummarizeSessionEvaluator
    ) -> None:
        case = CaseInput(
            case_id="synthetic",
            inputs={},
            expected={"should_produce_note": True, "frontmatter_valid": True},
        )
        parsed = summarizer.parse(
            self._good_note().replace("type: debugging", "type: bogus-type")
        )
        _, checks = summarizer.score(parsed, case)
        assert checks["frontmatter"] == 0.0  # derived invalid != expected valid

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


@pytest.fixture
def evidence_case(summarizer: SummarizeSessionEvaluator) -> CaseInput:
    return next(
        c for c in summarizer.load_cases() if c.case_id == "009-rejected-ammo-guidance"
    )


_EVIDENCE_NOTE = """---
date: 2026-10-25
type: project
tags: [sim, baseline]
confidence: medium
related: ["[[simulation]]"]
---
# Baseline evidence and uncertainty
The accepted baseline remains single-drone. Ammo and guidance variants were rejected.
The run ended at 840.0167s before earliest possible expiration at 850.033s.
There was no expiration edge. The source retained 4690 of its 6000 capacity.
Zero qualifying witnesses were recorded. The second drone was not requested.
Guidance remains a hypothesis, not a confirmed cause.
"""


class TestSummarizeSessionEvidence:
    def test_fixture_renders_real_evidence(
        self, summarizer: SummarizeSessionEvaluator, evidence_case: CaseInput
    ) -> None:
        rendered = summarizer.render(evidence_case)
        for fact in (
            "840.0167",
            "850.033",
            "source_remaining=4690",
            "qualifying_witnesses=0",
        ):
            assert fact in rendered
        score, checks = summarizer.score(
            summarizer.parse(_EVIDENCE_NOTE), evidence_case
        )
        assert score == 100.0
        assert checks["required_claims"] == 1.0
        assert checks["unsupported_claims"] == 1.0
        assert checks["causal_confidence"] == 1.0

    @pytest.mark.parametrize(
        "claim",
        [
            "The source expired.",
            "The source was exhausted.",
            "The supply ran out.",
            "Telemetry confirmed expiry.",
            "Telemetry confirmed exhaustion.",
            "The ammo variant was selected.",
            "The guidance variant was shipped.",
            "The guidance variant was accepted.",
            "A second drone was requested.",
            "Witnesses confirmed exhaustion.",
            "No issue was reported; the source expired.",
            "The cause is uncertain, but the source expired.",
        ],
    )
    def test_false_assertions_are_red_even_with_all_keywords(
        self,
        summarizer: SummarizeSessionEvaluator,
        evidence_case: CaseInput,
        claim: str,
    ) -> None:
        score, checks = summarizer.score(
            summarizer.parse(_EVIDENCE_NOTE + claim), evidence_case
        )
        assert checks["must_mention"] == 1.0
        assert checks["unsupported_claims"] == 0.0
        assert score == 0.0

    @pytest.mark.parametrize(
        "claim",
        [
            "The source did not expire.",
            "The source was not exhausted.",
            "There is no evidence the source expired.",
            "The source might have expired in a longer run.",
            "A possible source expiration remains unconfirmed.",
            "The ammo variant was not selected.",
            "No qualifying witnesses confirmed exhaustion.",
        ],
    )
    def test_negative_evidence_and_hypotheses_are_not_confirmed_causes(
        self,
        summarizer: SummarizeSessionEvaluator,
        evidence_case: CaseInput,
        claim: str,
    ) -> None:
        score, checks = summarizer.score(
            summarizer.parse(_EVIDENCE_NOTE + claim), evidence_case
        )
        assert checks["unsupported_claims"] == 1.0
        assert score == 100.0

    def test_high_confidence_inferred_cause_is_red(
        self, summarizer: SummarizeSessionEvaluator, evidence_case: CaseInput
    ) -> None:
        raw = _EVIDENCE_NOTE.replace("confidence: medium", "confidence: high")
        score, checks = summarizer.score(summarizer.parse(raw), evidence_case)
        assert checks["causal_confidence"] == 0.0
        assert score == 0.0

    def test_keyword_dump_without_measured_relationships_is_red(
        self, summarizer: SummarizeSessionEvaluator, evidence_case: CaseInput
    ) -> None:
        raw = _EVIDENCE_NOTE.split("# Baseline")[0] + (
            "baseline rejected 840.0167 850.033 4690"
        )
        score, checks = summarizer.score(summarizer.parse(raw), evidence_case)
        assert checks["must_mention"] == 1.0
        assert checks["required_claims"] == 0.0
        assert score == 0.0


@pytest.mark.parametrize("edges", ["expiration_edges=0", "expiration_edges = 0"])
@pytest.mark.parametrize(
    "scope",
    [
        "The unsolicited second-drone idea is not a requirement.",
        "The unrequested second drone is excluded.",
    ],
)
def test_evidence_valid_alternative_grammar(summarizer, evidence_case, edges, scope):
    raw = _EVIDENCE_NOTE.replace("There was no expiration edge.", edges + ".").replace(
        "The second drone was not requested.", scope
    )
    score, checks = summarizer.score(summarizer.parse(raw), evidence_case)
    assert checks["required_claims"] == 1.0
    assert score == 100.0


@pytest.mark.parametrize(
    "old,new",
    [
        ("There was no expiration edge.", "expiration_edges=01."),
        ("There was no expiration edge.", "expiration_edges=1."),
        ("The second drone was not requested.", "The second drone was requested."),
        ("The accepted baseline remains single-drone.", "The baseline was rejected."),
        (
            "Ammo and guidance variants were rejected.",
            "Ammo and guidance variants were explored.",
        ),
        ("4690 of its 6000 capacity", "4690 of its 7000 capacity"),
        (
            "Zero qualifying witnesses were recorded.",
            "Some qualifying witnesses were recorded.",
        ),
    ],
)
def test_evidence_missing_facts_remain_red(summarizer, evidence_case, old, new):
    parsed = summarizer.parse(_EVIDENCE_NOTE.replace(old, new))
    score, checks = summarizer.score(parsed, evidence_case)
    assert checks["required_claims"] == 0.0
    assert parsed["missing_required_patterns"]
    assert score == 0.0


def test_driver_preserves_full_output_and_bounds_case(monkeypatch):
    import prompt_eval_run as driver

    calls = []
    raw = _EVIDENCE_NOTE + (" diagnostic detail" * 200)

    def fake_call(prompt, model, tier):
        calls.append(prompt)
        return raw

    monkeypatch.setattr(driver, "_call_ai", fake_call)
    results = driver.run_eval("summarize-session", None, "small", 1, "009", False, True)
    assert len(calls) == len(results) == 1
    assert results[0].case_id == "009-rejected-ammo-guidance"
    assert results[0].raw_output == raw
    assert len(results[0].raw_output) > 500
    assert results[0].failed_required_patterns == []


def test_driver_reports_missing_required_patterns(monkeypatch):
    import prompt_eval_run as driver

    monkeypatch.setattr(driver, "_call_ai", lambda *args: "---\ntype: project\n---\n")
    results = driver.run_eval(
        "summarize-session", None, "small", None, "009", False, True
    )
    assert len(results[0].failed_required_patterns) == 8
    assert results[0].score == 0.0
