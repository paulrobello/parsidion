# Parsidion

[![CI](https://github.com/paulrobello/parsidion/actions/workflows/ci.yml/badge.svg)](https://github.com/paulrobello/parsidion/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python 3.13+](https://img.shields.io/badge/Python-3.13%2B-blue.svg)

A second brain for coding agents -- a markdown knowledge vault that gives AI coding assistants persistent memory, cross-session context, and a searchable store of everything they learn. [Obsidian](https://obsidian.md/) is **not required** -- it is an optional viewer for graph visualization and browsing.

Parsidion replaces fragile, tool-specific memory with a richly organized markdown vault. Runtime adapters load relevant context at startup, capture durable learnings from sessions, and snapshot working state before compaction where supported. A research agent saves structured findings, and an AI-powered summarizer generates vault notes from session transcripts.

> **New in 0.23.3:** summarizer timeout fallback (180s default window prevents premature transcript dead-letters), doctor required-fields rule aligned with `note_schema.py` to enforce `tags` on knowledge notes, and staleness/embeddings-disabled fallback for DB-first note reads with decoupled metadata table writes. See the [Changelog](CHANGELOG.md).

![Parsidion Architecture](https://raw.githubusercontent.com/paulrobello/parsidion/main/docs/parsidion-architecture.png)

> [View the interactive architecture slideshow](https://paulrobello.github.io/parsidion/vault-architecture-slideshow.html) for a detailed walkthrough of every component.
>
> **Build session slideshows:** [Vault Explorer Agent](https://paulrobello.github.io/parsidion/vault-explorer-slideshow.html) · [Research Documentation Agent](https://paulrobello.github.io/parsidion/research-agent-slideshow.html) · [Project Explorer Agent](https://paulrobello.github.io/parsidion/project-explorer-slideshow.html) · [Vault Deduplicator](https://paulrobello.github.io/parsidion/vault-deduplicator-slideshow.html)

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

> **Deep reference material has moved.** This README keeps the overview, install, and quick start; the full component catalogue (every script, hook, and agent), the complete `config.yaml` reference, and the CLI command-by-command guide live under [`docs/`](docs/README.md). Key references: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (components + configuration), [docs/USAGE.md](docs/USAGE.md) (full CLI reference), [docs/MULTI_VAULT.md](docs/MULTI_VAULT.md) (multi-vault), [docs/PI_EXTENSION.md](docs/PI_EXTENSION.md) (pi extension install + smoke tests).

## Prerequisites

- **Python 3.13+**
- **[uv](https://docs.astral.sh/uv/)** -- Python package runner and manager
- **[bun](https://bun.sh/)** -- JavaScript runtime and package manager for the Vault Visualizer and its quality gate (`make visualizer-check` is part of `make checkall`)
- **[Obsidian](https://obsidian.md/)** (optional) -- for vault browsing and graph view
- **Claude Code, Codex CLI, and/or Antigravity CLI (`agy`)** -- runtime integration target(s) selected during install
- **[jq](https://jqlang.github.io/jq/)** (optional) -- required by the `scripts/show-context` preview script; install via `brew install jq` (macOS) or your system package manager
- **[mcpl](https://github.com/kenneth-liao/mcp-launchpad)** (optional, legacy) -- MCP Launchpad, a unified CLI for discovering and calling tools from any MCP server. Not installed by Parsidion; the research agent only considers it when already on `PATH` (see [docs/archive/MCPL.md](docs/archive/MCPL.md))
- **[agentchrome](https://github.com/Nunley-Media-Group/AgentChrome)** (optional, recommended) -- native CLI for browser control via Chrome DevTools Protocol; used by the research agent to fetch fully-rendered pages for higher-quality markdown conversion (see [docs/AGENTCHROME.md](docs/AGENTCHROME.md)); falls back to `curl` when unavailable
- **parsight** (optional; **coming soon — not yet publicly available**) -- Rust code-memory daemon; when released and installed, vault semantic search upgrades to hybrid BM25+vector+graph retrieval with silent fallback to local embeddings, and agents gain a cross-repo code-memory bridge (see [docs/PARSIGHT.md](docs/PARSIGHT.md)). parsidion works fully without it today.

> **Platform support:** Works on macOS, Linux, and Windows. On macOS and Linux the installer **symlinks** `~/.claude/skills/parsidion` back to this repo, so edits under `skills/` are live in the installed location without a reinstall. On Windows the installer **copies** the skill files (symlinks require elevated privileges or Developer Mode), so edits under `skills/` are *not* picked up live — re-run `uv run install.py --force --yes` after every source change. This symlink-vs-copy split is the source of the "two copies of the same codebase" concern (ARC-021); it is real only on Windows.

## Quick Start

1. **Clone the repository:**
   ```bash
   git clone https://github.com/paulrobello/parsidion.git
   cd parsidion
   ```

2. **Run the installer:**
   ```bash
   uv run install.py
   ```
   The installer prompts for runtime integrations. Google shut down Gemini CLI for consumers on 2026-06-18; Antigravity CLI (`agy`) is the successor (enterprise licenses are unaffected). Depending on your selection, it may configure Claude Code assets under `~/.claude/`, Codex CLI hooks under `~/.codex/`, and Antigravity CLI (`agy`) hooks under `~/.gemini/config/hooks.json`.

3. **Restart the selected runtime(s)** to activate the hooks.

4. **Verify the install:**
   ```bash
   ls ~/.claude/skills/parsidion/SKILL.md   # skill is in place
   ls ~/ParsidionVault                       # vault directory exists (or ~/ClaudeVault on legacy installs)
   vault-stats --summary                     # installed CLIs are on PATH; expects 0 notes fresh after install
   ```
   Then start a runtime session, work in a project for a few minutes, and end it. Re-run `vault-stats --summary`; the note counts should be non-zero after your first session is summarized (run `summarize_sessions.py` or wait for the nightly scheduled run).

That's it. Your selected runtime integration(s) now have persistent memory backed by a markdown vault. New installs default to `~/ParsidionVault/`; existing `~/ClaudeVault/` installs are detected and reused automatically. Optionally, open that directory in [Obsidian](https://obsidian.md/) to browse notes and explore the knowledge graph.

## Installation

```bash
# Interactive install (prompts for vault location)
uv run install.py

# Non-interactive with default vault (~/ParsidionVault, or legacy ~/ClaudeVault if present)
uv run install.py --force --yes

# Non-interactive with custom vault path
uv run install.py --vault ~/MyVault --yes

# Preview without making changes
uv run install.py --dry-run

# Migrate a legacy default vault from ~/ClaudeVault to ~/ParsidionVault
uv run install.py --migrate-vault --yes

# Also install the seven vault CLI tools as global commands
# (vault-search, vault-new, vault-stats, vault-review, vault-export, vault-merge, vault-conflicts)
uv run install.py --force --yes --install-tools

# Schedule nightly auto-summarization (launchd on macOS, cron on Linux)
uv run install.py --schedule-summarizer
uv run install.py --schedule-summarizer --summarizer-hour 3   # run at 3 AM (default)

# Graph rebuild is enabled by default. To also include Daily notes:
uv run install.py --schedule-summarizer --graph-include-daily

# To disable graph rebuild:
uv run install.py --schedule-summarizer --no-rebuild-graph

# Install the omp extension into a non-default omp home (default: $PI_CONFIG_DIR or ~/.omp)
uv run install.py connect omp --omp-home ~/custom-omp

# During uninstall, also remove ~/.config/parsidion/vaults.yaml (always preserved otherwise;
# has no effect unless the Claude integration is being removed and is required even under --yes)
uv run install.py --uninstall --purge-config --yes

# Friendly multi-agent verbs — install or remove one runtime integration
uv run install.py connect claude      # install Claude Code integration only
uv run install.py connect codex       # install Codex CLI integration (hooks + AGENTS.md)
uv run install.py connect antigravity      # install Antigravity CLI (`agy`) integration (hooks + GEMINI.md)
uv run install.py connect pi          # install the pi TypeScript extension (~/.pi/agent/extensions)
uv run install.py connect omp         # install the same extension for omp (~/.omp/agent/extensions)
uv run install.py disconnect codex    # remove Codex CLI integration
uv run install.py disconnect omp      # remove the omp extension only
```

`connect <claude|codex|antigravity|pi|omp>` is a friendlier alias for `--runtime <agent>` that installs only one integration (`connect pi` / `connect omp` install the shared TypeScript extension into the runtime's extensions directory instead of hooks). `disconnect <...>` removes that agent's full Parsidion integration (equivalent to a targeted `--uninstall --runtime <agent>`; `disconnect pi` / `disconnect omp` remove just the extension files).

**Options:**

| Flag | Description |
|------|-------------|
| `--vault PATH` | Vault path (skips interactive prompt) |
| `--claude-dir PATH` | Target Claude config dir (default: `~/.claude`) |
| `--codex-home PATH` | Target Codex home for hooks/config (default: `$CODEX_HOME` or `~/.codex`) |
| `--gemini-home PATH` | Target Antigravity/Gemini config home for hook settings (default: `~/.gemini`) |
| `--omp-home PATH` | omp config home for the `connect omp` extension install (default: `$PI_CONFIG_DIR` or `~/.omp`); the extension lands in `<omp-home>/agent/extensions` |
| `--purge-config` | With `--uninstall`, also remove `~/.config/parsidion/vaults.yaml` (always preserved otherwise; no effect unless the Claude integration is being removed, and required even under `--yes`) |
| `--runtime {claude,codex,antigravity,both,all,none}` | Runtime integration target; interactive installs default to `both` (Claude + Codex), while `--yes` defaults to `claude` for backwards compatibility |
| `--dry-run / -n` | Preview all actions, no changes made |
| `--verbose / -v` | Show detailed output |
| `--force / -f` | Overwrite existing skill files without prompting |
| `--yes / -y` | Skip all confirmation prompts; uses `~/ParsidionVault` if `--vault` not given, or legacy `~/ClaudeVault` when it already exists |
| `--skip-hooks` | Do not modify runtime hook files (`~/.claude/settings.json`, `~/.codex/hooks.json`, or `~/.gemini/config/hooks.json`) |
| `--skip-agent` | Do not install any agents |
| `--enable-ai` | Enable AI-powered note selection: writes `ai_model` to `config.yaml` and uses the configured prompt AI backend (SessionStart timeout is 60 s for every install regardless) |
| `--enable-embeddings` | Enable semantic search embeddings: writes `embeddings.enabled = true` to `config.yaml` |
| `--install-tools` | Install `vault-search`, `vault-new`, `vault-stats`, `vault-review`, `vault-export`, `vault-merge`, and `vault-conflicts` as global CLI commands via `uv tool install` |
| `--schedule-summarizer` | Generate a launchd plist (macOS) or cron job (Linux) for nightly auto-summarization |
| `--summarizer-hour N` | Hour (0-23) for the scheduled summarizer job (default: 3) |
| `--rebuild-graph` | Add `--rebuild-graph` to the scheduled command so `graph.json` is regenerated each night (default: on; use with `--schedule-summarizer`) |
| `--no-rebuild-graph` | Disable graph rebuild in the scheduled summarizer |
| `--graph-include-daily` | Include Daily folder notes in the nightly graph rebuild (use with `--rebuild-graph`) |
| `--vault-username NAME` | Username suffix for per-user daily notes (`Daily/YYYY-MM/DD-{username}.md`); written to `vault.username` in `config.yaml` so it persists across sessions. Defaults to `$USER` when not set. The interactive installer prompts for this |
| `--create-vaults-config` | Create `~/.config/parsidion/vaults.yaml` for multi-vault support (see [Multi-Vault Support](#multi-vault-support)) |
| `--migrate-vault` | Rename legacy `~/ClaudeVault` to `~/ParsidionVault` and leave `~/ClaudeVault` as a compatibility symlink |
| `--no-legacy-vault-symlink` | With `--migrate-vault`, skip creating the compatibility symlink |
| `--uninstall` | Remove installed skill, agents, hook registrations, and launchd plist / cron job |
| `--uninstall-hooks` | Remove only installed hook registrations from runtime hook files (`~/.claude/settings.json`, `~/.codex/hooks.json`, or `~/.gemini/config/hooks.json`) |

### Runtime integrations

Interactive installs ask which runtime integrations to configure:

- `claude` — Claude Code skill, agents, and hooks under `~/.claude`
- `codex` — Codex CLI hooks under `~/.codex`
- `antigravity` — Antigravity CLI (`agy`) `PreInvocation` and `Stop` hooks under `~/.gemini/config/hooks.json`
- `both` — Claude Code and Codex CLI integrations
- `all` — Claude Code, Codex CLI, and Antigravity CLI (`agy`) integrations
- `none` — shared vault tooling only; do not register runtime hooks

Non-interactive installs keep the historical Claude-only default unless you pass `--runtime` explicitly:

```bash
uv run install.py --yes --runtime claude
uv run install.py --yes --runtime both
uv run install.py --yes --runtime codex
uv run install.py --yes --runtime antigravity
uv run install.py --yes --runtime all
```

Codex integration uses native Codex hooks for session lifecycle events (`SessionStart`, `Stop`, `SubagentStop`) and requires `hooks = true` in `~/.codex/config.toml`. Parsidion can enable this during install and registers hooks in `~/.codex/hooks.json`. Parsidion does not manage Codex auth or copy `~/.codex/auth.json`.

Antigravity runtime hooks are separate from prompt AI backend selection. `--runtime antigravity` or `--runtime all` registers Antigravity CLI (`agy`) `PreInvocation` and `Stop` commands in `~/.gemini/config/hooks.json`; it does not add an Antigravity prompt AI backend. Antigravity has no native subagent lifecycle hook, so subagent-style capture remains Claude/pi-specific.

During interactive installation, the installer prompts for three optional features:

1. **"Install CLI tools?"** (default: yes) — runs `uv tool install --editable ".[tools]"` to register `vault-search`, `vault-new`, `vault-stats`, `vault-review`, `vault-export`, `vault-merge`, and `vault-conflicts` as global commands. Use `--install-tools` to enable this non-interactively (e.g. with `--yes`).
2. **"Enable AI-powered note selection?"** (default: yes) — writes `ai_model` to `config.yaml`, enabling the configured prompt AI backend to intelligently select relevant vault notes at session start. Use `--enable-ai` to enable this non-interactively (e.g. with `--yes`). The SessionStart hook timeout is 60 s on every install, so no timeout adjustment is needed.
3. **"Enable embeddings?"** (default: yes) — writes `embeddings.enabled = true` to `config.yaml`, enabling the vector index used by `vault-search` semantic mode and `session_start_hook` with `use_embeddings`. Requires ~67 MB model download on first run. Use `--enable-embeddings` to enable this non-interactively (e.g. with `--yes`).

After installation, restart the selected runtime(s) to activate hooks. Optionally, open the vault path in Obsidian for graph visualization and note browsing -- this is not required for the system to work.

### Migrating a legacy default vault

If you have an older default vault at `~/ClaudeVault`, you can rename it to the new default `~/ParsidionVault`:

```bash
uv run install.py --migrate-vault --yes
```

The migration refuses unsafe states, such as both paths existing as separate real directories. By default it leaves `~/ClaudeVault` as a symlink to `~/ParsidionVault` so older scripts or editor bookmarks keep working. Add `--no-legacy-vault-symlink` if you do not want that compatibility symlink. Use `--dry-run` to preview without moving anything.

## Components

### Parsidion vault (`~/.claude/skills/parsidion/`)

A markdown vault-based knowledge management system that replaces flat runtime memory with a richly organized, searchable, cross-linked knowledge base. New installs use `~/ParsidionVault/`; legacy `~/ClaudeVault/` vaults are reused automatically. The vault is plain markdown -- [Obsidian](https://obsidian.md/) can be used to visualize the graph and browse notes but is not required.

**Auto-triggering:** The skill includes YAML frontmatter with a description that enables automatic invocation when users mention saving knowledge, checking notes, or persisting findings across sessions.

The skill ships ~130 Python modules (hooks, CLIs, and library code across the flat script layer and the `core`/`summarizer`/`doctor`/`cli`/`session_start` subpackages) and 9 note templates (daily, project, language, framework, pattern, debugging, tool, research, knowledge). The full per-script catalogue — purpose, public API surface, and which component consumes each one — lives in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) under **Component Details**, alongside the vault directory layout, the Obsidian graph color-group table, and the data-flow diagrams.

The default vault structure:

```text
~/ParsidionVault/            # Or legacy ~/ClaudeVault/ when upgrading
  CLAUDE.md                  # Auto-generated lean index (stats, conventions, recent activity)
  TAGS.md                    # Auto-generated tag cloud for summarizer tag reuse
  config.yaml                # Optional -- hook/summarizer settings (see Configuration)
  pending_summaries.jsonl    # Queue of sessions awaiting AI summarization
  dead_letters.jsonl         # Sessions that repeatedly failed summarization (gitignored)
  embeddings.db              # SQLite: note embeddings + note_index metadata
  hook_events.log            # Structured JSON log of hook executions
  graph.json                 # Pre-built knowledge graph for the visualizer (gitignored)
  Daily/YYYY-MM/DD-{username}.md   # Per-user daily notes
  Projects/ Languages/ Frameworks/ Patterns/ Debugging/
  Tools/ Research/ Knowledge/ History/ Templates/
```

For every script, the Obsidian color groups, and how the pieces fit together, see:

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) -- component catalogue, hook lifecycle, configuration reference, file layout, and graph color groups
- [docs/USAGE.md](docs/USAGE.md) -- the full vault CLI command reference (`vault-search`, `vault-stats`, `vault-doctor`, `vault-merge`, etc.)


### PARSIDION-VAULT.md (`~/.claude/PARSIDION-VAULT.md`)

An always-on guidance file loaded every Claude Code session via `@PARSIDION-VAULT.md` in `~/.claude/CLAUDE.md`. It enforces the **vault-first rule** unconditionally -- no explicit invocation needed.

**What it enforces:**
- **Debugging:** Search `<resolved vault>/Debugging/` before diagnosing any error. Extract the key signal (exception class, package name, distinctive phrase) and Grep the vault first. If found, apply the documented fix. If not, diagnose then save the solution.
- **Implementation:** Search `<resolved vault>/Patterns/`, `Frameworks/`, `Languages/`, and `Projects/` before writing non-trivial code. Reuse proven implementations from prior projects rather than writing from scratch.
- **Saving solutions:** After solving a non-obvious problem, save it to the appropriate vault folder and rebuild the index.

The installer copies `PARSIDION-VAULT.md` from the repo root to `~/.claude/` and ensures the `@PARSIDION-VAULT.md` import line exists in `~/.claude/CLAUDE.md`. Uninstall removes both. Pre-rename installs are migrated automatically: the legacy `CLAUDE-VAULT.md` copy and its `@CLAUDE-VAULT.md` import line are removed on reinstall.

### Vault Explorer Agent (`~/.claude/agents/vault-explorer.md`)

A Haiku-powered read-only subagent that isolates vault lookups from the main session context. Dispatched automatically when the main session needs to search the vault.

**7-step search procedure:**
1. **Semantic search** -- `vault-search "QUERY" --json`; ≥3 results with score ≥ 0.35 → done
2. **Metadata search** -- `vault-search --tag/--folder/--type/--project/--recent-days` with inferred filters; ≥3 results → done
3. **Orient** -- reads the resolved vault's `CLAUDE.md` index
4. **Extract signals** -- exception class, package name, or keyword
5. **Search by priority folder** -- Grep by query type table
6. **Rank & read** -- top 5 by semantic score, then folder priority
7. **Synthesize** -- returns `## Answer` + `## Sources`

> **Note:** The vault-explorer agent is listed in `excluded_agents` in `config.yaml` to prevent its own transcripts from being recursively harvested by the SubagentStop hook.

### Research Agent (`~/.claude/agents/research-agent.md`)

Technical research agent that searches the vault first, conducts web research, and saves findings to the appropriate vault folder with proper YAML frontmatter. Fetches pages via `agentchrome dom get-html "css:html"` piped through `html-to-md.py` for noise-free markdown (curl fallback if agentchrome unavailable). May fall back to `mcpl` for search when Brave Search hits rate limits, but only if it is already installed (`which mcpl`) -- see [docs/archive/MCPL.md](docs/archive/MCPL.md) for the legacy reference.

### Vault Deduplicator Agent (`~/.claude/agents/vault-deduplicator.md`)

A Haiku-powered agent that scans the vault for near-duplicate note pairs via `vault-merge --scan`, batches them into parallel subagents for evaluation and merging with `--no-index`, and runs one final index rebuild. See [CHANGELOG.md](CHANGELOG.md) for details.

### Project Explorer Agent (`~/.claude/agents/project-explorer.md`)

A read-only agent dispatched when the user asks to explore, analyze, or document a project's architecture, features, and patterns for cross-project vault reference. It walks the target repo, summarizes the architecture and notable patterns, and saves a structured project note under `<resolved vault>/Projects/` so future sessions can recall what the project does without re-reading it.

### HTML to Markdown (`skills/parsidion/scripts/html-to-md.py`)

A PEP 723 standalone script (installed to `~/.claude/skills/parsidion/scripts/html-to-md.py`) that converts HTML to clean, noise-free markdown optimized for LLM consumption. Strips navigation, banners, cookie notices, and script/style noise while preserving code fences with language annotations. Used by the research agent to clean `agentchrome` page output.

```bash
uv run --script ~/.claude/skills/parsidion/scripts/html-to-md.py page.html          # file → stdout
uv run --script ~/.claude/skills/parsidion/scripts/html-to-md.py - < page.html      # stdin → stdout
agentchrome dom get-html "css:html" | uv run --script ~/.claude/skills/parsidion/scripts/html-to-md.py - --url https://example.com
```

### Context Preview (`scripts/show-context`)

A shell script that previews what vault context would be injected at session start for a given project directory. Useful for debugging the SessionStart hook. Requires `jq` to be installed.

```bash
./scripts/show-context                    # Preview context for cwd
./scripts/show-context ~/Repos/myproject  # Preview context for a specific project
```

### pi Extension Installer (`scripts/install-pi-extension`)

Helper script to install the `parsidion` pi extension into `~/.pi/agent/extensions`.

```bash
./scripts/install-pi-extension            # copy mode (default)
./scripts/install-pi-extension --symlink  # dev mode, keeps files linked to this repo
```

### Hooks (`~/.claude/settings.json`)

All hooks read `<resolved vault>/config.yaml` for settings (see [Configuration](#configuration)).

| Hook Event | Script | Timeout | Config section | Notes |
|------------|--------|---------|----------------|-------|
| SessionStart | `session_start_hook.py` | 60 s (registered by the installer; covers `--ai`) | `session_start_hook` | `--ai [MODEL]` or `session_start_hook.ai_model` enables selection through the configured prompt AI backend |
| SessionEnd | `session_stop_wrapper.sh` → `session_stop_hook.py` | async | `session_stop_hook` | Shell wrapper outputs `{}` immediately; Python script runs detached via `nohup` |
| PreCompact | `pre_compact_hook.py` | 10 s | `pre_compact_hook` | Configurable transcript lines |
| PostCompact | `post_compact_hook.py` | 10 s | — | Reads last Pre-Compact Snapshot from today's daily note and returns it as `additionalContext` |
| SubagentStop | `subagent_stop_hook.py` | async | `subagent_stop_hook` | Non-blocking; skips agents listed in `excluded_agents` |

Transcript compatibility for stop hooks:
- Claude Code JSONL (`type: "assistant" | "user"`)
- pi JSONL (`type: "message"` + `message.role`)
- Antigravity JSONL model output records (`role: "model"`, `message.role: "model"`, or `llm_response.candidates[].content.parts`)
- Accepted roots: `~/.claude/`, `~/.pi/`, `<cwd>/.pi/`, `~/.codex/sessions/`, `~/.gemini/`, and `<cwd>/.gemini/`


### pi runtime integration

The pi adapter ships as a TypeScript extension that shells out to Parsidion's Python hook scripts, so pi sessions use the same vault and queue path as Claude Code, Codex CLI, and Antigravity CLI (`agy`). Install, Anthropic/GLM configuration, and the full smoke-test walkthrough have moved to a dedicated guide:

➜ **[docs/PI_EXTENSION.md](docs/PI_EXTENSION.md)** — install (`scripts/install-pi-extension --symlink`), the `/parsidion` status command, effective `anthropic_env` precedence, and the three-step pi SessionEnd/SubagentStop/summarizer validation.


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

![Vault Visualizer - Read Mode](https://raw.githubusercontent.com/paulrobello/parsidion/main/screenshot-vault-read.png)

![Vault Visualizer - Graph Mode](https://raw.githubusercontent.com/paulrobello/parsidion/main/screenshot-vault-graph.png)

**Running the visualizer:**

```bash
# First time: install dependencies
make visualizer-setup

# Build graph data from vault embeddings
make graph               # rebuild graph.json (Daily notes included by default)
make graph-with-daily    # alias — Daily inclusion is the default behaviour

# Start development server (port 3999)
cd visualizer && bun dev

# Production
make build-visualizer        # compile
cd visualizer && bun start   # serve on port 3999
make stop-visualizer         # stop
```

> **Note:** Requires vault embeddings to be built first: `uv run --no-project ~/.claude/skills/parsidion/scripts/build_embeddings.py`

See [docs/VISUALIZER.md](docs/VISUALIZER.md) for the full architecture, data model, graph engine details, and configuration reference.

## parsidion-mcp (Claude Desktop)

An optional MCP server that exposes Parsidion vault operations to **Claude Desktop** (and any other MCP-compatible client) over stdio. It lives in the `parsidion-mcp/` subdirectory and is installed independently from the main skill.

**Eight tools:**

| Tool | Description |
|------|-------------|
| `vault_search` | Semantic search (natural language query) or metadata search (tag/folder/type/project/days) |
| `vault_read` | Read a vault note by relative or absolute path |
| `vault_write` | Create or overwrite a vault note |
| `vault_context` | Return a session-start-style context block (compact index or verbose summaries) |
| `rebuild_index` | Rebuild `CLAUDE.md`, `MANIFEST.md` files, and the `note_index` SQLite table |
| `vault_doctor` | Scan vault notes for structural issues; optionally repair them; `--fix-sessions` detects multi-note sessions |
| `vault_health` | Composite 0–100 vault-health score across eight dimensions (index freshness, queue, graph, metadata, embeddings, tags, files, hook latency) with concrete next-action commands; subprocess wrapper around `vault-stats --health --json` (ENH-007) |
| `code_search` | Search a parsight-indexed repository's code graph by natural language; requires the [parsight](docs/PARSIGHT.md) backend (returns a clear error if parsight is unavailable) |

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

All hooks and the summarizer read `<resolved vault>/config.yaml`. Precedence: **defaults → config.yaml → config.local.yaml → CLI args** (last one wins). `config.local.yaml` is an optional, always-gitignored overlay in the same vault directory, deep-merged over `config.yaml` section-by-section. Environment variables Parsidion reads at runtime (`CLAUDE_VAULT`, `PARSIDION_RUNTIME`, `VAULT_SEARCH_*`, and others) are catalogued in [docs/USAGE.md](docs/USAGE.md#environment-variables).

Copy the template to get started:
```bash
cp ~/.claude/skills/parsidion/templates/config.yaml ~/ParsidionVault/config.yaml
```

> **Note:** Model IDs shown in the config block below (e.g. `claude-sonnet-4-6`,
> `claude-haiku-4-5-20251001`, `BAAI/bge-small-en-v1.5`) are the hardcoded script defaults.
> Override any of them via the corresponding key in `<resolved vault>/config.yaml`.
>
> **Anthropic-compatible transport settings:** You can also define `ANTHROPIC_*`
> and `API_TIMEOUT_MS` values in `config.yaml` under `anthropic_env:` using the
> real env var names as keys. The shipped template leaves every key `null` so
> traffic routes to the real Anthropic endpoint with Anthropic model IDs — useful
> as a baseline and for org-key / proxy / Bedrock overrides. To route traffic
> through a third-party Anthropic-compatible gateway instead (e.g. Z.ai / GLM),
> set `ANTHROPIC_BASE_URL` and the model overrides, and be aware that nightly
> summarization transcripts — including source code and file contents — will
> flow to that endpoint. Precedence for these values is: **real environment
> variable > `anthropic_env` in `config.yaml` > script default behavior**.
>
> The pi `/parsidion` command reports the effective source for these values
> (`env`, `vault config`, or `unset`) and masks secret previews, but Python hook
> scripts remain the runtime source of truth.

```yaml
session_start_hook:    # Context injection: ai_model, max_chars, ai_timeout, recent_days,
                       # use_embeddings, track_delta, graph_expand (Tier 1 wikilink neighbours),
                       # graph_rerank (Tier 2 tag-overlap rerank), verbose_mode, debug
session_stop_hook:     # Queue + auto-summarize: ai_model, auto_summarize, auto_summarize_after,
                       # transcript_tail_lines, pi_transcript_tail_lines
subagent_stop_hook:    # Subagent capture: enabled, min_messages, excluded_agents
pre_compact_hook:      # lines (transcript tail to snapshot)
summarizer:            # model, max_parallel, transcript_tail_lines/bytes, max_cleaned_chars,
                       # cluster_model, dedup_threshold, dead_letter_retention_days
ai:                    # backend: auto | claude-cli | codex-cli | grok-cli | none
ai_models:             # per-backend small/large model IDs (claude, codex, grok)
claude_cli:            # minimal_context, system_prompt, timeout (claude -p invocation)
grok_cli:              # command, timeout, minimal_context, system_prompt (grok CLI, OAuth)
codex_cli:             # command, timeout, sandbox, ephemeral, skip_git_repo_check, suppress_notify
anthropic_env:         # ANTHROPIC_API_KEY/AUTH_TOKEN/BASE_URL/CUSTOM_HEADERS,
                       # ANTHROPIC_DEFAULT_{HAIKU,SONNET,OPUS}_MODEL, API_TIMEOUT_MS, HTTPS/HTTP_PROXY
defaults:              # haiku_model (centralized)
embeddings:            # model, min_score, top_k, decay_*, service_enabled (ENH-003 opt-in),
                       # service_idle_exit
parsight:               # enabled, binary, timeout_s (see docs/PARSIGHT.md)
search:                # backend: auto | parsight | embeddings | none
git:                   # auto_commit
event_log:             # enabled, max_lines, path
adaptive_context:      # enabled, decay_days
transcripts:           # tail_lines, tail_bytes, max_line_bytes (transcript reading)
vault:                 # username (Daily/YYYY-MM/DD-{username}.md suffix)
adapters:              # load_external (opt-in ~/.config/parsidion/adapters/*.py drop-ins, ENH-006)
```

For the full per-key reference (every option with type, default, and description), see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) under **[Configuration](docs/ARCHITECTURE.md#configuration)**. The shipped template at `~/.claude/skills/parsidion/templates/config.yaml` is the canonical commented reference; copy it into your vault to get started.


`ai.backend` controls prompt-style AI helpers used by session-start selection, session-stop classification, session summarization, vault doctor repairs, vault merge synthesis, and eval utilities. `auto` prefers the active runtime when Parsidion can detect it: Codex runtime hints use `codex exec`, Grok runtime hints (`PARSIDION_RUNTIME=grok`) use the `grok` CLI, Claude runtime hints use `claude -p`, and ambiguous environments keep the historical Claude CLI behavior. The claude-cli and grok-cli backends run with `minimal_context` (default on): the system prompt is replaced and the call runs from a clean scratch cwd so the project's CLAUDE.md/AGENTS.md chain and the CLI's skill catalog are not ingested — parsidion prompts are self-contained text transforms.

Codex mode uses the Codex CLI and its normal authentication path. Parsidion does not read, copy, or manage `~/.codex/auth.json`, and this is not OpenAI API-key provider support. Prompt-style Codex calls default to `codex exec --ephemeral --sandbox read-only --skip-git-repo-check --config notify=[]` and write/read the final answer via `--output-last-message`. The `notify=[]` override suppresses user-configured Codex turn-complete notifications for internal Parsidion calls.

`summarize_sessions.py` uses the configured prompt AI backend: Claude runs through `claude -p`, Codex runs through `codex exec`, and grok runs through `grok --prompt-file` (OAuth login via the `grok` CLI). No Claude Agent SDK, Codex SDK, or Grok SDK is required for this path. Leave `summarizer.model` and `summarizer.cluster_model` as `null` to use backend-aware large/small defaults from `ai_models.<backend>`.

## Multi-Vault Support

Parsidion supports multiple isolated vaults per machine — separate work from personal notes, give each codebase its own knowledge base, or share a team vault while keeping a private one. Every vault-aware tool (`vault-search`, `vault-stats`, `vault-doctor`, `build_embeddings.py`, etc.) and every lifecycle hook accepts a `--vault PATH_OR_NAME` flag, and a `vaults.yaml` registry maps friendly names to paths.

The full setup walkthrough (`--create-vaults-config`), the vault-aware tool and hook tables, and the four-step default vault resolution order have moved to a dedicated guide:

➜ **[docs/MULTI_VAULT.md](docs/MULTI_VAULT.md)**

Quick example:

```bash
# Create ~/.config/parsidion/vaults.yaml
uv run install.py --create-vaults-config

# Then use names everywhere
vault-search "error patterns" --vault work
vault-stats --summary --vault personal
```

## Vault Git Integration

The vault supports optional git version control. When `<resolved vault>/.git` exists, scripts automatically stage and commit changes after every write (daily notes, index rebuilds, session notes). Controlled by `git.auto_commit` in config.

The installer initialises the vault as a git repo on first install and writes a `.gitignore` covering machine-local and secret-bearing files, including: `.obsidian/`, `embeddings.db`, `pending_summaries.jsonl`, `dead_letters.jsonl`, `hook_events.log`, `graph.json`, `summarizer_state.json`, `doctor_state.json`, `conflicts/`, and (since 0.12.0) `config.yaml` / `config.local.yaml` so API keys never enter git history. The auto-commit pathspec explicitly skips the vault-root `config.yaml`. Do **not** overwrite the installer-managed `.gitignore` with `echo "..." > .gitignore` — that truncates the protective list and lets `git add -A` stage secrets. Append with `>>` if you need extra entries.

```bash
cd ~/ParsidionVault
git init                                  # already done by the installer; run manually only for a pre-existing vault
# Verify the installer-managed .gitignore is in place (do NOT truncate it):
cat .gitignore
git add -A && git commit -m "chore(vault): initial commit"
```

If you are migrating a vault that predates the config.yaml gitignore, verify it is untracked:

```bash
git -C ~/ParsidionVault ls-files config.yaml    # any output = tracked; untrack with:
git -C ~/ParsidionVault rm --cached config.yaml
```

If no `.git` directory is present, all git operations are silent no-ops.

## File Locations

```text
~/.claude/
  CLAUDE.md                          # Global Claude Code instructions (@imports PARSIDION-VAULT.md)
  PARSIDION-VAULT.md                    # Always-on vault-first guidance (installed by parsidion)
  settings.json                      # Hooks, permissions, plugins
  agents/
    research-agent.md                # Research agent (vault-integrated)
    vault-explorer.md                # Read-only Haiku vault search agent (7-step)
    project-explorer.md              # Read-only project note writer (architecture recaps)
    vault-deduplicator.md            # Near-duplicate note scan/merge orchestrator
  skills/parsidion/
    SKILL.md                         # Vault skill definition
    scripts/                         # Hook scripts, utilities, and html-to-md.py
    templates/                       # Note templates + config.yaml reference

~/ParsidionVault/                    # Markdown vault (knowledge base; open in Obsidian for graph view)
  config.yaml                        # Optional hook/summarizer settings
  embeddings.db                      # Semantic search DB (note_embeddings + note_index tables)
  hook_events.log                    # Structured JSON log of hook executions
```

## Usage

The vault ships seven global CLI tools (installed by `--install-tools`) plus the maintenance scripts under `skills/parsidion/scripts/`. The full command-by-command reference (every `vault-stats` mode, every `vault-doctor` flag, every `vault-merge`/`vault-conflicts`/`vault-export` option, the `VAULT_SEARCH_*` env-var table, the trigger eval, the programmatic `vault_common` API, and the install/uninstall commands) lives in a dedicated guide:

➜ **[docs/USAGE.md](docs/USAGE.md)**

The most common operations:

```bash
# Rebuild the vault index after creating/renaming/deleting notes
uv run --no-project ~/.claude/skills/parsidion/scripts/update_index.py

# Build or rebuild semantic search embeddings
uv run --no-project ~/.claude/skills/parsidion/scripts/build_embeddings.py

# Semantic / metadata / full-text / interactive vault search
vault-search "sqlite vector search patterns" -r   # rich output, top results
vault-search -f Patterns -T python                 # by folder + tag
vault-search --grep "FLOCK" --grep-case            # case-sensitive body search
vault-search --interactive                         # curses TUI

# Vault health and analytics
vault-stats                                        # composite 0–100 health score (default)
vault-stats --pending                              # pending queue + dead-letter status
vault-stats --dashboard                            # every mode combined

# Summarize queued sessions (backend-aware: `claude -p`, `codex exec`, or `grok --prompt-file`)
uv run --no-project ~/.claude/skills/parsidion/scripts/summarize_sessions.py

# Scan + repair vault structural issues
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-all

# Preview what context would be injected at session start
./scripts/show-context ~/Repos/myproject
```

For the per-tool flag reference and the install/uninstall commands, see [docs/USAGE.md](docs/USAGE.md).

## Troubleshooting

### Hooks not firing

- Verify hooks are registered in the selected runtime config: `~/.claude/settings.json` for Claude Code, `~/.codex/hooks.json` for Codex CLI, or `~/.gemini/config/hooks.json` for Antigravity CLI (`agy`). Look for entries pointing to the hook scripts.
- Re-run `uv run install.py --force --yes --runtime all` (or your selected `--runtime`) to re-register hooks.
- Check that the script paths in the runtime hook config are correct and the files exist at those paths.
- Restart the selected runtime after any hook config change.

### Vault not created

- The vault directory (`~/ParsidionVault/` by default, or legacy `~/ClaudeVault/` if present) is created automatically by the SessionStart hook on first run.
- If it was not created, check that the hook is firing (see above).
- You can create it manually: `mkdir -p ~/ParsidionVault` and then run the installer.

### `uv` not found

- Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Ensure `uv` is on your PATH. Restart your shell after installation.
- Verify with `uv --version`.

### Timeout errors with `--ai` flag

- The `--ai` flag on session start/stop hooks calls the configured prompt AI backend for note selection; headless backends (claude `claude -p`, codex `codex exec`, grok `grok --prompt-file`) can take 8–40 s per prompt. Every runtime registration now gives SessionStart 60 s (Claude Code installs are raised to it on reinstall), so manual timeout edits should no longer be needed:
  ```json
  {
    "command": "uv run --no-project ~/.claude/skills/parsidion/scripts/session_start_hook.py --ai",
    "timeout": 60000
  }
  ```
- If timeouts persist, increase `ai_timeout` in `<resolved vault>/config.yaml` (grok-4.6 headless commonly needs 60 s).

### Summarizer fails to run

- The summarizer uses the configured prompt AI backend. Claude backend calls run through `claude -p`; Codex backend calls run through `codex exec`; grok backend calls run through `grok --prompt-file`.
- No Claude Agent SDK, Codex SDK, or Grok SDK is required for the summarizer path.

### Grok backend prompts fail or report an auth error

- The `grok-cli` backend authenticates with the grok CLI's own OAuth login. Run `grok` once interactively and complete the login before the first Parsidion AI call; credentials are stored under `~/.grok`.
- `grok-4.6` headless prompts measure 17–40 s each, so `grok_cli.timeout` defaults to 120 s. If prompts time out, raise it in `<resolved vault>/config.yaml`.
- Set `ai.backend: grok-cli` (or export `PARSIDION_RUNTIME=grok` for auto-resolution) and check that `grok` is on `PATH` or set `grok_cli.command` to its absolute path.
- If using the Claude CLI backend from inside Claude Code, unset the guard variable: `env -u CLAUDECODE uv run --no-project ~/.claude/skills/parsidion/scripts/summarize_sessions.py`
- Check that `pending_summaries.jsonl` exists and has entries.

### `vault-search` command not found

- Run `uv run install.py --force --yes --install-tools` to register the global command, or manually: `cd /path/to/parsidion && uv tool install --editable ".[tools]"`
- Ensure `~/.local/bin/` is on your PATH (Linux/macOS) or `%APPDATA%\Python\Scripts` (Windows).

## FAQ

### Will this use extra API tokens?

Parsidion is designed to minimize token usage. The lifecycle hooks (`SessionStart`, `SessionEnd`, `PreCompact`, `SubagentStop`) are **pure Python scripts** that run locally -- they read transcripts, parse frontmatter, and write files without calling any AI model. The only places that use API tokens are:

- **Session summarizer** (`summarize_sessions.py`) -- runs via the configured prompt AI backend. It launches automatically when the SessionEnd hook's queue threshold is reached (`session_stop_hook.auto_summarize`), runs on demand, and runs nightly when `--schedule-summarizer` is scheduled. By default it uses the backend large model (`ai_models.<backend>.large`) to generate vault notes from queued transcripts. Long transcripts are pre-chunked and summarized by the backend small model (`ai_models.<backend>.small`) first to reduce cost.
- **AI-powered note selection** (optional `--ai` flag on `SessionStart`) -- uses the configured prompt AI backend's small model to intelligently pick which vault notes to inject. Disabled by default.
- **Semantic dedup** during summarization -- uses local embeddings/search to compare candidate notes against existing vault content before writing.

Everything else -- indexing, embedding, searching, hook execution, daily notes, git commits -- is local Python with zero API calls.

### Will this bloat my agent context?

No. The `SessionStart` hook injects a **compact one-line-per-note index** (title + tags only) as context, not full note contents. For a vault with 300+ notes this typically adds 3-5 KB -- roughly 1,000 tokens. The `PreCompact` and `PostCompact` hooks inject a small snapshot (~20 lines) of the current task and recently-touched files so Claude can resume after context compaction. None of these inject full note bodies into the conversation. When Claude needs a specific note, it reads it on demand via the vault-explorer agent or the Read tool.

### How can I share a vault across multiple machines or with a team?

The installer sets up everything you need. It initializes the vault as a git repo, configures `.gitignore` for machine-local files (`embeddings.db`, `pending_summaries.jsonl`, `hook_events.log`, `graph.json`, `summarizer_state.json`, `doctor_state.json`), installs a `post-merge` git hook that automatically rebuilds the local search index after every `git pull`, and writes your OS username into `vault.username` in `config.yaml`.

Daily notes are stored as `Daily/YYYY-MM/DD-{username}.md` so multiple team members can push to the same remote without daily-note merge conflicts. Each person's notes land in their own file.

To share:

1. Run the installer on each machine: `uv run install.py --force --yes` (prompts for username interactively, or pass `--vault-username alice`)
2. Push to a private remote: `cd ~/ParsidionVault && git remote add origin <url> && git push -u origin main`
3. On other machines: clone the vault, then run the installer

If you have an existing vault with legacy `DD.md` daily notes, migrate them once:
```bash
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --migrate-daily-notes --execute
```

See [docs/VAULT_SYNC.md](docs/VAULT_SYNC.md) for the full setup guide and troubleshooting.

## Changelog

Latest release: **0.23.3** (summarizer timeout fallback, doctor tags rule alignment, and note_index staleness/embeddings-disabled fallback). See [CHANGELOG.md](CHANGELOG.md) for a detailed list of changes in each release.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding constraints, and PR guidelines.

## License

[MIT](LICENSE) -- [Paul Robello](https://github.com/paulrobello)

## Related Documentation

- [docs/README.md](docs/README.md) -- Navigation index for all files in the `docs/` directory
- [docs/api/](docs/api/) -- Generated API reference (Python `core`/`installer`/`vault_*` modules and the visualizer TypeScript lib); regenerate with `make docs-api`
- [docs/MCP.md](docs/MCP.md) -- parsidion-mcp MCP server: installation, configuration, and tools reference
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) -- System architecture, file layout, and hook design
- [docs/EMBEDDINGS.md](docs/EMBEDDINGS.md) -- Semantic search setup, embeddings database, and evaluation
- [docs/EMBEDDINGS_EVAL.md](docs/EMBEDDINGS_EVAL.md) -- Evaluation harness for benchmarking embedding models and chunking strategies
- [docs/archive/MCPL.md](docs/archive/MCPL.md) -- Legacy MCP Launchpad CLI reference (not installed; retained for history)
- [docs/PARSIGHT.md](docs/PARSIGHT.md) -- parsight code-memory backend: optional hybrid vault search, code-memory bridge, and 3D vault visualization
- [docs/AGENTCHROME.md](docs/AGENTCHROME.md) -- AgentChrome browser control CLI: installation, capabilities, and integration with the research agent
- [docs/VISUALIZER.md](docs/VISUALIZER.md) -- Vault Visualizer: architecture, graph engine, data model, and configuration
- [docs/DOCUMENTATION_STYLE_GUIDE.md](docs/DOCUMENTATION_STYLE_GUIDE.md) -- Documentation standards for this project
- [SECURITY.md](SECURITY.md) -- Vulnerability disclosure policy and security scope statement
