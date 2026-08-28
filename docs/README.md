# Documentation Index

Navigation guide for all documentation in the `docs/` directory.

## Table of Contents

- [Overview](#overview)
- [Documents](#documents)
- [Where to Start](#where-to-start)
- [Related Documentation](#related-documentation)

## Overview

This directory contains technical documentation for Parsidion. Each file is described
below with its intended audience and purpose.

## Documents

| File | Description |
|------|-------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System architecture, component overview, hook lifecycle, full configuration reference, file layout, and data flow. Start here for the component catalogue (every script, hook, and agent), the `config.yaml` per-key reference, and the Obsidian graph color-group table. |
| [USAGE.md](USAGE.md) | Complete vault CLI reference: every `vault-search` / `vault-new` / `vault-stats` / `vault-review` / `vault-doctor` / `vault-merge` / `vault-conflicts` / `vault-export` flag, the runtime environment-variable table, the trigger eval, the programmatic `vault_common` API, and the install/uninstall commands. |
| [MULTI_VAULT.md](MULTI_VAULT.md) | Multi-vault setup (`vaults.yaml`), the `--vault` flag, vault-aware tools and hooks, and the five-step default vault resolution order. |
| [PI_EXTENSION.md](PI_EXTENSION.md) | pi runtime integration: install the TypeScript extension (`scripts/install-pi-extension`), effective `anthropic_env` precedence, and the three-step pi SessionEnd/SubagentStop/summarizer smoke tests. |
| [AGENT-ADAPTERS.md](AGENT-ADAPTERS.md) | The runtime-adapter contract: one `AgentAdapter` per coding-agent runtime drives the hook shims, the installer, and `connect`/`disconnect` — and how to add a runtime (data-only) or a third-party drop-in adapter. |
| [EMBEDDINGS.md](EMBEDDINGS.md) | Semantic search setup: building the embedding index, searching the vault, configuration reference, and integration with hooks and agents. |
| [EMBEDDINGS_EVAL.md](EMBEDDINGS_EVAL.md) | Evaluation harness for benchmarking embedding model and chunking strategy combinations against Claude-generated ground-truth queries. |
| [PROMPTS.md](PROMPTS.md) | How Parsidion's six AI prompts are externalized into versioned template files, the strict-variable loader that renders them, prompt-version stamping on notes, and the opt-in eval harness that scores a prompt edit against a golden transcript set. |
| [MCP.md](MCP.md) | parsidion-mcp server: FastMCP-based MCP server that exposes vault read, write, search, and maintenance operations to Claude Desktop and MCP-capable clients. |
| [AGENTCHROME.md](AGENTCHROME.md) | AgentChrome browser control CLI: installation, capabilities, and integration with the research agent for fetching fully-rendered pages. |
| [archive/MCPL.md](archive/MCPL.md) | **Legacy, not installed** — MCP Launchpad CLI reference, retained for history. The research agent only considers `mcpl` when it is already on `PATH` (`which mcpl`). |
| [PARSIGHT.md](PARSIGHT.md) | Optional parsight code-memory backend (**parsight itself is coming soon — not yet publicly available**): when its daemon is running, vault semantic search is served by parsight's hybrid BM25+vector+graph retrieval instead of the local embeddings pipeline, and a code-memory bridge is exposed to the vault-explorer agent and parsidion-mcp. |
| [VISUALIZER.md](VISUALIZER.md) | Vault Visualizer: interactive web app for reading and graph-exploring vault notes — architecture, graph engine, data model, and configuration. |
| [VAULT_SYNC.md](VAULT_SYNC.md) | Multi-machine vault sync: strategies, recommended git-based setup, post-merge hook, conflict handling, and troubleshooting. |
| [CLAUDE.md](CLAUDE.md) | AI-assistant guidance specific to working in the `docs/` directory: points at the style guide and this index. |
| [DOCUMENTATION_STYLE_GUIDE.md](DOCUMENTATION_STYLE_GUIDE.md) | Documentation standards for this project: formatting, diagrams, code block conventions, and the review checklist. |
| [opus/](opus/) | Implementation plans for tracked enhancements ENH-001 through ENH-014 (semantic-edge capping, incremental graph generation, persistent embedding service, note-index single read path, cross-language parity fixtures, agent-adapter registry, vault health score, prompt templates & eval, Python-canonical vault resolution, incremental graph rebuild, generated API reference, SSE route tests, the obsolete eval dead-code sweep, and the typed config schema). |
| [fable/](fable/) | Implementation plans for the Fable-cycle enhancements ENH-015 through ENH-024 (doctor rule selection, adaptive context decay, config docs generator, unified transcript reader, hook latency budget, embedding-service lifecycle, reverse-link index, vec0 ANN search, SessionStart latency bench, and yaml-lite consolidation). |
| [superpowers/](superpowers/) | Implementation plans (`superpowers/plans/`) and design specs (`superpowers/specs/`) for major features (vault-explorer agent, subagent stop hook, parsidion-mcp, visualizer redesign, git diff viewer, multi-vault support, graph features, Codex/Gemini runtime hooks, and more). |
| [parsidion-architecture.png](parsidion-architecture.png) | Architecture diagram embedded by the root README. |
| Slideshows (`*.html`) | Self-contained walkthroughs published to GitHub Pages: [vault architecture](vault-architecture-slideshow.html), [vault explorer](vault-explorer-slideshow.html), [research agent](research-agent-slideshow.html), [project explorer](project-explorer-slideshow.html), [vault deduplicator](vault-deduplicator-slideshow.html). |
| [archive/CHANGELOG-0.11-and-older.md](archive/CHANGELOG-0.11-and-older.md) | Archived changelog entries for Parsidion 0.1.0 through 0.11.x (covers the pre-0.7.0 `parsidion-cc` era and the 0.6.0 rebrand). The current changelog (0.12.x onward) lives at the repo root: [../CHANGELOG.md](../CHANGELOG.md). |

> **Note:** `ideas.md` is gitignored locally (it is a personal scratchpad of visualizer enhancement ideas) and is intentionally not published to GitHub Pages or linked from this index.

## Where to Start

- **New to the project?** Read [ARCHITECTURE.md](ARCHITECTURE.md) first, then the root [README.md](../README.md).
- **Looking for a CLI command?** See [USAGE.md](USAGE.md) — the full vault CLI reference.
- **Setting up more than one vault?** See [MULTI_VAULT.md](MULTI_VAULT.md).
- **Installing the pi extension?** See [PI_EXTENSION.md](PI_EXTENSION.md).
- **Setting up semantic search?** See [EMBEDDINGS.md](EMBEDDINGS.md).
- **Evaluating which embedding model to use?** See [EMBEDDINGS_EVAL.md](EMBEDDINGS_EVAL.md).
- **Editing a vault-note prompt or running a prompt eval?** See [PROMPTS.md](PROMPTS.md).
- **Using the MCP server with Claude Desktop?** See [MCP.md](MCP.md).
- **Sharing the vault across machines?** See [VAULT_SYNC.md](VAULT_SYNC.md).
- **Using parsight as the vault search backend?** See [PARSIGHT.md](PARSIGHT.md).
- **Exploring the vault visually?** See [VISUALIZER.md](VISUALIZER.md).
- **Hunting for an older release note (0.11.x or earlier)?** See [archive/CHANGELOG-0.11-and-older.md](archive/CHANGELOG-0.11-and-older.md).
- **Writing or updating documentation?** Follow [DOCUMENTATION_STYLE_GUIDE.md](DOCUMENTATION_STYLE_GUIDE.md).

## Related Documentation

- [README.md](../README.md) — project overview, quick start, installation, and usage
- [CONTRIBUTING.md](../CONTRIBUTING.md) — development setup, coding constraints, and PR guidelines
- [SECURITY.md](../SECURITY.md) — vulnerability disclosure policy and scope statement
- [CHANGELOG.md](../CHANGELOG.md) — version history
- [api/](api/) — generated API reference (Python via pdoc, visualizer TypeScript via typedoc); regenerate with `make docs-api` and check for drift with `make docs-api-check`
- [generated/](generated/) — generated config reference (config-table.md, config-reference.md) derived from `core/vault_schema.py`; regenerate with `make config-docs` and check for drift with `make config-docs-check`
