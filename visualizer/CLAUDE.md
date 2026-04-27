@AGENTS.md

# Parsidion Visualizer

Next.js + sigma.js knowledge graph visualizer for Parsidion vaults. Renders vault notes as an interactive force-directed graph, with node sizing by recency/connections and live search/filter. The default vault directory is `~/ParsidionVault` for new installs, with automatic fallback to an existing legacy `~/ClaudeVault`; the UI is runtime-agnostic: Claude, Codex, Gemini, pi, and manually-created notes all flow through the same vault files and graph snapshot.

## Dev Workflow

```bash
# Install dependencies (first time)
bun install                  # or: make visualizer-setup (from repo root)

# Start dev server (port 3999)
bun dev                      # or: make visualizer (from repo root)

# Build for production
bun run build                # or: make build-visualizer (from repo root)

# Kill dev server
bun run kill                 # kills port 3999
```

## Data Source

The visualizer reads **`{vault}/graph.json`** — a pre-built snapshot of each Parsidion vault's knowledge graph, stored inside the vault directory itself (not in `public/`). Each vault has its own `graph.json`. Rebuild it after vault changes:

```bash
# From the repo root (recommended — also rebuilds the index):
uv run --no-project ~/.claude/skills/parsidion/scripts/update_index.py --rebuild-graph

# Include Daily notes in the graph:
uv run --no-project ~/.claude/skills/parsidion/scripts/update_index.py --rebuild-graph --graph-include-daily
```

`graph.json` is gitignored in the vault (rebuilt locally, not synced across machines).

## Runtime/Agent Support

No UI changes are required for new runtime hooks as long as they write normal Parsidion notes and pending summaries. Runtime-specific provenance filters are intentionally not present yet; add them only after notes have stable metadata such as `runtime: claude`, `runtime: codex`, or `runtime: gemini`.

## Architecture

- **`server.ts`** — custom Express dev server (`tsx server.ts`) that wraps Next.js and serves `public/` with a file-watcher WebSocket for live graph reload when `graph.json` changes
- **`app/`** — Next.js App Router pages
- **`components/`** — React components; sigma.js canvas rendering lives here
- **`lib/`** — graph layout utilities (graphology + ForceAtlas2)
- **`{vault}/graph.json`** — vault graph snapshot (nodes = notes, edges = wikilinks)

## Key Dependencies

| Package | Purpose |
|---|---|
| `sigma` | WebGL graph rendering |
| `graphology` | Graph data structure |
| `graphology-layout-forceatlas2` | Force-directed layout |
| `next` | React framework (App Router) |
| `chokidar` | File-watching for live reload |
