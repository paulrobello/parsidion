"""Configuration loading, parsing, and validation for the Parsidion vault.

Provides YAML parsing utilities (stdlib-only, no pyyaml), config file loading
from the vault root, and schema-based validation.

This module is part of the vault_common split (ARC-005).  All public symbols
are re-exported from ``vault_common`` for backward compatibility.
"""

from __future__ import annotations

import dataclasses
import functools
import math
import sys
from pathlib import Path
from typing import Any

from .vault_path import resolve_vault
from .yaml_lite import (
    parse_list_item,
    parse_scalar,
    split_key_value,
    split_list_items,
    strip_inline_comment,
)
from .vault_schema import (
    AdaptersConfig,
    AdaptiveContextConfig,
    AIConfig,
    AIModelsConfig,
    AnthropicEnvConfig,
    CodexCliConfig,
    DefaultsConfig,
    EmbeddingsConfig,
    EventLogConfig,
    GitConfig,
    ParsightConfig,
    PreCompactHookConfig,
    SearchConfig,
    SessionStartHookConfig,
    SessionStopHookConfig,
    SubagentStopHookConfig,
    SummarizerConfig,
    VaultAppConfig,
    VaultSectionConfig,
    schema_dict,
)

__all__: list[str] = [
    # YAML parsing helpers (also used by vault_index for frontmatter)
    "_parse_scalar",
    "_parse_list_item",
    "_split_list_items",
    "_strip_inline_comment",
    # Config loading
    "_parse_config_yaml",
    "_merge_config_dicts",
    "_apply_legacy_aliases",
    "load_config",
    "clear_config_cache",
    "clamp_timeout",
    "config_key_sources",
    "load_typed_config",
    "_clear_typed_config_cache",
    "_load_config_cached",
    "_clear_config_cache",
    "get_config",
    # Embedding-score temporal decay (ARC-023: leaf location so vault_search
    # and parsight_backend can both import it top-level without forming a cycle)
    "apply_decay_score",
    "resolve_decay_params",
    # Config validation
    "validate_config",
    "_CONFIG_SCHEMA",
    # ENH-014: typed config access (single source of truth lives in
    # vault_schema; re-exported here so callers can use ``from vault_config
    # import VaultAppConfig`` and the per-section dataclasses for annotations).
    "VaultAppConfig",
    "schema_dict",
    "AIConfig",
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
]

# ---------------------------------------------------------------------------
# Low-level YAML parsing helpers
# ---------------------------------------------------------------------------
# ENH-024: the implementations moved to core/yaml_lite, the shared YAML
# subset module (one set of quoting/comment/array rules for config.yaml,
# note frontmatter, and vaults.yaml). The private names remain as aliases
# because vault_common re-exports them for backward compatibility; the
# docstrings and tests live on the yaml_lite functions.

_parse_scalar = parse_scalar
_parse_list_item = parse_list_item
_split_list_items = split_list_items
_strip_inline_comment = strip_inline_comment


# ---------------------------------------------------------------------------
# Config file parser
# ---------------------------------------------------------------------------


