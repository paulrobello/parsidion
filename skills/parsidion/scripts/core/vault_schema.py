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
    "AIModelsConfig",
    "CodexCliConfig",
    "SessionStartHookConfig",
    "SessionStopHookConfig",
    "SubagentStopHookConfig",
    "PreCompactHookConfig",
    "SummarizerConfig",
    "EmbeddingsConfig",
    "ParMemConfig",
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
# ---------------------------------------------------------------------------


@dataclass
class AIConfig:
    """``ai`` section: which prompt-AI backend the hooks/summarizer use."""

    backend: str = None


@dataclass
class AIModelsConfig:
    """``ai_models`` section: per-backend model tiers.

    ``claude``/``codex`` are free-form mappings (e.g. ``{small: ..., large: ...}``)
    so they stay ``dict`` here and in the schema tuple.
    """

    claude: dict = None
    codex: dict = None


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
    ai_cooldown_seconds: int | float = None
    ai_single_flight: bool = None
    max_chars: int = None
    ai_timeout: int | float = None
    recent_days: int = None
    debug: bool = None
    verbose_mode: bool = None
    use_embeddings: bool = None
    track_delta: bool = None
    graph_expand: bool = None
    graph_expand_max: int = None
    graph_rerank: bool = None


@dataclass
class SessionStopHookConfig:
    """``session_stop_hook`` section."""

    ai_model: str | None = None
    ai_timeout: int | float = None
    auto_summarize: bool = None
    auto_summarize_after: int | None = None
    transcript_tail_lines: int = None
    pi_transcript_tail_lines: int = None
    transcript_tail_bytes: int = None


@dataclass
class SubagentStopHookConfig:
    """``subagent_stop_hook`` section."""

    enabled: bool = None
    min_messages: int = None
    excluded_agents: str = None
    transcript_tail_bytes: int = None


@dataclass
class PreCompactHookConfig:
    """``pre_compact_hook`` section."""

    lines: int = None
    transcript_tail_bytes: int = None


@dataclass
class SummarizerConfig:
    """``summarizer`` section."""

    model: str | None = None
    max_parallel: int = None
    transcript_tail_lines: int = None
    transcript_tail_bytes: int = None
    max_cleaned_chars: int = None
    persist: bool = None
    cluster_model: str | None = None
    dedup_threshold: float | int = None
    dead_letter_retention_days: int = None
    rebuild_graph: bool = None
    graph_include_daily: bool = None
    graph_incremental: bool = None
    ai_timeout: int | float | None = None


@dataclass
class EmbeddingsConfig:
    """``embeddings`` section."""

    enabled: bool = None
    model: str = None
    min_score: float | int = None
    top_k: int = None
    decay_enabled: bool = None
    decay_half_life_days: float | int = None
    decay_min_factor: float | int = None
    service_enabled: bool = None
    service_idle_exit: int = None


@dataclass
class ParMemConfig:
    """``par_mem`` section."""

    enabled: bool = None
    binary: str = None
    timeout_s: int | float = None


@dataclass
class SearchConfig:
    """``search`` section."""

    backend: str = None
    use_note_index: bool = None


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

    auto_commit: bool = None


@dataclass
class DefaultsConfig:
    """``defaults`` section: legacy model defaults."""

    haiku_model: str = None


@dataclass
class EventLogConfig:
    """``event_log`` section."""

    enabled: bool = None
    max_lines: int = None
    path: str | None = None


@dataclass
class AdaptiveContextConfig:
    """``adaptive_context`` section."""

    enabled: bool = None
    decay_days: int | float = None


@dataclass
class VaultSectionConfig:
    """``vault`` section (named to avoid clashing with the ``vault`` parameter)."""

    username: str = None


@dataclass
class AdaptersConfig:
    """``adapters`` section."""

    load_external: bool = None


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
    # suppression covers. The lambda resolves to ``Callable[[], X]`` directly.
    ai: AIConfig = field(default_factory=lambda: AIConfig())
    ai_models: AIModelsConfig = field(default_factory=lambda: AIModelsConfig())
    codex_cli: CodexCliConfig = field(default_factory=lambda: CodexCliConfig())
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
    par_mem: ParMemConfig = field(default_factory=lambda: ParMemConfig())
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
