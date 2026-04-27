"""Shared utility library for the Parsidion vault knowledge management system.

Provides functions for parsing frontmatter, searching notes, building context
blocks, and managing the vault directory structure. Uses only Python stdlib.

ARC-005: This module is now a thin re-export facade.  The implementation has
been split into focused sub-modules:

- ``vault_config`` -- config loading, parsing, validation
- ``vault_path`` -- path resolution, vault constants, secure logging
- ``vault_fs`` -- file locking, pending queue, git, daily notes
- ``vault_index`` -- frontmatter parsing, note search, context building
- ``vault_hooks`` -- hook event logging, env helpers, transcript analysis
- ``vault_adaptive`` -- per-note usefulness tracking, last-seen state

All public symbols are re-exported here so that existing callers
(``import vault_common; vault_common.X()``) continue to work unchanged.
"""

# ---------------------------------------------------------------------------
# Re-export everything from sub-modules
# ---------------------------------------------------------------------------

# vault_config: YAML parsing, config loading, validation
from vault_config import (  # noqa: F401
    _CONFIG_SCHEMA,
    _clear_config_cache,
    _load_config_cached,
    _parse_config_yaml,
    _parse_scalar,
    _split_list_items,
    _strip_inline_comment,
    get_config,
    load_config,
    validate_config,
)

# vault_path: path resolution, constants, secure logging
from vault_path import (  # noqa: F401
    EMBEDDINGS_DB_FILENAME,
    SCRIPTS_DIR,
    TEMPLATES_DIR,
    VAULT_ROOT,
    VaultConfigError,
    _VAULT_FORBIDDEN_PREFIXES,
    _resolve_vault_cached,
    _validate_vault_path,
    get_embeddings_db_path,
    get_vaults_config_path,
    list_named_vaults,
    resolve_templates_dir,
    resolve_vault,
    rotate_log_file,
    secure_log_dir,
)

# vault_fs: file locking, pending queue, git, daily notes
from vault_fs import (  # noqa: F401
    append_session_to_daily,
    append_to_pending,
    create_daily_note_if_missing,
    ensure_vault_dirs,
    flock_exclusive,
    flock_shared,
    funlock,
    get_vault_username,
    git_commit_vault,
    migrate_pending_paths,
    read_last_n_lines,
    today_daily_path,
)

# vault_index: frontmatter, note search, context building
from vault_index import (  # noqa: F401
    EXCLUDE_DIRS,
    VAULT_DIRS,
    all_vault_notes,
    build_compact_index,
    build_context_block,
    ensure_note_index_schema,
    extract_title,
    find_notes_by_project,
    find_notes_by_tag,
    find_notes_by_type,
    find_recent_notes,
    get_body,
    parse_frontmatter,
    query_note_index,
    read_note_summary,
    slugify,
)

# vault_hooks: hook event logging, env helpers, transcript analysis
from vault_hooks import (  # noqa: F401
    TRANSCRIPT_CATEGORIES,
    TRANSCRIPT_CATEGORY_LABELS,
    _SAFE_ENV_KEYS,
    allowed_transcript_roots,
    apply_configured_env_defaults,
    detect_categories,
    env_without_claudecode,
    extract_text_from_content,
    get_project_name,
    is_allowed_transcript_path,
    is_pi_transcript_path,
    is_process_running,
    parse_transcript_lines,
    write_hook_event,
)

# vault_adaptive: per-note usefulness tracking, last-seen state
from vault_adaptive import (  # noqa: F401
    get_injected_stems,
    get_last_seen_path,
    get_usefulness_path,
    load_last_seen,
    load_usefulness_scores,
    save_injected_notes,
    save_last_seen,
    update_usefulness_scores,
)

__version__ = "0.5.6"

__all__: list[str] = [
    # Version
    "__version__",
    # Module-level constants
    "VAULT_ROOT",
    "TEMPLATES_DIR",
    "SCRIPTS_DIR",
    "VAULT_DIRS",
    "EXCLUDE_DIRS",
    # Secure log directory
    "secure_log_dir",
    "rotate_log_file",
    # Vault resolver (multi-vault support)
    "VaultConfigError",
    "get_vaults_config_path",
    "list_named_vaults",
    "resolve_vault",
    "resolve_templates_dir",
    # Environment helpers
    "apply_configured_env_defaults",
    "env_without_claudecode",
    # Frontmatter and content parsing
    "parse_frontmatter",
    "get_body",
    # Note search functions
    "find_notes_by_project",
    "find_notes_by_tag",
    "find_notes_by_type",
    "find_recent_notes",
    "all_vault_notes",
    "read_note_summary",
    "build_context_block",
    "build_compact_index",
    # Project and vault management
    "get_project_name",
    "ensure_vault_dirs",
    "today_daily_path",
    "create_daily_note_if_missing",
    "append_session_to_daily",
    # Configuration
    "load_config",
    "get_config",
    "validate_config",
    # File locking
    "flock_exclusive",
    "flock_shared",
    "funlock",
    # Transcript helpers
    "extract_text_from_content",
    "allowed_transcript_roots",
    "is_allowed_transcript_path",
    "is_pi_transcript_path",
    "read_last_n_lines",
    # Transcript analysis and queuing (shared by session_stop and subagent_stop hooks)
    "TRANSCRIPT_CATEGORIES",
    "TRANSCRIPT_CATEGORY_LABELS",
    "parse_transcript_lines",
    "detect_categories",
    "append_to_pending",
    # Process utilities
    "is_process_running",
    # Utilities
    "slugify",
    "git_commit_vault",
    "write_hook_event",
    "get_last_seen_path",
    "load_last_seen",
    "save_last_seen",
    "get_usefulness_path",
    "load_usefulness_scores",
    "save_injected_notes",
    "update_usefulness_scores",
    "get_injected_stems",
    "EMBEDDINGS_DB_FILENAME",
    "get_embeddings_db_path",
    "ensure_note_index_schema",
    "query_note_index",
    # Content helpers
    "extract_title",
]
