"""Typed schema for the Parsidion vault config (ENH-014).

Single source of truth for the config schema: section names, key names, and
the allowed Python types for each key. Each top-level config section is a
dataclass whose field annotations reproduce the allowed-type tuples the
hand-maintained ``_CONFIG_SCHEMA`` literal in :mod:`vault_config` previously
encoded by hand. :func:`schema_dict` derives that structure from the
annotations so validation can consume one source of truth.

Stdlib-only (``dataclasses`` + ``typing``). Values pass through unchanged
-- no coercion, no canonical defaults invented. A field value of ``None``
means "absent from the file", mirroring the dict :func:`load_config` returns.
Validation (unknown keys, type mismatches) stays warn-only and lives in
:func:`vault_config.validate_config`; this module never raises on shape.

The dataclasses deliberately default every field to ``None`` even when the
field's allowed-type tuple does not include ``NoneType`` (e.g. ``backend: str``
defaulting to ``None``). The annotation says what *config.yaml* values are
valid; the default says what an unset field holds. ``validate_config`` already
treats an explicit ``None`` value as "skip the type check", so the two notions
are consistent.
"""

# pyright: reportAssignmentType=false
# Many fields encode a non-Optional allowed-type tuple (``backend: str``) but
# default to ``None`` ("absent from the file"). Suppressing the assignment-type
# diagnostic module-wide keeps the annotations faithful to the schema tuples
# rather than diluting every field with ``| None``.

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, get_type_hints

__all__ = [
    "AIConfig",
    "GrokCliConfig",
    "ClaudeCliConfig",
    "AIModelsConfig",
    "CodexCliConfig",
    "SessionStartHookConfig",
    "SessionStopHookConfig",
    "SubagentStopHookConfig",
    "PreCompactHookConfig",
    "SummarizerConfig",
    "EmbeddingsConfig",
    "ParsightConfig",
    "SearchConfig",
    "AnthropicEnvConfig",
    "GitConfig",
    "DefaultsConfig",
    "EventLogConfig",
    "AdaptiveContextConfig",
    "VaultSectionConfig",
    "AdaptersConfig",
    "VaultAppConfig",
    "schema_dict",
]


# ---------------------------------------------------------------------------
# Per-section dataclasses
#
# ARC-007: fields carry the REAL code defaults (moved from the inline
# defaults callers passed to get_config), so ``load_typed_config()`` readers
# get absent-key defaults for free and vault_config.get_config can use the
# schema as its default source. Fields still declared ``= None`` have no
# single code default (absent key, model names, or context-dependent values
# such as subagent_stop_hook.min_messages) — get_config falls back to the
# caller's default for those.
# ---------------------------------------------------------------------------


@dataclass
class AIConfig:
    """``ai`` section: which prompt-AI backend the hooks/summarizer use."""

    backend: str = None


@dataclass
class AIModelsConfig:
    """``ai_models`` section: per-backend model tiers.

    ``claude``/``codex``/``grok`` are free-form mappings (e.g.
    ``{small: ..., large: ...}``) so they stay ``dict`` here and in the
    schema tuple.
    """

    claude: dict = None
    codex: dict = None
    grok: dict = None


@dataclass
class ClaudeCliConfig:
    """``claude_cli`` section: ``claude -p`` invocation parameters.

    ``minimal_context`` (default true) replaces the system prompt and runs
    from a clean scratch cwd so ``claude -p`` does not ingest the project's
    CLAUDE.md chain, hooks, or plugin context — parsidion's selector /
    summarizer prompts are pure text transforms.
    """

    minimal_context: bool = None
    system_prompt: str = None
    timeout: int | float = None


@dataclass
class GrokCliConfig:
    """``grok_cli`` section: ``grok -p`` invocation parameters.

    ``minimal_context`` (default true) runs single-turn prompts from a clean
    scratch cwd with the system prompt overridden and built-in tools,
    subagents, and web search disabled — grok otherwise ingests CLAUDE.md /
    AGENTS.md rules and its full skill catalog from the working directory,
    which is dead weight (and a prompt-injection surface) for parsidion's
    pure text-transform prompts.
    """

    command: str = None
    timeout: int | float = None
    minimal_context: bool = None
    system_prompt: str = None


