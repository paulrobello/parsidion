"""Deprecated compatibility shim, retired by ARC-200 step 2 (release 0.29.0).

The implementation lives at ``parsidion.core.vault_constants``. Importing this module raises
:class:`ImportError` with the replacement path.
"""

raise ImportError(
    "parsidion.vault_constants was removed: import from parsidion.core.vault_constants instead "
    "(flat shim surface retired in ARC-200 step 2 / release 0.29.0)."
)
