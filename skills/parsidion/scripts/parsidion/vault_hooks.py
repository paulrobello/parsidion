"""vault_hooks -- compatibility shim (ARC-004 / QA-005 / ARC-012).

Implementation moved to ``parsidion.core.vault_hooks``. This shim re-exports the
module's controlled non-dunder surface (defined by
``parsidion.core.vault_hooks.__all__``) so every existing caller --
``import vault_hooks``, ``from parsidion.vault_hooks import X``, ``parsidion.vault_hooks.X``
-- keeps working unchanged. QA-005 / ARC-012: ``__all__`` deliberately
excludes stdlib modules (``os``, ``json``) and unused private helpers --
callers import the former directly and the latter are not part of the
public surface. The stdlib-only constraint is enforced on
``parsidion.core.vault_hooks`` by ``tests/test_stdlib_only.py``.
"""

from parsidion.core.vault_hooks import *  # noqa: F403 -- controlled by parsidion.core.vault_hooks.__all__
