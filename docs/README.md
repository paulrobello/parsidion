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
| [ARCHITECTURE.md](ARCHITECTURE.md) | System architecture, component overview, hook lifecycle, and data flow. Start here to understand how the pieces fit together. |
| [EMBEDDINGS.md](EMBEDDINGS.md) | Semantic search setup: building the embedding index, searching the vault, configuration reference, and integration with hooks and agents. |
| [EMBEDDINGS_EVAL.md](EMBEDDINGS_EVAL.md) | Evaluation harness for benchmarking embedding model and chunking strategy combinations against Claude-generated ground-truth queries. |
| [MCP.md](MCP.md) | parsidion-mcp server: FastMCP-based MCP server that exposes vault read, write, search, and maintenance operations to Claude Desktop and MCP-capable clients. |
| [AGENTCHROME.md](AGENTCHROME.md) | AgentChrome browser control CLI: installation, capabilities, and integration with the research agent for fetching fully-rendered pages. |
| [MCPL.md](MCPL.md) | MCP Launchpad CLI: installation, configuration, and use as a fallback search gateway when the research agent exhausts other search options. |
| [VISUALIZER.md](VISUALIZER.md) | Vault Visualizer: interactive web app for reading and graph-exploring vault notes — architecture, graph engine, data model, and configuration. |
| [VAULT_SYNC.md](VAULT_SYNC.md) | Multi-machine vault sync: strategies, recommended git-based setup, post-merge hook, conflict handling, and troubleshooting. |
| [DOCUMENTATION_STYLE_GUIDE.md](DOCUMENTATION_STYLE_GUIDE.md) | Documentation standards for this project: formatting, diagrams, code block conventions, and the review checklist. |
| [superpowers/](superpowers/) | Implementation plans and design specs for major features (vault-explorer agent, subagent stop hook, parsidion-mcp, visualizer redesign, git diff viewer). |

## Where to Start

- **New to the project?** Read [ARCHITECTURE.md](ARCHITECTURE.md) first, then the root [README.md](../README.md).
- **Setting up semantic search?** See [EMBEDDINGS.md](EMBEDDINGS.md).
- **Evaluating which embedding model to use?** See [EMBEDDINGS_EVAL.md](EMBEDDINGS_EVAL.md).
- **Using the MCP server with Claude Desktop?** See [MCP.md](MCP.md).
- **Sharing the vault across machines?** See [VAULT_SYNC.md](VAULT_SYNC.md).
- **Exploring the vault visually?** See [VISUALIZER.md](VISUALIZER.md).
- **Writing or updating documentation?** Follow [DOCUMENTATION_STYLE_GUIDE.md](DOCUMENTATION_STYLE_GUIDE.md).

## Related Documentation

- [README.md](../README.md) — project overview, quick start, installation, and usage
- [CONTRIBUTING.md](../CONTRIBUTING.md) — development setup, coding constraints, and PR guidelines
- [SECURITY.md](../SECURITY.md) — vulnerability disclosure policy and scope statement
- [CHANGELOG.md](../CHANGELOG.md) — version history