def _parse_config_yaml(text: str) -> dict[str, Any]:
    """Parse a simple YAML config with limited nesting.

    Handles top-level scalars, section dicts, and one additional mapping level
    for config sections such as ``ai_models.codex.small``::

        top_key: value
        section:
          nested_key: value
          nested_section:
            leaf_key: value
    """
    result: dict[str, Any] = {}
    current_section: str | None = None
    current_nested_key: str | None = None
    current_nested_indent = 0
    # Indent of leaf keys inside the current nested dict; anything indented
    # deeper is a 3rd nesting level, which this parser does not support.
    current_nested_leaf_indent: int | None = None

    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        key_value = split_key_value(stripped)
        if key_value is None:
            print(
                f"vault_config: ignoring unparsable config line: {stripped!r}",
                file=sys.stderr,
            )
            continue

        key, value_str = key_value

        if not key:
            print(
                f"vault_config: ignoring config line with empty key: {stripped!r}",
                file=sys.stderr,
            )
            continue

        if indent == 0:
            if not value_str:
                # Section header -- start collecting nested keys
                current_section = key
                current_nested_key = None
                current_nested_indent = 0
                current_nested_leaf_indent = None
                result[key] = {}
            else:
                value_str = strip_inline_comment(value_str)
                result[key] = parse_scalar(value_str)
                current_section = None
                current_nested_key = None
                current_nested_indent = 0
                current_nested_leaf_indent = None
        elif current_section is not None and indent > 0:
            value_str = strip_inline_comment(value_str)
            section = result.get(current_section)
            if not isinstance(section, dict):
                continue

            if (
                current_nested_key is not None
                and indent > current_nested_indent
                and isinstance(section.get(current_nested_key), dict)
            ):
                nested = section[current_nested_key]
                if isinstance(nested, dict):
                    if current_nested_leaf_indent is None:
                        current_nested_leaf_indent = indent
                    if not value_str or indent > current_nested_leaf_indent:
                        # A 3rd nesting level (a sub-section header inside a
                        # nested dict, or a key indented deeper than the
                        # nested dict's leaf keys) would silently flatten
                        # into the 2nd-level dict -- skip it visibly instead.
                        print(
                            "vault_config: config nesting deeper than 2 levels "
                            f"is not supported; key '{key}' (line {lineno}) ignored",
                            file=sys.stderr,
                        )
                        continue
                    nested[key] = parse_scalar(value_str)
                continue

            if not value_str:
                section[key] = {}
                current_nested_key = key
                current_nested_indent = indent
                current_nested_leaf_indent = None
            else:
                section[key] = parse_scalar(value_str)
                current_nested_key = None
                current_nested_indent = 0
                current_nested_leaf_indent = None
        elif indent > 0:
            # Indented line outside any section -- likely a typo
            print(
                f"vault_config: ignoring indented config line outside any section: {stripped!r}",
                file=sys.stderr,
            )

    return result


# ---------------------------------------------------------------------------
# Config loading (cached)
# ---------------------------------------------------------------------------


def _merge_config_dicts(
    base: dict[str, Any], overlay: dict[str, Any]
) -> dict[str, Any]:
    """Deep-merge *overlay* over *base*, with *overlay* winning on conflict.

    Dict values present in both are merged recursively key-by-key (matching
    the parser's supported nesting depth, e.g. ``ai_models.codex``). A dict
    value present only in *overlay* is added as a new section. Any other
    value type -- including top-level scalars -- is replaced outright by the
    *overlay* value.
    """
    merged = dict(base)
    for key, overlay_value in overlay.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(overlay_value, dict):
            merged[key] = _merge_config_dicts(base_value, overlay_value)
        else:
            merged[key] = overlay_value
    return merged


# Legacy config aliases for deployments that predate the parsight rename
# (the backend product was formerly "par-mem"). ``_apply_legacy_aliases``
# folds these into their canonical spellings at load time so every consumer
# (get_config, load_typed_config, validate_config) sees canonical names.
_LEGACY_SECTION_ALIASES: dict[str, str] = {"par_mem": "parsight"}
_LEGACY_ENUM_ALIASES: dict[tuple[str, str], dict[str, str]] = {
    ("search", "backend"): {"par-mem": "parsight"},
}


def _apply_legacy_aliases(config: dict[str, Any]) -> dict[str, Any]:
    """Fold legacy section/enum spellings into their canonical names.

    Compat for configs that predate the parsight rename (the backend product
    was formerly "par-mem"): the legacy ``par_mem`` section is merged under
    ``parsight`` (the canonical section wins per key when both are present),
    and the legacy ``par-mem`` value of ``search.backend`` is rewritten to
    ``parsight``. Applied to each file's parse *before* the
    ``config.local.yaml`` merge so overlay precedence (local wins) is
    preserved across the alias. Mutates and returns *config*.
    """
    for old_name, new_name in _LEGACY_SECTION_ALIASES.items():
        old_section = config.get(old_name)
        if not isinstance(old_section, dict):
            continue
        new_section = config.get(new_name)
        if isinstance(new_section, dict):
            merged = dict(old_section)
            merged.update(new_section)  # canonical section wins per key
            config[new_name] = merged
        else:
            config[new_name] = dict(old_section)
        del config[old_name]
    for (section, key), aliases in _LEGACY_ENUM_ALIASES.items():
        section_dict = config.get(section)
        if isinstance(section_dict, dict):
            value = section_dict.get(key)
            if isinstance(value, str) and value in aliases:
                section_dict[key] = aliases[value]
    return config


