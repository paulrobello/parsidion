"""vault_index -- compatibility shim (ARC-004 / QA-005 / ARC-012).

Implementation moved to ``core.vault_index``. This shim re-exports the
module's controlled non-dunder surface (defined by
``core.vault_index.__all__``) so every existing caller --
``import vault_index``, ``from vault_index import X``, ``vault_index.X``
-- keeps working unchanged. QA-005 / ARC-012: ``__all__`` deliberately
excludes stdlib modules (``re``, ``sys``, ``os``, ``json``, ``sqlite3``,
``hashlib``, ``datetime``, ``unicodedata``) and unused private helpers --
callers import the former directly and the latter are not part of the
public surface. The stdlib-only constraint is enforced on
``core.vault_index`` by ``tests/test_stdlib_only.py``.
"""

from core.vault_index import *  # noqa: F401,F403 -- controlled by core.vault_index.__all__
