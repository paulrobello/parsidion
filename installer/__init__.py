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
