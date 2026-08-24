"""vault_constants -- compatibility shim (ARC-004 / QA-008).

Implementation moved to ``core.vault_constants``. This shim re-exports the
module's controlled public surface (defined by
``core.vault_constants.__all__``) so every existing caller --
``import vault_constants``, ``from vault_constants import
TRANSCRIPT_CATEGORY_LABELS`` -- keeps working unchanged. QA-008:
``__all__`` deliberately excludes stdlib modules and the
``from __future__ import annotations`` marker -- callers import the former
directly. The stdlib-only constraint is enforced on
``core.vault_constants`` by ``tests/test_stdlib_only.py``.
"""

from core.vault_constants import *  # noqa: F403 -- controlled by core.vault_constants.__all__
