"""Evaluator for the ``merge-notes`` prompt (vault-merge note combiner).

The model receives two vault note bodies as untrusted data and must emit a
single merged note body (no frontmatter, no fences). Golden cases ship pairs of
``<stem>.body_a.md`` / ``<stem>.body_b.md`` fixtures plus an
``<stem>.expected.yaml`` describing the facts each side contributes and any
phrase that must not be duplicated.
"""

from __future__ import annotations

import re
from typing import Any

from ._base import BaseEvaluator, CaseInput

# Rubric weights — must sum to 100.
WEIGHT_FACTS_A = 30
WEIGHT_FACTS_B = 30
WEIGHT_NO_DUPLICATION = 20
WEIGHT_FORMAT = 20
assert WEIGHT_FACTS_A + WEIGHT_FACTS_B + WEIGHT_NO_DUPLICATION + WEIGHT_FORMAT == 100

_HEADING_RE = re.compile(r"^##\s", re.MULTILINE)


class MergeNotesEvaluator(BaseEvaluator):
    prompt_id = "merge-notes"

    def _load_inputs(self, stem: str) -> dict[str, str] | None:
        body_a = self._input_text(stem, "body_a")
        body_b = self._input_text(stem, "body_b")
        if body_a is None or body_b is None:
            return None
        return {"body_a": body_a, "body_b": body_b}

    def render(self, case: CaseInput) -> str:
        import prompt_templates

        title = str(case.expected.get("title", "the topic"))
        return prompt_templates.render(
            self.prompt_id,
            title=title,
            body_a=case.inputs["body_a"],
            body_b=case.inputs["body_b"],
        )

    def parse(self, raw: str) -> dict[str, Any]:
        return {
            "raw": raw,
            "has_heading": bool(_HEADING_RE.search(raw)),
            "has_fence": "```" in raw,
            "has_frontmatter": raw.lstrip().startswith("---"),
        }

    def score(
        self, parsed: dict[str, Any], case: CaseInput
    ) -> tuple[float, dict[str, float]]:
        expected = case.expected
        raw_lower = parsed["raw"].lower()

        must_a = [str(s) for s in (expected.get("must_mention_a") or [])]
        if must_a:
            hits_a = sum(1 for s in must_a if s.lower() in raw_lower)
            frac_a = hits_a / len(must_a)
        else:
            frac_a = 1.0

        must_b = [str(s) for s in (expected.get("must_mention_b") or [])]
        if must_b:
            hits_b = sum(1 for s in must_b if s.lower() in raw_lower)
            frac_b = hits_b / len(must_b)
        else:
            frac_b = 1.0

        dup_phrases = [str(s) for s in (expected.get("must_not_duplicate") or [])]
        if dup_phrases:
            ok = sum(1 for p in dup_phrases if raw_lower.count(p.lower()) <= 1)
            frac_dup = ok / len(dup_phrases)
        else:
            frac_dup = 1.0

        format_ok = (
            parsed["has_heading"]
            and not parsed["has_fence"]
            and not parsed["has_frontmatter"]
        )
        frac_format = 1.0 if format_ok else 0.0

        checks = {
            "facts_a_present": frac_a,
            "facts_b_present": frac_b,
            "no_duplication": frac_dup,
            "has_headings_no_fence_no_fm": frac_format,
        }
        score = (
            WEIGHT_FACTS_A * frac_a
            + WEIGHT_FACTS_B * frac_b
            + WEIGHT_NO_DUPLICATION * frac_dup
            + WEIGHT_FORMAT * frac_format
        )
        return round(score, 1), checks
