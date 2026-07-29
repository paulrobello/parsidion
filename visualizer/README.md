# Parsidion Visualizer

Interactive web UI for browsing a Parsidion vault as both a file tree and a knowledge graph. It renders vault notes from `graph.json`, supports live file updates over Server-Sent Events (SSE), and lets you read, edit, diff, and create markdown notes without leaving the browser.

The default vault path is `~/ParsidionVault` for new installs, with automatic fallback to an existing legacy `~/ClaudeVault`; the UI is runtime-agnostic: notes captured from Claude Code, Codex CLI, Gemini CLI, pi, or manual editing all appear through the same vault files and graph snapshot.

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

The app runs on plain `next dev` / `next start` — there is no custom Node server. Live vault updates are delivered by the Server-Sent Events route handler at `app/api/vault/events/route.ts` (a reference-counted per-vault `chokidar` watcher, same-origin `Sec-Fetch-Site` guard, optional `VISUALIZER_TOKEN` bearer auth). The previous `ws`-based `server.ts` was retired in 0.12.0.

- `app/` — Next.js App Router pages and API routes (including the SSE events stream).
- `components/` — React UI components, including the sigma.js graph canvas.
- `lib/` — graph loading, vault resolution, file APIs, local UI state helpers, and `apiAuth.ts` (guard helpers).

## Commands

```bash
bun dev        # Start the dev server (next dev, port 3999)
bun run build  # Build Next.js for production (next build)
bun run lint   # Run ESLint
bun run kill   # Kill the dev server on port 3999
```
