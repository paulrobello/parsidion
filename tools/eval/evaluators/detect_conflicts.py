"""Evaluator for the ``detect-conflicts`` prompt (vault-conflicts).

The prompt inlines a block of semantically-similar notes and asks the model to
return ONLY a JSON array of contradiction pairs. This evaluator scores the
parsed array on four axes (valid JSON array, per-element schema, contradiction
recall against expected conflicting stems, and contradiction precision) — with
NO AI call. The render path goes through the real ``prompt_templates.render``,
whose strict bidirectional variable check is the drift gate.
"""

from __future__ import annotations

import json
from typing import Any

from ._base import BaseEvaluator, CaseInput

# Rubric weights — must sum to 100.
WEIGHT_VALID_JSON = 30
WEIGHT_ELEMENT_SCHEMA = 20
WEIGHT_RECALL = 30
WEIGHT_PRECISION = 20
assert (
    WEIGHT_VALID_JSON + WEIGHT_ELEMENT_SCHEMA + WEIGHT_RECALL + WEIGHT_PRECISION == 100
)


class DetectConflictsEvaluator(BaseEvaluator):
    prompt_id = "detect-conflicts"

    def _load_inputs(self, stem: str) -> dict[str, str] | None:
        note_block = self._input_text(stem, "note_block")
        if note_block is None:
            return None
        return {"note_block": note_block}

    def render(self, case: CaseInput) -> str:
        import prompt_templates  # local: keeps the module import-light

        note_count = str(
            case.expected.get(
                "note_count",
                case.inputs["note_block"].count("### ") or 1,
            )
        )
        return prompt_templates.render(
            self.prompt_id,
            note_count=note_count,
            note_block=case.inputs["note_block"],
        )

    def parse(self, raw: str) -> dict[str, Any]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {"array": [], "valid": False, "elements_valid": False}
        if not isinstance(data, list):
            return {"array": [], "valid": False, "elements_valid": False}
        elements_valid = all(
            isinstance(el, dict) and "a" in el and "b" in el for el in data
        )
        return {"array": data, "valid": True, "elements_valid": elements_valid}

    def score(
        self, parsed: dict[str, Any], case: CaseInput
    ) -> tuple[float, dict[str, float]]:
        expected = case.expected
        array = parsed["array"]
        expect_empty = bool(expected.get("expect_empty", False))
        expected_conflicts = [
            str(s) for s in (expected.get("expected_conflicts") or [])
        ]

        # valid_json_array (30): parsed as a real JSON array.
        valid_json = 1.0 if parsed["valid"] else 0.0

        # element_schema (20): fraction of elements that are dicts with a/b;
        # an empty array is schema-valid by convention.
        if not array:
            schema_frac = 1.0
        else:
            good = sum(
                1 for el in array if isinstance(el, dict) and "a" in el and "b" in el
            )
            schema_frac = good / len(array)

        # Stems the model flagged as one side of a contradiction.
        flagged: set[str] = set()
        for el in array:
            if isinstance(el, dict):
                for key in ("a", "b"):
                    val = el.get(key)
                    if isinstance(val, str) and val:
                        flagged.add(val)

        # contradiction_recall (30): did we catch the expected conflicts?
        if expect_empty:
            recall = 1.0 if not array else 0.0
        elif expected_conflicts:
            hit = sum(1 for stem in expected_conflicts if stem in flagged)
            recall = hit / len(expected_conflicts)
        else:
            recall = 1.0

        # contradiction_precision (20): did we avoid false positives?
        if expect_empty:
            precision = 1.0 if not array else 0.0
        elif expected_conflicts:
            expected_set = set(expected_conflicts)
            precision = 1.0 if all(stem in expected_set for stem in flagged) else 0.5
        else:
            precision = 1.0

        checks = {
            "valid_json_array": valid_json,
            "element_schema": schema_frac,
            "contradiction_recall": recall,
            "contradiction_precision": precision,
        }
        score = (
            WEIGHT_VALID_JSON * valid_json
            + WEIGHT_ELEMENT_SCHEMA * schema_frac
            + WEIGHT_RECALL * recall
            + WEIGHT_PRECISION * precision
        )
        return round(score, 1), checks
