"""ARC-003/ARC-201: the wheel manifest is self-checking.

ARC-003's history: a cycle shipped console scripts whose transitive imports
were missing from the build manifest, so non-editable installs produced broken
CLIs; CI's import smoke only covered five hand-picked names, so the failure
was invisible. The manifest tests pin build config to the tree.

ARC-201 tightened the shape: the wheel ships ONLY the ``parsidion.*``
namespace (packages.find include=["parsidion*"], no py-modules). The by-path
entry scripts at the scripts/ root are NOT shipped — they are invoked by file
path against repo/symlink installs, never imported from a wheel, and several
carry test monkeypatch surfaces keyed on their root module names. The suite
therefore pins:

1. every package dir under scripts/parsidion/ is covered by an include pattern
   (a new subpackage can't be silently dropped from the wheel);
2. no generic top-level name ships (py-modules absent/empty, include list has
   no top-level patterns outside parsidion*);
3. console scripts resolve inside the namespace;
4. nothing inside parsidion/ imports a staying root script at MODULE level
   (the reverse ARC-003 check — a lazy import inside a function is fine
   because it resolves only in repo-layout execution contexts; a module-level
   one would break wheel installs of the console scripts);
5. the clean-room smoke enumerates the same surface these tests pin.
"""

from __future__ import annotations

import ast
import tomllib
from fnmatch import fnmatch
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "parsidion" / "scripts"
PARSIDION_DIR = SCRIPTS_DIR / "parsidion"

# Filenames that cannot be imported as modules (hyphenated) or that exit at
# import time without their optional extra.
NOT_IMPORTABLE = {"html-to-md"}
# By-path entry scripts that STAY at the scripts/ root and are NOT shipped.
ROOT_ENTRY_SCRIPTS = {
    "session_start_hook",
    "session_stop_hook",
    "subagent_stop_hook",
    "pre_compact_hook",
    "post_compact_hook",
    "pre_tool_use_hook",
    "user_prompt_submit_hook",
    "codex_session_start_hook",
    "codex_stop_hook",
    "codex_subagent_stop_hook",
    "antigravity_session_start_hook",
    "antigravity_session_end_hook",
    "update_index",
    "summarize_sessions",
    "vault_doctor",
    "vault_resolve",
    "run_trigger_eval",
    "build_graph",
    "build_embeddings",
    "check_graph_coverage",
    "html-to-md",
}


def _pyproject() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _setuptools() -> dict:
    return _pyproject().get("tool", {}).get("setuptools", {})


def test_wheel_ships_only_the_parsidion_namespace() -> None:
    cfg = _setuptools()
    assert not cfg.get("py-modules"), (
        "py-modules must stay absent/empty (ARC-201): a top-level module name "
        "in the wheel is a generic site-packages collision surface"
    )
    find_cfg = cfg.get("packages", {}).get("find", {})
    include = find_cfg.get("include", [])
    assert include == ["parsidion*"], (
        f"packages.find include must be exactly ['parsidion*'], got {include!r}"
    )
    assert find_cfg.get("where") == ["skills/parsidion/scripts"]


def test_find_include_patterns_cover_every_parsidion_package() -> None:
    include = _setuptools()["packages"]["find"]["include"]
    for pkg in _package_dirs_on_disk():
        assert any(fnmatch(pkg, pat) for pat in include), (
            f"package directory {pkg!r} matches no packages.find include "
            f"pattern {include}; it would be silently dropped from the wheel"
        )


def _package_dirs_on_disk() -> set[str]:
    return {
        "parsidion." + str(p.relative_to(PARSIDION_DIR)).replace("/", ".")
        for p in PARSIDION_DIR.iterdir()
        if p.is_dir() and (p / "__init__.py").is_file()
    }


def test_console_script_targets_are_in_the_namespace() -> None:
    cfg = _pyproject()
    for script, target in cfg["project"]["scripts"].items():
        module = target.split(":")[0]
        assert module.startswith("parsidion."), (
            f"console script {script} targets {module!r} outside the "
            f"parsidion namespace"
        )
        mod_path = PARSIDION_DIR / Path(*module.split(".")[1:]).with_suffix(".py")
        assert mod_path.is_file(), (
            f"console script {script} targets {module!r} with no module file "
            f"at {mod_path}"
        )


def test_root_entry_scripts_are_not_shipped() -> None:
    """Root scripts/ .py files are by-path entry points, not wheel content."""
    cfg = _setuptools()
    assert not cfg.get("py-modules")
    on_disk = {p.stem for p in SCRIPTS_DIR.glob("*.py")}
    unexpected = on_disk - ROOT_ENTRY_SCRIPTS - NOT_IMPORTABLE
    assert not unexpected, (
        f"modules appeared at the scripts/ root without being added to "
        f"ROOT_ENTRY_SCRIPTS or moved into parsidion/: {sorted(unexpected)}"
    )
    missing = ROOT_ENTRY_SCRIPTS - on_disk
    assert not missing, f"declared root entry scripts absent: {sorted(missing)}"


def test_no_module_level_imports_of_root_scripts_inside_parsidion() -> None:
    """Reverse ARC-003: parsidion.* must not import staying root scripts at
    module level, or console scripts die on import in a clean wheel install.
    Function-level lazy imports are allowed (repo-layout execution contexts
    have the scripts dir on sys.path)."""
    offenders: list[str] = banned_at_module_level()
    assert not offenders, (
        f"module-level imports of root entry scripts inside parsidion/ "
        f"(would break wheel-installed console scripts): {offenders}"
    )


def banned_at_module_level() -> list[str]:
    offenders: list[str] = []
    for path in PARSIDION_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                root = name.split(".")[0]
                if root in ROOT_ENTRY_SCRIPTS:
                    offenders.append(f"{path.name}: {name}")
    return offenders
