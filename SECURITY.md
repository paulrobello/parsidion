# Security Policy

Security policy, scope statement, and vulnerability disclosure process for Parsidion.

## Table of Contents

- [Overview](#overview)
- [Scope](#scope)
- [Stdlib-Only Hook Constraint](#stdlib-only-hook-constraint)
- [Reporting a Vulnerability](#reporting-a-vulnerability)
- [What to Expect](#what-to-expect)
- [Out of Scope](#out-of-scope)
- [Related Documentation](#related-documentation)

## Overview

Parsidion installs runtime adapters with hook scripts that execute during coding-agent
lifecycle events. The Claude Code adapter runs on Claude lifecycle events (SessionStart,
SessionEnd, PreCompact, PostCompact, SubagentStop), and the Codex adapter registers native
Codex session lifecycle hooks (SessionStart and Stop). These adapters run with the same
privileges as the user's agent process and have read/write access to the markdown vault
and their configuration directories (`~/.claude/` and `~/.codex/`). This makes the hook
execution surface security-sensitive.

## Scope

The following components are in scope for security reports:

| Component | Location | Risk surface |
|-----------|----------|--------------|
| Hook scripts | `skills/parsidion/scripts/session_start_hook.py`, `session_stop_hook.py`, `pre_compact_hook.py`, `post_compact_hook.py`, `subagent_stop_hook.py`, `session_stop_wrapper.sh`, `codex_session_start_hook.py`, `codex_stop_hook.py` | Executed on Claude Code lifecycle events and Codex SessionStart/Stop hooks |
| Shared library | `skills/parsidion/scripts/vault_common.py` | Vault path resolution, subprocess environment, SQLite access, file locking |
| Installer | `install.py` | Writes to `~/.claude/settings.json`, `~/.codex/hooks.json`, and `~/.codex/config.toml`; copies files into the user's Claude config directory |
| Session summarizer | `skills/parsidion/scripts/summarize_sessions.py` | Processes transcript content via Claude API; writes vault notes from AI-generated content |
| Vault index | `skills/parsidion/scripts/update_index.py` | Reads all vault notes; writes SQLite database |
| Semantic search | `skills/parsidion/scripts/vault_search.py`, `build_embeddings.py` | Reads SQLite database; returns paths for injection into session context |

## Stdlib-Only Hook Constraint

All hook scripts (`session_start_hook.py`, `session_stop_hook.py`, `pre_compact_hook.py`,
`post_compact_hook.py`, `subagent_stop_hook.py`, `codex_session_start_hook.py`,
`codex_stop_hook.py`, `vault_common.py`, `update_index.py`) use only the **Python standard
library**. No third-party packages are imported at runtime.

This constraint is intentional and security-relevant:

- It eliminates the supply-chain attack surface from third-party packages in the most
  frequently executed code paths
- It ensures the hooks run without prior `pip install` or `uv sync`, reducing the window
  between installation and first execution
- It prevents a compromised package in the Python environment from intercepting vault writes
  or session context

**Exception:** `summarize_sessions.py` and `build_embeddings.py` are PEP 723 scripts with
inline dependency declarations. They run in isolated `uv` environments and are never executed
automatically by hook events — they require explicit user invocation.

Any contribution that adds a third-party import to a hook script or to `vault_common.py`
will be rejected on security grounds, even if the package is widely trusted.

## Reporting a Vulnerability

> **⚠️ Warning:** Do not open a public GitHub issue for security vulnerabilities. Use the
> private channel below.

To report a vulnerability, email **probello@gmail.com** with:

1. A clear description of the vulnerability
2. The affected component(s) and file path(s)
3. Steps to reproduce, including any required preconditions
4. The potential impact (what an attacker could achieve)
5. Any proposed fix or mitigation (optional but appreciated)

Use the subject line: `[SECURITY] Parsidion — <brief description>`

## What to Expect

| Step | Timeline |
|------|----------|
| Acknowledgement | Within 48 hours |
| Initial assessment | Within 5 business days |
| Fix or mitigation published | Depends on severity; critical issues within 7 days |
| Public disclosure | After fix is available, coordinated with the reporter |

Reporters who responsibly disclose a valid vulnerability will be credited in the release
notes (unless they prefer to remain anonymous).

## Out of Scope

The following are not considered security vulnerabilities for the purposes of this policy:

- Vulnerabilities in Obsidian, Claude Code, or other third-party tools this project
  integrates with — report those to their respective maintainers
- Issues requiring physical access to the user's machine
- Social engineering attacks
- Theoretical attacks with no practical exploit path against a default installation
- Denial of service via intentionally malformed vault notes (the vault is user-controlled)

## Related Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system architecture and hook lifecycle
- [CONTRIBUTING.md](CONTRIBUTING.md) — coding constraints including the stdlib-only rule
- [CLAUDE.md](CLAUDE.md) — project-specific guidance for AI assistants
