"""vault_config -- compatibility shim (ARC-004 / QA-005 / ARC-012).

Implementation moved to ``core.vault_config``. This shim re-exports the
module's controlled non-dunder surface (defined by
``core.vault_config.__all__``) so every existing caller --
``import vault_config``, ``from vault_config import X``, ``vault_config.X``
-- keeps working unchanged. QA-005 / ARC-012: ``__all__`` deliberately
excludes stdlib modules (``re``, ``sys``, ``math``, ``functools``) and
unused private helpers -- callers import the former directly and the
latter are not part of the public surface. The stdlib-only constraint is
enforced on ``core.vault_config`` by ``tests/test_stdlib_only.py``.
"""

from core.vault_config import *  # noqa: F401,F403 -- controlled by core.vault_config.__all__
