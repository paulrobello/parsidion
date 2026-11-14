"""vault_metrics -- compatibility shim (ARC-004 / QA-005 / ARC-012).

Implementation moved to ``parsidion.core.vault_metrics``. This shim re-exports the
module's controlled non-dunder surface (defined by
``parsidion.core.vault_metrics.__all__``) so every existing caller --
``import vault_metrics``, ``from parsidion.vault_metrics import X``,
``parsidion.vault_metrics.X`` -- keeps working unchanged. QA-005 / ARC-012:
``__all__`` deliberately excludes stdlib modules (``sqlite3``, ``json``,
``time``, ``datetime``) and the ``vault_common`` re-export -- callers
import those directly. The stdlib-only constraint is enforced on
``parsidion.core.vault_metrics`` by ``tests/test_stdlib_only.py``.
"""

from parsidion.core.vault_metrics import *  # noqa: F403 -- controlled by parsidion.core.vault_metrics.__all__
