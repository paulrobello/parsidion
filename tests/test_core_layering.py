"""ARC-001 (namespace-updated by ARC-201): the core library package must not
import through the flat re-export shims.

The stdlib library implementations live in
``skills/parsidion/scripts/parsidion/core/``; the flat ``vault_*.py`` /
``subproc_util.py`` names now live directly under ``parsidion/`` (the wheel
namespace root) as thin re-export shims kept for backwards compatibility. A
core module importing ``parsidion.vault_common`` resolves, at runtime, to the
*shim* module — round-tripping through the deprecated facade (and, for
monkeypatch-driven tests, binding the wrong module object). Each core module
must import its dependencies as relative sibling imports instead.

This test parses every ``parsidion/core/*.py`` with ``ast`` and fails on any
absolute import that names a shim module (``parsidion.vault_common`` or a
``from parsidion import vault_common`` binding). Relative imports
(``level >= 1``) are always allowed — they name the sibling inside the
package, never the shim.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
_CORE_DIR = _SCRIPTS_DIR / "parsidion" / "core"

# Shim modules at the parsidion/ namespace root (see CLAUDE.md "stdlib-only
# rule"). ``subproc_util`` and ``vault_metrics`` etc. exist BOTH as
# parsidion/core/* modules (the implementation) and as parsidion/-root
# re-export shims — only the shim module paths are forbidden inside core/.
# ARC-006 added ``ai_backend``/``parsight_backend`` when their implementations
# moved into core/.
ROOT_SHIMS: frozenset[str] = frozenset(
    {
        "vault_common",
        "vault_metrics",
        "vault_index",
        "vault_path",
        "vault_fs",
        "vault_hooks",
        "vault_config",
        "vault_adaptive",
        "vault_links",
        "vault_health",
        "vault_tui",
        "vault_constants",
        "subproc_util",
        "ai_backend",
        "parsight_backend",
    }
)


def _shim_ref(name: str) -> bool:
    """True when *name* references a shim module (parsidion.<shim>[.<sub>])."""
    if name == "parsidion":
        return False
    parts = name.split(".")
    return len(parts) >= 2 and parts[0] == "parsidion" and parts[1] in ROOT_SHIMS


def _shim_imports(path: Path) -> list[str]:
    """Return human-readable descriptions of shim imports in *path*."""
    offenders: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _shim_ref(alias.name):
                    offenders.append(f"{path.name}:{node.lineno} import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level >= 1:
                continue  # relative import — names a core sibling, never a shim
            if node.module and _shim_ref(node.module):
                offenders.append(
                    f"{path.name}:{node.lineno} from {node.module} import "
                    + ", ".join(a.name for a in node.names)
                )
            elif node.module == "parsidion":
                # `from parsidion import vault_common` binds the shim module.
                for alias in node.names:
                    if alias.name in ROOT_SHIMS:
                        offenders.append(
                            f"{path.name}:{node.lineno} imports shim name {alias.name}"
                        )
    return offenders


def test_no_core_module_imports_a_shim() -> None:
    core_files = sorted(_CORE_DIR.glob("*.py"))
    assert core_files, "parsidion/core/ package not found — test wiring is broken"
    offenders: list[str] = []
    for path in core_files:
        offenders.extend(_shim_imports(path))
    assert not offenders, (
        "ARC-001: parsidion/core/ modules must import siblings via relative "
        "imports, not the shim surface. Found: " + "; ".join(offenders)
    )


def test_core_package_imports_without_facade() -> None:
    """The three former offenders import cleanly with the shims never imported.

    Imports ``parsidion.core.vault_health`` (which pulls ``parsidion.core.vault_links``
    and ``parsidion.core.vault_metrics`` through the package graph) in a
    subprocess where the shim modules are explicitly blocked from
    ``sys.modules`` — proving the package no longer depends on the shims at
    import time.
    """
    import subprocess
    import sys

    prog = (
        "import sys\n"
        # Block the shims: any core -> shim import raises ImportError.
        "class _Blocked:\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        parts = fullname.split('.')\n"
        "        if len(parts) >= 2 and parts[0] == 'parsidion' and parts[1] in ("
        + ", ".join(repr(s) for s in sorted(ROOT_SHIMS))
        + "):\n"
        "            raise ImportError('ARC-001: core/ imported shim ' + fullname)\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Blocked())\n"
        "import parsidion.core.vault_health, parsidion.core.vault_links, parsidion.core.vault_metrics\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", prog],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(_SCRIPTS_DIR),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
