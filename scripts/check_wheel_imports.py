"""Mirror CI's shipped-module import smoke inside a clean wheel-install venv.

ARC-201: the wheel ships ONLY the parsidion.* namespace, so the smoke imports
every module under the installed parsidion package (plus the console-script
targets, which are inside it). Run through stdin from the repository root,
matching CI's import search path.
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

for module in sorted(mods):
    importlib.import_module(module)
print(f"ok: imported {len(mods)} shipped modules/packages")
