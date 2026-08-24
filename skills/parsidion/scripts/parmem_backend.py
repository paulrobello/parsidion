"""parmem_backend -- compatibility shim (ARC-006).

Implementation moved to ``core.parmem_backend``. This shim re-exports the
module's controlled public surface (defined by ``core.parmem_backend.__all__``)
so every existing ``import parmem_backend`` caller keeps working unchanged.

ARC-006 note for tests: patching attributes on THIS shim only affects
callers that resolve the name through the shim. Monkeypatches of module
internals (``_run_parmem``, ``_LOG_DIR``, ...) must target the
implementation module — ``from core import parmem_backend`` — because
function bodies resolve their globals in ``core.parmem_backend``.
"""

from core.parmem_backend import *  # noqa: F403 -- controlled by core.parmem_backend.__all__
