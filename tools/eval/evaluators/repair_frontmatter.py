"""Evaluator for the ``repair-frontmatter`` prompt (vault_doctor's note fixer).

The prompt takes an existing note plus a list of frontmatter issues and asks the
model to return ONLY the corrected note. This evaluator scores the correction
against the listed issues plus the structural invariants the prompt enforces:
exactly one well-formed frontmatter block, no echoed ``---BEGIN---`` /
``---END---`` markers, and the body otherwise preserved.

Stdlib-only (``prompt_templates`` / ``note_schema`` imported locally inside
methods) so render/parse/score unit-test under the numpy-free ``make test``.
"""

from __future__ import annotations

import re
from typing import Any

from ._base import BaseEvaluator, CaseInput

# Rubric weights — must sum to 100.
WEIGHT_ISSUES_FIXED = 35
WEIGHT_VALID_TYPE = 15
WEIGHT_SINGLE_FM = 20
WEIGHT_NO_MARKERS = 15
WEIGHT_BODY_PRESERVED = 15
assert (
    WEIGHT_ISSUES_FIXED
    + WEIGHT_VALID_TYPE
    + WEIGHT_SINGLE_FM
    + WEIGHT_NO_MARKERS
    + WEIGHT_BODY_PRESERVED
    == 100
)

_FM_TYPE_RE = re.compile(r"^type:\s*(\S+)", re.MULTILINE)
# A fence line is exactly "---" (the YAML frontmatter delimiter).
_FENCE_RE = re.compile(r"^---$", re.MULTILINE)
# A non-empty inline list on the `related:` field (e.g. related: ["[[x]]"]).
_RELATED_NONEMPTY_RE = re.compile(r"^related:\s*\[.+\]", re.MULTILINE)


class RepairFrontmatterEvaluator(BaseEvaluator):
    prompt_id = "repair-frontmatter"

    def _load_inputs(self, stem: str) -> dict[str, str] | None:
        content = self._input_text(stem, "content")
        if content is None:
            return None
        return {"content": content}

    def render(self, case: CaseInput) -> str:
        import note_schema
        import prompt_templates

        expected = case.expected
        issues = expected.get("issues", []) or []
        issue_lines = "\n".join(f"- {x}" for x in issues)
        valid_types = ", ".join(sorted(note_schema.VALID_NOTE_TYPES))
        return prompt_templates.render(
            self.prompt_id,
            rel=expected.get("rel", "Patterns/foo.md"),
            issue_lines=issue_lines,
            valid_types=valid_types,
            related_rule=(
                "- 'related' must be a non-empty list of quoted [[wikilinks]]"
            ),
            candidate_section="",
            content=case.inputs["content"],
        )

    def parse(self, raw: str) -> dict[str, Any]:
        fence_count = len(_FENCE_RE.findall(raw))
        type_match = _FM_TYPE_RE.search(raw)
        return {
            "raw": raw,
            # A well-formed frontmatter block has 2 "---" fence lines; their
            # count divided by 2 is the number of complete blocks.
            "fm_count": fence_count // 2,
            "type": type_match.group(1).strip() if type_match else "",
            "has_markers": ("---BEGIN---" in raw or "---END---" in raw),
        }

    def score(
        self, parsed: dict[str, Any], case: CaseInput
    ) -> tuple[float, dict[str, float]]:
        import note_schema

        expected = case.expected
        raw = str(parsed["raw"])

        # issues_fixed: fraction of declared issues the output actually resolved.
        issues = [str(x) for x in (expected.get("issues") or [])]
        resolved = 0
        for issue in issues:
            if issue == "type":
                if str(parsed["type"]) == str(expected.get("expected_type", "")):
                    resolved += 1
            elif issue == "related":
                if _RELATED_NONEMPTY_RE.search(raw):
                    resolved += 1
        issues_frac = resolved / len(issues) if issues else 1.0

        valid_type_frac = (
            1.0 if str(parsed["type"]) in note_schema.VALID_NOTE_TYPES else 0.0
        )
        single_fm_frac = 1.0 if parsed["fm_count"] == 1 else 0.0
        no_markers_frac = 0.0 if parsed["has_markers"] else 1.0

        must_mention = [str(s) for s in (expected.get("must_mention") or [])]
        hits = sum(1 for s in must_mention if s.lower() in raw.lower())
        body_frac = hits / len(must_mention) if must_mention else 1.0

        checks = {
            "issues_fixed": issues_frac,
            "valid_type": valid_type_frac,
            "single_wellformed_fm": single_fm_frac,
            "no_BEGIN_END_markers": no_markers_frac,
            "body_otherwise_preserved": body_frac,
        }
        score = (
            WEIGHT_ISSUES_FIXED * checks["issues_fixed"]
            + WEIGHT_VALID_TYPE * checks["valid_type"]
            + WEIGHT_SINGLE_FM * checks["single_wellformed_fm"]
            + WEIGHT_NO_MARKERS * checks["no_BEGIN_END_markers"]
            + WEIGHT_BODY_PRESERVED * checks["body_otherwise_preserved"]
        )
        return round(score, 1), checks
