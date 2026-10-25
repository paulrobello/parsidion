"""Mirror CI's shipped-module import smoke inside a clean wheel-install venv.

Run through stdin from the repository root, matching CI's import search path.
"""

import importlib
import pathlib
import tomllib

with pathlib.Path("pyproject.toml").open("rb") as config_file:
    cfg = tomllib.load(config_file)
scripts_root = pathlib.Path("skills/parsidion/scripts")
skip: set[str] = {"build_embeddings", "build_graph"}

mods: list[str] = sorted({t.split(":")[0] for t in cfg["project"]["scripts"].values()})
mods += sorted(m for m in cfg["tool"]["setuptools"]["py-modules"] if m not in skip)
mods += sorted(
    str(p.with_suffix("")).replace("/", ".")
    for p in scripts_root.rglob("*.py")
    if p.parent != scripts_root and "__pycache__" not in p.parts
)
for module in sorted(set(mods)):
    importlib.import_module(module)
print(f"ok: imported {len(set(mods))} shipped modules/packages")
