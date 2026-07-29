"""QA-009: build_embeddings.py and embed_eval_run.py must degrade gracefully
when the ``search``/``eval`` extra is absent.

The audit found both files import ``sqlite_vec``/``fastembed`` at module top
level and raise a raw ``ImportError`` when the extra is missing. ``CLAUDE.md``
states these files "degrade gracefully when absent" — three of the four
optional-dependency consumers already did (vault_search, vault_merge,
vault_conflicts). These tests pin the corrected behaviour so the next
regression surfaces as a clean test failure rather than a user-visible
traceback.

The test poisons ``sys.modules`` to make ``fastembed``/``sqlite_vec`` raise
``ImportError`` on import (the documented Python convention), then spawns the
script as a real subprocess so the import-time guard fires the same way it
would in production.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)


def _run_with_poisoned_imports(script: Path) -> subprocess.CompletedProcess:
    """Spawn ``script`` with ``fastembed``/``sqlite_vec`` forced to ImportError.

    Uses Python's documented convention: ``sys.modules[name] = None`` makes
    ``import name`` raise ``ImportError``. The poison runs before the script
    body via ``-c``, then ``runpy.run_path`` executes the script under
    ``__main__`` so ``if __name__ == "__main__":`` still fires.
    """
    poison = (
        "import sys; "
        "sys.modules['fastembed'] = None; "
        "sys.modules['sqlite_vec'] = None; "
        "sys.modules['rich'] = None; "
        f"import runpy; runpy.run_path({str(script)!r}, run_name='__main__')"
    )
    env = {**os.environ, "PYTHONPATH": str(_SCRIPTS_DIR)}
    return subprocess.run(
        [sys.executable, "-c", poison],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )


@pytest.mark.timeout(20)
class TestBuildEmbeddingsGracefulDegrade:
    """build_embeddings.py must exit 1 with a clean message, not a traceback."""

    def test_missing_extra_emits_actionable_error(self) -> None:
        result = _run_with_poisoned_imports(_SCRIPTS_DIR / "build_embeddings.py")
        assert result.returncode == 1
        # Actionable: the message names the extra and how to install it
        assert "search" in result.stderr
        # Must NOT leak a raw traceback — that's the regression this guards
        assert "Traceback" not in result.stderr


@pytest.mark.timeout(20)
class TestEmbedEvalRunGracefulDegrade:
    """embed_eval_run.py must exit 1 with a clean message, not a traceback."""

    def test_missing_extra_emits_actionable_error(self) -> None:
        result = _run_with_poisoned_imports(_SCRIPTS_DIR / "embed_eval_run.py")
        assert result.returncode == 1
        assert "eval" in result.stderr
        assert "Traceback" not in result.stderr
