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
# implementations; the wheel-namespace names are the TUI surface (vault_tui),
# and every hook entry point plus the shared adapter registry. The
# summarizer/, doctor/, and cli/ subpackages are in scope since QA-010: their
# third-party use is guarded-optional (graceful degradation), which the gate
# allows by design; the only out-of-surface file is the PEP 723
# ``summarize_sessions.py`` entry script, which legitimately requires anyio.
_ENFORCEMENT_SCOPE = [
    # core/ stdlib-only library package
    "parsidion.core.vault_config",
    "parsidion.core.vault_schema",
    "parsidion.core.vault_path",
    "parsidion.core.vault_fs",
    # ARC-203: the former vault_index monolith split into frontmatter +
    # note_index + the walk/policy surface; all three share the gate.
    "parsidion.core.frontmatter",
    "parsidion.core.note_index",
    "parsidion.core.vault_index",
    "parsidion.core.rule_triggers",
    "parsidion.core.vault_hooks",
    "parsidion.core.vault_adaptive",
    "parsidion.core.vault_links",
    "parsidion.core.vault_constants",
    "parsidion.core.vault_metrics",
    "parsidion.core.vault_health",
    "parsidion.core.subproc_util",
    # ENH-024: the shared YAML subset module (imported by vault_config,
    # vault_index, and vault_path) shares the same stdlib-only contract.
    "parsidion.core.yaml_lite",
    # ARC-006: the AI/parsight backends moved into core/ and are held to the
    # same contract there.
    "parsidion.core.ai_backend",
    "parsidion.core.parsight_backend",
    # parsidion/ stdlib libraries (ARC-201: shipped in the wheel namespace;
    # the root names remain by-path entry scripts, held to the same
    # contract, and are not part of the wheel. The parsidion.vault_common /
    # ai_backend / parsight_backend / vault_health / transcript_reader
    # re-export shims were retired with a raising ImportError in ARC-200
    # step 2 — the core twins carry the stdlib-only contract now.)
    "parsidion.vault_tui",
    # hooks + shared adapter registry
    "session_start_hook",
    "session_stop_hook",
    "subagent_stop_hook",
    "pre_compact_hook",
    "post_compact_hook",
    "codex_session_start_hook",
    "codex_stop_hook",
    "codex_subagent_stop_hook",
    "antigravity_session_start_hook",
    "antigravity_session_end_hook",
    # ARC-203: session-end pipeline stages split out of agent_adapter.
    "parsidion.agent_pipeline",
    "parsidion.agent_adapter",
    # ARC-202: the shared recall-context pipeline is imported in-process by
    # both retrieval hooks, so it shares the stdlib-only contract.
    "parsidion.recall_context",
    # ENH-007: vault_health scoring is imported by vault_stats + the MCP
    # server, so it is held to the same stdlib-only contract as the rest of
    # the core/hook surface.
    "parsidion.core.vault_health",
    # ENH-008: prompt_templates + note_schema are imported by hooks
    # (session_start_hook → prompt_templates for select-notes) and by the
    # summarizer/doctor, so they share the stdlib-only contract.
    "parsidion.prompt_templates",
    "parsidion.note_schema",
    # ENH-018: the unified transcript reader is imported (lazily) by every
    # hook and adapter, so it shares the stdlib-only contract.
    "parsidion.core.transcript_reader",
    # QA-010: the summarizer/, doctor/, and cli/ subpackages share the
    # stdlib-only contract. Their third-party imports are guarded-optional
    # (the gate allows those by design); the PEP 723 entry script
    # summarize_sessions.py stays out of scope — it legitimately requires
    # anyio. The summarizer subpackage imports anyio only guarded
    # (summarizer/prompt.py) or under TYPE_CHECKING (summarizer/pipeline.py).
    "parsidion.summarizer.__init__",
    "parsidion.summarizer._state_const",
    "parsidion.summarizer.dead_letter",
    "parsidion.summarizer.dedup",
    "parsidion.summarizer.failure",
    "parsidion.summarizer.lock",
    "parsidion.summarizer.notes",
    "parsidion.summarizer.pipeline",
    "parsidion.summarizer.progress",
    "parsidion.summarizer.prompt",
    "parsidion.summarizer.queue",
    "parsidion.summarizer.transcript",
    "parsidion.doctor.__init__",
    "parsidion.doctor._state",
    "parsidion.doctor.check",
    "parsidion.doctor.cli",
    "parsidion.doctor.daily",
    "parsidion.doctor.frontmatter",
    "parsidion.doctor.graph",
    "parsidion.doctor.headings",
    "parsidion.doctor.links",
    "parsidion.doctor.orchestrator",
    "parsidion.doctor.permissions",
    "parsidion.doctor.prefixes",
    "parsidion.doctor.protocol",
    "parsidion.doctor.scan",
    "parsidion.doctor.subfolder",
    "parsidion.doctor.tags",
    "parsidion.doctor.trash",
    "parsidion.doctor.worker",
    "parsidion.cli.__init__",
    "parsidion.cli.index.__init__",
    "parsidion.cli.index._common",
    "parsidion.cli.index.build",
    "parsidion.cli.index.cli",
    "parsidion.cli.index.db",
    "parsidion.cli.index.graph",
    "parsidion.cli.index.models",
    "parsidion.cli.index.parse",
    "parsidion.cli.index.render",
    "parsidion.cli.merge.__init__",
    "parsidion.cli.merge.ai_helpers",
    "parsidion.cli.merge.display",
    "parsidion.cli.merge.frontmatter",
    "parsidion.cli.merge.index",
    "parsidion.cli.merge.lookup",
    "parsidion.cli.merge.preview",
    "parsidion.cli.merge.scan",
    "parsidion.cli.search.__init__",
    "parsidion.cli.search._common",
    "parsidion.cli.search.embeddings",
    "parsidion.cli.search.format",
    "parsidion.cli.search.metadata",
    "parsidion.cli.stats.__init__",
    "parsidion.cli.stats._common",
    "parsidion.cli.stats.cli",
    "parsidion.cli.stats.dashboard",
    "parsidion.cli.stats.graph",
    "parsidion.cli.stats.health",
    "parsidion.cli.stats.operations",
    "parsidion.cli.stats.overview",
    "parsidion.cli.stats.rollups",
    "parsidion.cli.stats.summary",
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
