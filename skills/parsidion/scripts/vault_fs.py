"""vault_fs -- compatibility shim (ARC-004 / QA-005 / ARC-012).

Implementation moved to ``core.vault_fs``. This shim re-exports the
module's controlled non-dunder surface (defined by
``core.vault_fs.__all__``) so every existing caller --
``import vault_fs``, ``from vault_fs import X``, ``vault_fs.X`` -- keeps
working unchanged. QA-005 / ARC-012: ``__all__`` deliberately excludes
stdlib modules (``json``, ``os``, ``re``, ``shutil``, ``stat``,
``subprocess``, ``date``, ``datetime``) and unused private helpers --
callers import the former directly and the latter are not part of the
public surface. The stdlib-only constraint is enforced on
``core.vault_fs`` by ``tests/test_stdlib_only.py``.
"""

from core.vault_fs import *  # noqa: F401,F403 -- controlled by core.vault_fs.__all__
