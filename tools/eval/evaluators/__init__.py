"""Registry of per-prompt evaluators for the prompt-eval harness.

Each evaluator (one module per prompt) implements the :class:`PromptEvaluator`
contract. The CLI driver imports :data:`EVALUATORS` and dispatches on the
``--prompt`` id. Add a prompt by dropping a module here and registering it.
"""

from __future__ import annotations

from ._base import (
    BaseEvaluator,
    CaseInput,
    PromptEvaluator,
    ScoredCase,
    parse_flat_yaml,
)
from .detect_conflicts import DetectConflictsEvaluator
from .merge_notes import MergeNotesEvaluator
from .repair_frontmatter import RepairFrontmatterEvaluator
from .select_notes import SelectNotesEvaluator
from .summarize_chunk import SummarizeChunkEvaluator
from .summarize_session import SummarizeSessionEvaluator

#: prompt id -> evaluator instance. One entry per externalized prompt; the CLI
#: driver dispatches ``--prompt <id>`` over this map.
EVALUATORS: dict[str, PromptEvaluator] = {
    "summarize-session": SummarizeSessionEvaluator(),
    "summarize-chunk": SummarizeChunkEvaluator(),
    "repair-frontmatter": RepairFrontmatterEvaluator(),
    "merge-notes": MergeNotesEvaluator(),
    "detect-conflicts": DetectConflictsEvaluator(),
    "select-notes": SelectNotesEvaluator(),
}

__all__ = [
    "EVALUATORS",
    "BaseEvaluator",
    "CaseInput",
    "ScoredCase",
    "PromptEvaluator",
    "parse_flat_yaml",
]
