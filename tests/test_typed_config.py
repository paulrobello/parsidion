"""ARC-007: typed config access — schema defaults + the get_config adapter.

The schema dataclasses now carry the real code defaults (moved from the
inline defaults callers passed to ``get_config``), ``get_config`` resolves
through the schema, and the hot readers use ``load_typed_config()``
attribute access. These tests pin the behavioural contract:

- for every historical ``(section, key, inline_default)`` call shape, an
  empty config must return the inline default (schema default == the value
  callers used to inline);
- an explicit ``null`` still returns ``None``;
- a configured value still wins;
- keys with no schema default (``min_messages`` is context-dependent) still
  fall back to the caller's default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import vault_common
from core import vault_config

# (section, key, the inline default the pre-ARC-007 callers passed)
_HISTORICAL_DEFAULTS: list[tuple[str, str, object]] = [
    ("session_start_hook", "ai_cooldown_seconds", 30),
    ("session_start_hook", "ai_single_flight", True),
    ("session_start_hook", "ai_candidates_max", None),
    ("session_start_hook", "max_chars", 4000),
    ("session_start_hook", "ai_timeout", 25),
    ("session_start_hook", "recent_days", 3),
    ("session_start_hook", "debug", False),
    ("session_start_hook", "verbose_mode", False),
    ("session_start_hook", "use_embeddings", True),
    ("session_start_hook", "track_delta", True),
    ("session_start_hook", "graph_expand", True),
    ("session_start_hook", "graph_expand_max", 8),
    ("session_start_hook", "graph_rerank", True),
    ("session_stop_hook", "ai_timeout", 25),
    ("session_stop_hook", "auto_summarize", True),
    ("session_stop_hook", "auto_summarize_after", 1),
    ("session_stop_hook", "transcript_tail_lines", 200),
    ("session_stop_hook", "pi_transcript_tail_lines", 1000),
    ("session_stop_hook", "transcript_tail_bytes", 1_500_000),
    ("subagent_stop_hook", "enabled", True),
    ("subagent_stop_hook", "transcript_tail_bytes", 1_500_000),
    ("pre_compact_hook", "lines", 200),
    ("pre_compact_hook", "transcript_tail_bytes", 1_500_000),
    ("summarizer", "model", None),
    ("summarizer", "max_parallel", 5),
    ("summarizer", "transcript_tail_lines", 400),
    ("summarizer", "transcript_tail_bytes", 262_144),
    ("summarizer", "max_cleaned_chars", 12_000),
    ("summarizer", "cluster_model", None),
    ("summarizer", "dedup_threshold", 0.80),
    ("summarizer", "dead_letter_retention_days", 7),
    ("summarizer", "rebuild_graph", False),
    ("summarizer", "graph_include_daily", False),
    ("summarizer", "graph_incremental", True),
    ("summarizer", "ai_timeout", None),
    ("embeddings", "enabled", True),
    ("embeddings", "model", "BAAI/bge-small-en-v1.5"),
    ("embeddings", "min_score", 0.45),
    ("embeddings", "top_k", 10),
    ("embeddings", "decay_enabled", True),
    ("embeddings", "decay_half_life_days", 90.0),
    ("embeddings", "decay_min_factor", 0.5),
    ("embeddings", "service_enabled", False),
    ("embeddings", "service_idle_exit", 600),
    ("search", "backend", "auto"),
    ("search", "use_note_index", True),
    ("git", "auto_commit", True),
    ("defaults", "haiku_model", "claude-haiku-4-5-20251001"),
    ("event_log", "enabled", True),
    ("event_log", "max_lines", 10000),
    ("event_log", "path", None),
    ("adaptive_context", "enabled", False),
    ("vault", "username", ""),
    ("adapters", "load_external", False),
]


@pytest.fixture()
def empty_config(tmp_vault: Path) -> Path:
    """A vault with an empty config; caches cleared."""
    (tmp_vault / "config.yaml").write_text("", encoding="utf-8")
    vault_common.load_config.cache_clear()
    vault_config._clear_typed_config_cache()
    return tmp_vault


@pytest.fixture()
def configured_vault(tmp_vault: Path) -> Path:
    """A vault with a config.yaml setting max_chars + ai_model."""
    (tmp_vault / "config.yaml").write_text(
        "session_start_hook:\n  max_chars: 123\n  ai_model: sonnet\n",
        encoding="utf-8",
    )
    vault_common.load_config.cache_clear()
    vault_config._clear_typed_config_cache()
    return tmp_vault


@pytest.mark.parametrize(
    "section,key,inline_default",
    _HISTORICAL_DEFAULTS,
    ids=[f"{s}.{k}" for s, k, _ in _HISTORICAL_DEFAULTS],
)
def test_empty_config_returns_the_historical_inline_default(
    empty_config: Path, section: str, key: str, inline_default: object
) -> None:
    assert vault_common.get_config(section, key, inline_default) == inline_default


def test_explicit_null_still_returns_none(tmp_vault: Path) -> None:
    (tmp_vault / "config.yaml").write_text(
        "session_start_hook:\n  max_chars: null\n", encoding="utf-8"
    )
    vault_common.load_config.cache_clear()
    vault_config._clear_typed_config_cache()
    assert vault_common.get_config("session_start_hook", "max_chars", 4000) is None


def test_configured_value_wins_over_schema_default(tmp_vault: Path) -> None:
    (tmp_vault / "config.yaml").write_text(
        "session_start_hook:\n  max_chars: 999\n", encoding="utf-8"
    )
    vault_common.load_config.cache_clear()
    vault_config._clear_typed_config_cache()
    assert vault_common.get_config("session_start_hook", "max_chars", 4000) == 999


def test_context_dependent_key_uses_caller_default(empty_config: Path) -> None:
    """min_messages has NO schema default (pi: 1, others: 3) — the caller's
    contextual default must keep applying."""
    assert vault_common.get_config("subagent_stop_hook", "min_messages", 3) == 3
    assert vault_common.get_config("subagent_stop_hook", "min_messages", 1) == 1


def test_typed_access_returns_schema_defaults(empty_config: Path) -> None:
    from core.vault_config import load_typed_config

    cfg = load_typed_config()
    assert cfg.session_start_hook.max_chars == 4000
    assert cfg.session_start_hook.graph_expand is True
    assert cfg.summarizer.dedup_threshold == 0.80
    assert cfg.search.backend == "auto"
    # No-default fields stay None on the typed view.
    assert cfg.session_start_hook.ai_model is None
    assert cfg.subagent_stop_hook.min_messages is None


def test_typed_access_reflects_configured_values(configured_vault: Path) -> None:
    from core.vault_config import load_typed_config

    cfg = load_typed_config()
    assert cfg.session_start_hook.max_chars == 123
    assert cfg.session_start_hook.ai_model == "sonnet"
    assert cfg.session_start_hook.recent_days == 3  # untouched → schema default
