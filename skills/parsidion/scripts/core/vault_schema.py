"""Typed schema for the Parsidion vault config (ENH-014).

Single source of truth for the config schema: section names, key names, and
the allowed Python types for each key. Each top-level config section is a
dataclass whose field annotations reproduce the allowed-type tuples the
hand-maintained ``_CONFIG_SCHEMA`` literal in :mod:`vault_config` previously
encoded by hand. :func:`schema_dict` derives that structure from the
annotations so validation can consume one source of truth.

ENH-017: every field also carries ``metadata={"doc": ..., "read_by": ...}``
— a one-line description and the modules that read the key — and the
section docstrings carry the prose that used to live only in
``templates/config.yaml``. ``scripts/gen_config_docs.py`` generates the
CLAUDE.md config table, the ``docs/ARCHITECTURE.md`` reference block, and
the template from this module, and CI fails when the committed copies
drift. Optional metadata keys: ``example`` (a template-recommended value
that differs from the code default), ``reserved`` (True when no code reads
the key yet — the schema-vs-code coverage test allows exactly these),
``section_read`` (True when the whole section is consumed as one mapping,
keys forwarded verbatim — e.g. ``anthropic_env``).

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
    "UserPromptSubmitHookConfig",
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
    "TranscriptsConfig",
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
#
# ENH-017: metadata doc/read_by feed scripts/gen_config_docs.py; the
# docstring of each section is the template's section-header comment.
# ---------------------------------------------------------------------------


@dataclass
class AIConfig:
    """Prompt backend selection. Chooses which prompt backend the hooks and
    the summarizer use for AI calls. Default ``auto`` picks the first
    available backend: claude-cli on a Claude Code machine, codex-cli where
    ``codex`` is on PATH; ``PARSIDION_RUNTIME=grok`` hints grok-cli.
    ``none`` disables AI features entirely."""

    backend: str = field(
        default=None,
        metadata={
            "doc": "auto | claude-cli | codex-cli | grok-cli | none",
            "read_by": "ai_backend.py",
            "example": "auto",
        },
    )


@dataclass
class AIModelsConfig:
    """Per-backend model configuration. Each backend has a ``small`` and
    ``large`` tier: hooks use ``small`` (haiku-equivalent) for fast
    classification; the summarizer uses ``large`` (sonnet-equivalent) for
    note generation. Values are passed verbatim to the backend CLI."""

    claude: dict = field(
        default=None,
        metadata={
            "doc": "{small, large} model ids for the claude-cli backend",
            "read_by": "ai_backend.py",
            "example": {
                "small": "claude-haiku-4-5-20251001",
                "large": "claude-sonnet-4-6",
            },
        },
    )
    codex: dict = field(
        default=None,
        metadata={
            "doc": "{small, large} model ids for the codex-cli backend",
            "read_by": "ai_backend.py",
            "example": {"small": "gpt-5.6-luna", "large": "gpt-5.6-terra"},
        },
    )
    grok: dict = field(
        default=None,
        metadata={
            "doc": "{small, large} model ids for the grok-cli backend",
            "read_by": "ai_backend.py",
            "example": {"small": "grok-4.6", "large": "grok-4.6"},
        },
    )


@dataclass
class ClaudeCliConfig:
    """``claude -p`` invocation (ai_backend.py). Only used when
    ``ai.backend`` is ``claude-cli`` (or ``auto`` resolves to it).
    ``minimal_context`` (default true) passes ``--system-prompt`` and runs
    from a clean scratch cwd so ``claude -p`` does not ingest the project's
    CLAUDE.md chain — parsidion prompts are self-contained text transforms."""

    minimal_context: bool = field(
        default=None,
        metadata={
            "doc": "Replace the system prompt, run from a clean scratch cwd, and "
            "point CLAUDE_CONFIG_DIR at a scratch dir so neither the project's "
            "CLAUDE.md chain nor the user's global CLAUDE.md/skills/agents/MCP "
            "servers load — measured ~73k fewer input tokens per call",
            "read_by": "ai_backend.py",
            "example": True,
        },
    )
    system_prompt: str = field(
        default=None,
        metadata={
            "doc": "Override the minimal system prompt text",
            "read_by": "ai_backend.py",
        },
    )
    timeout: int | float = field(
        default=None,
        metadata={"doc": "Per-prompt timeout in seconds", "read_by": "ai_backend.py"},
    )


@dataclass
class GrokCliConfig:
    """``grok`` CLI invocation (ai_backend.py). Only used when
    ``ai.backend`` is ``grok-cli``. Auth uses the CLI's own OAuth login.
    Tools, subagents, and web search are always disabled (SEC-202) and every
    prompt runs from a clean scratch cwd; ``allow_tools`` is the explicit
    double opt-in that re-arms them. ``minimal_context`` (default true)
    overrides the system prompt — grok otherwise appends every
    CLAUDE.md/AGENTS.md it finds plus its full skill catalog to it — and
    points ``GROK_HOME`` at a scratch dir (auth-only) to drop grok's native
    skills and keep its state writes out of the real home. Grok 1.0.5 has
    no lever against its cross-agent ``~/.claude`` discovery (139 skills,
    5 MCP servers, global Claude.md) — that layer loads regardless."""

    command: str = field(
        default=None,
        metadata={
            "doc": "PATH lookup or absolute path to the grok CLI",
            "read_by": "ai_backend.py",
            "example": "grok",
        },
    )
    timeout: int | float = field(
        default=None,
        metadata={
            "doc": "Per-prompt timeout in seconds (grok-4.6 headless runs 17-40 s)",
            "read_by": "ai_backend.py",
            "example": 120,
        },
    )
    minimal_context: bool = field(
        default=None,
        metadata={
            "doc": "Override the system prompt (tools stay disabled; see allow_tools)",
            "read_by": "ai_backend.py",
            "example": True,
        },
    )
    system_prompt: str = field(
        default=None,
        metadata={
            "doc": "Override the minimal system prompt text",
            "read_by": "ai_backend.py",
        },
    )
    allow_tools: bool = field(
        default=None,
        metadata={
            "doc": "SEC-202 explicit opt-in to run grok with tools/subagents/web search enabled",
            "read_by": "ai_backend.py",
        },
    )


@dataclass
class CodexCliConfig:
    """``codex exec`` invocation (ai_backend.py). Only used when
    ``ai.backend`` resolves to ``codex-cli``. ``sandbox`` mirrors codex's
    own ``--sandbox`` flag and is passed verbatim; ``danger-full-access``
    requires an explicit opt-in (SEC-117). The three boolean defaults below
    are shown at their code defaults — copying the template with them set to
    false would silently flip parsidion's internal codex invocations to
    persistent, repo-checking, notifying runs."""

    command: str = field(
        default=None,
        metadata={
            "doc": "PATH lookup or absolute path to the codex CLI",
            "read_by": "ai_backend.py",
            "example": "codex",
        },
    )
    timeout: int | float = field(
        default=None,
        metadata={
            "doc": "Per-prompt timeout in seconds",
            "read_by": "ai_backend.py",
            "example": 60,
        },
    )
    sandbox: str | None = field(
        default=None,
        metadata={
            "doc": "read-only | workspace-write | danger-full-access (latter needs allow_danger_full_access)",
            "read_by": "ai_backend.py",
            "example": "read-only",
        },
    )
    ephemeral: bool = field(
        default=None,
        metadata={
            "doc": "Start each prompt with no session state",
            "read_by": "ai_backend.py",
            "example": True,
        },
    )
    skip_git_repo_check: bool = field(
        default=None,
        metadata={
            "doc": "Skip the git-repo check for internal calls",
            "read_by": "ai_backend.py",
            "example": True,
        },
    )
    minimal_context: bool = field(
        default=None,
        metadata={
            "doc": "Run from a scratch CODEX_HOME (auth-only) and clean cwd so "
            "codex loads no MCP servers, AGENTS.md instructions, skills, or "
            "execpolicy rules — measured ~27k fewer input tokens per call",
            "read_by": "ai_backend.py",
            "example": True,
        },
    )
    suppress_notify: bool = field(
        default=None,
        metadata={
            "doc": "Suppress user turn-complete notifications for internal calls",
            "read_by": "ai_backend.py",
            "example": True,
        },
    )
    allow_danger_full_access: bool = field(
        default=None,
        metadata={
            "doc": "SEC-117 opt-in required for sandbox: danger-full-access",
            "read_by": "ai_backend.py",
        },
    )


@dataclass
class SessionStartHookConfig:
    """Session start hook (session_start_hook.py)."""

    ai_model: str | None = field(
        default=None,
        metadata={
            "doc": "Model for AI note selection (null = disabled)",
            "read_by": "session_start_hook.py",
        },
    )
    ai_cooldown_seconds: int | float = field(
        default=30,
        metadata={
            "doc": "Skip nested claude -p if AI SessionStart ran recently for this vault",
            "read_by": "session_start_hook.py",
        },
    )
    ai_single_flight: bool = field(
        default=True,
        metadata={
            "doc": "Allow only one nested AI SessionStart selector per vault at a time",
            "read_by": "session_start_hook.py",
        },
    )
    ai_candidates_max: int | None = field(
        default=None,
        metadata={
            "doc": (
                "Cap on the AI selector's ranked candidate pool "
                "(0 = unlimited, unset = seed_selection's default of 48)"
            ),
            "read_by": "session_start_hook.py",
            "example": 48,
        },
    )
    max_chars: int = field(
        default=4000,
        metadata={
            "doc": "Max context injection characters",
            "read_by": "session_start_hook.py",
        },
    )
    ai_timeout: int | float = field(
        default=25,
        metadata={
            "doc": "AI call timeout in seconds",
            "read_by": "session_start_hook.py",
        },
    )
    recent_days: int = field(
        default=3,
        metadata={
            "doc": "Days to look back for recent notes",
            "read_by": "session_start_hook.py",
        },
    )
    debug: bool = field(
        default=False,
        metadata={
            "doc": "Append injected context + metadata to a debug log in $TMPDIR",
            "read_by": "session_start_hook.py",
        },
    )
    verbose_mode: bool = field(
        default=False,
        metadata={
            "doc": "Inject full note summaries instead of the compact one-line-per-note index",
            "read_by": "session_start_hook.py",
        },
    )
    use_embeddings: bool = field(
        default=True,
        metadata={
            "doc": "Blend semantic matches into context; graceful fallback if db absent",
            "read_by": "session_start_hook.py",
        },
    )
    track_delta: bool = field(
        default=True,
        metadata={
            "doc": "Prepend a 'Since last session' delta of new/updated notes per project",
            "read_by": "session_start_hook.py",
        },
    )
    show_dead_letter_notice: bool = field(
        default=False,
        metadata={
            "doc": "Include the dead-letter queue warning in SessionStart context",
            "read_by": "session_start_hook.py",
        },
    )
    graph_expand: bool = field(
        default=True,
        metadata={
            "doc": "Tier 1: splice in 1-hop wikilink neighbours of selected notes",
            "read_by": "session_start_hook.py",
        },
    )
    graph_expand_max: int = field(
        default=8,
        metadata={
            "doc": "Max neighbour notes added per session (best-connected first)",
            "read_by": "session_start_hook.py",
        },
    )
    graph_rerank: bool = field(
        default=True,
        metadata={
            "doc": "Tier 2: re-rank by seed-cluster tag overlap + hubness",
            "read_by": "session_start_hook.py",
        },
    )


@dataclass
class SessionStopHookConfig:
    """Session stop hook (session_stop_hook.py; the same pipeline runs for
    every runtime via agent_adapter.run_session_end)."""

    ai_model: str | None = field(
        default=None,
        metadata={
            "doc": "Model for AI classification (null = disabled)",
            "read_by": "session_stop_hook.py, agent_adapter.py",
        },
    )
    ai_timeout: int | float = field(
        default=25,
        metadata={
            "doc": "AI call timeout in seconds",
            "read_by": "session_stop_hook.py, agent_adapter.py",
        },
    )
    auto_summarize: bool = field(
        default=True,
        metadata={
            "doc": "Auto-launch summarizer when pending entries exist",
            "read_by": "agent_adapter.py",
        },
    )
    auto_summarize_after: int | None = field(
        default=1,
        metadata={
            "doc": "Queue threshold to trigger the auto-summarizer (0 = always)",
            "read_by": "agent_adapter.py",
        },
    )
    transcript_tail_lines: int = field(
        default=200,
        metadata={
            "doc": "Default transcript tail lines to parse",
            "read_by": "agent_adapter.py",
        },
    )
    pi_transcript_tail_lines: int = field(
        default=1000,
        metadata={
            "doc": "Deeper fallback tail for pi transcripts when no assistant text is found",
            "read_by": "agent_adapter.py",
        },
    )
    transcript_tail_bytes: int = field(
        default=1_500_000,
        metadata={
            "doc": "Byte ceiling on the raw tail; bounds huge-line transcripts",
            "read_by": "agent_adapter.py",
        },
    )


@dataclass
class SubagentStopHookConfig:
    """Subagent stop hook (subagent_stop_hook.py)."""

    enabled: bool = field(
        default=True,
        metadata={
            "doc": "Set false to disable subagent transcript capture entirely",
            "read_by": "subagent_stop_hook.py",
        },
    )
    min_messages: int = field(
        default=None,
        metadata={
            "doc": "Minimum assistant turns before queuing (pi defaults to 1 when unset)",
            "read_by": "subagent_stop_hook.py",
        },
    )
    excluded_agents: str = field(
        default=None,
        metadata={
            "doc": "Comma-separated agent-type skip list (config parser limitation: a string, not a list)",
            "read_by": "subagent_stop_hook.py",
            "example": "vault-explorer,research-agent",
        },
    )
    transcript_tail_bytes: int = field(
        default=1_500_000,
        metadata={
            "doc": "Byte ceiling on the subagent transcript tail; bounds huge-line rollouts",
            "read_by": "subagent_stop_hook.py",
        },
    )


@dataclass
class UserPromptSubmitHookConfig:
    """User prompt submit hook (user_prompt_submit_hook.py)."""

    enabled: bool = field(
        default=True,
        metadata={
            "doc": "Set false to disable per-prompt vault recall injection",
            "read_by": "user_prompt_submit_hook.py",
            "section_read": True,
        },
    )
    top_k: int = field(
        default=3,
        metadata={
            "doc": "Notes to retrieve per prompt",
            "read_by": "user_prompt_submit_hook.py",
            "section_read": True,
        },
    )
    max_chars: int = field(
        default=1500,
        metadata={
            "doc": "Total additionalContext character budget",
            "read_by": "user_prompt_submit_hook.py",
            "section_read": True,
        },
    )
    per_note_chars: int = field(
        default=350,
        metadata={
            "doc": "Per-note excerpt character budget",
            "read_by": "user_prompt_submit_hook.py",
            "section_read": True,
        },
    )
    min_term_matches: int = field(
        default=2,
        metadata={
            "doc": "Relevance gate — distinct tokens shared between prompt and note title/tags/stem, 0 disables",
            "read_by": "user_prompt_submit_hook.py",
            "section_read": True,
        },
    )
    min_prompt_chars: int = field(
        default=4,
        metadata={
            "doc": "Prompts shorter than this skip retrieval",
            "read_by": "user_prompt_submit_hook.py",
            "section_read": True,
        },
    )
    probe_cache_seconds: int = field(
        default=300,
        metadata={
            "doc": "Negative-cache freshness for the parsight availability probe",
            "read_by": "user_prompt_submit_hook.py",
            "section_read": True,
        },
    )
    debug: bool = field(
        default=False,
        metadata={
            "doc": "Append injected context + metadata to a debug log in $TMPDIR",
            "read_by": "user_prompt_submit_hook.py",
            "section_read": True,
        },
    )


@dataclass
class PreCompactHookConfig:
    """Pre-compact hook (pre_compact_hook.py)."""

    lines: int = field(
        default=200,
        metadata={
            "doc": "Transcript lines to analyse",
            "read_by": "pre_compact_hook.py",
        },
    )
    transcript_tail_bytes: int = field(
        default=1_500_000,
        metadata={
            "doc": "Byte ceiling on the transcript tail read",
            "read_by": "pre_compact_hook.py",
        },
    )


@dataclass
class SummarizerConfig:
    """Session summarizer (summarize_sessions.py)."""

    model: str | None = field(
        default=None,
        metadata={
            "doc": "Large model for final note generation (null = ai_models.<backend>.large)",
            "read_by": "summarize_sessions.py",
        },
    )
    max_parallel: int = field(
        default=5,
        metadata={
            "doc": "Concurrent summarization tasks",
            "read_by": "summarize_sessions.py",
        },
    )
    transcript_tail_lines: int = field(
        default=400,
        metadata={
            "doc": "Transcript tail lines to parse",
            "read_by": "summarizer/transcript.py",
        },
    )
    transcript_tail_bytes: int = field(
        default=262_144,
        metadata={
            "doc": "Byte ceiling on the raw tail; bounds huge-line transcripts (e.g. codex subagent rollouts)",
            "read_by": "summarizer/transcript.py",
        },
    )
    max_cleaned_chars: int = field(
        default=12_000,
        metadata={
            "doc": "Cleaned-transcript char budget; longer transcripts are chunk-summarized first",
            "read_by": "summarize_sessions.py",
        },
    )
    ai_timeout: int | float | None = field(
        default=None,
        metadata={
            "doc": "Per-summarizer-prompt timeout in seconds (null = backend default)",
            "read_by": "summarize_sessions.py",
        },
    )
    cluster_model: str | None = field(
        default=None,
        metadata={
            "doc": "Small model for hierarchical chunk summarization (null = ai_models.<backend>.small)",
            "read_by": "summarize_sessions.py",
        },
    )
    dedup_threshold: float | int = field(
        default=0.80,
        metadata={
            "doc": "Cosine similarity above which a near-duplicate note is detected and skipped (1.0 disables)",
            "read_by": "summarize_sessions.py",
        },
    )
    dead_letter_retention_days: int = field(
        default=7,
        metadata={
            "doc": "Prune dead_letters.jsonl entries older than N days each run (<=0 disables)",
            "read_by": "summarize_sessions.py",
        },
    )
    rebuild_graph: bool = field(
        default=False,
        metadata={
            "doc": "Rebuild visualizer graph.json after indexing (same as --rebuild-graph)",
            "read_by": "summarize_sessions.py",
        },
    )
    graph_include_daily: bool = field(
        default=False,
        metadata={
            "doc": "Include Daily notes in graph rebuild (same as --graph-include-daily)",
            "read_by": "summarize_sessions.py",
        },
    )
    graph_incremental: bool = field(
        default=True,
        metadata={
            "doc": "ENH-010: reuse the previous graph and recompute only changed notes; automatic full-rebuild fallback",
            "read_by": "summarize_sessions.py",
        },
    )
    persist: bool = field(
        default=None,
        metadata={
            "doc": "Legacy no-op",
            "read_by": "(none — legacy no-op)",
            "reserved": True,
        },
    )


@dataclass
class EmbeddingsConfig:
    """Embeddings / semantic search (build_embeddings.py, vault_search.py).
    When parsight is installed and healthy, vault semantic search is served
    by parsight's hybrid retrieval; the local embeddings pipeline remains
    the always-on silent fallback."""

    enabled: bool = field(
        default=True,
        metadata={
            "doc": "Set false to disable embedding builds, note_index writes, and auto-rebuild after update_index.py",
            "read_by": "build_embeddings.py, vault_search.py",
        },
    )
    model: str = field(
        default="BAAI/bge-small-en-v1.5",
        metadata={
            "doc": "~67 MB ONNX model, cached after first run",
            "read_by": "build_embeddings.py, vault_search.py",
        },
    )
    min_score: float | int = field(
        default=0.45,
        metadata={
            "doc": "Minimum cosine similarity for search results (embeddings backend; parsight gates by rank/top_k)",
            "read_by": "vault_search.py",
        },
    )
    top_k: int = field(
        default=10,
        metadata={
            "doc": "Default result count for vault_search.py",
            "read_by": "vault_search.py",
        },
    )
    decay_enabled: bool = field(
        default=True,
        metadata={
            "doc": "Apply temporal decay so newer notes score higher",
            "read_by": "vault_search.py",
        },
    )
    decay_half_life_days: float | int = field(
        default=90.0,
        metadata={
            "doc": "Days for score to decay halfway to decay_min_factor",
            "read_by": "vault_search.py",
        },
    )
    decay_min_factor: float | int = field(
        default=0.5,
        metadata={
            "doc": "Floor multiplier for very old notes (0.0-1.0); prevents scores from vanishing",
            "read_by": "vault_search.py",
        },
    )
    service_enabled: bool = field(
        default=True,
        metadata={
            "doc": "ENH-003/ENH-020: persistent embedding service (vault_embed_serve.py), auto-spawned single-flight on the first enabled client; set false to force cold in-process loads; never used while parsight serves retrieval",
            "read_by": "vault_embed_serve.py, vault_search.py",
        },
    )
    service_idle_exit: int = field(
        default=600,
        metadata={
            "doc": "Seconds the embedding service stays alive idle before exiting",
            "read_by": "vault_embed_serve.py",
        },
    )


@dataclass
class ParsightConfig:
    """parsight code-memory backend (optional external CLI + always-on
    daemon; parsight_backend.py, vault_search.py — see docs/PARSIGHT.md).
    A legacy ``par_mem:`` section is honored as an alias; this canonical
    section wins per key when both are present."""

    enabled: bool = field(
        default=None,
        metadata={
            "doc": "Probe for parsight when available; false = never probe",
            "read_by": "parsight_backend.py, vault_search.py",
            "example": True,
        },
    )
    binary: str = field(
        default=None,
        metadata={
            "doc": "PATH lookup or absolute path to the parsight CLI (falls back to the legacy par-mem name)",
            "read_by": "parsight_backend.py",
            "example": "parsight",
        },
    )
    timeout_s: int | float = field(
        default=None,
        metadata={
            "doc": "Per-query subprocess timeout in seconds",
            "read_by": "parsight_backend.py",
            "example": 10,
        },
    )


@dataclass
class SearchConfig:
    """Vault search backend selection (vault_search.py)."""

    backend: str = field(
        default="auto",
        metadata={
            "doc": "auto | parsight | embeddings | none (legacy 'par-mem' accepted as an alias for parsight)",
            "read_by": "vault_search.py",
        },
    )
    use_note_index: bool = field(
        default=True,
        metadata={
            "doc": "ENH-004: read note metadata from the note_index DB; false forces filesystem walks (index builders always walk)",
            "read_by": "vault_search.py, vault_index.py",
        },
    )


@dataclass
class AnthropicEnvConfig:
    """Anthropic-compatible transport/env settings. Keys mirror real env
    var names so values can be copied directly from env-based configs.
    Precedence: real environment variable > this section. SECURITY: a real
    API key or auth token here means this file must NOT be committed to the
    vault git repo — prefer config.local.yaml (always gitignored) for
    secret keys. null means the API's own default for that tier; set
    ANTHROPIC_BASE_URL to route through a trusted gateway."""

    # section_read: vault_hooks merges the whole anthropic_env mapping into
    # the claude -p subprocess env (keys forwarded verbatim), so per-key
    # reader evidence does not exist for these fields.
    ANTHROPIC_API_KEY: str | None = field(
        default=None,
        metadata={
            "doc": "API key forwarded to claude -p (prefer config.local.yaml for secrets)",
            "read_by": "vault_hooks.py",
            "section_read": True,
        },
    )
    ANTHROPIC_AUTH_TOKEN: str | None = field(
        default=None,
        metadata={
            "doc": "Auth token forwarded to claude -p (prefer config.local.yaml for secrets)",
            "read_by": "vault_hooks.py",
            "section_read": True,
        },
    )
    ANTHROPIC_BASE_URL: str | None = field(
        default=None,
        metadata={
            "doc": "null = the real Anthropic endpoint; set to route through a gateway",
            "read_by": "vault_hooks.py",
            "section_read": True,
        },
    )
    ANTHROPIC_CUSTOM_HEADERS: str | None = field(
        default=None,
        metadata={
            "doc": "Extra headers forwarded to claude -p",
            "read_by": "vault_hooks.py",
            "section_read": True,
        },
    )
    ANTHROPIC_DEFAULT_HAIKU_MODEL: str | None = field(
        default=None,
        metadata={
            "doc": "null = the API's own haiku-tier default",
            "read_by": "vault_hooks.py",
            "example": "claude-haiku-4-5-20251001",
            "section_read": True,
        },
    )
    ANTHROPIC_DEFAULT_SONNET_MODEL: str | None = field(
        default=None,
        metadata={
            "doc": "null = the API's own sonnet-tier default",
            "read_by": "vault_hooks.py",
            "example": "claude-sonnet-4-6",
            "section_read": True,
        },
    )
    ANTHROPIC_DEFAULT_OPUS_MODEL: str | None = field(
        default=None,
        metadata={
            "doc": "null = the API's own opus-tier default",
            "read_by": "vault_hooks.py",
            "section_read": True,
        },
    )
    API_TIMEOUT_MS: int | str | None = field(
        default=None,
        metadata={
            "doc": "Request timeout in milliseconds forwarded to claude -p",
            "read_by": "vault_hooks.py",
            "example": 3000000,
            "section_read": True,
        },
    )
    HTTPS_PROXY: str | None = field(
        default=None,
        metadata={
            "doc": "HTTPS proxy forwarded to claude -p",
            "read_by": "vault_hooks.py",
            "section_read": True,
        },
    )
    HTTP_PROXY: str | None = field(
        default=None,
        metadata={
            "doc": "HTTP proxy forwarded to claude -p",
            "read_by": "vault_hooks.py",
            "section_read": True,
        },
    )


@dataclass
class GitConfig:
    """Git integration. When ``auto_commit`` is false,
    ``git_commit_vault()`` returns immediately without staging or
    committing — disabling all automatic vault git commits across hooks and
    the summarizer."""

    auto_commit: bool = field(
        default=True,
        metadata={
            "doc": "Auto-commit vault changes after writes",
            "read_by": "vault_common.py (vault_fs.git_commit_vault)",
        },
    )


@dataclass
class DefaultsConfig:
    """Legacy centralized model IDs. Only ``haiku_model`` is read — a
    fallback for scripts that have not migrated to tiers; per-tier
    overrides for any backend live in ``ai_models.<backend>``
    (``sonnet_model`` is no longer read)."""

    haiku_model: str = field(
        default="claude-haiku-4-5-20251001",
        metadata={
            "doc": "Legacy haiku-tier fallback used by session hooks and vault_doctor repair",
            "read_by": "ai_backend.py",
        },
    )


@dataclass
class EventLogConfig:
    """Hook event log (all hooks — structured JSON events via
    ``vault_hooks.write_hook_event``)."""

    enabled: bool = field(
        default=True,
        metadata={
            "doc": "Write structured JSON events to hook_events.log",
            "read_by": "vault_hooks.py",
        },
    )
    max_lines: int = field(
        default=10_000,
        metadata={
            "doc": "Rotate (keep the second half) when the log exceeds this many lines",
            "read_by": "vault_hooks.py",
        },
    )
    path: str | None = field(
        default=None,
        metadata={
            "doc": "Absolute path override (null = <vault>/hook_events.log)",
            "read_by": "vault_hooks.py",
        },
    )


@dataclass
class AdaptiveContextConfig:
    """Adaptive context tracking — derank notes never referenced by the
    agent (session_start_hook.py, vault_adaptive.py)."""

    enabled: bool = field(
        default=False,
        metadata={
            "doc": "Track per-note usefulness; derank unreferenced notes over time",
            "read_by": "session_start_hook.py, vault_adaptive.py",
        },
    )
    decay_days: int | float = field(
        default=30,
        metadata={
            "doc": "ENH-016: half-life (days) of usefulness scores — unused notes derank over time; 0 disables decay",
            "read_by": "session_start/seed_selection.py, vault_adaptive.py",
        },
    )


@dataclass
class TranscriptsConfig:
    """Unified transcript tail settings (ENH-018). One byte-bounded reader
    (``core/transcript_reader.read_tail``) serves every runtime; the
    per-hook ``transcript_tail_*`` keys still override these for one
    release but are deprecated — ``validate_config`` warns when they are
    set. Set per-hook keys to ``null`` to fall back to these values."""

    tail_lines: int = field(
        default=200,
        metadata={
            "doc": "Default transcript tail lines across hooks",
            "read_by": "agent_adapter.py",
        },
    )
    tail_bytes: int = field(
        default=1_500_000,
        metadata={
            "doc": "Byte ceiling on every transcript tail read; bounds huge-line transcripts",
            "read_by": "agent_adapter.py, subagent_stop_hook.py, pre_compact_hook.py, summarizer/transcript.py",
        },
    )
    max_line_bytes: int = field(
        default=262_144,
        metadata={
            "doc": "A JSONL line longer than this is kept with its long string fields truncated (256 KiB default)",
            "read_by": "core/transcript_reader.py",
        },
    )


@dataclass
class VaultSectionConfig:
    """Vault identity — used for per-user daily note filenames (team vault
    sharing)."""

    username: str = field(
        default="",
        metadata={
            "doc": "Username suffix for daily notes (DD-{username}.md); defaults to $USER if blank",
            "read_by": "vault_fs.py, installer",
        },
    )


@dataclass
class AdaptersConfig:
    """Agent adapters (agent_adapter.py, ENH-006). External adapter loading
    is opt-in because loading arbitrary Python is code execution: each file
    is refused if group/world-writable and every load is logged."""

    load_external: bool = field(
        default=False,
        metadata={
            "doc": "Opt-in: load ~/.config/parsidion/adapters/*.py drop-in AgentAdapter descriptors (permission-checked, logged)",
            "read_by": "agent_adapter.py",
        },
    )


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
    user_prompt_submit_hook: UserPromptSubmitHookConfig = field(
        default_factory=lambda: UserPromptSubmitHookConfig()
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
    transcripts: TranscriptsConfig = field(default_factory=lambda: TranscriptsConfig())
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
