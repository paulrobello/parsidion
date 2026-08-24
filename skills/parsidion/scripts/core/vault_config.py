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
import re
import sys
from pathlib import Path
from typing import Any

from .vault_path import resolve_vault
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
    ParMemConfig,
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
    "load_config",
    "clamp_timeout",
    "config_key_sources",
    "load_typed_config",
    "_clear_typed_config_cache",
    "_load_config_cached",
    "_clear_config_cache",
    "get_config",
    # Embedding-score temporal decay (ARC-023: leaf location so vault_search
    # and parmem_backend can both import it top-level without forming a cycle)
    "apply_decay_score",
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
    "ParMemConfig",
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

_YAML_LIST_INLINE_RE = re.compile(r"^\[(.*)]\s*$")


def _split_list_items(text: str) -> list[str]:
    """Split a comma-separated list, respecting quoted strings.

    SEC-033(c): an escaped double quote (``\\"``) does not close the string —
    writers (vault_merge's frontmatter emitter) escape embedded quotes, and
    the split must not let one toggle the quote state and split mid-item.
    """
    items: list[str] = []
    current: list[str] = []
    in_quote: str | None = None

    for i, ch in enumerate(text):
        if in_quote:
            current.append(ch)
            if ch == in_quote:
                # The quote is escaped (does not close the string) only when
                # preceded by an odd run of backslashes.
                j = i - 1
                run = 0
                while j >= 0 and text[j] == "\\":
                    run += 1
                    j -= 1
                if run % 2 == 0:
                    in_quote = None
        elif ch in ('"', "'"):
            in_quote = ch
            current.append(ch)
        elif ch == ",":
            items.append("".join(current).strip())
            current = []
        else:
            current.append(ch)

    remaining = "".join(current).strip()
    if remaining:
        items.append(remaining)

    return items


def _parse_scalar(value: str) -> Any:
    """Parse a scalar YAML value into a Python type.

    Handles booleans, None/null, integers, floats, quoted strings, and bare
    strings. Date strings (YYYY-MM-DD) are kept as strings for simplicity.
    """
    # Strip surrounding quotes
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]

    lower = value.lower()
    if lower in ("true", "yes"):
        return True
    if lower in ("false", "no"):
        return False
    if lower in ("null", "~", ""):
        return None

    # Try integer
    try:
        return int(value)
    except ValueError:
        pass

    # Try float
    try:
        return float(value)
    except ValueError:
        pass

    return value


def _parse_list_item(value: str) -> str:
    """Parse a YAML list item, keeping it as a string.

    Unlike ``_parse_scalar``, list items are never coerced to bool/int/float:
    frontmatter list fields (``tags``, ``sources``, ``related``) are always
    string-valued, and coercing e.g. ``tags: [2026, python]`` to an int makes
    the tag silently unfindable downstream. Surrounding quotes are stripped.

    SEC-033(c): double-quoted items unescape ``\\"`` → ``"`` and ``\\\\`` →
    ``\\``, matching what the frontmatter emitters write.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        inner = value[1:-1]
        if value[0] == '"':
            out: list[str] = []
            i = 0
            while i < len(inner):
                if (
                    inner[i] == "\\"
                    and i + 1 < len(inner)
                    and inner[i + 1] in ('"', "\\")
                ):
                    out.append(inner[i + 1])
                    i += 2
                else:
                    out.append(inner[i])
                    i += 1
            return "".join(out)
        return inner
    return value


def _strip_inline_comment(value: str) -> str:
    """Strip a trailing ``# comment`` from a YAML value, respecting quotes."""
    in_quote: str | None = None
    for i, ch in enumerate(value):
        if in_quote:
            if ch == in_quote:
                in_quote = None
        elif ch in ('"', "'"):
            in_quote = ch
        elif ch == "#" and i > 0 and value[i - 1] in (" ", "\t"):
            return value[:i].rstrip()
    return value


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
        colon_idx = stripped.find(":")
        if colon_idx == -1:
            print(
                f"vault_config: ignoring unparsable config line: {stripped!r}",
                file=sys.stderr,
            )
            continue

        key = stripped[:colon_idx].strip()
        value_str = stripped[colon_idx + 1 :].strip()

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
                value_str = _strip_inline_comment(value_str)
                result[key] = _parse_scalar(value_str)
                current_section = None
                current_nested_key = None
                current_nested_indent = 0
                current_nested_leaf_indent = None
        elif current_section is not None and indent > 0:
            value_str = _strip_inline_comment(value_str)
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
                    nested[key] = _parse_scalar(value_str)
                continue

            if not value_str:
                section[key] = {}
                current_nested_key = key
                current_nested_indent = indent
                current_nested_leaf_indent = None
            else:
                section[key] = _parse_scalar(value_str)
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


