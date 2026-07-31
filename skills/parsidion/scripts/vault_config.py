"""vault_config -- compatibility shim (ARC-004).

Implementation moved to ``core.vault_config``. This shim re-exports the
module's complete non-dunder surface so every existing caller --
``import vault_config``, ``from vault_config import X``, ``vault_config.X`` (hooks, CLIs,
tests, parsidion-mcp, the installer) -- keeps working unchanged,
including imported constants and test monkeypatch targets. The
stdlib-only constraint is enforced on ``core.vault_config`` by
``tests/test_stdlib_only.py``.
"""

from core.vault_config import (  # noqa: F401 -- full-surface re-export
    Any,
    Path,
    _CONFIG_SCHEMA,
    _YAML_LIST_INLINE_RE,
    _clear_config_cache,
    _deep_copy_config,
    _load_config_cached,
    _merge_config_dicts,
    _parse_config_yaml,
    _parse_list_item,
    _parse_scalar,
    _split_list_items,
    _strip_inline_comment,
    annotations,
    apply_decay_score,
    functools,
    get_config,
    load_config,
    math,
    re,
    resolve_vault,
    sys,
    validate_config,
)
