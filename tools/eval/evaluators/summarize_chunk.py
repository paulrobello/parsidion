"""Evaluator for the ``summarize-chunk`` prompt (hierarchical summarization).

When a transcript exceeds ``max_cleaned_chars``, ``summarize_sessions.py``
chunks it and summarizes each piece via this prompt. The expected output is a
short prose summary (3-5 sentences, no markdown fence, no chatty preamble) that
the driver stitches together for a second-level summary pass.

Stdlib-only (no ``rich`` / ``pyyaml`` at import) so render/parse/score are
unit-testable in the numpy-free ``make test`` suite.
"""

from __future__ import annotations

import re
from typing import Any

from ._base import BaseEvaluator, CaseInput

# Rubric weights — must sum to 100.
WEIGHT_SENTENCE_COUNT = 40
WEIGHT_MUST_MENTION = 40
WEIGHT_NO_PREAMBLE_NO_FENCE = 20
assert WEIGHT_SENTENCE_COUNT + WEIGHT_MUST_MENTION + WEIGHT_NO_PREAMBLE_NO_FENCE == 100

# Split on one or more terminal punctuation followed by whitespace or end of
# string. The zero-width lookahead keeps decimal points ("3.14") and dotted
# identifiers ("fs.readFileSync") from being treated as sentence boundaries.
_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+(?=\s|$)")

# A chatty preamble before the actual summary — models often ignore the
# "just the summary" instruction and lead with "Here is ..." / "Summary:".
_PREAMBLE_RE = re.compile(
    r"^\s*(here(?:'s| is)|summary\s*:|sure[,\.]|certainly[,\.]|below is|of course[,\.])",
    re.IGNORECASE,
)


class SummarizeChunkEvaluator(BaseEvaluator):
    prompt_id = "summarize-chunk"

    def _load_inputs(self, stem: str) -> dict[str, str] | None:
        chunk_text = self._input_text(stem, "chunk_text")
        if chunk_text is None:
            return None
        return {"chunk_text": chunk_text}

    def render(self, case: CaseInput) -> str:
        import prompt_templates  # local: keeps the module import-light

        return prompt_templates.render(
            self.prompt_id,
            chunk_num="1",
            total_chunks="2",
            chunk_text=case.inputs["chunk_text"],
        )

    def parse(self, raw: str) -> dict[str, Any]:
        fragments = _SENTENCE_SPLIT_RE.split(raw)
        sentences = sum(1 for frag in fragments if frag.strip())
        return {
            "text": raw,
            "sentences": sentences,
            "has_fence": "```" in raw,
            "has_preamble": bool(_PREAMBLE_RE.match(raw)),
        }

    def score(
        self, parsed: dict[str, Any], case: CaseInput
    ) -> tuple[float, dict[str, float]]:
        expected = case.expected

        sentence_min = int(expected.get("sentence_min", 3))
        sentence_max = int(expected.get("sentence_max", 6))
        sentence_count = int(parsed["sentences"])
        sentence_count_in_range = (
            1.0 if sentence_min <= sentence_count <= sentence_max else 0.0
        )

        must_mention = [str(s) for s in (expected.get("must_mention") or [])]
        if must_mention:
            haystack = str(parsed["text"]).lower()
            hits = sum(1 for kw in must_mention if kw.lower() in haystack)
            must_mention_frac = hits / len(must_mention)
        else:
            must_mention_frac = 1.0

        no_preamble_no_fence = (
            1.0 if not parsed["has_fence"] and not parsed["has_preamble"] else 0.0
        )

        checks = {
            "sentence_count_in_range": sentence_count_in_range,
            "must_mention": must_mention_frac,
            "no_preamble_no_fence": no_preamble_no_fence,
        }
        score = (
            WEIGHT_SENTENCE_COUNT * sentence_count_in_range
            + WEIGHT_MUST_MENTION * must_mention_frac
            + WEIGHT_NO_PREAMBLE_NO_FENCE * no_preamble_no_fence
        )
        return round(score, 1), checks