@functools.lru_cache(maxsize=8)
def _load_config_cached(vault: Path | None = None) -> dict[str, Any]:
    """Read and merge the config files once per vault (QA-101 cache layer).

    Returns the *shared* cached parsed dict. Callers must treat it as
    read-only -- the public :func:`load_config` wrapper hands each caller
    a deep copy of this value. The cache cap is 8 so alternating vaults
    (multi-vault setups, tests with ``tmp_vault`` plus the real vault) stop
    evicting each other on every call.

    ``config.local.yaml`` is an optional gitignored overlay read from the
    same vault directory. When present, it is deep-merged over ``config.yaml``
    (section-by-section, with local values winning on conflict) so users can
    keep secrets in the local-only file while git-syncing a secret-free
    ``config.yaml``, or vice versa. Legacy section/enum spellings are
    normalized per file before the merge (see :func:`_apply_legacy_aliases`).

    Both files are read within the same cached call, so the cache covers
    them jointly -- call :func:`clear_config_cache` to invalidate it (in
    tests, or after either file changes).

    Args:
        vault: Optional vault path. Defaults to resolve_vault().

    Returns an empty dict when both files are missing or unreadable.
    """
    if vault is None:
        vault = resolve_vault()
    else:
        vault = Path(vault)
    config: dict[str, Any] = {}
    config_path = vault / "config.yaml"
    if config_path.is_file():
        try:
            content = config_path.read_text(encoding="utf-8")
            config = _apply_legacy_aliases(_parse_config_yaml(content))
        except (OSError, UnicodeDecodeError):
            config = {}

    local_path = vault / "config.local.yaml"
    if local_path.is_file():
        try:
            local_content = local_path.read_text(encoding="utf-8")
            local_config = _apply_legacy_aliases(_parse_config_yaml(local_content))
            config = _merge_config_dicts(config, local_config)
        except (OSError, UnicodeDecodeError):
            pass

    return config


