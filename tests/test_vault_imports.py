"""ARC-023: import-isolation / top-level-cycle tests for the vault_* cluster.

Two cycles were called out:

* **Cycle 1** (``vault_fs -> vault_hooks -> vault_fs``): historically broken
  only by a function-body lazy import of ``TRANSCRIPT_CATEGORY_LABELS``.
  ARC-023 hoists that constant to a true leaf module (``vault_constants``)
  so both consumers can take a top-level import.  This test proves the
  top-level cycle is gone by importing the pair in a *fresh* subprocess
  (the test process already has every module loaded via conftest, so an
  in-process import would hide a regression).

* **Cycle 3** (``vault_search`` <-> ``vault_tui``): confirmed already lazy
  on both sides -- neither top-level imports the other.  We assert that
  structurally via AST (no top-level ``import vault_tui`` in vault_search
  and no top-level ``import vault_search`` in vault_tui), so a future edit
  that promotes the lazy import to top level fails this test loudly rather
  than silently re-introducing a cycle.  No code change was needed here;
  this test documents & closes cycle 3.

stdlib-only and always-running (no numpy / fastembed / curses gate).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _REPO_ROOT / "skills" / "parsidion" / "scripts"

_VAULT_FS = _SCRIPTS_DIR / "vault_fs.py"
_VAULT_SEARCH = _SCRIPTS_DIR / "vault_search.py"
_VAULT_TUI = _SCRIPTS_DIR / "vault_tui.py"


def _top_level_imported_modules(path: Path) -> set[str]:
    """Return the set of module names imported at module level in *path*.

    Only ``import X`` / ``import X.Y`` and ``from X import ...`` statements
    that sit directly in the module body (not nested inside a function or
    class) are collected.  Lazy imports inside function bodies are
    intentionally excluded -- the cycle check cares only about top-level
    edges.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module.split(".")[0])
    return names


class TestArc023Cycle1:
    """``vault_fs`` and ``vault_hooks`` must not form a top-level cycle.

    Pre-ARC-023 the cycle was masked by ``append_session_to_daily`` doing a
    function-body ``from vault_hooks import TRANSCRIPT_CATEGORY_LABELS``.
    The constant now lives in the ``vault_constants`` leaf and both modules
    top-level-import it, so neither direction of the vault_fs/vault_hooks
    edge may resurrect a cycle.
    """

    def test_vault_constants_is_a_true_leaf(self) -> None:
        """vault_constants must not import any other vault_* module.

        A leaf with no vault_* edges is what makes the hoist cycle-proof.
        If someone adds ``import vault_fs`` here the cycle comes straight
        back.
        """
        imports = _top_level_imported_modules(_SCRIPTS_DIR / "vault_constants.py")
        vault_imports = {m for m in imports if m.startswith("vault_")}
        assert vault_imports == set(), (
            f"vault_constants.py must be a leaf but top-level imports: {vault_imports}"
        )

    def test_no_top_level_cycle_vault_fs_hooks(self) -> None:
        """Importing vault_fs then vault_hooks in a fresh process must succeed.

        A top-level ImportCycle would raise ``ImportError: cannot import
        name ...`` at module load.  Running in a subprocess is load-bearing:
        the test process has already imported both via conftest, so an
        in-process ``import`` would silently succeed even if a cycle were
        re-introduced.
        """
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import vault_fs, vault_hooks, vault_constants; "
                "assert vault_hooks.TRANSCRIPT_CATEGORY_LABELS['error_fix'] "
                "== vault_fs.TRANSCRIPT_CATEGORY_LABELS['error_fix'] "
                "== 'Error Resolution'",
            ],
            env={"PYTHONPATH": str(_SCRIPTS_DIR), "PATH": ""},
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, (
            "ARC-023 cycle 1 regression: importing vault_fs + vault_hooks "
            f"failed in a fresh process.\nstdout={result.stdout}\n"
            f"stderr={result.stderr}"
        )

    def test_vault_fs_top_level_imports_constants(self) -> None:
        """vault_fs must top-level-import TRANSCRIPT_CATEGORY_LABELS.

        Guards against a regression where the hoist is reverted to the old
        function-body lazy import.
        """
        src = _VAULT_FS.read_text(encoding="utf-8")
        assert "from vault_constants import TRANSCRIPT_CATEGORY_LABELS" in src, (
            "vault_fs.py should top-level-import TRANSCRIPT_CATEGORY_LABELS "
            "from vault_constants (ARC-023 cycle 1)"
        )
        # The old lazy import inside append_session_to_daily must be gone.
        assert "from vault_hooks import TRANSCRIPT_CATEGORY_LABELS" not in src, (
            "vault_fs.py still lazy-imports TRANSCRIPT_CATEGORY_LABELS from "
            "vault_hooks -- the cycle was not fully hoisted"
        )


class TestArc023Cycle3:
    """``vault_search`` and ``vault_tui`` have no actionable top-level cycle.

    Both sides already defer the cross-import to a function body
    (``vault_search.run_interactive`` lazy-imports ``vault_tui``; the TUI's
    ``_search_notes`` lazy-imports ``vault_search``).  No top-level edge in
    either direction means no top-level cycle.  These tests pin that
    invariant so a future refactor promoting the lazy import to module level
    is caught here rather than producing a confusing import error at runtime.
    """

    def test_vault_search_no_top_level_vault_tui(self) -> None:
        imports = _top_level_imported_modules(_VAULT_SEARCH)
        assert "vault_tui" not in imports, (
            "vault_search.py top-level imports vault_tui -- this "
            "re-introduces the cycle the lazy import was avoiding"
        )

    def test_vault_tui_no_top_level_vault_search(self) -> None:
        imports = _top_level_imported_modules(_VAULT_TUI)
        assert "vault_search" not in imports, (
            "vault_tui.py top-level imports vault_search -- this "
            "re-introduces the cycle the lazy import was avoiding"
        )

    def test_cross_imports_are_lazy(self) -> None:
        """Document that the vault_search/vault_tui cross-imports ARE lazy.

        Confirms the bidirectional edge still exists but only inside
        function bodies -- which is why there is no top-level cycle to fix.
        Closing note for ARC-023 cycle 3.
        """
        search_src = _VAULT_SEARCH.read_text(encoding="utf-8")
        tui_src = _VAULT_TUI.read_text(encoding="utf-8")
        # The cross-imports must remain present (just deferred).
        assert "import vault_tui" in search_src or "from vault_tui" in search_src, (
            "vault_search should still lazy-import vault_tui inside a function"
        )
        assert "import vault_search" in tui_src or "from vault_search" in tui_src, (
            "vault_tui should still lazy-import vault_search inside a function"
        )
