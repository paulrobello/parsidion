"""ai_backend -- compatibility shim (ARC-006).

Implementation moved to ``parsidion.core.ai_backend``. This shim re-exports the
module's controlled public surface (defined by ``parsidion.core.ai_backend.__all__``)
so every existing ``import ai_backend`` caller keeps working unchanged.

ARC-006 note for tests: patching attributes on THIS shim only affects
callers that resolve the name through the shim. Monkeypatches of module
internals (``_run_prompt_subprocess``, ``parsidion.ai_backend.shutil``, ...) must
target the implementation module — ``from parsidion.core import ai_backend`` —
because function bodies resolve their globals in ``parsidion.core.ai_backend``.
"""

from parsidion.core.ai_backend import *  # noqa: F403 -- controlled by parsidion.core.ai_backend.__all__
