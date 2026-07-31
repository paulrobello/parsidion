"""Parsidion installer package.

Sub-modules:
  colors   — ANSI colour helpers
  ui       — interactive print/prompt helpers
  paths    — path constants, VAULT_DIRS, runtime predicates
  hooks    — hook merge/remove for Claude, Codex, Gemini
  schedule — launchd/cron nightly-summarizer scheduler
  vault    — vault dir creation, git setup, config.yaml, vaults.yaml
  skill    — skill/agent/script install, AI mode, legacy cleanup, uninstall

install.py remains the public entry point and re-exports all public symbols
so that ``import install; install.<name>`` continues to work for test suites
and callers that rely on the flat public API.
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