@dataclass
class CodexCliConfig:
    """``codex_cli`` section: ``codex exec`` invocation parameters."""

    command: str = None
    timeout: int | float = None
    sandbox: str | None = None
    ephemeral: bool = None
    skip_git_repo_check: bool = None
    suppress_notify: bool = None
    allow_danger_full_access: bool = None


@dataclass
class SessionStartHookConfig:
    """``session_start_hook`` section."""

    ai_model: str | None = None
    ai_cooldown_seconds: int | float = 30
    ai_single_flight: bool = True
    ai_candidates_max: int = None
    max_chars: int = 4000
    ai_timeout: int | float = 25
    recent_days: int = 3
    debug: bool = False
    verbose_mode: bool = False
    use_embeddings: bool = True
    track_delta: bool = True
    graph_expand: bool = True
    graph_expand_max: int = 8
    graph_rerank: bool = True


@dataclass
class SessionStopHookConfig:
    """``session_stop_hook`` section."""

    ai_model: str | None = None
    ai_timeout: int | float = 25
    auto_summarize: bool = True
    auto_summarize_after: int | None = 1
    transcript_tail_lines: int = 200
    pi_transcript_tail_lines: int = 1000
    transcript_tail_bytes: int = 1_500_000


@dataclass
class SubagentStopHookConfig:
    """``subagent_stop_hook`` section."""

    enabled: bool = True
    min_messages: int = None  # context-dependent (pi: 1, others: 3)
    excluded_agents: str = None
    transcript_tail_bytes: int = 1_500_000


@dataclass
class PreCompactHookConfig:
    """``pre_compact_hook`` section."""

    lines: int = 200
    transcript_tail_bytes: int = 1_500_000


@dataclass
class SummarizerConfig:
    """``summarizer`` section."""

    model: str | None = None
    max_parallel: int = 5
    transcript_tail_lines: int = 400
    transcript_tail_bytes: int = 262_144
    max_cleaned_chars: int = 12_000
    persist: bool = None  # legacy no-op
    cluster_model: str | None = None
    dedup_threshold: float | int = 0.80
    dead_letter_retention_days: int = 7
    rebuild_graph: bool = False
    graph_include_daily: bool = False
    graph_incremental: bool = True
    ai_timeout: int | float | None = None


@dataclass
class EmbeddingsConfig:
    """``embeddings`` section."""

    enabled: bool = True
    model: str = "BAAI/bge-small-en-v1.5"
    min_score: float | int = 0.45
    top_k: int = 10
    decay_enabled: bool = True
    decay_half_life_days: float | int = 90.0
    decay_min_factor: float | int = 0.5
    service_enabled: bool = False
    service_idle_exit: int = 600


@dataclass
class ParsightConfig:
    """``parsight`` section."""

    enabled: bool = None
    binary: str = None
    timeout_s: int | float = None


@dataclass
class SearchConfig:
    """``search`` section."""

    backend: str = "auto"
    use_note_index: bool = True


@dataclass
class AnthropicEnvConfig:
    """``anthropic_env`` section: env vars forwarded to ``claude -p``."""

    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_AUTH_TOKEN: str | None = None
    ANTHROPIC_BASE_URL: str | None = None
    ANTHROPIC_CUSTOM_HEADERS: str | None = None
    ANTHROPIC_DEFAULT_HAIKU_MODEL: str | None = None
    ANTHROPIC_DEFAULT_SONNET_MODEL: str | None = None
    ANTHROPIC_DEFAULT_OPUS_MODEL: str | None = None
    API_TIMEOUT_MS: int | str | None = None
    HTTPS_PROXY: str | None = None
    HTTP_PROXY: str | None = None


@dataclass
class GitConfig:
    """``git`` section."""

    auto_commit: bool = True


