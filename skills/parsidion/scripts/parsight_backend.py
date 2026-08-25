"""parsight_backend -- compatibility shim (ARC-006).

Implementation moved to ``core.parsight_backend``. This shim re-exports the
module's controlled public surface (defined by ``core.parsight_backend.__all__``)
so every existing ``import parsight_backend`` caller keeps working unchanged.

ARC-006 note for tests: patching attributes on THIS shim only affects
callers that resolve the name through the shim. Monkeypatches of module
internals (``_run_parsight``, ``_LOG_DIR``, ...) must target the
implementation module — ``from core import parsight_backend`` — because
function bodies resolve their globals in ``core.parsight_backend``.
"""

from core.parsight_backend import *  # noqa: F403 -- controlled by core.parsight_backend.__all__
