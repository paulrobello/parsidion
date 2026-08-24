"""vault_health -- compatibility shim (ARC-004 / ENH-007 / QA-005 / ARC-012).

Implementation moved to ``core.vault_health``. This shim re-exports the
module's controlled non-dunder surface (defined by
``core.vault_health.__all__``) so every existing caller --
``import vault_health``, ``from vault_health import X``,
``vault_health.X`` (``vault_stats``, the MCP server, hooks, tests) --
keeps working unchanged. QA-005 / ARC-012: ``__all__`` deliberately
excludes stdlib modules (``json``, ``sqlite3``, ``stat``, ``time``),
the ``dataclasses`` re-exports, and unused private helpers -- callers
import the former directly and the latter are not part of the public
surface. The stdlib-only constraint is enforced on ``core.vault_health``
by ``tests/test_stdlib_only.py``.
"""

from core.vault_health import *  # noqa: F403 -- controlled by core.vault_health.__all__
