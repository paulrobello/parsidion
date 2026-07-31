"""vault_adaptive -- compatibility shim (ARC-004).

Implementation moved to ``core.vault_adaptive``. This shim re-exports the
module's complete non-dunder surface so every existing caller --
``import vault_adaptive``, ``from vault_adaptive import X``, ``vault_adaptive.X`` (hooks, CLIs,
tests, parsidion-mcp, the installer) -- keeps working unchanged,
including imported constants and test monkeypatch targets. The
stdlib-only constraint is enforced on ``core.vault_adaptive`` by
``tests/test_stdlib_only.py``.
"""

from core.vault_adaptive import (  # noqa: F401 -- full-surface re-export
    Iterator,
    Path,
    _LAST_SEEN_FILENAME,
    _NOTE_USEFULNESS_FILENAME,
    _atomic_write_json,
    _locked,
    annotations,
    contextmanager,
    datetime,
    flock_exclusive,
    funlock,
    get_injected_stems,
    get_last_seen_path,
    get_usefulness_path,
    json,
    load_last_seen,
    load_usefulness_scores,
    os,
    save_injected_notes,
    save_last_seen,
    update_usefulness_scores,
)
