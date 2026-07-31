"""vault_path -- compatibility shim (ARC-004).

Implementation moved to ``core.vault_path``. This shim re-exports the
module's complete non-dunder surface so every existing caller --
``import vault_path``, ``from vault_path import X``, ``vault_path.X`` (hooks, CLIs,
tests, parsidion-mcp, the installer) -- keeps working unchanged,
including imported constants and test monkeypatch targets. The
stdlib-only constraint is enforced on ``core.vault_path`` by
``tests/test_stdlib_only.py``.
"""

from core.vault_path import (  # noqa: F401 -- full-surface re-export
    DEFAULT_VAULT_NAME,
    EMBEDDINGS_DB_FILENAME,
    EXCLUDE_DIRS,
    LEGACY_DEFAULT_VAULT_NAME,
    Path,
    SCRIPTS_DIR,
    TEMPLATES_DIR,
    VAULT_DIRS,
    VAULT_ROOT,
    VaultConfigError,
    _HOOK_ERROR_LOG_MAX_LINES,
    _VAULT_FORBIDDEN_PREFIXES,
    _resolve_vault_cached,
    _resolve_vault_reference,
    _validate_vault_path,
    annotations,
    default_vault_root,
    functools,
    get_embeddings_db_path,
    get_vaults_config_path,
    is_path_inside_vault,
    is_symlink_inside_vault,
    list_named_vaults,
    os,
    resolve_templates_dir,
    resolve_vault,
    rotate_log_file,
    secure_log_dir,
    sys,
)
