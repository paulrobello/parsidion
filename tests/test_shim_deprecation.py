"""ARC-200 step 2: every retired re-export shim raises a clean deprecation error.

The parsidion/<name>.py compatibility shims (ARC-005/ARC-006 split) were
retired: each file remains in the wheel as a module whose body is a single
``raise ImportError`` naming the ``parsidion.core.*`` replacement, so an
out-of-tree consumer importing a retired name gets an actionable error
instead of ModuleNotFoundError. One parametrized test pins that contract for
the whole surface.
"""

import importlib
import sys

import pytest

# shim module -> substring that MUST appear in the raised ImportError message.
RETIRED: dict[str, str] = {
    "parsidion.vault_common": "parsidion.core.vault_config",
    "parsidion.vault_config": "parsidion.core.vault_config",
    "parsidion.vault_path": "parsidion.core.vault_path",
    "parsidion.vault_fs": "parsidion.core.vault_fs",
    "parsidion.vault_index": "parsidion.core.vault_index",
    "parsidion.vault_hooks": "parsidion.core.vault_hooks",
    "parsidion.vault_adaptive": "parsidion.core.vault_adaptive",
    "parsidion.vault_constants": "parsidion.core.vault_constants",
    "parsidion.vault_health": "parsidion.core.vault_health",
    "parsidion.vault_links": "parsidion.core.vault_links",
    "parsidion.vault_metrics": "parsidion.core.vault_metrics",
    "parsidion.ai_backend": "parsidion.core.ai_backend",
    "parsidion.parsight_backend": "parsidion.core.parsight_backend",
    "parsidion.subproc_util": "parsidion.core.subproc_util",
    "parsidion.transcript_reader": "parsidion.core.transcript_reader",
}


@pytest.mark.parametrize(("module", "hint"), sorted(RETIRED.items()))
def test_retired_shim_raises_deprecation_importerror(module: str, hint: str) -> None:
    sys.modules.pop(module, None)
    with pytest.raises(ImportError) as excinfo:
        importlib.import_module(module)
    message = str(excinfo.value)
    assert module in message, message
    assert hint in message, message
    assert "ARC-200" in message, message