@functools.lru_cache(maxsize=8)
def load_config(vault: Path | None = None) -> dict[str, Any]:
    """Load ``config.yaml`` from the vault, layered with ``config.local.yaml``.

    ``config.local.yaml`` is an optional gitignored overlay read from the
    same vault directory. When present, it is deep-merged over ``config.yaml``
    (section-by-section, with local values winning on conflict) so users can
    keep secrets in the local-only file while git-syncing a secret-free
    ``config.yaml``, or vice versa.

    Results are cached per-process via ``functools.lru_cache``. Both files
    are read within the same cached call, so the cache covers them jointly --
    call ``load_config.cache_clear()`` to invalidate the cache in tests (or
    after either file changes) when the vault path has been changed.

    ARC-034: returns a deep copy so a caller that mutates the returned dict
    (e.g. ``config["summarizer"]["model"] = "claude-..."``) cannot corrupt the
    cached values for the rest of the process. The cache cap was also raised
    from 1 to 8 so alternating vaults (multi-vault setups, tests with
    ``tmp_vault`` plus the real vault) stop evicting each other on every call.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().

    Returns an empty dict when both files are missing or unreadable.
    """
    if vault is None:
        vault = resolve_vault()

    config: dict[str, Any] = {}
    config_path = vault / "config.yaml"
    if config_path.is_file():
        try:
            content = config_path.read_text(encoding="utf-8")
            config = _parse_config_yaml(content)
        except (OSError, UnicodeDecodeError):
            config = {}

    local_path = vault / "config.local.yaml"
    if local_path.is_file():
        try:
            local_content = local_path.read_text(encoding="utf-8")
            local_config = _parse_config_yaml(local_content)
            config = _merge_config_dicts(config, local_config)
        except (OSError, UnicodeDecodeError):
            pass

    return _deep_copy_config(config)


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
    it can never disagree with a ``load_config.cache_clear()`` in tests.
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
_load_config_cached = load_config
_clear_config_cache = load_config.cache_clear


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


def get_config(section: str, key: str, default: Any = None) -> Any:
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

    Returns:
        The configured value (which may be ``None`` if explicitly set), the
        schema default, or *default*.
    """
    config = load_config()
    section_dict = config.get(section)
    if isinstance(section_dict, dict) and key in section_dict:
        return section_dict[key]

    cfg = load_typed_config()
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
    """Build the typed config from the parsed dict (compat name).

    ARC-007: the separate lru cache was removed. It could serve stale values
    after a caller cleared only ``load_config.cache_clear()`` (the common
    test/invalidation idiom), and it only saved the cheap dict-to-dataclass
    mapping -- the expensive work (file read + YAML parse) is cached inside
    :func:`load_config`. Kept as an alias because the name is re-exported.
    """
    return VaultAppConfig.from_dict(load_config(vault=vault))


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
    :func:`load_config` and ``from_dict`` builds a fresh tree per call, so
    invalidation is exactly ``load_config.cache_clear()``.

    Args:
        vault: Optional vault path. Defaults to :func:`resolve_vault`.

    Returns:
        A :class:`VaultAppConfig`. Absent or unreadable config files yield an
        instance whose sections hold only the schema field defaults.
    """
    return _load_typed_config_cached(vault)


# Expose cache management so tests/callers can invalidate the typed-config
# cache the same way they do ``load_config.cache_clear()`` (via the
# ``_clear_config_cache`` alias below). A named function -- rather than a
# ``.cache_clear`` attribute bolted onto the public wrapper -- keeps the API
# statically visible to type checkers.
def _clear_typed_config_cache() -> None:
    """Clear the typed-config cache (test helper; compat no-op since ARC-007).

    ``load_typed_config`` no longer keeps a separate cache — invalidation is
    ``load_config.cache_clear()``. Kept because tests call it beside
    ``load_config.cache_clear()``.
    """
    return None


# ---------------------------------------------------------------------------
# Embedding-score temporal decay
# ---------------------------------------------------------------------------
#
# ARC-023: this helper previously lived on vault_search.py as ``_apply_decay``
# and was lazy-imported by parmem_backend._decayed_score to avoid the
# vault_search ↔ parmem_backend top-level import cycle (vault_search imports
# parmem_backend at module top; parmem_backend needed vault_search._apply_decay).
# Moving it here — a true leaf module both files already depend on — lets both
# callers import it at module top level and drops the lazy import.


def apply_decay_score(score: float, mtime: float, now: float) -> float:
    """Apply exponential temporal decay to an embedding/RRF search score.

    Reads ``embeddings.decay_half_life_days`` (default 90) and
    ``embeddings.decay_min_factor`` (default 0.5) from config. The decay factor
    is ``min_factor + (1 - min_factor) * e^(-lambda * age_days)`` where
    ``lambda = ln(2) / half_life_days`` — so a note exactly ``half_life_days``
    old decays to the midpoint between 1.0 and ``min_factor``.

    Args:
        score: Raw similarity / RRF score.
        mtime: Note file modification time (Unix timestamp).
        now: Reference timestamp (typically ``time.time()``); pass 0.0 to skip
            decay (used when the caller has already decided decay is disabled).

    Returns:
        Decay-adjusted score. When ``mtime`` is 0/missing the score is returned
        unchanged (the original code path that gated on ``if mtime``).
    """
    half_life: float = get_config("embeddings", "decay_half_life_days", 90.0)
    min_factor: float = get_config("embeddings", "decay_min_factor", 0.5)
    if not mtime:
        return score
    age_days = max(0.0, (now - mtime) / 86400.0)
    lam = math.log(2) / half_life
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


def validate_config() -> list[str]:
    """Validate config.yaml against the known schema.

    Checks for unknown sections, unknown keys within known sections, and
    type mismatches. Warnings are informational -- never raises.

    Returns:
        A list of warning strings (empty when config is valid or absent).
    """
    config = load_config()
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

    return warnings
