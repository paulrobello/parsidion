"""vault_adaptive -- compatibility shim (ARC-004 / QA-005 / ARC-012).

Implementation moved to ``core.vault_adaptive``. This shim re-exports
the module's controlled non-dunder surface (defined by
``core.vault_adaptive.__all__``) so every existing caller --
``import vault_adaptive``, ``from vault_adaptive import X``,
``vault_adaptive.X`` -- keeps working unchanged. QA-005 / ARC-012:
``__all__`` deliberately excludes stdlib modules (``json``, ``os``,
``datetime``), ``contextlib``/``collections.abc`` re-exports, and the
private ``_locked``/``_atomic_write_json`` helpers -- callers import
the former directly and the latter are not part of the public surface.
The stdlib-only constraint is enforced on ``core.vault_adaptive`` by
``tests/test_stdlib_only.py``.
"""

from core.vault_adaptive import *  # noqa: F401,F403 -- controlled by core.vault_adaptive.__all__
