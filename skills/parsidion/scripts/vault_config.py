"""Configuration loading, parsing, and validation for the Parsidion vault.

Provides YAML parsing utilities (stdlib-only, no pyyaml), config file loading
from the vault root, and schema-based validation.

This module is part of the vault_common split (ARC-005).  All public symbols
are re-exported from ``vault_common`` for backward compatibility.
"""

from __future__ import annotations

import functools
import re
import sys
from pathlib import Path
from typing import Any

__all__: list[str] = [
    # YAML parsing helpers (also used by vault_index for frontmatter)
    "_parse_scalar",
    "_split_list_items",
    "_strip_inline_comment",
    # Config loading
    "_parse_config_yaml",
    "load_config",
    "_load_config_cached",
    "_clear_config_cache",
    "get_config",
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

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        colon_idx = stripped.find(":")
        if colon_idx == -1:
            print(
                f"vault_common: ignoring unparsable config line: {stripped!r}",
                file=sys.stderr,
            )
            continue

        key = stripped[:colon_idx].strip()
        value_str = stripped[colon_idx + 1 :].strip()

        if not key:
            print(
                f"vault_common: ignoring config line with empty key: {stripped!r}",
                file=sys.stderr,
            )
            continue

        if indent == 0:
            if not value_str:
                # Section header -- start collecting nested keys
                current_section = key
                current_nested_key = None
                current_nested_indent = 0
                result[key] = {}
            else:
                value_str = _strip_inline_comment(value_str)
                result[key] = _parse_scalar(value_str)
                current_section = None
                current_nested_key = None
                current_nested_indent = 0
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
                    nested[key] = _parse_scalar(value_str)
                continue

            if not value_str:
                section[key] = {}
                current_nested_key = key
                current_nested_indent = indent
            else:
                section[key] = _parse_scalar(value_str)
                current_nested_key = None
                current_nested_indent = 0
        elif indent > 0:
            # Indented line outside any section -- likely a typo
            print(
                f"vault_common: ignoring indented config line outside any section: {stripped!r}",
                file=sys.stderr,
            )

    return result


# ---------------------------------------------------------------------------
# Config loading (cached)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def load_config(vault: Path | None = None) -> dict[str, Any]:
    """Load ``config.yaml`` from the vault.

    Results are cached per-process via ``functools.lru_cache``.  Call
    ``load_config.cache_clear()`` to invalidate the cache in tests when
    the vault path has been changed.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().

    Returns an empty dict when the file is missing or unreadable.
    """
    if vault is None:
        # Lazy import to avoid circular dependency with vault_path
        from vault_path import resolve_vault

        vault = resolve_vault()
    config_path = vault / "config.yaml"
    if not config_path.is_file():
        return {}

    try:
        content = config_path.read_text(encoding="utf-8")
        return _parse_config_yaml(content)
    except (OSError, UnicodeDecodeError):
        return {}


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
    },
    "session_stop_hook": {
        "ai_model": (str, type(None)),
        "ai_timeout": (int, float),
        "auto_summarize": (bool,),
        "auto_summarize_after": (int, type(None)),
    },
    "subagent_stop_hook": {
        "enabled": (bool,),
        "min_messages": (int,),
        "excluded_agents": (str,),
    },
    "pre_compact_hook": {
        "lines": (int,),
    },
    "summarizer": {
        "model": (str,),
        "max_parallel": (int,),
        "transcript_tail_lines": (int,),
        "max_cleaned_chars": (int,),
        "persist": (bool,),
        "cluster_model": (str,),
        "dedup_threshold": (float, int),
    },
    "embeddings": {
        "enabled": (bool,),
        "model": (str,),
        "min_score": (float, int),
        "top_k": (int,),
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
        "sonnet_model": (str,),
    },
    "event_log": {
        "enabled": (bool,),
        "max_lines": (int,),
    },
    "adaptive_context": {
        "enabled": (bool,),
        "decay_days": (int, float),
    },
    "vault": {
        "username": (str,),
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
