"""Mirror CI's shipped-module import smoke inside a clean wheel-install venv.

ARC-201: the wheel ships ONLY the parsidion.* namespace, so the smoke imports
every module under the installed parsidion package (plus the console-script
targets, which are inside it). Run through stdin from the repository root,
matching CI's import search path.

ARC-200 step 2: the parsidion/<name>.py re-export shims were retired - the
files remain in the wheel as raising stubs so importing one produces a clean
deprecation ImportError naming the parsidion.core.* replacement instead of
ModuleNotFoundError. The smoke asserts that contract for the retired names
and a clean import for everything else.
"""

import importlib
import pathlib

SCRIPTS_ROOT = pathlib.Path("skills/parsidion/scripts")
PKG_ROOT = SCRIPTS_ROOT / "parsidion"

mods: set[str] = set()
for p in sorted(PKG_ROOT.rglob("*.py")):
    if "__pycache__" in p.parts:
        continue
    rel = p.relative_to(SCRIPTS_ROOT).with_suffix("")
    mods.add(str(rel).replace("/", "."))

retired: dict[str, str] = {
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

for module in sorted(mods):
    hint = retired.get(module)
    if hint is not None:
        try:
            importlib.import_module(module)
        except ImportError as exc:
            assert hint in str(exc), (
                f"{module} raises ImportError but the message does not name "
                f"its replacement {hint}: {exc}"
            )
            continue
        raise AssertionError(
            f"{module} was retired in ARC-200 step 2 and must raise ImportError, "
            "not import cleanly"
        )
    importlib.import_module(module)
print(
    f"ok: imported {len(mods)} shipped modules/packages "
    f"({len(retired)} retired stubs asserted raising)"
)
