"""vault_hooks -- compatibility shim (ARC-004).

Implementation moved to ``core.vault_hooks``. This shim re-exports the
module's complete non-dunder surface so every existing caller --
``import vault_hooks``, ``from vault_hooks import X``, ``vault_hooks.X`` (hooks, CLIs,
tests, parsidion-mcp, the installer) -- keeps working unchanged,
including imported constants and test monkeypatch targets. The
stdlib-only constraint is enforced on ``core.vault_hooks`` by
``tests/test_stdlib_only.py``.
"""

from core.vault_hooks import (  # noqa: F401 -- full-surface re-export
    Path,
    SAFE_ENV_KEYS,
    TRANSCRIPT_CATEGORIES,
    TRANSCRIPT_CATEGORY_LABELS,
    _CONFIGURABLE_ENV_KEYS,
    _SAFE_ENV_KEYS,
    _coerce_env_value,
    _configured_env_defaults,
    _extract_gemini_content,
    _extract_gemini_parts,
    allowed_transcript_roots,
    annotations,
    apply_configured_env_defaults,
    codex_home,
    detect_categories,
    env_without_claudecode,
    extract_text_from_content,
    gemini_home,
    get_project_name,
    is_allowed_transcript_path,
    is_codex_transcript_path,
    is_gemini_transcript_path,
    is_pi_transcript_path,
    is_process_running,
    json,
    load_config,
    os,
    parse_codex_transcript_lines,
    parse_gemini_transcript_lines,
    parse_transcript_lines,
    write_hook_event,
)
