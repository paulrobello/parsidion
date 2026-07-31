"""Configuration loading, parsing, and validation for the Parsidion vault.

Provides YAML parsing utilities (stdlib-only, no pyyaml), config file loading
from the vault root, and schema-based validation.

This module is part of the vault_common split (ARC-005).  All public symbols
are re-exported from ``vault_common`` for backward compatibility.
"""

from __future__ import annotations

import functools
import math
import re
import sys
from pathlib import Path
from typing import Any

from .vault_path import resolve_vault

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
    "_load_config_cached",
    "_clear_config_cache",
    "get_config",
    # Embedding-score temporal decay (ARC-023: leaf location so vault_search
    # and parmem_backend can both import it top-level without forming a cycle)
    "apply_decay_score",
    # Config validation
    "validate_config",
    "_CONFIG_SCHEMA",
]

# ---------------------------------------------------------------------------
# Low-level YAML parsing helpers
# ---------------------------------------------------------------------------

_YAML_LIST_INLINE_RE = re.compile(r"^\[(.*)]\s*$")


def _split_list_items(text: str) -> list[str]:
    """Split a comma-separated list, respecting quoted strings."""
    items: list[str] = []
    current: list[str] = []
    in_quote: str | None = None

    for ch in text:
        if in_quote:
            current.append(ch)
            if ch == in_quote:
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
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
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


def get_config(section: str, key: str, default: Any = None) -> Any:
    """Look up a config value with fallback to *default*.

    Distinguishes between a key that is absent (returns *default*) and a key
    that is explicitly set to ``null`` in config.yaml (returns ``None``).  This
    allows users to disable optional features by setting e.g. ``ai_model: null``.

    Args:
        section: Top-level section name (e.g. ``"session_start_hook"``).
        key: Key within the section (e.g. ``"max_chars"``).
        default: Value returned when the key is absent from the config file.

    Returns:
        The configured value (which may be ``None`` if explicitly set), or
        *default* when the key is absent.
    """
    config = load_config()
    section_dict = config.get(section)
    if isinstance(section_dict, dict):
        if key in section_dict:
            return section_dict[key]
    return default


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

# Schema: section -> key -> expected Python type(s)
_CONFIG_SCHEMA: dict[str, dict[str, tuple[type, ...]]] = {
    "ai": {
        "backend": (str,),
    },
    "ai_models": {
        "claude": (dict,),
        "codex": (dict,),
    },
    "codex_cli": {
        "command": (str,),
        "timeout": (int, float),
        "sandbox": (str, type(None)),
        "ephemeral": (bool,),
        "skip_git_repo_check": (bool,),
        "suppress_notify": (bool,),
        "allow_danger_full_access": (bool,),
    },
    "session_start_hook": {
        "ai_model": (str, type(None)),
        "ai_cooldown_seconds": (int, float),
        "ai_single_flight": (bool,),
        "max_chars": (int,),
        "ai_timeout": (int, float),
        "recent_days": (int,),
        "debug": (bool,),
        "verbose_mode": (bool,),
        "use_embeddings": (bool,),
        "track_delta": (bool,),
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
    "pre_compact_hook": {
        "lines": (int,),
        "transcript_tail_bytes": (int,),
    },
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
        "service_enabled": (bool,),  # ENH-003: opt-in persistent embedding service
        "service_idle_exit": (int,),  # ENH-003: daemon idle-exit seconds
    },
    "par_mem": {
        "enabled": (bool,),
        "binary": (str,),
        "timeout_s": (int, float),
    },
    "search": {
        "backend": (str,),
        "use_note_index": (bool,),
    },
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
    "git": {
        "auto_commit": (bool,),
    },
    "defaults": {
        "haiku_model": (str,),
    },
    "event_log": {
        "enabled": (bool,),
        "max_lines": (int,),
        "path": (str, type(None)),
    },
    "adaptive_context": {
        "enabled": (bool,),
        "decay_days": (int, float),
    },
    "vault": {
        "username": (str,),
    },
    "adapters": {
        "load_external": (bool,),  # ENH-006: opt-in ~/.config/parsidion/adapters/*.py
    },
}


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