@dataclass
class DefaultsConfig:
    """``defaults`` section: legacy model defaults."""

    haiku_model: str = "claude-haiku-4-5-20251001"


@dataclass
class EventLogConfig:
    """``event_log`` section."""

    enabled: bool = True
    max_lines: int = 10_000
    path: str | None = None


@dataclass
class AdaptiveContextConfig:
    """``adaptive_context`` section."""

    enabled: bool = False
    # ENH-016: half-life in days for the usefulness-score decay applied by
    # session_start's adaptive rerank; <= 0 disables decay.
    decay_days: int | float = 30


@dataclass
class VaultSectionConfig:
    """``vault`` section (named to avoid clashing with the ``vault`` parameter)."""

    username: str = ""


@dataclass
class AdaptersConfig:
    """``adapters`` section."""

    load_external: bool = False


# ---------------------------------------------------------------------------
# Top-level aggregate
# ---------------------------------------------------------------------------


@dataclass
class VaultAppConfig:
    """Typed view of the parsed vault config.

    Every section is always present: an absent (or non-mapping) section in
    the file yields an empty instance of its dataclass (all fields ``None``),
    so consumers can chain ``cfg.session_start_hook.ai_model`` without
    None-guarding the section itself. Whether a section was *explicitly* set
    in the file is not distinguishable from this view (check the dict from
    :func:`load_config` if you need that); individual field values are
    ``None`` for keys absent from the file. Construct via :meth:`from_dict`
    or :func:`vault_config.load_typed_config`; the dataclasses carry no
    validation -- that remains :func:`vault_config.validate_config`'s job.
    """

    # ``default_factory=lambda: X()`` (rather than ``= X``) sidesteps a pyright
    # overload-resolution hiccup that arises under ``from __future__ import
    # annotations`` when the factory is a dataclass whose own fields use the
    # ``attr: str = None`` pattern the module-level reportAssignmentType
    # suppression covers.
    ai: AIConfig = field(default_factory=lambda: AIConfig())
    ai_models: AIModelsConfig = field(default_factory=lambda: AIModelsConfig())
    claude_cli: ClaudeCliConfig = field(default_factory=lambda: ClaudeCliConfig())
    codex_cli: CodexCliConfig = field(default_factory=lambda: CodexCliConfig())
    grok_cli: GrokCliConfig = field(default_factory=lambda: GrokCliConfig())
    session_start_hook: SessionStartHookConfig = field(
        default_factory=lambda: SessionStartHookConfig()
    )
    session_stop_hook: SessionStopHookConfig = field(
        default_factory=lambda: SessionStopHookConfig()
    )
    subagent_stop_hook: SubagentStopHookConfig = field(
        default_factory=lambda: SubagentStopHookConfig()
    )
    pre_compact_hook: PreCompactHookConfig = field(
        default_factory=lambda: PreCompactHookConfig()
    )
    summarizer: SummarizerConfig = field(default_factory=lambda: SummarizerConfig())
    embeddings: EmbeddingsConfig = field(default_factory=lambda: EmbeddingsConfig())
    parsight: ParsightConfig = field(default_factory=lambda: ParsightConfig())
    search: SearchConfig = field(default_factory=lambda: SearchConfig())
    anthropic_env: AnthropicEnvConfig = field(
        default_factory=lambda: AnthropicEnvConfig()
    )
    git: GitConfig = field(default_factory=lambda: GitConfig())
    defaults: DefaultsConfig = field(default_factory=lambda: DefaultsConfig())
    event_log: EventLogConfig = field(default_factory=lambda: EventLogConfig())
    adaptive_context: AdaptiveContextConfig = field(
        default_factory=lambda: AdaptiveContextConfig()
    )
    vault: VaultSectionConfig = field(default_factory=lambda: VaultSectionConfig())
    adapters: AdaptersConfig = field(default_factory=lambda: AdaptersConfig())

    @classmethod
    def from_dict(cls, parsed: dict[str, Any]) -> VaultAppConfig:
        """Map a parsed config dict onto the section dataclasses.

        Values pass through unchanged -- no coercion. Absent keys default to
        ``None`` on the section dataclass. Unknown sections/keys and non-mapping
        section values are skipped silently; :func:`vault_config.validate_config`
        is responsible for warning about them.

        Args:
            parsed: The dict returned by :func:`vault_config.load_config`.

        Returns:
            A populated :class:`VaultAppConfig`. Returns an instance whose
            sections are all empty (every field ``None``) when *parsed* is not
            a dict.
        """
        if not isinstance(parsed, dict):
            return cls()

        hints = get_type_hints(cls)
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            section_cls = _unwrap_optional(hints[f.name])
            # ``is_dataclass`` alone narrows ``Any`` to the instance protocol
            # (not callable); gating on ``isinstance(..., type)`` first lets
            # pyright see a class we can instantiate.
            if not (isinstance(section_cls, type) and is_dataclass(section_cls)):
                continue
            raw_section = parsed.get(f.name)
            if not isinstance(raw_section, dict):
                continue
            sub_kwargs: dict[str, Any] = {}
            for sub_f in fields(section_cls):
                if sub_f.name in raw_section:
                    sub_kwargs[sub_f.name] = raw_section[sub_f.name]
            kwargs[f.name] = section_cls(**sub_kwargs)
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# Schema derivation
# ---------------------------------------------------------------------------


