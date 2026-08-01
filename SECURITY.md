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
SessionEnd, PreCompact, PostCompact, SubagentStop), the Codex adapter registers native
Codex session lifecycle hooks (SessionStart, Stop, SubagentStop), and the Gemini adapter
registers Gemini CLI `SessionStart` / `SessionEnd` hooks in `~/.gemini/settings.json`.
These adapters run with the same privileges as the user's agent process and have read/write
access to the markdown vault and their configuration directories (`~/.claude/`, `~/.codex/`,
`~/.gemini/`). This makes the hook execution surface security-sensitive. The pi runtime ships a
TypeScript extension (no hook registration), and opt-in **external adapter loading**
(`adapters.load_external`, default off) executes Python from `~/.config/parsidion/adapters/` — gated
behind the flag, permission-checked (group/world-writable files refused), and load-time logged. See
[docs/AGENT-ADAPTERS.md](docs/AGENT-ADAPTERS.md).

## Scope

The following components are in scope for security reports:

| Component | Location | Risk surface |
|-----------|----------|--------------|
| Hook scripts | `skills/parsidion/scripts/session_start_hook.py`, `session_stop_hook.py`, `pre_compact_hook.py`, `post_compact_hook.py`, `subagent_stop_hook.py`, `session_stop_wrapper.sh`, `codex_session_start_hook.py`, `codex_stop_hook.py`, `codex_subagent_stop_hook.py`, `gemini_session_start_hook.py`, `gemini_session_end_hook.py` | Executed on Claude Code, Codex CLI, and Gemini CLI lifecycle events |
| Shared library (stdlib-only, see constraint below) | `skills/parsidion/scripts/vault_common.py` re-export facade plus the `core/` implementations (`core/vault_config.py`, `vault_path.py`, `vault_fs.py`, `vault_index.py`, `vault_hooks.py`, `vault_adaptive.py`, `vault_links.py`, `vault_constants.py`, `vault_metrics.py`, `vault_health.py`, `subproc_util.py`) and the flat re-export shims (`vault_config.py`, `vault_path.py`, `vault_fs.py`, `vault_index.py`, `vault_hooks.py`, `vault_adaptive.py`, `vault_links.py`, `vault_metrics.py`, `vault_tui.py`); plus `ai_backend.py`, `parmem_backend.py`, `agent_adapter.py`, `prompt_templates.py`, `note_schema.py` | Vault path resolution, subprocess environment, SQLite access, file locking, prompt-AI backend selection, par-mem bridge, adapter registry, prompt rendering. Imports cleanly under the stdlib-only enforcement test |
| Vault CLI tools (user-invoked, not hook-driven) | `skills/parsidion/scripts/vault_new.py`, `vault_review.py`, `vault_export.py`, `vault_merge.py`, `vault_conflicts.py`, `vault_doctor.py`, `vault_stats.py`, `build_graph.py`, `vault_embed_serve.py`, `update_index.py` | Invoked explicitly by the user or MCP server. Several use guarded optional extras: `build_graph.py` requires `numpy` (PEP 723), `vault_search.py`/`build_embeddings.py` use `fastembed` + `sqlite-vec`; these are out of the stdlib-only enforcement scope by design |
| Installer | `install.py` | Writes to `~/.claude/settings.json`, `~/.codex/hooks.json`, `~/.codex/config.toml`, and `~/.gemini/settings.json`; copies files into the user's agent config directory |
| Runtime adapters | `skills/parsidion/scripts/agent_adapter.py`, `~/.config/parsidion/adapters/*.py` | Registry of hook/adapter descriptors; opt-in external adapter loading executes Python from the drop-in dir (default off, permission-checked, logged) |
| Session summarizer | `skills/parsidion/scripts/summarize_sessions.py` | Processes transcript content via Claude API; writes vault notes from AI-generated content |
| Semantic search | `skills/parsidion/scripts/vault_search.py`, `build_embeddings.py` | Reads SQLite database; returns paths for injection into session context |
| Vault Visualizer | `visualizer/app/api/**/*.ts`, `visualizer/lib/apiAuth.ts` | The only network-facing component: a local Next.js server (port 3999) with read/write API routes over the vault directory. Same-origin (`Sec-Fetch-Site`) guard on every route; optional `VISUALIZER_TOKEN` bearer-token auth that gates both reads and writes (SEC-102); vault path validated against an allowlist (`resolveVault`) |

## Stdlib-Only Hook Constraint

The hot path — every module imported on a hook event — uses only the **Python standard
library**. No third-party packages are imported at runtime. This is structurally enforced by
`tests/test_stdlib_only.py`, which imports each in-scope module in a fresh interpreter with
`rich`, `fastembed`, `sqlite_vec`, `anyio`, `yaml`, `numpy`, `PIL`, `requests`, and `aiohttp`
poisoned in `sys.modules`; a violation — even a transitive one — fails CI.

**In scope (enforced):**

- The `core/` library package: `vault_config`, `vault_path`, `vault_fs`, `vault_index`,
  `vault_hooks`, `vault_adaptive`, `vault_links`, `vault_constants`, `vault_metrics`,
  `vault_health`, `subproc_util`
- The flat re-export shims: `vault_common`, `vault_config`, `vault_path`, `vault_fs`,
  `vault_index`, `vault_hooks`, `vault_adaptive`, `vault_links`, `vault_metrics`, `vault_tui`
- Supporting libraries imported by hooks: `ai_backend`, `parmem_backend`, `agent_adapter`,
  `vault_health`, `prompt_templates`, `note_schema`
- Every hook entry point: `session_start_hook`, `session_stop_hook`, `subagent_stop_hook`,
  `pre_compact_hook`, `post_compact_hook`, `codex_session_start_hook`, `codex_stop_hook`,
  `codex_subagent_stop_hook`, `gemini_session_start_hook`, `gemini_session_end_hook`

**Out of scope (explicit-invocation tools):** the CLI and build tools — `vault_new.py`,
`vault_review.py`, `vault_export.py`, `vault_merge.py`, `vault_conflicts.py`,
`vault_doctor.py`, `vault_stats.py`, `vault_search.py`, `build_embeddings.py`,
`build_graph.py`, `vault_embed_serve.py`, `update_index.py`, `summarize_sessions.py`,
`embed_eval*.py`, `html-to-md.py` — are user-invoked and legitimately use guarded optional
extras. `build_graph.py` is a PEP 723 script requiring `numpy`; `summarize_sessions.py` and
`build_embeddings.py` are PEP 723 scripts with inline dependency declarations that run in
isolated `uv` environments and are never executed by hook events. None of these are imported
from the hot path.

This constraint is intentional and security-relevant:

- It eliminates the supply-chain attack surface from third-party packages in the most
  frequently executed code paths
- It ensures the hooks run without prior `pip install` or `uv sync`, reducing the window
  between installation and first execution
- It prevents a compromised package in the Python environment from intercepting vault writes
  or session context

The Vault Visualizer is a TypeScript/Next.js component (not Python) and is therefore out of
scope for the stdlib-only constraint, but its network-facing routes are listed above and are
covered by the same vulnerability-disclosure process.

Any contribution that adds a third-party import to an in-scope module (the `core/` package,
any flat re-export shim, the hook entry points, or the supporting libraries listed above)
will be rejected on security grounds, even if the package is widely trusted.

## Reporting a Vulnerability

> **Warning:** Do not open a public GitHub issue for security vulnerabilities. Use the
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