def config_key_sources(vault: Path | None = None) -> dict[tuple[str, str], str]:
    """Map each first-level ``section.key`` to the config file it came from.

    SEC-007: callers must distinguish values that originate in the
    git-synced ``config.yaml`` from those in the machine-local
    ``config.local.yaml`` before honoring them (e.g. network-affecting
    ``anthropic_env`` keys). Values are ``"config.yaml"`` /
    ``"config.local.yaml"``; keys absent from both files are absent from
    the map. On conflict ``config.local.yaml`` wins, mirroring
    :func:`load_config`'s merge order.

    Deliberately uncached: it re-reads the two small files on each call so
    it can never disagree with a :func:`clear_config_cache` call in tests.
    """
    if vault is None:
        vault = resolve_vault()

    sources: dict[tuple[str, str], str] = {}
    for file_name in ("config.yaml", "config.local.yaml"):
        path = vault / file_name
        if not path.is_file():
            continue
        try:
            data = _parse_config_yaml(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        for section, values in data.items():
            if not isinstance(values, dict):
                continue
            for key in values:
                sources[(str(section), str(key))] = file_name
    return sources


def _deep_copy_config(obj: Any) -> Any:
    """Return a recursive copy of a config-derived value.

    Mirrors the shape ``_parse_config_yaml`` can produce: dicts at up to two
    nested levels (section -> nested_section -> scalar), lists only inside
    ``related``/``tags`` frontmatter (not in config), and scalars (str/int/
    float/bool/None). Handled without ``copy.deepcopy`` so the cost is
    proportional to the (small) config tree, not a generic Python object walk.
    """
    if isinstance(obj, dict):
        return {k: _deep_copy_config(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deep_copy_config(v) for v in obj]
    return obj


# QA-015: Keep backward-compatible aliases for callers that used the old names.
# QA-101: ``_load_config_cached`` is now the real cached function (defined
# above, returning the shared parsed dict); ``_clear_config_cache`` is bound
# to the public invalidation helper just below. Use :func:`load_config` /
# :func:`clear_config_cache` in new code.


def load_config(vault: Path | None = None) -> dict[str, Any]:
    """Load ``config.yaml`` from the vault, layered with ``config.local.yaml``.

    Public wrapper over :func:`_load_config_cached`: the file read + parse is
    cached per-process (keyed on the vault path), and every call returns a
    fresh deep copy of the cached dict.

    ARC-034 / QA-101: a caller that mutates the returned dict (e.g.
    ``config["summarizer"]["model"] = "claude-..."``) cannot corrupt the
    values seen by later callers -- the deep copy is made per call, outside
    the cache. (Previously the copy happened inside the cached function, so
    ``functools.lru_cache`` handed every caller the same copy object and the
    documented isolation did not hold.)

    Args:
        vault: Optional vault path. Defaults to resolve_vault().

    Returns an empty dict when both files are missing or unreadable.
    """
    return _deep_copy_config(_load_config_cached(vault))


def clear_config_cache() -> None:
    """Clear the per-process config cache (QA-101 public invalidation helper).

    Invalidates the :func:`_load_config_cached` cache so the next
    :func:`load_config` / :func:`load_typed_config` / :func:`get_config`
    call re-reads the files from disk. Tests (and tools that just rewrote
    a config file) call this instead of reaching into ``lru_cache``
    internals.
    """
    _load_config_cached.cache_clear()


_clear_config_cache = clear_config_cache


def clamp_timeout(
    value: int | float | None,
    default: int | float,
    lo: int | float = 1,
    hi: int | float = 3600,
) -> int | float:
    """Coerce a configured timeout into ``[lo, hi]`` (SEC-024).

    Config-sourced timeouts previously reached ``subprocess.run(timeout=...)``
    unvalidated: ``float('nan')`` and negatives raise at call time, and
    ``inf`` silently means "no timeout at all". Non-finite, non-positive, or
    non-numeric input (including ``True``/``False``, which ``isinstance``
    would otherwise treat as ``int``) falls back to *default*.

    Args:
        value: Raw configured value.
        default: Fallback for invalid input.
        lo: Lower bound (inclusive) for valid values.
        hi: Upper bound (inclusive) for valid values.

    Returns:
        The clamped timeout as int or float.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if not math.isfinite(float(value)) or value <= 0:
        return default
    return min(max(value, lo), hi)


def get_config(
    section: str, key: str, default: Any = None, vault: Path | None = None
) -> Any:
    """Look up a config value with fallback to *default*.

    ARC-007 adapter over the typed schema. Resolution order:

    1. Key present in the parsed config dict (config.yaml layered with
       config.local.yaml) → that value, unchanged. An explicit ``null`` thus
       returns ``None``, so users can disable optional features with e.g.
       ``ai_model: null``.
    2. Key absent but the schema declares a real default for it
       (:data:`vault_schema` field default) → the schema default.
    3. Otherwise → *default*.

    Args:
        section: Top-level section name (e.g. ``"session_start_hook"``).
        key: Key within the section (e.g. ``"max_chars"``).
        default: Value returned when the key is absent and the schema
            declares no default for it.
        vault: Optional vault path so multi-vault callers read the config of
            the vault they are operating on (ARC-101). Defaults to
            resolve_vault() — hook-path callers must pass the vault resolved
            from the hook payload instead of relying on the process cwd.

    Returns:
        The configured value (which may be ``None`` if explicitly set), the
        schema default, or *default*.
    """
    config = load_config(vault=vault)
    section_dict = config.get(section)
    if isinstance(section_dict, dict) and key in section_dict:
        return section_dict[key]

    cfg = load_typed_config(vault=vault)
    section_obj = getattr(cfg, section, None)
    if section_obj is not None:
        for field in dataclasses.fields(section_obj):
            if field.name != key:
                continue
            if field.default is not dataclasses.MISSING and field.default is not None:
                return field.default
            if field.default_factory is not dataclasses.MISSING:  # pragma: no cover
                return field.default_factory()
            break
    return default


def _load_typed_config_cached(vault: Path | None = None) -> VaultAppConfig:
    """Build the typed config from the cached parsed dict (compat name).

    ARC-007: the separate lru cache was removed. It could serve stale values
    after a caller cleared only the parse cache (the common test/invalidation
    idiom), and it only saved the cheap dict-to-dataclass mapping -- the
    expensive work (file read + YAML parse) is cached inside
    :func:`_load_config_cached`. Kept as an alias because the name is
    re-exported.

    QA-101: builds directly from the shared cached dict without a deep copy.
    This is safe because ``VaultAppConfig.from_dict`` assigns only the
    parsed dict's scalar leaf values onto the section dataclasses (the
    config parser produces no lists), so the dataclass tree never aliases
    a mutable leaf of the cached dict.
    """
    return VaultAppConfig.from_dict(_load_config_cached(vault))


def load_typed_config(vault: Path | None = None) -> VaultAppConfig:
    """Load the vault config as a typed :class:`VaultAppConfig`.

    Reads the same parsed dict as :func:`load_config` (config.yaml layered
    with config.local.yaml) and maps it onto the section dataclasses defined
    in :mod:`vault_schema` -- the single source of truth for section/key names
    and allowed types. Values pass through unchanged; absent keys are ``None``
    on the section dataclass. No coercion, no validation side effects:
    :func:`validate_config` remains the sole source of warnings.

    Additive to the dict-returning :func:`load_config`; callers that prefer
    typed attribute access (``cfg.summarizer.model``) can use this instead.
    ARC-007: no separate cache -- the parse is cached inside
    :func:`_load_config_cached` and ``from_dict`` builds a fresh tree per
    call, so invalidation is exactly :func:`clear_config_cache`.

    Args:
        vault: Optional vault path. Defaults to :func:`resolve_vault`.

    Returns:
        A :class:`VaultAppConfig`. Absent or unreadable config files yield an
        instance whose sections hold only the schema field defaults.
    """
    return _load_typed_config_cached(vault)


# Expose cache management so tests/callers can invalidate the typed-config
# cache the same way they do :func:`clear_config_cache` (via the
# ``_clear_config_cache`` alias above). A named function -- rather than a
# ``.cache_clear`` attribute bolted onto the public wrapper -- keeps the API
# statically visible to type checkers.
def _clear_typed_config_cache() -> None:
    """Clear the typed-config cache (test helper; compat no-op since ARC-007).

    ``load_typed_config`` no longer keeps a separate cache — invalidation is
    :func:`clear_config_cache`. Kept because tests call it beside it.
    """
    return None


# ---------------------------------------------------------------------------
# Embedding-score temporal decay
# ---------------------------------------------------------------------------
#
# ARC-023: this helper previously lived on vault_search.py as ``_apply_decay``
# and was lazy-imported by parsight_backend._decayed_score to avoid the
# vault_search ↔ parsight_backend top-level import cycle (vault_search imports
# parsight_backend at module top; parsight_backend needed vault_search._apply_decay).
# Moving it here — a true leaf module both files already depend on — lets both
# callers import it at module top level and drops the lazy import.


def resolve_decay_params(vault: Path | None = None) -> tuple[float, float]:
    """Resolve ``(decay_half_life_days, decay_min_factor)`` for batch scoring.

    PRF-103: the scoring loops in both search backends call this once per
    search instead of paying two :func:`get_config` reads per scored row
    (each of which rebuilds/copies the config tree). Non-numeric configured
    values fall back to the schema defaults so a malformed config value
    degrades to default decay instead of failing the whole search.

    Args:
        vault: Optional vault path so multi-vault callers read the config of
            the vault they are operating on (ARC-101).

    Returns:
        ``(half_life_days, min_factor)`` as floats.
    """
    embeddings = load_typed_config(vault=vault).embeddings
    half_life = embeddings.decay_half_life_days
    if isinstance(half_life, bool) or not isinstance(half_life, (int, float)):
        half_life = 90.0
    min_factor = embeddings.decay_min_factor
    if isinstance(min_factor, bool) or not isinstance(min_factor, (int, float)):
        min_factor = 0.5
    return float(half_life), float(min_factor)


def apply_decay_score(
    score: float,
    mtime: float,
    now: float,
    *,
    half_life_days: float | None = None,
    min_factor: float | None = None,
    vault: Path | None = None,
) -> float:
    """Apply exponential temporal decay to an embedding/RRF search score.

    The decay factor is ``min_factor + (1 - min_factor) * e^(-lambda *
    age_days)`` where ``lambda = ln(2) / half_life_days`` — so a note exactly
    ``half_life_days`` old decays to the midpoint between 1.0 and
    ``min_factor``.

    PRF-103: batch callers pass *half_life_days* and *min_factor* explicitly
    (resolved once per search via :func:`resolve_decay_params`) so scored rows
    never trigger config reads. When omitted, both are read from config
    (``embeddings.decay_half_life_days``, default 90, and
    ``embeddings.decay_min_factor``, default 0.5) — the pre-PRF-103 behavior
    for non-hot callers.

    Args:
        score: Raw similarity / RRF score.
        mtime: Note file modification time (Unix timestamp).
        now: Reference timestamp (typically ``time.time()``); pass 0.0 to skip
            decay (used when the caller has already decided decay is disabled).
        half_life_days: Decay half-life in days; None reads it from config.
        min_factor: Floor multiplier for very old notes; None reads it from
            config.
        vault: Optional vault path used only when reading config because the
            decay parameters were omitted.

    Returns:
        Decay-adjusted score. When ``mtime`` is 0/missing the score is returned
        unchanged (the original code path that gated on ``if mtime``).
    """
    if half_life_days is None or min_factor is None:
        half_life_days, min_factor = resolve_decay_params(vault)
    if not mtime:
        return score
    age_days = max(0.0, (now - mtime) / 86400.0)
    lam = math.log(2) / half_life_days
    decay = min_factor + (1.0 - min_factor) * math.exp(-lam * age_days)
    return score * decay


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

# Schema: section -> key -> expected Python type(s).
# ENH-014: derived from the dataclass annotations in vault_schema (the single
# source of truth) rather than hand-maintained here. ``schema_dict()`` returns
# the same structure -- section names, key names, and allowed-type tuples
# (including ``type(None)`` where the annotation is ``X | None``) -- that the
# literal below previously encoded, so ``validate_config`` and its ``__all__``
# export are unchanged.
_CONFIG_SCHEMA: dict[str, dict[str, tuple[type, ...]]] = schema_dict()


def validate_config(vault: Path | None = None) -> list[str]:
    """Validate config.yaml against the known schema.

    Checks for unknown sections, unknown keys within known sections, and
    type mismatches. Warnings are informational -- never raises.

    Args:
        vault: Optional vault path so multi-vault callers validate the config
            of the vault they are operating on (ARC-101). Defaults to
            resolve_vault().

    Returns:
        A list of warning strings (empty when config is valid or absent).
    """
    config = load_config(vault=vault)
    if not config:
        return []

    warnings: list[str] = []
    known_sections = set(_CONFIG_SCHEMA.keys())

    for section, section_value in config.items():
        if section not in known_sections:
            warnings.append(f"config.yaml: unknown section '{section}'")
            continue

        if not isinstance(section_value, dict):
            warnings.append(
                f"config.yaml: section '{section}' should be a mapping, got {type(section_value).__name__}"
            )
            continue

        schema_keys = _CONFIG_SCHEMA[section]
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

    # ENH-018: per-hook transcript tail keys are deprecated for one
    # release in favour of the unified `transcripts` section. They still
    # override it; the warning nudges migration. A key sitting at its code
    # default (as the shipped template shows them) is inert, so only
    # non-default values warn.
    _DEPRECATED_TRANSCRIPT_KEYS = (
        ("session_stop_hook", "transcript_tail_bytes", "transcripts.tail_bytes"),
        ("session_stop_hook", "transcript_tail_lines", "transcripts.tail_lines"),
        ("subagent_stop_hook", "transcript_tail_bytes", "transcripts.tail_bytes"),
        ("pre_compact_hook", "transcript_tail_bytes", "transcripts.tail_bytes"),
        ("summarizer", "transcript_tail_bytes", "transcripts.tail_bytes"),
        ("summarizer", "transcript_tail_lines", "transcripts.tail_lines"),
    )

    _typed_defaults = VaultAppConfig()
    for section, key, replacement in _DEPRECATED_TRANSCRIPT_KEYS:
        value = config.get(section, {}).get(key)
        if value is None:
            continue
        default = getattr(getattr(_typed_defaults, section, None), key, None)
        if default is not None and value == default:
            continue
        warnings.append(
            f"config.yaml: '{section}.{key}' is deprecated; "
            f"set {replacement} instead (the per-hook key still overrides "
            f"it for one release)"
        )

    return warnings
