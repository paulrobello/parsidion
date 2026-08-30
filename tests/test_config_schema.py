"""Tests for ENH-014: typed config schema (deduplication + typed access).

The schema that ``validate_config`` enforces used to be a hand-maintained
literal (``_CONFIG_SCHEMA`` in vault_config). ENH-014 makes the dataclasses in
``vault_schema`` the single source of truth and *derives* that structure from
their annotations. These tests prove the refactor is behaviour-preserving:

* :data:`GOLDEN_SCHEMA` is a frozen copy of the pre-refactor literal. The
  snapshot test asserts the live derived schema equals it key-for-key and
  tuple-for-tuple -- so any drift in the dataclass annotations is caught.
* The validation battery feeds config dicts exercising every warning path
  (unknown section/key, one mismatch per distinct type-combo, explicit-None
  skipping, non-mapping sections, valid and empty configs) through
  ``validate_config`` and compares against a reference implementation run
  against the frozen golden schema. If the derived schema OR the validation
  logic drifts, the comparison fails.

Plus direct tests for ``load_typed_config`` / ``VaultAppConfig.from_dict``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import vault_config
from vault_config import (
    AIConfig,
    AIModelsConfig,
    CodexCliConfig,
    EmbeddingsConfig,
    GitConfig,
    SessionStartHookConfig,
    SummarizerConfig,
    VaultAppConfig,
)

# ---------------------------------------------------------------------------
# Golden snapshot of the pre-refactor _CONFIG_SCHEMA literal.
#
# Captured verbatim before ENH-014 replaced the literal with the derived
# schema. Any change to the dataclass annotations that drives
# ``vault_schema.schema_dict()`` must keep the derived output equal to this
# snapshot, or update it deliberately (and the battery below will surface the
# resulting validation-output change).
# ---------------------------------------------------------------------------
GOLDEN_SCHEMA: dict[str, dict[str, tuple[type, ...]]] = {
    "ai": {"backend": (str,)},
    "ai_models": {"claude": (dict,), "codex": (dict,), "grok": (dict,)},
    "claude_cli": {
        "minimal_context": (bool,),
        "system_prompt": (str,),
        "timeout": (int, float),
    },
    "grok_cli": {
        "command": (str,),
        "timeout": (int, float),
        "minimal_context": (bool,),
        "system_prompt": (str,),
        "allow_tools": (bool,),  # SEC-202: explicit tools opt-in
    },
    "codex_cli": {
        "command": (str,),
        "timeout": (int, float),
        "sandbox": (str, type(None)),
        "ephemeral": (bool,),
        "skip_git_repo_check": (bool,),
        "minimal_context": (bool,),
        "suppress_notify": (bool,),
        "allow_danger_full_access": (bool,),
    },
    "session_start_hook": {
        "ai_model": (str, type(None)),
        "ai_cooldown_seconds": (int, float),
        # ARC-108: the field has always defaulted to None (seed_selection's
        # _build_candidates handles it); only the annotation said otherwise.
        "ai_candidates_max": (int, type(None)),
        "ai_single_flight": (bool,),
        "max_chars": (int,),
        "ai_timeout": (int, float),
        "recent_days": (int,),
        "debug": (bool,),
        "verbose_mode": (bool,),
        "use_embeddings": (bool,),
        "track_delta": (bool,),
        "show_dead_letter_notice": (bool,),
        "graph_expand": (bool,),
        "graph_expand_max": (int,),
        "graph_rerank": (bool,),
    },
    "session_stop_hook": {
        "ai_model": (str, type(None)),
        "ai_timeout": (int, float),
        "auto_summarize": (bool,),
        "auto_summarize_after": (int, type(None)),
        "transcript_tail_lines": (int,),
        "pi_transcript_tail_lines": (int,),
        "transcript_tail_bytes": (int,),
    },
    "subagent_stop_hook": {
        "enabled": (bool,),
        "min_messages": (int,),
        "excluded_agents": (str,),
        "transcript_tail_bytes": (int,),
    },
    "user_prompt_submit_hook": {
        "enabled": (bool,),
        "top_k": (int,),
        "max_chars": (int,),
        "per_note_chars": (int,),
        "min_term_matches": (int,),
        "min_prompt_chars": (int,),
        "probe_cache_seconds": (int,),
        "recall_timeout_s": (float,),
        "debug": (bool,),
    },
    "pre_compact_hook": {"lines": (int,), "transcript_tail_bytes": (int,)},
    "summarizer": {
        "model": (str, type(None)),
        "max_parallel": (int,),
        "transcript_tail_lines": (int,),
        "transcript_tail_bytes": (int,),
        "max_cleaned_chars": (int,),
        "persist": (bool,),
        "cluster_model": (str, type(None)),
        "dedup_threshold": (float, int),
        "dead_letter_retention_days": (int,),
        "rebuild_graph": (bool,),
        "graph_include_daily": (bool,),
        "graph_incremental": (bool,),
        "ai_timeout": (int, float, type(None)),
    },
    "embeddings": {
        "enabled": (bool,),
        "model": (str,),
        "min_score": (float, int),
        "top_k": (int,),
        "decay_enabled": (bool,),
        "decay_half_life_days": (float, int),
        "decay_min_factor": (float, int),
        "service_enabled": (bool,),
        "service_idle_exit": (int,),
    },
    "parsight": {"enabled": (bool,), "binary": (str,), "timeout_s": (int, float)},
    "search": {"backend": (str,), "use_note_index": (bool,)},
    "anthropic_env": {
        "ANTHROPIC_API_KEY": (str, type(None)),
        "ANTHROPIC_AUTH_TOKEN": (str, type(None)),
        "ANTHROPIC_BASE_URL": (str, type(None)),
        "ANTHROPIC_CUSTOM_HEADERS": (str, type(None)),
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": (str, type(None)),
        "ANTHROPIC_DEFAULT_SONNET_MODEL": (str, type(None)),
        "ANTHROPIC_DEFAULT_OPUS_MODEL": (str, type(None)),
        "API_TIMEOUT_MS": (int, str, type(None)),
        "HTTPS_PROXY": (str, type(None)),
        "HTTP_PROXY": (str, type(None)),
    },
    "git": {"auto_commit": (bool,)},
    "defaults": {"haiku_model": (str,)},
    "event_log": {
        "enabled": (bool,),
        "max_lines": (int,),
        "path": (str, type(None)),
    },
    "adaptive_context": {"enabled": (bool,), "decay_days": (int, float)},
    "vault": {"username": (str,)},
    "transcripts": {
        "tail_lines": (int,),
        "tail_bytes": (int,),
        "max_line_bytes": (int,),
    },
    "adapters": {"load_external": (bool,)},
}


def _reference_validate(
    config: dict[str, Any], schema: dict[str, dict[str, tuple[type, ...]]]
) -> list[str]:
    """Frozen reimplementation of ``validate_config``'s logic.

    Run against :data:`GOLDEN_SCHEMA`. If the live ``validate_config`` (which
    uses the derived schema) ever diverges from this reference, the battery
    below fails -- catching either a schema-derivation drift or a logic change.
    """
    if not config:
        return []
    warnings: list[str] = []
    known_sections = set(schema.keys())
    for section, section_value in config.items():
        if section not in known_sections:
            warnings.append(f"config.yaml: unknown section '{section}'")
            continue
        if not isinstance(section_value, dict):
            warnings.append(
                f"config.yaml: section '{section}' should be a mapping, "
                f"got {type(section_value).__name__}"
            )
            continue
        schema_keys = schema[section]
        for key, value in section_value.items():
            if key not in schema_keys:
                warnings.append(f"config.yaml: unknown key '{section}.{key}'")
                continue
            expected_types = schema_keys[key]
            if value is not None and not isinstance(value, expected_types):
                type_names = " | ".join(
                    t.__name__ for t in expected_types if t is not type(None)
                )
                warnings.append(
                    f"config.yaml: '{section}.{key}' expected {type_names}, "
                    f"got {type(value).__name__}"
                )
    return warnings


class TestSchemaEquivalence:
    """The derived schema must equal the frozen golden literal exactly."""

    def test_derived_schema_equals_golden_snapshot(self) -> None:
        derived = vault_config.schema_dict()
        assert derived == GOLDEN_SCHEMA, (
            "vault_schema.schema_dict() drifted from the golden snapshot -- "
            "the dataclass annotations no longer reproduce the pre-refactor "
            "_CONFIG_SCHEMA. If this is intentional, update GOLDEN_SCHEMA "
            "deliberately and re-check the validation battery."
        )

    def test_live_config_schema_is_derived_from_vault_schema(self) -> None:
        # The module-level _CONFIG_SCHEMA must be the derived structure, not a
        # hand-maintained literal that could drift from the dataclasses.
        assert vault_config._CONFIG_SCHEMA == vault_config.schema_dict()

    def test_section_count_matches(self) -> None:
        assert len(vault_config.schema_dict()) == len(GOLDEN_SCHEMA) == 22

    def test_no_schema_key_dropped_or_added(self) -> None:
        derived = vault_config.schema_dict()
        for section, keys in GOLDEN_SCHEMA.items():
            assert set(derived[section]) == set(keys), (
                f"key set for section '{section}' changed"
            )


class TestValidateConfigBattery:
    """``validate_config`` output must match the reference against the golden
    schema across every warning path -- the byte-identical-behaviour proof.
    """

    @pytest.mark.parametrize(
        ("name", "yaml_text"),
        [
            # --- unknown section / key ---
            (
                "unknown_section",
                "bogus_section:\n  key: value\n",
            ),
            (
                "unknown_key_in_known_section",
                "ai:\n  backend: claude-cli\n  not_a_key: x\n",
            ),
            # --- one type mismatch per distinct type-combo in the schema ---
            ("mismatch_str_field_gets_int", "ai:\n  backend: 123\n"),
            ("mismatch_int_field_gets_str", "session_start_hook:\n  max_chars: foo\n"),
            (
                "mismatch_bool_field_gets_str",
                "session_start_hook:\n  debug: maybe\n",
            ),
            ("mismatch_dict_field_gets_str", "ai_models:\n  claude: not-a-dict\n"),
            (
                "mismatch_int_float_field_gets_str",
                "codex_cli:\n  timeout: soon\n",
            ),
            (
                "mismatch_str_or_none_field_gets_int",
                "codex_cli:\n  sandbox: 5\n",
            ),
            (
                "mismatch_int_or_none_field_gets_str",
                "session_stop_hook:\n  auto_summarize_after: never\n",
            ),
            (
                "mismatch_float_int_field_gets_bool",
                "summarizer:\n  dedup_threshold: yes\n",
            ),
            (
                "mismatch_int_str_none_field_gets_bool",
                "anthropic_env:\n  API_TIMEOUT_MS: yes\n",
            ),
            (
                "mismatch_int_float_none_field_gets_str",
                "summarizer:\n  ai_timeout: soon\n",
            ),
            # --- explicit None skips the type check (allowed everywhere) ---
            ("explicit_none_on_str_field", "codex_cli:\n  sandbox: null\n"),
            (
                "explicit_none_on_int_field",
                "session_stop_hook:\n  auto_summarize_after: null\n",
            ),
            ("explicit_none_on_dict_field", "ai_models:\n  claude: null\n"),
            # --- section value not a mapping ---
            (
                "section_value_is_scalar",
                "git: true\n",
            ),
            # --- fully valid config ---
            (
                "valid_config",
                "ai:\n  backend: claude-cli\n"
                "session_start_hook:\n  max_chars: 4000\n  debug: true\n"
                "codex_cli:\n  timeout: 30.5\n  sandbox: null\n"
                "summarizer:\n  dedup_threshold: 0.85\n",
            ),
        ],
    )
    def test_validate_config_matches_reference(
        self, name: str, yaml_text: str, tmp_vault: Path
    ) -> None:
        (tmp_vault / "config.yaml").write_text(yaml_text, encoding="utf-8")
        vault_config.clear_config_cache()

        live = vault_config.validate_config()

        # Reference: parse the same YAML with the same parser the live code
        # uses, then validate against the FROZEN golden schema. Equal output
        # proves derived-schema + live-logic == golden-schema + reference-logic.
        parsed = vault_config._parse_config_yaml(yaml_text)
        expected = _reference_validate(parsed, GOLDEN_SCHEMA)

        assert live == expected, (
            f"battery case '{name}' diverged:\n"
            f"  yaml: {yaml_text!r}\n"
            f"  live:      {live}\n"
            f"  expected:  {expected}"
        )

    def test_empty_config_yields_no_warnings(self, tmp_vault: Path) -> None:
        # No config.yaml at all -> load_config returns {} -> validate_config [].
        vault_config.clear_config_cache()
        assert vault_config.validate_config() == []


class TestLoadTypedConfig:
    """Typed access via load_typed_config / VaultAppConfig.from_dict."""

    def test_from_dict_maps_section_fields_without_coercion(self) -> None:
        parsed = {
            "ai": {"backend": "claude-cli"},
            "session_start_hook": {"max_chars": "4000", "debug": True},
            "summarizer": {"model": None, "dedup_threshold": 0.9},
        }
        cfg = VaultAppConfig.from_dict(parsed)

        assert isinstance(cfg.ai, AIConfig)
        assert cfg.ai.backend == "claude-cli"
        # Values pass through unchanged -- "4000" stays a str (no coercion).
        assert cfg.session_start_hook.max_chars == "4000"  # type: ignore[comparison-overlap]
        assert cfg.session_start_hook.debug is True
        # Explicit None passes through.
        assert cfg.summarizer.model is None
        assert cfg.summarizer.dedup_threshold == 0.9

    def test_from_dict_absent_keys_follow_schema_defaults(self) -> None:
        cfg = VaultAppConfig.from_dict({"ai": {"backend": "claude-cli"}})
        assert cfg.ai.backend == "claude-cli"
        # An absent section yields an empty instance at schema defaults.
        assert cfg.session_start_hook == SessionStartHookConfig()
        # ARC-007: a present section missing keys -> schema default where one
        # is declared, None for the deliberately default-less fields.
        cfg2 = VaultAppConfig.from_dict({"session_start_hook": {"max_chars": 4000}})
        assert cfg2.session_start_hook.max_chars == 4000
        assert cfg2.session_start_hook.debug is False
        assert cfg2.session_start_hook.ai_model is None

    def test_from_dict_nested_section_maps_through(self) -> None:
        # ai_models.claude / .codex are free-form dicts; they flow through.
        parsed = {
            "ai_models": {
                "claude": {"small": "haiku", "large": "sonnet"},
                "codex": {"small": "gpt-4o-mini", "large": "gpt-4o"},
            }
        }
        cfg = VaultAppConfig.from_dict(parsed)
        assert isinstance(cfg.ai_models, AIModelsConfig)
        assert cfg.ai_models.claude == {"small": "haiku", "large": "sonnet"}
        assert cfg.ai_models.codex == {"small": "gpt-4o-mini", "large": "gpt-4o"}

    def test_from_dict_unknown_section_ignored(self) -> None:
        # Unknown sections are validation's concern, not from_dict's.
        cfg = VaultAppConfig.from_dict(
            {"bogus": {"x": 1}, "git": {"auto_commit": True}}
        )
        assert cfg.git.auto_commit is True
        # No attribute for the unknown section.
        assert not hasattr(cfg, "bogus")

    def test_from_dict_non_dict_section_is_skipped(self) -> None:
        # A corrupt section (scalar where a mapping is expected) is left empty;
        # validate_config warns about it separately.
        cfg = VaultAppConfig.from_dict({"git": True})
        assert cfg.git == GitConfig()

    def test_from_dict_non_dict_input_returns_empty(self) -> None:
        cfg = VaultAppConfig.from_dict("not a dict")  # type: ignore[arg-type]
        assert cfg.ai == AIConfig()
        assert cfg.summarizer == SummarizerConfig()

    def test_from_dict_empty_dict_yields_default_config(self) -> None:
        cfg = VaultAppConfig.from_dict({})
        # No sections in the file -> every section at its empty default, so the
        # whole thing equals a freshly-constructed VaultAppConfig.
        assert cfg == VaultAppConfig()

    def test_load_typed_config_reads_same_data_as_load_config(
        self, tmp_vault: Path
    ) -> None:
        (tmp_vault / "config.yaml").write_text(
            "summarizer:\n  model: claude-x\n  dedup_threshold: 0.9\n"
            "embeddings:\n  top_k: 8\n",
            encoding="utf-8",
        )
        vault_config.clear_config_cache()
        vault_config._clear_typed_config_cache()

        typed = vault_config.load_typed_config()
        raw = vault_config.load_config()

        # The typed view and the dict view read the same underlying values.
        assert typed.summarizer.model == raw["summarizer"]["model"]
        assert typed.summarizer.dedup_threshold == raw["summarizer"]["dedup_threshold"]
        assert typed.embeddings.top_k == raw["embeddings"]["top_k"]

    def test_load_typed_config_returns_independent_instances(
        self, tmp_vault: Path
    ) -> None:
        # lru_cache shares the cached build; callers must not be able to mutate
        # it. load_typed_config deep-copies on return (mirrors load_config).
        (tmp_vault / "config.yaml").write_text(
            "ai:\n  backend: claude-cli\n", encoding="utf-8"
        )
        vault_config.clear_config_cache()
        vault_config._clear_typed_config_cache()

        first = vault_config.load_typed_config()
        first.ai.backend = "mutated"
        second = vault_config.load_typed_config()
        assert second.ai.backend == "claude-cli", (
            "load_typed_config returned a shared mutable instance -- "
            "caller mutation leaked across calls"
        )

    def test_section_dataclasses_are_reexported_from_vault_config(self) -> None:
        # The flat shim re-exports the section dataclasses so callers can use
        # them in type annotations without importing vault_schema directly.
        from vault_config import (  # noqa: F401
            AdaptersConfig,
            AdaptiveContextConfig,
            AnthropicEnvConfig,
            DefaultsConfig,
            EventLogConfig,
            GitConfig,
            ParsightConfig,
            PreCompactHookConfig,
            SearchConfig,
            SessionStartHookConfig,
            SessionStopHookConfig,
            SubagentStopHookConfig,
            VaultSectionConfig,
        )

        # Sanity: a couple of these are the same class object the typed view
        # produces, so isinstance checks against the re-exported names work.
        cfg = VaultAppConfig.from_dict({"codex_cli": {"timeout": 5}})
        assert isinstance(cfg.codex_cli, CodexCliConfig)
        # Absent sections are empty instances of their class (not None), so an
        # isinstance check still passes against the re-exported names.
        assert isinstance(cfg.summarizer, SummarizerConfig) is True
        assert isinstance(cfg.embeddings, EmbeddingsConfig) is True
