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
from .summarize_session import SummarizeSessionEvaluator

#: prompt id -> evaluator instance. Phase B (board item #3) registers the five
#: remaining prompts here as their modules land.
EVALUATORS: dict[str, PromptEvaluator] = {
    "summarize-session": SummarizeSessionEvaluator(),
}

__all__ = [
    "EVALUATORS",
    "BaseEvaluator",
    "CaseInput",
    "ScoredCase",
    "PromptEvaluator",
    "parse_flat_yaml",
]
