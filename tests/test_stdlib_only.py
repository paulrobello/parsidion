"""ARC-004: structural enforcement of the stdlib-only constraint.

The project's hardest rule -- the ``core`` library package and every hook
script must depend on the Python standard library only, never on ``rich``,
``fastembed``, ``sqlite_vec``, ``anyio``, etc. -- was previously documented
but unenforced: nothing prevented a hook from gaining a ``import rich``. This
test converts the rule into an executable invariant.

For every module in the enforcement scope we spawn a **fresh interpreter**
(the test process has already imported everything via conftest, so an
in-process import would hide a violation), inject ``None`` into
``sys.modules`` for each forbidden package (the standard CPython convention
that makes ``import <name>`` raise ``ImportError``), set ``PYTHONPATH`` at the
scripts dir, and assert the module imports cleanly. A violation -- even a
transitive one, where a core module imports another module that imports an
extra -- fails here loudly.

``test_poison_actually_blocks_an_installed_module`` proves the harness has
teeth: it poisons ``this`` (a stdlib module that is always installed and never
imported by this codebase) and asserts the import is blocked, so a future
change that silently disabled the poison cannot make every case pass vacuously.

Note: a *guarded* optional import (``try: import rich ... except ImportError``)
passes this test by design -- graceful degradation to stdlib is allowed; an
*unguarded* or *required* third-party import is what fails.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"

# Third-party packages the stdlib-only surface must never require. ``None`` in
# sys.modules makes ``import <name>`` raise ImportError ("import halted; None
# in sys.modules") -- including transitive imports triggered at module load.
_FORBIDDEN = (
    "rich",
    "fastembed",
    "sqlite_vec",
    "sqlitevec",
    "anyio",
    "yaml",
    "pyyaml",
    "numpy",
    "PIL",
    "pillow",
    "requests",
    "aiohttp",
)

_POISON_SETUP = "import sys\n" + "\n".join(
    f"sys.modules[{name!r}] = None" for name in _FORBIDDEN
)

# Modules whose stdlib-only status is enforced. ``core.*`` are the library
# implementations; the root names are the facade (vault_common), the TUI
# surface (vault_tui), the AI/parsight backends hooks depend on, and every hook
# entry point plus the shared adapter registry. CLIs and eval/build tools are
# excluded -- they legitimately use guarded optional extras.
_ENFORCEMENT_SCOPE = [
    # core/ stdlib-only library package
    "core.vault_config",
    "core.vault_schema",
    "core.vault_path",
    "core.vault_fs",
    "core.vault_index",
    "core.vault_hooks",
    "core.vault_adaptive",
    "core.vault_links",
    "core.vault_constants",
    "core.vault_metrics",
    "core.vault_health",
    "core.subproc_util",
    # ARC-006: the AI/parsight backends moved into core/ (the root names below
    # remain as re-export shims and are held to the same contract).
    "core.ai_backend",
    "core.parsight_backend",
    # root stdlib libraries / facade
    "vault_common",
    "vault_tui",
    "ai_backend",
    "parsight_backend",
    # hooks + shared adapter registry
    "session_start_hook",
    "session_stop_hook",
    "subagent_stop_hook",
    "pre_compact_hook",
    "post_compact_hook",
    "codex_session_start_hook",
    "codex_stop_hook",
    "codex_subagent_stop_hook",
    "gemini_session_start_hook",
    "gemini_session_end_hook",
    "agent_adapter",
    # ENH-007: vault_health scoring is imported by vault_stats + the MCP
    # server, so it is held to the same stdlib-only contract as the rest of
    # the core/hook surface.
    "vault_health",
    # ENH-008: prompt_templates + note_schema are imported by hooks
    # (session_start_hook → prompt_templates for select-notes) and by the
    # summarizer/doctor, so they share the stdlib-only contract.
    "prompt_templates",
    "note_schema",
    # ENH-018: the unified transcript reader is imported (lazily) by every
    # hook and adapter, so it shares the stdlib-only contract.
    "transcript_reader",
]


def _import_under_poison(module: str) -> None:
    code = f"{_POISON_SETUP}\nimport {module}\n"
    env = {**os.environ, "PYTHONPATH": str(_SCRIPTS)}
    env.pop("CLAUDECODE", None)
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, (
        f"{module} failed to import under stdlib-only enforcement "
        f"(one of {_FORBIDDEN} is required). A core/hook module must not "
        f"depend on a third-party package.\nstdout={result.stdout}\n"
        f"stderr={result.stderr}"
    )


@pytest.mark.parametrize("module", _ENFORCEMENT_SCOPE)
def test_stdlib_only(module: str) -> None:
    """The module imports cleanly with third-party packages blocked."""
    _import_under_poison(module)


def test_poison_actually_blocks_an_installed_module() -> None:
    """The harness must block an installed module, or every case passes vacuously.

    ``this`` is stdlib (always installed) and never imported by this codebase,
    so poisoning it and expecting failure proves the None-in-sys.modules
    mechanism is live regardless of which extras happen to be installed.
    """
    code = "import sys\nsys.modules['this'] = None\nimport this\n"
    result = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "PYTHONPATH": str(_SCRIPTS)},
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode != 0, (
        "Poison harness is inert: `import this` succeeded despite being "
        "poisoned, so the stdlib-only test cannot detect violations."
    )
