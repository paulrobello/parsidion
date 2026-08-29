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

# Rubric weights — must sum to 100. frontmatter_valid and related_links_min
# are read from the fixture: frontmatter validity is derived from the parsed
# type via note_schema and compared to the expected flag; related links are
# counted in the generated frontmatter and compared to the expected minimum.
WEIGHT_WRITE_GATE = 25
WEIGHT_TYPE = 15
WEIGHT_FRONTMATTER = 15
WEIGHT_TAGS = 20
WEIGHT_MUST_MENTION = 15
WEIGHT_RELATED_LINKS = 10
assert (
    WEIGHT_WRITE_GATE
    + WEIGHT_TYPE
    + WEIGHT_FRONTMATTER
    + WEIGHT_TAGS
    + WEIGHT_MUST_MENTION
    + WEIGHT_RELATED_LINKS
    == 100
)

_SKIP_JSON_RE = re.compile(r'\{"decision"\s*:\s*"skip"', re.IGNORECASE)
_FM_TYPE_RE = re.compile(r"^type:\s*(\S+)", re.MULTILINE)


def _fm_list_items(raw: str, field: str) -> list[str]:
    """Extract a frontmatter list field in inline (``[a, b]``) or block form.

    The summarizer model emits either YAML shape for ``tags`` / ``related``;
    both must parse or the rubric silently scores real hits as misses.
    """
    m = re.search(
        rf"^{field}:[ \t]*(.*?)(?=\n[a-zA-Z_][\w-]*:|\n---|\Z)",
        raw,
        re.MULTILINE | re.DOTALL,
    )
    if not m:
        return []
    blob = m.group(1).strip()
    if blob.startswith("["):
        inner = blob.strip("[]")
        return [t.strip().strip("\"'") for t in inner.split(",") if t.strip()]
    return [
        line.strip()[2:].strip().strip("\"'")
        for line in blob.splitlines()
        if line.strip().startswith("- ")
    ]


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
        tags = _fm_list_items(raw, "tags")
        related_count = sum(
            1 for item in _fm_list_items(raw, "related") if "[[" in item
        )
        return {
            "decision": decision,
            "type": type_match.group(1).strip() if type_match else "",
            "tags": tags,
            "related_count": related_count,
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
        # The fixture's frontmatter_valid is the expected verdict; the check
        # passes when derivation agrees with it.
        frontmatter_ok = frontmatter_valid == bool(
            expected.get("frontmatter_valid", True)
        )

        related_min = int(expected.get("related_links_min", 0) or 0)
        related_ok = int(parsed.get("related_count", 0)) >= related_min

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
            frontmatter_ok = True
            related_ok = True
            tag_recall = 1.0
            tag_precision = 1.0
            must_mention_frac = 1.0

        tags_frac = 0.5 * tag_precision + 0.5 * tag_recall
        checks = {
            "write_gate": 1.0 if write_gate_correct else 0.0,
            "type": 1.0 if type_correct else 0.0,
            "frontmatter": 1.0 if frontmatter_ok else 0.0,
            "tags": tags_frac,
            "must_mention": must_mention_frac,
            "related_links": 1.0 if related_ok else 0.0,
        }
        score = (
            WEIGHT_WRITE_GATE * checks["write_gate"]
            + WEIGHT_TYPE * checks["type"]
            + WEIGHT_FRONTMATTER * checks["frontmatter"]
            + WEIGHT_TAGS * checks["tags"]
            + WEIGHT_MUST_MENTION * checks["must_mention"]
            + WEIGHT_RELATED_LINKS * checks["related_links"]
        )
        return round(score, 1), checks
