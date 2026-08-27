"""ARC-101: hook-path config lookups must name the vault they read.

The vault is resolved once per hook run (from the stdin payload's ``cwd``
via ``resolve_vault``), but the config/DB lookup layer
(``load_typed_config`` / ``get_config``) re-resolves from the process cwd
when called without a ``vault`` argument — and ``_resolve_vault_cached``
is LRU-frozen on the first such call. One vault-less lookup therefore
silently pins the process to the wrong vault, mixing two vaults' config,
seed notes, and graph data in a single hook run (the multi-vault
correctness bug this test guards against).

This test parses the hook-path modules with ``ast`` and fails on any call
to ``load_typed_config`` / ``get_config`` that does not pass an explicit
``vault`` keyword. Sites that genuinely execute before a vault exists are
allowlisted below; every new hook-path config read must thread the vault
resolved from the hook payload instead of relying on the process cwd.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)

# (module path relative to scripts/, enclosing function name) pairs that run
# before any vault exists. Keep this list short: a new entry needs a comment
# in the source file (search "ARC-101 allowlist") explaining why no vault is
# resolvable at that point.
_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        # Registry population runs at first adapter lookup, before any hook
        # payload (and thus before any vault) exists.
        ("agent_adapter.py", "_load_external_adapters"),
        # The summarizer resolves CLI/config options in _resolve_options
        # before main() resolves the vault from --vault/cwd.
        ("summarizer/queue.py", "_resolve"),
    }
)

_SCOPED_MODULES: tuple[str, ...] = (
    "session_start_hook.py",
    "agent_adapter.py",
)


def _scoped_files() -> list[Path]:
    files = [_SCRIPTS_DIR / name for name in _SCOPED_MODULES]
    files.extend(sorted((_SCRIPTS_DIR / "session_start").glob("*.py")))
    files.extend(sorted((_SCRIPTS_DIR / "summarizer").glob("*.py")))
    return files


def _callee_name(call: ast.Call) -> str | None:
    """Return the callee's simple name for ``f(...)`` and ``obj.f(...)``."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _vaultless_calls(path: Path) -> list[str]:
    """Return human-readable descriptions of vault-less config calls in *path*."""
    offenders: list[str] = []

    def visit(node: ast.AST, enclosing: str) -> None:
        for child in ast.iter_child_nodes(node):
            child_fn = enclosing
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                child_fn = child.name
            if isinstance(child, ast.Call):
                name = _callee_name(child)
                if name in ("load_typed_config", "get_config") and not any(
                    kw.arg == "vault" for kw in child.keywords
                ):
                    rel = path.relative_to(_SCRIPTS_DIR).as_posix()
                    if (rel, child_fn) not in _ALLOWLIST:
                        offenders.append(f"{rel}:{child.lineno} in {child_fn}()")
            visit(child, child_fn)

    tree = ast.parse(path.read_text(encoding="utf-8"))
    visit(tree, "<module>")
    return offenders


def test_hook_path_config_calls_name_a_vault() -> None:
    files = _scoped_files()
    assert files, "hook-path modules not found — test wiring is broken"

    offenders: list[str] = []
    for path in files:
        offenders.extend(_vaultless_calls(path))
    assert not offenders, (
        "ARC-101: hook-path load_typed_config()/get_config() calls must pass "
        "the vault resolved from the hook payload (vault=...). A vault-less "
        "call re-resolves from the process cwd and silently reads the wrong "
        "vault in multi-vault setups. Found: " + "; ".join(offenders)
    )


def test_allowlist_entries_still_exist() -> None:
    """Every allowlist entry must match a real function — dead entries rot."""

    def has_function(path: Path, name: str) -> bool:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
            for node in ast.walk(tree)
        )

    for rel, fn_name in sorted(_ALLOWLIST):
        path = _SCRIPTS_DIR / rel
        assert path.is_file(), f"ARC-101 allowlist names missing module {rel}"
        assert has_function(path, fn_name), (
            f"ARC-101 allowlist entry ({rel}, {fn_name}) matches no function"
        )
