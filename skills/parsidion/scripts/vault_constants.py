"""vault_constants -- compatibility shim (ARC-004).

Implementation moved to ``core.vault_constants``. This shim re-exports the
module's complete non-dunder surface so every existing caller --
``import vault_constants``, ``from vault_constants import X``, ``vault_constants.X`` (hooks, CLIs,
tests, parsidion-mcp, the installer) -- keeps working unchanged,
including imported constants and test monkeypatch targets. The
stdlib-only constraint is enforced on ``core.vault_constants`` by
``tests/test_stdlib_only.py``.
"""

from core.vault_constants import (  # noqa: F401 -- full-surface re-export
    TRANSCRIPT_CATEGORY_LABELS,
    annotations,
)
