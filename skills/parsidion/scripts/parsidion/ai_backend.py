"""Deprecated compatibility shim, retired by ARC-200 step 2 (release 0.29.0).

The implementation lives at ``parsidion.core.ai_backend``. Importing this module raises
:class:`ImportError` with the replacement path.
"""

raise ImportError(
    "parsidion.ai_backend was removed: import from parsidion.core.ai_backend instead "
    "(flat shim surface retired in ARC-200 step 2 / release 0.29.0)."
)
