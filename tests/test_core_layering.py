"""ARC-001: ``core/`` must not import back through the deprecated root shims.

The stdlib library implementations live in ``skills/parsidion/scripts/core/``;
the flat ``vault_*.py`` / ``subproc_util.py`` names at the scripts root are
thin re-export shims kept for backwards compatibility. A ``core`` module
importing a root-shim name resolves, at runtime, to the *shim* module —
round-tripping through the deprecated facade (and, for monkeypatch-driven
tests, binding the wrong module object). Each core module must import its
dependencies as relative sibling imports instead.

This test parses every ``core/*.py`` with ``ast`` and fails on any absolute
import that names a root shim. Relative imports (``level >= 1``) are always
allowed — they name the sibling inside the package, never the shim.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
_CORE_DIR = _SCRIPTS_DIR / "core"

# Root shim modules (see CLAUDE.md "stdlib-only rule"). ``subproc_util`` and
# ``vault_metrics`` etc. exist BOTH as core/* modules (the implementation) and
# as flat scripts-root re-export shims — only the flat names are forbidden
# inside core/.
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
    }
)


def _first_segment(dotted: str) -> str:
    return dotted.split(".", 1)[0]


def _shim_imports(path: Path) -> list[str]:
    """Return human-readable descriptions of absolute root-shim imports in *path*."""
    offenders: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _first_segment(alias.name) in ROOT_SHIMS:
                    offenders.append(f"{path.name}:{node.lineno} import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level >= 1:
                continue  # relative import — names a core sibling, never a shim
            if node.module and _first_segment(node.module) in ROOT_SHIMS:
                offenders.append(
                    f"{path.name}:{node.lineno} from {node.module} import "
                    + ", ".join(a.name for a in node.names)
                )
            elif not node.module:
                # `from X import vault_common`-style bindings (module is None
                # only for relative imports, but guard future syntax anyway).
                for alias in node.names:
                    if _first_segment(alias.name) in ROOT_SHIMS:
                        offenders.append(
                            f"{path.name}:{node.lineno} imports shim name {alias.name}"
                        )
    return offenders


def test_no_core_module_imports_a_root_shim() -> None:
    core_files = sorted(_CORE_DIR.glob("*.py"))
    assert core_files, "core/ package not found — test wiring is broken"

    offenders: list[str] = []
    for path in core_files:
        offenders.extend(_shim_imports(path))
    assert not offenders, (
        "ARC-001: core/ modules must import siblings via relative imports, "
        "not the deprecated root shims. Found: " + "; ".join(offenders)
    )


def test_core_package_imports_without_facade() -> None:
    """The three former offenders import cleanly with the facade never imported.

    Imports ``core.vault_health`` (which pulls ``core.vault_links`` and
    ``core.vault_metrics`` through the package graph) in a subprocess where
    the facade is explicitly blocked from ``sys.modules`` — proving the
    package no longer depends on the shims at import time.
    """
    import subprocess
    import sys

    prog = (
        "import sys\n"
        # Block the facade: any core -> root-shim import raises ImportError.
        "class _Blocked:\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        first = fullname.split('.')[0]\n"
        "        if first in ("
        + ", ".join(repr(s) for s in sorted(ROOT_SHIMS))
        + "):\n"
        "            raise ImportError('ARC-001: core/ imported root shim ' + first)\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Blocked())\n"
        "import core.vault_health, core.vault_links, core.vault_metrics\n"
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
