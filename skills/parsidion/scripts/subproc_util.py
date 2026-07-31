"""subproc_util -- compatibility shim (ARC-004).

Implementation moved to ``core.subproc_util``. This shim re-exports the
module's complete non-dunder surface so every existing caller --
``import subproc_util``, ``from subproc_util import X``, ``subproc_util.X`` (hooks, CLIs,
tests, parsidion-mcp, the installer) -- keeps working unchanged,
including imported constants and test monkeypatch targets. The
stdlib-only constraint is enforced on ``core.subproc_util`` by
``tests/test_stdlib_only.py``.
"""

from core.subproc_util import (  # noqa: F401 -- full-surface re-export
    Any,
    Mapping,
    PGKILL_GRACE_SECS,
    Path,
    _kill_process_group,
    annotations,
    os,
    run_with_pgkill,
    signal,
    subprocess,
)
