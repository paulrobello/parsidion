"""vault_path -- compatibility shim (ARC-004 / QA-005 / ARC-012).

Implementation moved to ``parsidion.core.vault_path``. This shim re-exports the
module's controlled non-dunder surface (defined by
``parsidion.core.vault_path.__all__``) so every existing caller --
``import vault_path``, ``from parsidion.vault_path import X``, ``parsidion.vault_path.X``
-- keeps working unchanged. QA-005 / ARC-012: ``__all__`` deliberately
excludes stdlib modules (``os``, ``sys``, ``functools``) and unused
private helpers -- callers import the former directly and the latter are
not part of the public surface. The stdlib-only constraint is enforced
on ``parsidion.core.vault_path`` by ``tests/test_stdlib_only.py``.
"""

from parsidion.core.vault_path import *  # noqa: F403 -- controlled by parsidion.core.vault_path.__all__
