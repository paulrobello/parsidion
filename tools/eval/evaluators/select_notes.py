"""Evaluator for the ``select-notes`` prompt (session-start AI note selector).

The ``select-notes`` prompt inlines candidate vault notes as untrusted data and
asks the model to pick the relevant ones for the upcoming session. This evaluator
scores the model's formatted output against the golden case's ``must_select`` /
``must_not_select`` lists, the declared ``output_limit``, and the required
``### Title (path/note.md)`` block format — with no AI call.
"""

from __future__ import annotations

import re
from typing import Any

from ._base import BaseEvaluator, CaseInput

# Rubric weights — must sum to 100.
WEIGHT_MUST_SELECT = 35
WEIGHT_UNDER_LIMIT = 20
WEIGHT_FORMAT = 25
WEIGHT_MUST_NOT_SELECT = 20
assert (
    WEIGHT_MUST_SELECT + WEIGHT_UNDER_LIMIT + WEIGHT_FORMAT + WEIGHT_MUST_NOT_SELECT
    == 100
)

# A header line ``### Note Title (folder/note.md)``. Capture group is the text
# after the ``### `` marker (the title-plus-path payload).
_HEADER_RE = re.compile(r"^###\s+(.*)$")
# A well-formed selected-title payload ends with a markdown path in parens,
# e.g. ``Acme CLI argument parsing (Patterns/acme-cli-args.md)``. Mirrors the
# ``### .* \(.*\.md\)`` shape declared in the prompt body, with the ``### ``
# prefix already stripped by :meth:`SelectNotesEvaluator.parse`.
_TITLE_PATH_RE = re.compile(r".* \(.*\.md\)\s*$")


class SelectNotesEvaluator(BaseEvaluator):
    prompt_id = "select-notes"

    def _load_inputs(self, stem: str) -> dict[str, str] | None:
        candidates_text = self._input_text(stem, "candidates_text")
        if candidates_text is None:
            return None
        return {"candidates_text": candidates_text}

    def render(self, case: CaseInput) -> str:
        import prompt_templates

        expected = case.expected
        project_name = str(expected.get("project_name", "acme-cli"))
        cwd = str(expected.get("cwd", "/workspace/acme-cli"))
        output_limit = str(expected.get("output_limit", 2000))
        return prompt_templates.render(
            self.prompt_id,
            project_name=project_name,
            cwd=cwd,
            output_limit=output_limit,
            candidates_text=case.inputs["candidates_text"],
        )

    def parse(self, raw: str) -> dict[str, Any]:
        selected_titles: list[str] = []
        has_blocks = False
        for line in raw.splitlines():
            if line.startswith("### "):
                has_blocks = True
                match = _HEADER_RE.match(line)
                if match:
                    selected_titles.append(match.group(1).strip())
        return {
            "raw": raw,
            "selected_titles": selected_titles,
            "char_len": len(raw),
            "has_blocks": has_blocks,
        }

    def score(
        self, parsed: dict[str, Any], case: CaseInput
    ) -> tuple[float, dict[str, float]]:
        expected = case.expected
        selected = list(parsed["selected_titles"])
        selected_lower = [s.lower() for s in selected]

        must_select = [str(s) for s in (expected.get("must_select") or [])]
        if must_select:
            present = sum(
                1
                for needle in must_select
                if any(needle.lower() in sel for sel in selected_lower)
            )
            must_select_frac = present / len(must_select)
        else:
            must_select_frac = 1.0

        output_limit = int(expected.get("output_limit", 2000))
        under_limit_frac = 1.0 if parsed["char_len"] <= output_limit else 0.0

        if parsed["has_blocks"] and selected:
            matched = sum(1 for title in selected if _TITLE_PATH_RE.match(title))
            format_frac = matched / len(selected)
        else:
            format_frac = 0.0

        must_not_select = [str(s) for s in (expected.get("must_not_select") or [])]
        if must_not_select:
            leaked = any(
                needle.lower() in sel
                for needle in must_not_select
                for sel in selected_lower
            )
            must_not_select_frac = 0.0 if leaked else 1.0
        else:
            must_not_select_frac = 1.0

        checks = {
            "must_select_present": must_select_frac,
            "under_output_limit": under_limit_frac,
            "correct_format": format_frac,
            "must_not_select_absent": must_not_select_frac,
        }
        score = (
            WEIGHT_MUST_SELECT * checks["must_select_present"]
            + WEIGHT_UNDER_LIMIT * checks["under_output_limit"]
            + WEIGHT_FORMAT * checks["correct_format"]
            + WEIGHT_MUST_NOT_SELECT * checks["must_not_select_absent"]
        )
        return round(score, 1), checks