def schema_dict() -> dict[str, dict[str, tuple[type, ...]]]:
    """Derive the ``section -> key -> allowed-types`` schema from annotations.

    The structure mirrors the hand-maintained ``_CONFIG_SCHEMA`` literal:
    each section dataclass field's annotation becomes an allowed-types tuple.
    A bare type (e.g. ``str``) yields ``(str,)``; a union (e.g. ``int | float``)
    yields ``(int, float)`` preserving annotation order; ``None`` in a union
    maps to ``type(None)`` (``NoneType``), matching the convention
    ``validate_config`` already uses to filter None out of warning messages.
    """
    result: dict[str, dict[str, tuple[type, ...]]] = {}
    top_hints = get_type_hints(VaultAppConfig)
    for f in fields(VaultAppConfig):
        section_cls = _unwrap_optional(top_hints[f.name])
        if not (isinstance(section_cls, type) and is_dataclass(section_cls)):
            continue
        section_hints = get_type_hints(section_cls)
        section_schema: dict[str, tuple[type, ...]] = {}
        for sub_f in fields(section_cls):
            section_schema[sub_f.name] = _annotation_to_allowed_types(
                section_hints[sub_f.name]
            )
        result[f.name] = section_schema
    return result


def _unwrap_optional(tp: Any) -> Any:
    """Return the single non-``None`` arg of an Optional union, else *tp*.

    ``ai: AIConfig | None`` resolves to a union whose ``__args__`` are
    ``(AIConfig, NoneType)``; this returns ``AIConfig`` so callers can inspect
    the section dataclass directly. Unions with multiple non-None args (none
    here today, but defensively) and non-union types are returned unchanged.
    """
    args = getattr(tp, "__args__", None)
    if args is None:
        return tp
    non_none = [a for a in args if a is not type(None)]
    if len(non_none) == 1:
        return non_none[0]
    return tp


def _annotation_to_allowed_types(ann: Any) -> tuple[type, ...]:
    """Turn a resolved annotation into the allowed-types tuple.

    A bare type has no ``__args__`` and yields a one-tuple. A union yields
    its ``__args__`` as a tuple, preserving source order (e.g.
    ``float | int`` -> ``(float, int)``). ``None`` in a PEP 604 union arrives
    as ``NoneType`` in ``__args__``, which IS ``type(None)`` -- the form
    ``_CONFIG_SCHEMA`` used and ``validate_config``'s message filter expects.
    """
    args = getattr(ann, "__args__", None)
    if args is None:
        return (ann,)
    return tuple(args)
