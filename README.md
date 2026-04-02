# Parsidion CC

[![CI](https://github.com/paulrobello/parsidion-cc/actions/workflows/ci.yml/badge.svg)](https://github.com/paulrobello/parsidion-cc/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python 3.13+](https://img.shields.io/badge/Python-3.13%2B-blue.svg)

A second brain for Claude Code -- a markdown knowledge vault that gives Claude persistent memory, cross-session context, and a searchable store of everything it learns. [Obsidian](https://obsidian.md/) is **not required** -- it is an optional viewer for graph visualization and browsing.

Parsidion CC replaces Claude Code's built-in auto memory with a richly organized markdown vault. Session lifecycle hooks automatically capture learnings, load relevant context at startup, and snapshot working state before compaction. A research agent saves structured findings, and an AI-powered summarizer generates vault notes from session transcripts.

![Parsidion CC Architecture](https://raw.githubusercontent.com/paulrobello/parsidion-cc/main/parsidion-cc-architecture.png)

> [View the interactive architecture slideshow](https://paulrobello.github.io/parsidion-cc/vault-architecture-slideshow.html) for a detailed walkthrough of every component.
>
> **Build session slideshows:** [Vault Explorer Agent](https://paulrobello.github.io/parsidion-cc/vault-explorer-slideshow.html) · [Research Documentation Agent](https://paulrobello.github.io/parsidion-cc/research-agent-slideshow.html) · [Project Explorer Agent](https://paulrobello.github.io/parsidion-cc/project-explorer-slideshow.html) · [Vault Deduplicator](https://paulrobello.github.io/parsidion-cc/vault-deduplicator-slideshow.html)

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Components](#components)
- [Vault Visualizer](#vault-visualizer)
- [parsidion-mcp (Claude Desktop)](#parsidion-mcp-claude-desktop)
- [Configuration](#configuration)
- [Multi-Vault Support](#multi-vault-support)
- [Vault Git Integration](#vault-git-integration)
- [File Locations](#file-locations)
- [Usage](#usage)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Contributing](#contributing)
- [License](#license)
- [Changelog](#changelog)
- [Related Documentation](#related-documentation)

## Prerequisites

- **Python 3.13+**
- **[uv](https://docs.astral.sh/uv/)** -- Python package runner and manager
- **[Obsidian](https://obsidian.md/)** (optional) -- for vault browsing and graph view
- **Claude Code** -- the CLI this toolkit extends
- **[jq](https://jqlang.github.io/jq/)** (optional) -- required by the `scripts/show-context` preview script; install via `brew install jq` (macOS) or your system package manager
- **[mcpl](https://github.com/kenneth-liao/mcp-launchpad)** (optional) -- MCP Launchpad, a unified CLI for discovering and calling tools from any MCP server; used by the research agent as a fallback search gateway (see [docs/MCPL.md](docs/MCPL.md))
- **[agentchrome](https://github.com/Nunley-Media-Group/AgentChrome)** (optional, recommended) -- native CLI for browser control via Chrome DevTools Protocol; used by the research agent to fetch fully-rendered pages for higher-quality markdown conversion (see [docs/AGENTCHROME.md](docs/AGENTCHROME.md)); falls back to `curl` when unavailable

> **Platform support:** Works on macOS, Linux, and Windows. On Windows, the installer gracefully falls back from symlinks to directory copies when elevated privileges are unavailable.

## Quick Start

1. **Clone the repository:**
   ```bash
   git clone https://github.com/paulrobello/parsidion-cc.git
   cd parsidion-cc
   ```

2. **Run the installer:**
   ```bash
   uv run install.py
   ```
   This installs the vault skill, research agent, vault-explorer agent, session hooks, and always-on vault guidance into `~/.claude/`.

3. **Restart Claude Code** to activate the hooks.

That's it. Claude Code now has persistent memory backed by a markdown vault at `~/ClaudeVault/`. Optionally, open that directory in [Obsidian](https://obsidian.md/) to browse notes and explore the knowledge graph.

## Installation

```bash
# Interactive install (prompts for vault location)
uv run install.py

# Non-interactive with default vault (~/ClaudeVault)
uv run install.py --force --yes

# Non-interactive with custom vault path
uv run install.py --vault ~/MyVault --yes

# Preview without making changes
uv run install.py --dry-run

# Also install vault-search as a global CLI command
uv run install.py --force --yes --install-tools

# Schedule nightly auto-summarization (launchd on macOS, cron on Linux)
uv run install.py --schedule-summarizer
uv run install.py --schedule-summarizer --summarizer-hour 3   # run at 3 AM instead of default

# Also rebuild visualizer graph.json each night (after indexing)
uv run install.py --schedule-summarizer --rebuild-graph
uv run install.py --schedule-summarizer --rebuild-graph --graph-include-daily
```

**Options:**

| Flag | Description |
|------|-------------|
| `--vault PATH` | Vault path (skips interactive prompt) |
| `--claude-dir PATH` | Target Claude config dir (default: `~/.claude`) |
| `--dry-run / -n` | Preview all actions, no changes made |
| `--verbose / -v` | Show detailed output |
| `--force / -f` | Overwrite existing skill files without prompting |
| `--yes / -y` | Skip all confirmation prompts; uses `~/ClaudeVault` if `--vault` not given |
| `--skip-hooks` | Do not modify `settings.json` |
| `--skip-agent` | Do not install any agents |
| `--enable-ai` | Enable AI-powered note selection: writes `ai_model` to `config.yaml` and sets SessionStart timeout to 30 s |
| `--enable-embeddings` | Enable semantic search embeddings: writes `embeddings.enabled = true` to `config.yaml` |
| `--install-tools` | Install `vault-search`, `vault-new`, `vault-stats`, `vault-review`, `vault-export`, and `vault-merge` as global CLI commands via `uv tool install` |
| `--schedule-summarizer` | Generate a launchd plist (macOS) or cron job (Linux) for nightly auto-summarization |
| `--summarizer-hour N` | Hour (0-23) for the scheduled summarizer job (default: 2) |
| `--rebuild-graph` | Add `--rebuild-graph` to the scheduled command so `graph.json` is regenerated each night (use with `--schedule-summarizer`) |
| `--graph-include-daily` | Include Daily folder notes in the nightly graph rebuild (use with `--rebuild-graph`) |
| `--create-vaults-config` | Create `~/.claude/vaults.yaml` for multi-vault support (see [Multi-Vault Support](#multi-vault-support)) |
| `--uninstall` | Remove installed skill, agents, hook registrations, and launchd plist / cron job |

During interactive installation, the installer prompts for three optional features:

1. **"Install CLI tools?"** (default: yes) — runs `uv tool install --editable ".[tools]"` to register `vault-search`, `vault-new`, `vault-stats`, `vault-review`, `vault-export`, and `vault-merge` as global commands. Use `--install-tools` to enable this non-interactively (e.g. with `--yes`).
2. **"Enable AI-powered note selection?"** (default: yes) — writes `ai_model` to `config.yaml` and sets the SessionStart hook timeout to 30 s, enabling claude-haiku to intelligently select relevant vault notes at session start. Use `--enable-ai` to enable this non-interactively (e.g. with `--yes`).
3. **"Enable embeddings?"** (default: yes) — writes `embeddings.enabled = true` to `config.yaml`, enabling the vector index used by `vault-search` semantic mode and `session_start_hook` with `use_embeddings`. Requires ~67 MB model download on first run. Use `--enable-embeddings` to enable this non-interactively (e.g. with `--yes`).

After installation, restart Claude Code to activate hooks. Optionally, open the vault path in Obsidian for graph visualization and note browsing -- this is not required for the system to work.

## Components

### Claude Vault (`~/.claude/skills/parsidion-cc/`)

A markdown vault-based knowledge management system that replaces Claude Code's built-in auto memory with a richly organized, searchable, cross-linked knowledge base at `~/ClaudeVault/`. The vault is plain markdown -- [Obsidian](https://obsidian.md/) can be used to visualize the graph and browse notes but is not required.

**Auto-triggering:** The skill includes YAML frontmatter with a description that enables automatic invocation when users mention saving knowledge, checking notes, or persisting findings across sessions.

**Scripts:**

| Script | Purpose |
|--------|---------|
| `vault_common.py` | Shared library (frontmatter parsing, search, path utilities, config loader, git commit, `build_compact_index()`) |
| `vault_links.py` | Shared backlink module (stdlib-only) -- `find_related_by_tags()`, `find_related_by_semantic()`, `inject_related_links()`, `add_backlinks_to_existing()`; used by `summarize_sessions.py` and `parsidion-mcp` |
| `session_start_hook.py` | SessionStart hook -- loads project-relevant vault context; `--ai [MODEL]` enables AI-powered note selection via `claude -p`; `--debug` logs injected context to `$TMPDIR` |
| `session_stop_hook.py` | SessionEnd hook (launched via `session_stop_wrapper.sh`) -- queues sessions to `pending_summaries.jsonl` (deduped by session_id, `fcntl`-locked) |
| `subagent_stop_hook.py` | SubagentStop hook (async) -- captures subagent transcripts and queues them to `pending_summaries.jsonl`; skips agents listed in `excluded_agents` |
| `summarize_sessions.py` | On-demand AI summarizer -- generates structured vault notes from queued sessions (PEP 723, uses `claude-agent-sdk`); checks for near-duplicate notes before writing (configurable via `summarizer.dedup_threshold`) |
| `pre_compact_hook.py` | PreCompact hook -- snapshots working state before compaction |
| `post_compact_hook.py` | PostCompact hook -- reads today's daily note, finds the last `## Pre-Compact Snapshot`, and returns it as `additionalContext` to restore context after compaction |
| `build_embeddings.py` | Builds the semantic search embeddings database (`embeddings.db`) using fastembed and sqlite-vec |
| `vault_search.py` | Unified search CLI -- semantic mode (natural language query), metadata mode (`--tag`/`--folder`/`--type`/`--project`/`--recent-days`), full-text body search (`--grep`/`-G`), or interactive curses TUI (`--interactive`/`-i`); available as `vault-search` global command with `--install-tools` |
| `vault_new.py` | CLI to scaffold new vault notes from templates -- `vault-new --type pattern --title "My Note" --project myproj --tags python,vault --open`; available as `vault-new` global command with `--install-tools` |
| `vault_stats.py` | Analytics CLI for vault health and activity -- modes: `--summary`, `--stale`, `--top-linked`, `--by-project`, `--growth`, `--tags` (tag cloud), `--pending` (pending queue status), `--graph` (knowledge graph metrics), `--hooks N` (last N hook events), `--weekly` (weekly rollup note), `--monthly` (monthly rollup note), `--timeline N` (activity bar chart for last N days), `--summarizer-progress` (live summarizer status), `--dashboard` (all modes combined); available as `vault-stats` global command with `--install-tools` |
| `vault_review.py` | Interactive TUI for inspecting and approving/rejecting pending sessions before AI summarization; available as `vault-review` global command |
| `vault_export.py` | Export vault to HTML static site, filtered zip, or PDF via pandoc; available as `vault-export` global command |
| `vault_merge.py` | AI-assisted merging of near-duplicate notes with automatic backlink updates; `--scan` finds near-duplicate pairs via embedding similarity; `--no-index` skips per-merge index rebuild for batch workflows; available as `vault-merge` global command |
| `update_index.py` | Rebuilds `~/ClaudeVault/CLAUDE.md` index and populates the `note_index` SQLite table; includes tag cloud and vault health from `doctor_state.json` |
| `vault_doctor.py` | Scans vault notes for structural issues (missing frontmatter, broken wikilinks, orphan notes, etc.); auto-repairs broken wikilinks via exact stem match or `vault-search` semantic lookup (Python-only, no Claude call); repairs other issues via Claude haiku with semantic candidates from `vault-search`; singleton-guarded via PID in `doctor_state.json`; auto-commits uncommitted vault files ≥ 15 min old before scanning |
| `check_graph_coverage.py` | Audits vault tags vs graph.json color groups; shows uncovered tags and stale entries |
| `html-to-md.py` | PEP 723 standalone script -- converts HTML to clean, noise-free markdown optimized for LLM consumption; used by the research agent |
| `embed_eval.py` | Embedding evaluation harness -- benchmarks model and chunking strategy combinations against Claude-generated ground-truth queries; outputs Rich table, JSON results, and interactive HTML report (see [docs/EMBEDDINGS_EVAL.md](docs/EMBEDDINGS_EVAL.md)) |
| `run_trigger_eval.py` | Trigger accuracy eval (skill-selection simulation) |
| `run_trigger_eval.sh` | Shell wrapper for running eval from a separate terminal (macOS/Linux) |
| `run_trigger_eval.bat` | Batch wrapper for running eval from a separate terminal (Windows) |
| `migrate_research.py` | One-time migration from `~/Repos/research/` |
| `migrate_memory.py` | One-time migration from `~/.claude/memory/` |

**Templates:** 9 note templates (daily, project, language, framework, pattern, debugging, tool, research, knowledge)

**Vault structure:**
```
~/ClaudeVault/
  CLAUDE.md                  # Auto-generated index (includes tag cloud + Existing Tags list)
  config.yaml                # Optional -- hook/summarizer settings (see Configuration)
  pending_summaries.jsonl    # Queue of sessions awaiting AI summarization
  embeddings.db              # SQLite database: note embeddings + note_index metadata table
  hook_events.log            # Structured JSON log of hook executions (SessionStart/End/SubagentStop/PreCompact)
  graph.json                 # Pre-built knowledge graph for the visualizer (gitignored, rebuilt locally)
  Daily/YYYY-MM/DD-{username}.md  # Per-user daily notes (e.g. Daily/2026-03/13-alice.md)
  Projects/                  # Per-project context
  Languages/                 # Language-specific knowledge
  Frameworks/                # Framework knowledge
  Patterns/                  # Design patterns, solutions
  Debugging/                 # Error patterns, fixes
  Tools/                     # CLI tools, packages
  Research/                  # Deep-dive research
  Knowledge/                 # General knowledge, concepts, reference material
  History/                   # Historical notes
  Templates/                 # Symlink to skill templates
```

**Graph view color groups** (configured in `~/ClaudeVault/.obsidian/graph.json`, first-match-wins):

| Priority | Category | Color | RGB (decimal) | Tags |
|----------|----------|-------|---------------|------|
| 1 | Projects | Cyan `#00BCD4` | 48340 | synknot, fractal-flythroughs, parvitar, parsistant, termflix, parvault, cctmux, parsidion-cc |
| 2 | Debugging | Red/Orange `#FF5722` | 16733986 | debugging |
| 3 | Patterns | Green `#4CAF50` | 5025616 | memory, migration, sync |
| 4 | Research | Purple `#9C27B0` | 10233776 | research, e2b, qdrant, pkm-apps-comparison |
| 5 | Tools & SDKs | Blue `#2196F3` | 2201331 | claude-code, claude-agent-sdk, claude, rich, mcp, ollama, maturin, redis, websockets, sentry, mermaid-cli, custom-tools, acp-protocol, tool, api, encryption |
| 6 | Languages | Amber `#FFC107` | 16761095 | rust, python, swift, swiftui, typescript, nextjs, react, macos, macos-26, rust-packages |
| 7 | Terminal | Teal `#009688` | 38536 | terminal, par-term, par-term-emu-core-rust |
| 8 | Graphics / 3D | Pink `#E91E63` | 15277667 | wgpu, sdf, sdf-terrain, voxel, fractals, mandel, vrm, avatar, face-tracking |

The parsidion-cc skill includes a sub-workflow for updating these groups -- add tags to existing groups or create new ones when new projects or topics are introduced. RGB colors are stored as decimal integers (e.g., `int("FF5722", 16)` -> `16733986`).

### CLAUDE-VAULT.md (`~/.claude/CLAUDE-VAULT.md`)

An always-on guidance file loaded every Claude Code session via `@CLAUDE-VAULT.md` in `~/.claude/CLAUDE.md`. It enforces the **vault-first rule** unconditionally -- no explicit invocation needed.

**What it enforces:**
- **Debugging:** Search `~/ClaudeVault/Debugging/` before diagnosing any error. Extract the key signal (exception class, package name, distinctive phrase) and Grep the vault first. If found, apply the documented fix. If not, diagnose then save the solution.
- **Implementation:** Search `~/ClaudeVault/Patterns/`, `Frameworks/`, `Languages/`, and `Projects/` before writing non-trivial code. Reuse proven implementations from prior projects rather than writing from scratch.
- **Saving solutions:** After solving a non-obvious problem, save it to the appropriate vault folder and rebuild the index.

The installer copies `CLAUDE-VAULT.md` from the repo root to `~/.claude/` and ensures the `@CLAUDE-VAULT.md` import line exists in `~/.claude/CLAUDE.md`. Uninstall removes both.

### Vault Explorer Agent (`~/.claude/agents/vault-explorer.md`)

A Haiku-powered read-only subagent that isolates vault lookups from the main session context. Dispatched automatically when the main session needs to search the vault.

**7-step search procedure:**
1. **Semantic search** -- `vault-search "QUERY" --json`; ≥3 results with score ≥ 0.35 → done
2. **Metadata search** -- `vault-search --tag/--folder/--type/--project/--recent-days` with inferred filters; ≥3 results → done
3. **Orient** -- reads `~/ClaudeVault/CLAUDE.md` index
4. **Extract signals** -- exception class, package name, or keyword
5. **Search by priority folder** -- Grep by query type table
6. **Rank & read** -- top 5 by semantic score, then folder priority
7. **Synthesize** -- returns `## Answer` + `## Sources`

> **📝 Note:** The vault-explorer agent is listed in `excluded_agents` in `config.yaml` to prevent its own transcripts from being recursively harvested by the SubagentStop hook.

### Research Agent (`~/.claude/agents/research-agent.md`)

Technical research agent that searches the vault first, conducts web research, and saves findings to the appropriate vault folder with proper YAML frontmatter. Fetches pages via `agentchrome page html` piped through `html-to-md.py` for noise-free markdown (curl fallback if agentchrome unavailable). Uses `mcpl` as a fallback search gateway when Brave Search hits rate limits -- see [docs/MCPL.md](docs/MCPL.md) for mcpl setup.

### Vault Deduplicator Agent (`~/.claude/agents/vault-deduplicator.md`)

A Haiku-powered agent that scans the vault for near-duplicate note pairs via `vault-merge --scan`, batches them into parallel subagents for evaluation and merging with `--no-index`, and runs one final index rebuild. See [CHANGELOG.md](CHANGELOG.md) for details.

### HTML to Markdown (`skills/parsidion-cc/scripts/html-to-md.py`)

A PEP 723 standalone script (installed to `~/.claude/skills/parsidion-cc/scripts/html-to-md.py`) that converts HTML to clean, noise-free markdown optimized for LLM consumption. Strips navigation, banners, cookie notices, and script/style noise while preserving code fences with language annotations. Used by the research agent to clean `agentchrome` page output.

```bash
uv run --script ~/.claude/skills/parsidion-cc/scripts/html-to-md.py page.html          # file → stdout
uv run --script ~/.claude/skills/parsidion-cc/scripts/html-to-md.py - < page.html      # stdin → stdout
agentchrome page html | uv run --script ~/.claude/skills/parsidion-cc/scripts/html-to-md.py - --url https://example.com
```

### Context Preview (`scripts/show-context`)

A shell script that previews what vault context would be injected at session start for a given project directory. Useful for debugging the SessionStart hook. Requires `jq` to be installed.

```bash
./scripts/show-context                    # Preview context for cwd
./scripts/show-context ~/Repos/myproject  # Preview context for a specific project
```

### Hooks (`~/.claude/settings.json`)

All hooks read `~/ClaudeVault/config.yaml` for settings (see [Configuration](#configuration)).

| Hook Event | Script | Timeout | Config section | Notes |
|------------|--------|---------|----------------|-------|
| SessionStart | `session_start_hook.py` | 10 s (30 s with `--ai`) | `session_start_hook` | `--ai [MODEL]` or `session_start_hook.ai_model` enables AI selection |
| SessionEnd | `session_stop_wrapper.sh` → `session_stop_hook.py` | 10 s | `session_stop_hook` | Shell wrapper outputs `{}` immediately; Python script runs detached via `nohup` |
| PreCompact | `pre_compact_hook.py` | 10 s | `pre_compact_hook` | Configurable transcript lines |
| PostCompact | `post_compact_hook.py` | 10 s | — | Reads last Pre-Compact Snapshot from today's daily note and returns it as `additionalContext` |
| SubagentStop | `subagent_stop_hook.py` | async | `subagent_stop_hook` | Non-blocking; skips agents listed in `excluded_agents` |

## Vault Visualizer

An interactive web application for exploring and navigating the vault through dual-mode reading and graph visualization. It runs as a local Next.js server on **port 3999** and is built from the `visualizer/` subdirectory.

**Two modes:**

| Mode | Description |
|------|-------------|
| **Read** | Centered Markdown pane with wikilink navigation, tag pills, and related-notes section |
| **Graph** | Force-directed Sigma.js graph — 2-hop neighborhood around the active note, or full-vault view |

**Key features:**
- Multi-tab browsing (up to 20 tabs, state persisted to localStorage)
- Collapsible file explorer sidebar with nested folder tree
- Unified **⌘K** search across titles, tags, and folders (no server round-trips)
- Graph HUD panel: semantic threshold slider, node-type filters, physics controls, live stats
- Pre-computed `graph.json` from vault embeddings — no live queries during navigation

![Vault Visualizer - Read Mode](https://raw.githubusercontent.com/paulrobello/parsidion-cc/main/screenshot-vault-read.png)

![Vault Visualizer - Graph Mode](https://raw.githubusercontent.com/paulrobello/parsidion-cc/main/screenshot-vault-graph.png)

**Running the visualizer:**

```bash
# First time: install dependencies
make visualizer-setup

# Build graph data from vault embeddings
make graph               # exclude Daily notes (recommended)
make graph-with-daily    # include Daily folder notes

# Start development server (port 3999)
cd visualizer && bun dev

# Production
make build-visualizer    # compile
make start-visualizer    # serve on port 3999
make stop-visualizer     # stop
```

> **📝 Note:** Requires vault embeddings to be built first: `uv run --no-project ~/.claude/skills/parsidion-cc/scripts/build_embeddings.py`

See [docs/VISUALIZER.md](docs/VISUALIZER.md) for the full architecture, data model, graph engine details, and configuration reference.

## parsidion-mcp (Claude Desktop)

An optional MCP server that exposes Claude Vault operations to **Claude Desktop** (and any other MCP-compatible client) over stdio. It lives in the `parsidion-mcp/` subdirectory and is installed independently from the main skill.

**Six tools:**

| Tool | Description |
|------|-------------|
| `vault_search` | Semantic search (natural language query) or metadata search (tag/folder/type/project/days) |
| `vault_read` | Read a vault note by relative or absolute path |
| `vault_write` | Create or overwrite a vault note |
| `vault_context` | Return a session-start-style context block (compact index or verbose summaries) |
| `rebuild_index` | Rebuild `CLAUDE.md`, `MANIFEST.md` files, and the `note_index` SQLite table |
| `vault_doctor` | Scan vault notes for structural issues; optionally repair them |

**Install:**

```bash
cd parsidion-mcp
uv tool install --editable .
```

**Configure Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "parsidion": {
      "command": "/Users/yourname/.local/bin/parsidion-mcp"
    }
  }
}
```

Replace the path with the output of `which parsidion-mcp`. See [docs/MCP.md](docs/MCP.md) for the full tools reference.

## Configuration

All hooks and the summarizer read `~/ClaudeVault/config.yaml`. Precedence: **defaults -> config.yaml -> CLI args** (last one wins).

Copy the template to get started:
```bash
cp ~/.claude/skills/parsidion-cc/templates/config.yaml ~/ClaudeVault/config.yaml
```

> **📝 Note:** Model IDs shown in the config block below (e.g. `claude-sonnet-4-6`,
> `claude-haiku-4-5-20251001`, `BAAI/bge-small-en-v1.5`) are the hardcoded script defaults.
> Override any of them via the corresponding key in `~/ClaudeVault/config.yaml`.

```yaml
session_start_hook:
  ai_model: null           # Model for AI note selection (null = disabled)
  max_chars: 4000          # Max context injection characters
  ai_timeout: 25           # AI call timeout in seconds
  recent_days: 3           # Days to look back for recent notes
  debug: false             # Append injected context to debug log in $TMPDIR
  verbose_mode: false      # If true, inject full note summaries instead of compact one-line index
  use_embeddings: true     # Blend semantic search results into context injection
  track_delta: true        # Show "Since last time" delta of new/modified notes per project

session_stop_hook:
  ai_model: null           # Model for AI classification (null = disabled)
  ai_timeout: 25           # AI call timeout in seconds
  auto_summarize: true     # Auto-launch summarizer when pending entries exist
  auto_summarize_after: 1  # Queue size threshold before auto-launching summarizer

subagent_stop_hook:
  enabled: true            # Enable/disable subagent transcript capture
  min_messages: 3          # Minimum messages before capturing transcript
  excluded_agents: "vault-explorer,research-agent"  # Never capture these

pre_compact_hook:
  lines: 200               # Transcript lines to analyse

summarizer:
  model: claude-sonnet-4-6
  max_parallel: 5          # Concurrent summarization tasks
  transcript_tail_lines: 400
  max_cleaned_chars: 12000
  persist: false           # SDK session persistence (for debugging)
  cluster_model: claude-haiku-4-5-20251001  # Model for hierarchical chunk summarization (default; override via config.yaml)
  dedup_threshold: 0.80    # Cosine similarity above which a near-duplicate note is detected and skipped

defaults:
  haiku_model: claude-haiku-4-5-20251001   # Centralized haiku model ID used across hooks
  sonnet_model: claude-sonnet-4-6          # Centralized sonnet model ID used across scripts

embeddings:
  model: BAAI/bge-small-en-v1.5  # fastembed model for semantic search
  min_score: 0.45          # Minimum cosine similarity threshold
  top_k: 10                # Maximum semantic search results

git:
  auto_commit: true        # Auto-commit vault changes after writes

event_log:
  enabled: true            # Write structured JSON events to hook_events.log
  max_lines: 10000         # Rotate log after this many lines

adaptive_context:
  enabled: false           # Track which injected notes were referenced; derank unused ones
  decay_days: 30           # Half-life in days for deranking unreferenced notes
```

## Multi-Vault Support

Parsidion CC supports multiple isolated vaults with per-vault configuration. This enables:

- **Separate work/personal vaults** — keep client work isolated from personal notes
- **Project-specific vaults** — each codebase can have its own knowledge base
- **Team vaults** — share a vault via git with teammates while maintaining a private vault

### Setup

Use `--create-vaults-config` to generate a vaults configuration file:

```bash
uv run install.py --create-vaults-config
```

This creates `~/.claude/vaults.yaml`:

```yaml
vaults:
  default:
    path: ~/ClaudeVault
  work:
    path: ~/WorkVault
  personal:
    path: ~/PersonalVault
```

### Using Multiple Vaults

All vault tools support a `--vault` flag to specify the target vault:

```bash
# Search in a specific vault
vault-search "error patterns" --vault work

# Create a note in the personal vault
vault-new --type pattern --title "My Pattern" --vault personal

# View stats for work vault
vault-stats --summary --vault work

# Run doctor on a specific vault
vault-doctor --vault work --dry-run

# Build embeddings for a vault
build_embeddings.py --vault work
update_index.py --vault work
```

### Vault-Aware Tools

| Tool | `--vault` Flag |
|------|----------------|
| `vault-search` | ✅ |
| `vault-new` | ✅ |
| `vault-stats` | ✅ |
| `vault-review` | ✅ |
| `vault-export` | ✅ |
| `vault-merge` | ✅ |
| `vault-doctor` | ✅ |
| `build_embeddings.py` | ✅ |
| `update_index.py` | ✅ |
| `summarize_sessions.py` | ✅ |

### Vault-Aware Hooks

All session hooks support multi-vault via the vaults config:

- `session_start_hook.py` — loads context from the project's associated vault
- `session_stop_hook.py` — queues sessions to the appropriate vault
- `pre_compact_hook.py` — snapshots to the project's vault
- `post_compact_hook.py` — restores from the project's vault
- `subagent_stop_hook.py` — queues to the active vault

### Default Vault Resolution

When `--vault` is not specified, tools use this resolution order:

1. `--vault NAME` CLI flag (highest priority)
2. `VAULT_DEFAULT` environment variable
3. `default` vault in `~/.claude/vaults.yaml`
4. `~/ClaudeVault` (fallback if no config exists)

## Vault Git Integration

The vault supports optional git version control. When `~/ClaudeVault/.git` exists, scripts automatically stage and commit changes after every write (daily notes, index rebuilds, session notes). Controlled by `git.auto_commit` in config.

```bash
cd ~/ClaudeVault
git init
echo ".obsidian/" > .gitignore
git add -A && git commit -m "chore(vault): initial commit"
```

If no `.git` directory is present, all git operations are silent no-ops.

## File Locations

```
~/.claude/
  CLAUDE.md                          # Global Claude Code instructions (@imports CLAUDE-VAULT.md)
  CLAUDE-VAULT.md                    # Always-on vault-first guidance (installed by parsidion-cc)
  settings.json                      # Hooks, permissions, plugins
  agents/
    research-agent.md  # Research agent (vault-integrated)
    vault-explorer.md                # Read-only Haiku vault search agent (7-step)
  skills/parsidion-cc/
    SKILL.md                         # Vault skill definition
    scripts/                         # Hook scripts, utilities, and html-to-md.py
    templates/                       # Note templates + config.yaml reference

~/ClaudeVault/                       # Markdown vault (knowledge base; open in Obsidian for graph view)
  config.yaml                        # Optional hook/summarizer settings
  embeddings.db                      # Semantic search DB (note_embeddings + note_index tables)
  hook_events.log                    # Structured JSON log of hook executions
```

## Usage

**Rebuild vault index:**
```bash
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/update_index.py
```

**Build or rebuild semantic search embeddings:**
```bash
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/build_embeddings.py
```

**Search vault from the CLI:**
```bash
# Semantic search (natural language) — three output formats
vault-search "sqlite vector search patterns"          # JSON output (default)
vault-search "sqlite vector search patterns" -t       # human-readable text
vault-search "sqlite vector search patterns" -r       # Rich-colorized output
vault-search "hook patterns" -n 5 -r                  # top 5, rich output

# Metadata search (filter flags, no query)
vault-search -f Patterns -T python                    # short options
vault-search --folder Patterns --tag python           # long options (also valid)
vault-search -d 7                                     # modified in last 7 days
vault-search -p parsidion-cc -k debugging             # by project and type

# Full-text body search
vault-search --grep "dedup_threshold"                 # case-insensitive body search
vault-search --grep "FLOCK" --grep-case               # case-sensitive body search
vault-search --grep "pattern" -f Patterns             # combine with metadata filters

# Interactive curses TUI (real-time results, navigation, editor integration)
vault-search --interactive
vault-search -i

# Environment variables (override config.yaml defaults)
VAULT_SEARCH_FORMAT=rich vault-search "query"
VAULT_SEARCH_MIN_SCORE=0.5 VAULT_SEARCH_TOP=5 vault-search "query"
```

**`VAULT_SEARCH_*` environment variables:**

| Variable | Description | Example |
|---|---|---|
| `VAULT_SEARCH_FORMAT` | Default output format: `json`, `text`, or `rich` | `VAULT_SEARCH_FORMAT=rich` |
| `VAULT_SEARCH_MIN_SCORE` | Minimum cosine similarity threshold (0.0–1.0) | `VAULT_SEARCH_MIN_SCORE=0.5` |
| `VAULT_SEARCH_TOP` | Max semantic results | `VAULT_SEARCH_TOP=5` |
| `VAULT_SEARCH_LIMIT` | Max metadata results | `VAULT_SEARCH_LIMIT=20` |
| `VAULT_SEARCH_MODEL` | fastembed model ID | `VAULT_SEARCH_MODEL=BAAI/bge-small-en-v1.5` |

Precedence: **CLI flag > env var > config.yaml > built-in default**

> **📝 Note:** `vault-search` requires `uv run install.py --install-tools` (or `uv tool install --editable ".[tools]"` from the repo root) to register it as a global command. Without this, use `uv run --no-project ~/.claude/skills/parsidion-cc/scripts/vault_search.py` instead.

**Scaffold a new vault note:**
```bash
# Create a new pattern note and open it in your editor
vault-new --type pattern --title "My Reusable Pattern" --project myproj --tags python,vault --open

# Create a debugging note without opening
vault-new --type debugging --title "Fix SQLite Connection Error" --tags sqlite,python

# See all options
vault-new --help
```

**Vault analytics and health:**
```bash
vault-stats --summary              # note counts, growth, top tags
vault-stats --stale                # notes with no incoming links, older than 30 days
vault-stats --top-linked           # most-referenced notes
vault-stats --by-project           # note counts per project
vault-stats --growth               # notes added per week
vault-stats --tags                 # tag frequency cloud
vault-stats --pending              # pending queue status: count, sources, oldest entry, estimated cost
vault-stats --graph                # knowledge graph metrics: avg degree, hubs, isolated clusters, orphans
vault-stats --hooks 50             # last 50 hook events from hook_events.log
vault-stats --weekly               # generate weekly rollup note from daily notes
vault-stats --monthly              # generate monthly rollup note from daily notes
vault-stats --timeline 90          # activity bar chart for last 90 days
vault-stats --summarizer-progress  # live feedback from running summarize_sessions.py
vault-stats --dashboard            # full combined dashboard (all modes)
```

**Review and approve pending sessions before summarization:**
```bash
vault-review                       # interactive TUI: inspect sessions, approve (y) / reject (n) / skip (s)
summarize_sessions.py --approved-only  # only process sessions approved by vault-review
```

**Export vault:**
```bash
vault-export --html ~/vault-site   # export to HTML static site
vault-export --zip ~/vault.zip     # export filtered subset as zip
vault-export --pdf ~/vault.pdf     # export via pandoc to PDF
```

**Merge near-duplicate notes:**
```bash
vault-merge                        # AI-assisted: detect and merge near-duplicate notes, update backlinks
```

**Summarize queued sessions** (generates structured vault notes via Claude Agent SDK):
```bash
# Process all pending sessions (run from a terminal, not inside Claude Code)
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/summarize_sessions.py

# If running from inside a Claude Code session, unset CLAUDECODE to allow nesting:
env -u CLAUDECODE uv run --no-project ~/.claude/skills/parsidion-cc/scripts/summarize_sessions.py

# Preview without writing
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/summarize_sessions.py --dry-run

# Process an explicit file (e.g. to test a single entry)
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/summarize_sessions.py --sessions /path/to/file.jsonl
```

**Run vault doctor** (scan for issues and repair via Claude haiku):
```bash
# Scan and report only
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/vault_doctor.py --dry-run

# Repair repairable issues (must unset CLAUDECODE to allow nested claude calls)
env -u CLAUDECODE uv run --no-project ~/.claude/skills/parsidion-cc/scripts/vault_doctor.py --fix --limit 20

# Errors only; skip warnings
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/vault_doctor.py --errors-only --dry-run

# Ignore state file, rescan everything
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/vault_doctor.py --no-state --dry-run

# Detect 3+ prefix clusters and show candidates for subfolder migration
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/vault_doctor.py --migrate-subfolders

# Preview + execute: move files and update all wikilinks
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/vault_doctor.py --migrate-subfolders --execute
```

The doctor is singleton-guarded -- it stores its PID in `doctor_state.json` and exits if another instance is already running. Before scanning it auto-commits any uncommitted vault files whose mtime is ≥ 15 minutes old. Notes that time out twice are flagged `needs_review` and skipped on future runs. The vault health summary appears in `CLAUDE.md` after running `update_index.py`.

**Run trigger eval** (from a separate terminal, not inside Claude Code):
```bash
bash ~/.claude/skills/parsidion-cc/scripts/run_trigger_eval.sh
```

**Preview session start context:**
```bash
./scripts/show-context
./scripts/show-context /path/to/project
```

**Search vault programmatically:**
```python
import sys
sys.path.insert(0, str(Path.home() / ".claude/skills/parsidion-cc/scripts"))
from vault_common import find_notes_by_tag, find_notes_by_project
```

**Audit graph color group coverage** (find uncovered vault tags, spot stale group entries):
```bash
python ~/.claude/skills/parsidion-cc/scripts/check_graph_coverage.py

# Only show tags used 2+ times
python ~/.claude/skills/parsidion-cc/scripts/check_graph_coverage.py --threshold 2

# JSON output for scripting
python ~/.claude/skills/parsidion-cc/scripts/check_graph_coverage.py --json
```

**Reinstall after source changes:**
```bash
uv run install.py --force --yes
```

**Uninstall:**
```bash
uv run install.py --uninstall
```

## Troubleshooting

### Hooks not firing

- Verify hooks are registered in `~/.claude/settings.json`. Look for `hooks` entries pointing to the hook scripts.
- Re-run `uv run install.py --force --yes` to re-register hooks.
- Check that the script paths in `settings.json` are correct and the files exist at those paths.
- Restart Claude Code after any settings.json change.

### Vault not created

- The vault directory (`~/ClaudeVault/` by default) is created automatically by the SessionStart hook on first run.
- If it was not created, check that the hook is firing (see above).
- You can create it manually: `mkdir -p ~/ClaudeVault` and then run the installer.

### `uv` not found

- Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Ensure `uv` is on your PATH. Restart your shell after installation.
- Verify with `uv --version`.

### Timeout errors with `--ai` flag

- The `--ai` flag on session start/stop hooks requires a longer timeout (30 seconds) because it calls `claude -p` for AI-powered note selection.
- Update `settings.json` to set the hook timeout to `30000` ms:
  ```json
  {
    "command": "uv run --no-project ~/.claude/skills/parsidion-cc/scripts/session_start_hook.py --ai",
    "timeout": 30000
  }
  ```
- If timeouts persist, increase `ai_timeout` in `~/ClaudeVault/config.yaml`.

### Summarizer fails to run

- The summarizer cannot run inside an active Claude Code session. Run from a separate terminal.
- If running from inside Claude Code, unset the guard variable: `env -u CLAUDECODE uv run --no-project ~/.claude/skills/parsidion-cc/scripts/summarize_sessions.py`
- Check that `pending_summaries.jsonl` exists and has entries.

### `vault-search` command not found

- Run `uv run install.py --force --yes --install-tools` to register the global command, or manually: `cd /path/to/parsidion-cc && uv tool install --editable ".[tools]"`
- Ensure `~/.local/bin/` is on your PATH (Linux/macOS) or `%APPDATA%\Python\Scripts` (Windows).

## FAQ

### Will this use extra API tokens?

Parsidion CC is designed to minimize token usage. The lifecycle hooks (`SessionStart`, `SessionEnd`, `PreCompact`, `SubagentStop`) are **pure Python scripts** that run locally -- they read transcripts, parse frontmatter, and write files without calling any AI model. The only places that use API tokens are:

- **Session summarizer** (`summarize_sessions.py`) -- runs on-demand (not automatically) and uses **Sonnet** to generate vault notes from queued transcripts. This is the only component that uses a large model. Long transcripts are pre-chunked and summarized by **Haiku** first to reduce cost.
- **AI-powered note selection** (optional `--ai` flag on `SessionStart`) -- uses **Haiku** to intelligently pick which vault notes to inject. Disabled by default.
- **Semantic dedup** during summarization -- uses **Haiku** to compare candidate notes against existing vault content before writing.

Everything else -- indexing, embedding, searching, hook execution, daily notes, git commits -- is local Python with zero API calls.

### Will this bloat my agent context?

No. The `SessionStart` hook injects a **compact one-line-per-note index** (title + tags only) as context, not full note contents. For a vault with 300+ notes this typically adds 3-5 KB -- roughly 1,000 tokens. The `PreCompact` and `PostCompact` hooks inject a small snapshot (~20 lines) of the current task and recently-touched files so Claude can resume after context compaction. None of these inject full note bodies into the conversation. When Claude needs a specific note, it reads it on demand via the vault-explorer agent or the Read tool.

### How can I share a vault across multiple machines or with a team?

The installer sets up everything you need. It initializes the vault as a git repo, configures `.gitignore` for machine-local files (`embeddings.db`, `pending_summaries.jsonl`, `hook_events.log`), installs a `post-merge` git hook that automatically rebuilds the local search index after every `git pull`, and writes your OS username into `vault.username` in `config.yaml`.

Daily notes are stored as `Daily/YYYY-MM/DD-{username}.md` so multiple team members can push to the same remote without daily-note merge conflicts. Each person's notes land in their own file.

To share:

1. Run the installer on each machine: `uv run install.py --force --yes` (prompts for username interactively, or pass `--vault-username alice`)
2. Push to a private remote: `cd ~/ClaudeVault && git remote add origin <url> && git push -u origin main`
3. On other machines: clone the vault, then run the installer

If you have an existing vault with legacy `DD.md` daily notes, migrate them once:
```bash
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/vault_doctor.py --migrate-daily-notes --execute
```

See [docs/VAULT_SYNC.md](docs/VAULT_SYNC.md) for the full setup guide and troubleshooting.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for a detailed list of changes in each release.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding constraints, and PR guidelines.

## License

[MIT](LICENSE) -- [Paul Robello](https://github.com/paulrobello)

## Related Documentation

- [docs/README.md](docs/README.md) -- Navigation index for all files in the `docs/` directory
- [docs/MCP.md](docs/MCP.md) -- parsidion-mcp MCP server: installation, configuration, and tools reference
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) -- System architecture, file layout, and hook design
- [docs/EMBEDDINGS.md](docs/EMBEDDINGS.md) -- Semantic search setup, embeddings database, and evaluation
- [docs/EMBEDDINGS_EVAL.md](docs/EMBEDDINGS_EVAL.md) -- Evaluation harness for benchmarking embedding models and chunking strategies
- [docs/MCPL.md](docs/MCPL.md) -- MCP Launchpad CLI: installation, configuration, and integration with Claude Code
- [docs/AGENTCHROME.md](docs/AGENTCHROME.md) -- AgentChrome browser control CLI: installation, capabilities, and integration with the research agent
- [docs/VISUALIZER.md](docs/VISUALIZER.md) -- Vault Visualizer: architecture, graph engine, data model, and configuration
- [docs/DOCUMENTATION_STYLE_GUIDE.md](docs/DOCUMENTATION_STYLE_GUIDE.md) -- Documentation standards for this project
- [SECURITY.md](SECURITY.md) -- Vulnerability disclosure policy and security scope statement
