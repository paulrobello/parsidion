"""Post-merge ``update_index.py`` invocation (ARC-005).

Extracted from ``vault_merge.py``. Re-exported by the entry shim so
``vault_merge._rebuild_index`` keeps resolving for tests and other callers.

Stdlib-only at module load.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _rebuild_index() -> None:
    """Run update_index.py to rebuild the vault index after a merge.

    Locates ``update_index.py`` either alongside this module (editable
    checkout) or under the installed ``~/.claude/skills/parsidion/scripts``
    path, then invokes it under ``uv run`` with a 300 s timeout that matches
    the bound ``update_index.py`` itself applies to its graph-rebuild child.
    Best-effort: every failure mode logs a warning and returns so a stuck
    or absent indexer never aborts an already-completed merge.
    """
    # ``cli/merge/index.py`` is three levels below ``scripts/``, so the
    # editable-checkout indexer sits at ``<scripts>/update_index.py``.
    index_script = Path(__file__).parent.parent.parent / "update_index.py"
    if not index_script.exists():
        index_script = (
            Path.home()
            / ".claude"
            / "skills"
            / "parsidion"
            / "scripts"
            / "update_index.py"
        )
    if not index_script.exists():
        print(
            "Warning: update_index.py not found, skipping index rebuild.",
            file=sys.stderr,
        )
        return
    try:
        # QA-005: bound the index rebuild — a hung child stalls the merge
        # flow and leaves the vault with stale manifests. 300 s matches the
        # bound update_index.py applies to its own graph-rebuild child.
        subprocess.run(
            ["uv", "run", str(index_script)],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        print("Vault index rebuilt.")
    except subprocess.TimeoutExpired:
        print(
            "Warning: update_index.py timed out after 300s — index left stale.",
            file=sys.stderr,
        )
    except subprocess.CalledProcessError as e:
        print(f"Warning: index rebuild failed: {e.stderr}", file=sys.stderr)
    except OSError as e:
        print(f"Warning: could not run update_index.py: {e}", file=sys.stderr)
