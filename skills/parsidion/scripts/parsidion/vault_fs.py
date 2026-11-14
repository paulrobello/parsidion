"""vault_fs -- compatibility shim (ARC-004 / QA-005 / ARC-012).

Implementation moved to ``parsidion.core.vault_fs``. This shim re-exports the
module's controlled non-dunder surface (defined by
``parsidion.core.vault_fs.__all__``) so every existing caller --
``import vault_fs``, ``from parsidion.vault_fs import X``, ``parsidion.vault_fs.X`` -- keeps
working unchanged. QA-005 / ARC-012: ``__all__`` deliberately excludes
stdlib modules (``json``, ``os``, ``re``, ``shutil``, ``stat``,
``subprocess``, ``date``, ``datetime``) and unused private helpers --
callers import the former directly and the latter are not part of the
public surface. The stdlib-only constraint is enforced on
``parsidion.core.vault_fs`` by ``tests/test_stdlib_only.py``.
"""

from parsidion.core.vault_fs import *  # noqa: F403 -- controlled by parsidion.core.vault_fs.__all__
