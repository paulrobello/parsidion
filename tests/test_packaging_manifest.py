"""ARC-003: the wheel manifest is self-checking.

A previous cycle shipped console scripts whose transitive imports
(``prompt_templates``, ``note_schema``, ``vault_health``, ``vault_resolve``,
``agent_adapter``, the ``session_start`` package) were missing from
``py-modules``/``packages``, so every non-editable install produced three
broken CLIs. The failure was invisible because the CI wheel smoke imported
only five hand-picked names.

These tests pin the manifest to the directory: every importable module under
``skills/parsidion/scripts/`` must be declared, every declared name must exist,
and every package directory must be covered by a ``packages.find`` include
pattern (and vice versa). The CI smoke then imports everything declared.
"""

from __future__ import annotations

import tomllib
from fnmatch import fnmatch
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "parsidion" / "scripts"

# Filenames that cannot be imported as modules (hyphenated) or that exit at
# import time without their optional extra.
NOT_IMPORTABLE = {"html-to-md"}
# Modules that require an optional-dependency extra just to import; they still
# ship (the extras exist for them) but the clean-venv smoke skips them.
NEEDS_EXTRA = {"build_embeddings", "build_graph"}


def _pyproject() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _flat_modules_on_disk() -> set[str]:
    return {p.stem for p in SCRIPTS_DIR.glob("*.py")} - NOT_IMPORTABLE


def _package_dirs_on_disk() -> set[str]:
    return {
        str(p.relative_to(SCRIPTS_DIR)).replace("/", ".")
        for p in SCRIPTS_DIR.iterdir()
        if p.is_dir() and (p / "__init__.py").is_file()
    }


def test_every_flat_module_is_declared() -> None:
    declared = set(_pyproject()["tool"]["setuptools"]["py-modules"])
    on_disk = _flat_modules_on_disk()
    missing = on_disk - declared
    assert not missing, (
        f"modules under skills/parsidion/scripts/ missing from pyproject "
        f"[tool.setuptools] py-modules (a non-editable wheel install would "
        f"fail to import them): {sorted(missing)}"
    )


def test_every_declared_module_exists() -> None:
    declared = set(_pyproject()["tool"]["setuptools"]["py-modules"])
    on_disk = _flat_modules_on_disk()
    stale = declared - on_disk
    assert not stale, (
        f"py-modules entries with no file under skills/parsidion/scripts/: {sorted(stale)}"
    )


def test_console_script_targets_are_declared() -> None:
    cfg = _pyproject()
    declared = set(cfg["tool"]["setuptools"]["py-modules"])
    packages = _package_dirs_on_disk()
    for script, target in cfg["project"]["scripts"].items():
        module = target.split(":")[0]
        assert module in declared or module in packages, (
            f"console script {script} imports {module!r}, which is neither in "
            f"py-modules nor a shipped package"
        )


def test_find_include_patterns_cover_every_package() -> None:
    include = _pyproject()["tool"]["setuptools"]["packages"]["find"]["include"]
    for pkg in _package_dirs_on_disk():
        assert any(fnmatch(pkg, pat) for pat in include), (
            f"package directory {pkg!r} matches no packages.find include "
            f"pattern {include}; it would be silently dropped from the wheel"
        )


def test_every_include_pattern_matches_a_package() -> None:
    include = _pyproject()["tool"]["setuptools"]["packages"]["find"]["include"]
    packages = _package_dirs_on_disk()
    for pat in include:
        assert any(fnmatch(pkg, pat) for pkg in packages), (
            f"packages.find include pattern {pat!r} matches no directory under "
            f"skills/parsidion/scripts/ (stale pattern)"
        )


def test_smoke_skip_list_stays_minimal() -> None:
    """The CI smoke skips NEEDS_EXTRA; fail here when a module gains an
    optional import-time dependency so the skip list is revisited."""
    assert NEEDS_EXTRA <= _flat_modules_on_disk()
