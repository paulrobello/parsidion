# Parsidion -- Persistent Memory for Claude Code via a Markdown Vault


---

Claude Code's built-in memory is... fine. It stores flat key-value style notes in `~/.claude/memory/` and forgets context between sessions pretty aggressively. If you've ever had Claude re-solve a problem it already solved last week, or watched it ignore a pattern you established three sessions ago, you know the pain.

I built **Parsidion** to fix that. It replaces Claude Code's auto memory with a full markdown knowledge vault (`~/ClaudeVault/`) that persists across every session, every project, and is searchable by both Claude and you.

## What it actually does

**Session lifecycle hooks** wire into Claude Code's event system:

- **SessionStart** -- loads relevant vault notes as context before you even type anything. It picks notes based on your current project, tags, and (optionally) an AI-powered selection pass via haiku.
- **SessionEnd** -- detects learnable content from the session transcript and queues it for summarization. Runs detached so it doesn't block Claude from exiting.
- **PreCompact / PostCompact** -- snapshots your working state (current task, touched files, git branch, uncommitted changes) before context compaction and restores it after, so Claude doesn't lose track of what it was doing.
- **SubagentStop** -- captures subagent transcripts too, so knowledge from research agents and explorers gets harvested automatically.

**AI summarizer** (`summarize_sessions.py`) processes the queue and generates structured vault notes. It uses the Claude Agent SDK with up to 5 parallel sessions, does hierarchical summarization for long transcripts, and checks for near-duplicates via embedding similarity before writing anything. Notes get automatic bidirectional wikilinks.

**Vault search** has four modes: semantic (fastembed + sqlite-vec), metadata filtering (tag/folder/type/project/recency), full-text grep, and an interactive curses TUI. All available as a global `vault-search` CLI command.

**Vault explorer agent** -- a haiku-powered read-only subagent that isolates vault lookups from your main session context. The main session dispatches it automatically when it needs to check for prior art or debugging solutions.

**Research agent** -- searches the vault first, then does web research, and saves findings back to the vault with proper frontmatter and backlinks.

## The vault itself

Plain markdown with YAML frontmatter. Organized into folders: `Debugging/`, `Patterns/`, `Frameworks/`, `Languages/`, `Tools/`, `Research/`, `Projects/`, `Daily/`. Every note has tags, a confidence level, sources, and related wikilinks. No orphan notes allowed -- everything links to something.

You can open it in Obsidian for graph visualization and browsing, but Obsidian is entirely optional. The system works without it.

## Vault visualizer (web UI)

The project includes a full web-based vault viewer built with Next.js, Sigma.js (WebGL), and Graphology. It runs locally on port 3999 and has two modes:

**Read mode** -- a clean, centered reading pane with GitHub Flavored Markdown rendering, clickable wikilinks (cmd+click opens in new tab), tag pills, metadata header (type, date, confidence), and a related-notes section. You can toggle into inline editing to modify note content and frontmatter directly in the browser -- the frontmatter editor gives you structured fields for type, tags, project, related links with tag autocomplete pulled from the graph.

**Graph mode** -- a force-directed graph powered by ForceAtlas2 that visualizes the entire vault's link structure. By default it shows a 2-hop neighborhood around the active note (using explicit wikilinks for BFS traversal, plus semantic edges within the neighborhood). Toggle to full-vault view to see everything at once. Nodes are color-coded by note type and sized by incoming link count. Click a node to open the note; drag to pin it and reheat the physics simulation.

The graph has a HUD panel with real controls:
- Semantic similarity threshold slider (filter edges by embedding cosine score)
- Graph source toggle (semantic vs. wiki edges, with overlay mode for both)
- Node type filter checkboxes (show/hide patterns, debugging, research, etc.)
- Full physics controls -- scaling ratio, gravity, cooling rate, edge weight influence, start temperature, stop threshold, pause/resume
- Live stats: visible node/edge counts, average similarity score
- A temperature bar showing simulation energy so you can see when the layout has converged

Other features: multi-tab browsing (up to 20 tabs, state persisted to localStorage), a collapsible file explorer sidebar with nested folder tree and note counts, unified search via **Cmd+K** with three modes (title fuzzy match, `#tag` exact match, `/path` folder prefix), keyboard shortcuts for everything, and note creation/deletion from the UI.

The graph data is pre-computed from vault embeddings (`make graph`), so navigation is instant -- no live embedding queries during browsing. You can schedule nightly rebuilds alongside the summarizer.

```bash
make visualizer-setup   # install deps (first time)
make graph              # build graph.json from embeddings
cd visualizer && bun dev  # start on port 3999
```

## CLI tools

The installer can set up several global commands:

- `vault-search` -- search notes (semantic, metadata, grep, or interactive TUI)
- `vault-new` -- scaffold notes from templates
- `vault-stats` -- analytics dashboard (growth, stale notes, tag cloud, graph metrics, pending queue, hook event log, weekly/monthly rollups)
- `vault-review` -- interactive TUI to approve/reject pending sessions before AI summarization
- `vault-export` -- export to HTML static site, zip, or PDF
- `vault-merge` -- AI-assisted deduplication with backlink updates
- `vault-doctor` -- structural health checks and auto-repair

## MCP server for Claude Desktop

There's an optional MCP server (`parsidion-mcp/`) that exposes vault operations to Claude Desktop and other MCP clients -- search, read, write, context loading, index rebuild, and doctor.

## Install

```bash
git clone https://github.com/paulrobello/parsidion.git
cd parsidion
uv run install.py
```

Restart Claude Code. That's it. You now have persistent memory.

Optional nightly auto-summarization:

```bash
uv run install.py --schedule-summarizer
```

## Design decisions worth mentioning

- **stdlib-only hooks** -- all hook scripts and the installer use Python stdlib exclusively. No pip install, no third-party deps. The summarizer is the one exception (it needs `claude-agent-sdk`), and it uses PEP 723 inline deps so `uv run` handles it.
- **No Obsidian lock-in** -- the vault is plain markdown. Obsidian is a nice viewer but the system doesn't depend on it.
- **Git integration** -- if `~/ClaudeVault/.git` exists, scripts auto-commit after writes. Optional but useful for history.
- **Config via YAML** -- all hook and summarizer behavior is configurable in `~/ClaudeVault/config.yaml`. Sensible defaults, override what you want.

---

I've been using this daily for a few weeks now across multiple projects. The difference is noticeable -- Claude stops re-solving problems it already solved, picks up patterns from other projects, and the vault becomes genuinely useful as a searchable knowledge base over time.

**GitHub:** https://github.com/paulrobello/parsidion
**License:** MIT | **Python 3.13+** | **Requires:** Claude Code + uv

Happy to answer questions or take feedback. Issues and PRs welcome on GitHub.
