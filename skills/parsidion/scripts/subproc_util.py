"""subproc_util -- compatibility shim (ARC-004 / QA-008).

Implementation moved to ``core.subproc_util``. This shim re-exports the
module's controlled public surface (defined by ``core.subproc_util.__all__``)
so every existing caller -- ``import subproc_util``,
``from subproc_util import run_with_pgkill``, ``subproc_util.PGKILL_GRACE_SECS``
(hooks, CLIs, tests, parsidion-mcp, the installer) -- keeps working
unchanged. QA-008: ``__all__`` deliberately excludes stdlib modules
(``os``, ``signal``, ``subprocess``) and the private ``_kill_process_group``
helper -- callers import the former directly and the latter is not part of
the public surface. The stdlib-only constraint is enforced on
``core.subproc_util`` by ``tests/test_stdlib_only.py``.
"""

from core.subproc_util import *  # noqa: F403 -- controlled by core.subproc_util.__all__
