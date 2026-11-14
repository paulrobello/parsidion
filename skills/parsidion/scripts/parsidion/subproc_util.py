"""Deprecated compatibility shim, retired by ARC-200 step 2 (release 0.29.0).

The implementation lives at ``parsidion.core.subproc_util``. Importing this module raises
:class:`ImportError` with the replacement path.
"""

raise ImportError(
    "parsidion.subproc_util was removed: import from parsidion.core.subproc_util instead "
    "(flat shim surface retired in ARC-200 step 2 / release 0.29.0)."
)
