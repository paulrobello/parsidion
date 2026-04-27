# Parsidion Visualizer

Interactive web UI for browsing a Parsidion vault as both a file tree and a knowledge graph. It renders vault notes from `graph.json`, supports live file updates over WebSocket, and lets you read, edit, diff, and create markdown notes without leaving the browser.

The default vault path is still `~/ClaudeVault`, but the UI is runtime-agnostic: notes captured from Claude Code, Codex CLI, Gemini CLI, pi, or manual editing all appear through the same vault files and graph snapshot.

## Getting Started

Install dependencies from the visualizer directory:

```bash
bun install
```

Start the development server on port 3999:

```bash
bun dev
```

Or from the repository root:

```bash
make visualizer
```

Open <http://localhost:3999> in your browser.

## Data Source

The visualizer reads each vault's local graph snapshot:

```text
{vault}/graph.json
```

Rebuild the graph after vault changes:

```bash
uv run --no-project ~/.claude/skills/parsidion/scripts/update_index.py --rebuild-graph
```

Include Daily notes when desired:

```bash
uv run --no-project ~/.claude/skills/parsidion/scripts/update_index.py --rebuild-graph --graph-include-daily
```

`graph.json` is gitignored in the vault and rebuilt locally.

## Runtime Support

No runtime-specific UI setup is required. Claude, Codex, Gemini, and pi integrations all write to the same Parsidion vault pipeline; the visualizer displays the resulting notes once the index/graph are rebuilt.

Agent/runtime provenance filters are not implemented yet. If future notes include stable source metadata such as `runtime: codex` or `runtime: gemini`, the UI can add filters without changing the core vault browser.

## Architecture

- `server.ts` — custom Next.js dev server with WebSocket vault file watching.
- `app/` — Next.js App Router pages and metadata.
- `components/` — React UI components, including the sigma.js graph canvas.
- `lib/` — graph loading, vault resolution, file APIs, and local UI state helpers.

## Commands

```bash
bun dev        # Start the dev server
bun run build  # Build Next.js and the custom server
bun run lint   # Run ESLint
bun run kill   # Kill the dev server on port 3999
```
