"""Parsidion installer package.

Sub-modules:
  colors   — ANSI colour helpers
  ui       — interactive print/prompt helpers
  paths    — path constants, VAULT_DIRS, runtime predicates
  hooks    — hook merge/remove for Claude, Codex, Gemini
  schedule — launchd/cron nightly-summarizer scheduler
  vault    — vault dir creation, git setup, config.yaml, vaults.yaml
  skill    — skill/agent/script install, AI mode, legacy cleanup, uninstall
  plan     — InstallPlan matrix, option prompts, StepList builder (ARC-008)
  cli      — argument parsing + the install()/uninstall() driver (ARC-008)

``install.py`` is a thin entry shim over ``installer.cli.main`` (ARC-008);
it no longer re-exports the installer API — import from the owning
sub-module instead.
"""

import sys as _sys
from pathlib import Path as _Path

# ENH-006: make the skill's scripts/ importable so installer sub-modules can
# read the agent_adapter runtime registry. Runs once at package import, before
# any sub-module body, so a sub-module can `import agent_adapter` unconditionally.
_SCRIPTS_DIR = (
    _Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in _sys.path:
    _sys.path.insert(0, str(_SCRIPTS_DIR))
