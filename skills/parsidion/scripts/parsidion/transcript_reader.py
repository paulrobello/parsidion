"""Deprecated compatibility shim, retired by ARC-200 step 2 (release 0.29.0).

The implementation lives at ``parsidion.core.transcript_reader``. Importing this module raises
:class:`ImportError` with the replacement path.
"""

raise ImportError(
    "parsidion.transcript_reader was removed: import from parsidion.core.transcript_reader instead "
    "(flat shim surface retired in ARC-200 step 2 / release 0.29.0)."
)
