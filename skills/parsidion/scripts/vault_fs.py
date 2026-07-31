"""vault_fs -- compatibility shim (ARC-004).

Implementation moved to ``core.vault_fs``. This shim re-exports the
module's complete non-dunder surface so every existing caller --
``import vault_fs``, ``from vault_fs import X``, ``vault_fs.X`` (hooks, CLIs,
tests, parsidion-mcp, the installer) -- keeps working unchanged,
including imported constants and test monkeypatch targets. The
stdlib-only constraint is enforced on ``core.vault_fs`` by
``tests/test_stdlib_only.py``.
"""

from core.vault_fs import (  # noqa: F401 -- full-surface re-export
    Any,
    IO,
    Path,
    TRANSCRIPT_CATEGORY_LABELS,
    VAULT_DIRS,
    _HOOK_EVENTS_FILENAME,
    _HOOK_EVENTS_MAX_LINES_DEFAULT,
    _fcntl,
    annotations,
    append_session_to_daily,
    append_to_pending,
    atomic_write_text,
    create_daily_note_if_missing,
    date,
    datetime,
    ensure_vault_dirs,
    flock_exclusive,
    flock_shared,
    funlock,
    get_config,
    get_vault_username,
    git_commit_vault,
    json,
    migrate_pending_paths,
    os,
    re,
    read_last_n_lines,
    release_singleton_lock,
    resolve_templates_dir,
    resolve_vault,
    stat,
    subprocess,
    today_daily_path,
    try_singleton_lock,
    write_hook_event,
)
