"""Evaluator for the ``summarize-session`` prompt (the note producer).

Migrated verbatim from ``prompt_eval_run.py``'s former
``_build_prompt_for_case``/``_parse_output``/``_score_case`` so behaviour and
weights are unchanged — this is a refactor into the per-prompt shape, not a
behaviour change. The eight existing golden cases move into
``golden/summarize-session/``.
"""

from __future__ import annotations

import datetime
import re
from typing import Any

from ._base import BaseEvaluator, CaseInput

# Rubric weights — must sum to 100 (matches the original driver).
WEIGHT_WRITE_GATE = 25
WEIGHT_TYPE = 20
WEIGHT_FRONTMATTER = 20
WEIGHT_TAGS = 20
WEIGHT_MUST_MENTION = 15
assert (
    WEIGHT_WRITE_GATE
    + WEIGHT_TYPE
    + WEIGHT_FRONTMATTER
    + WEIGHT_TAGS
    + WEIGHT_MUST_MENTION
    == 100
)

_SKIP_JSON_RE = re.compile(r'\{"decision"\s*:\s*"skip"', re.IGNORECASE)
_FM_TYPE_RE = re.compile(r"^type:\s*(\S+)", re.MULTILINE)
_FM_TAGS_RE = re.compile(r"^tags:\s*\[(.*?)\]", re.MULTILINE)


class SummarizeSessionEvaluator(BaseEvaluator):
    prompt_id = "summarize-session"

    def _load_inputs(self, stem: str) -> dict[str, str] | None:
        transcript = self._input_text(stem, "transcript")
        if transcript is None:
            return None
        return {"cleaned_transcript": transcript}

    def render(self, case: CaseInput) -> str:
        import note_schema
        import prompt_templates

        return prompt_templates.render(
            self.prompt_id,
            project="eval-project",
            cats_str="general",
            today=datetime.date.today().isoformat(),
            dedup_block="",
            cleaned_transcript=case.inputs["cleaned_transcript"],
            tags_instruction=(
                "  tags (2-4 relevant tags;\n"
                "  NEVER use underscores — always kebab-case (hyphens);\n"
                "  prefer short singular tags: 'voxel' not 'voxel-engine', "
                "'hook' not 'hooks')"
            ),
            valid_types=", ".join(sorted(note_schema.VALID_NOTE_TYPES)),
            session_id="eval-case",
        )

    def parse(self, raw: str) -> dict[str, Any]:
        decision = "save"
        if _SKIP_JSON_RE.search(raw):
            decision = "skip"
        type_match = _FM_TYPE_RE.search(raw)
        tags_match = _FM_TAGS_RE.search(raw)
        tags: list[str] = []
        if tags_match:
            tags = [
                t.strip().strip("\"'")
                for t in tags_match.group(1).split(",")
                if t.strip()
            ]
        return {
            "decision": decision,
            "type": type_match.group(1).strip() if type_match else "",
            "tags": tags,
            "raw": raw,
        }

    def score(
        self, parsed: dict[str, Any], case: CaseInput
    ) -> tuple[float, dict[str, float]]:
        import note_schema

        expected = case.expected
        should_produce = bool(expected.get("should_produce_note", True))

        write_gate_correct = (parsed["decision"] != "skip") == should_produce
        type_correct = not should_produce or str(parsed["type"]) == str(
            expected.get("expected_type", "")
        )

        frontmatter_valid = True
        if should_produce and parsed["decision"] != "skip":
            frontmatter_valid = parsed["type"] in note_schema.VALID_NOTE_TYPES

        expected_include = set(
            str(t) for t in expected.get("expected_tags_include") or []
        )
        expected_exclude = set(
            str(t) for t in expected.get("expected_tags_exclude") or []
        )
        actual_tags = set(parsed["tags"])
        tag_recall = (
            len(actual_tags & expected_include) / len(expected_include)
            if expected_include
            else 1.0
        )
        forbidden_hits = actual_tags & expected_exclude
        tag_precision = (
            1.0
            if not forbidden_hits
            else (
                len(actual_tags - expected_exclude) / len(actual_tags)
                if actual_tags
                else 0.0
            )
        )

        must_mention = [str(s) for s in (expected.get("must_mention") or [])]
        hits = sum(1 for s in must_mention if s.lower() in parsed["raw"].lower())
        must_mention_total = len(must_mention)
        must_mention_frac = hits / must_mention_total if must_mention_total else 1.0

        # When the case expects no note and the write-gate agreed, the other
        # checks are vacuously satisfied.
        if not should_produce and parsed["decision"] == "skip":
            type_correct = True
            frontmatter_valid = True
            tag_recall = 1.0
            tag_precision = 1.0
            must_mention_frac = 1.0

        tags_frac = 0.5 * tag_precision + 0.5 * tag_recall
        checks = {
            "write_gate": 1.0 if write_gate_correct else 0.0,
            "type": 1.0 if type_correct else 0.0,
            "frontmatter": 1.0 if frontmatter_valid else 0.0,
            "tags": tags_frac,
            "must_mention": must_mention_frac,
        }
        score = (
            WEIGHT_WRITE_GATE * checks["write_gate"]
            + WEIGHT_TYPE * checks["type"]
            + WEIGHT_FRONTMATTER * checks["frontmatter"]
            + WEIGHT_TAGS * checks["tags"]
            + WEIGHT_MUST_MENTION * checks["must_mention"]
        )
        return round(score, 1), checks
