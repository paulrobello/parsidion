# WebSocket Real-Time File View Design

**Date:** 2026-03-24
**Status:** Approved
**Scope:** `visualizer/` — Next.js app inside parsidion-cc

---

## Problem Statement

The visualizer's `FileExplorer` sidebar is built from `graph.json`, a static file regenerated on demand. New notes and folders created outside the visualizer (e.g., via hooks, Obsidian, or CLI tools) do not appear until the page is refreshed. Additionally, notes edited externally while open in the visualizer can silently diverge from what the user sees.

---

## Goals

1. `FileExplorer` reflects the vault filesystem in real time — new notes and folders appear as they are created, deletions disappear immediately.
2. Notes open in **read mode** auto-refresh when modified externally, preserving scroll position.
3. Notes open in **edit mode** detect external modifications on save and offer conflict resolution (take theirs / keep mine / merge).
4. Graph view receives a lightweight notification when `graph.json` is rebuilt and refetches it — no graph data travels over the WebSocket.
5. A connection status indicator (toolbar dot) gives the user live feedback on sync health.

---

## Architecture Overview

```
┌─────────────────── server.ts ───────────────────┐
│  HTTP server (port 8030)                         │
│  ├── Next.js request handler (all HTTP routes)  │
│  ├── WebSocketServer (upgrade on /ws/vault)     │
│  └── chokidar watcher → broadcasts events       │
└──────────────────────────────────────────────────┘
         │ WebSocket /ws/vault          │ HTTP /api/*
         ▼                              ▼
┌─── Browser ──────────────────────────────────────┐
│  useVaultFiles hook                               │
│  ├── fetch /api/files → initial file tree        │
│  ├── WebSocket → live events                     │
│  └── drives FileExplorer (real-time)             │
│                                                   │
│  GraphCanvas                                      │
│  └── graph.json → refetch on graph:rebuilt event │
└───────────────────────────────────────────────────┘
```

A shared `vaultBroadcast` `EventEmitter` (module-level singleton in `lib/vaultBroadcast.server.ts`) connects route handlers to the WebSocket layer. Both run in the same Node.js process.

**Data source split:**
- `FileExplorer` → real-time `VaultFile[]` from `useVaultFiles`
- `GraphCanvas` → continues using `NoteNode[]` from `graph.json`

---

## Backend

### `server.ts` (new, `visualizer/` root)

Replaces `next dev` / `next start` as the entry point.

- Creates a Node.js `http.Server`; attaches Next.js as the request handler
- Attaches `ws.WebSocketServer({ noServer: true })` — upgrades only requests to `/ws/vault`
- Initialises a single `chokidar` watcher on `VAULT_ROOT` with ignored paths:
  `.obsidian`, `Templates`, `.git`, `.trash`, `TagsRoutes`, `embeddings.db`, `*.log`, `pending_summaries.jsonl`, `hook_events.log`
- On chokidar `add` / `unlink` / `change`: reads frontmatter via stdlib regex, broadcasts the appropriate JSON event to all live WebSocket clients
- Heartbeat: sends `{ type: "ping" }` every 30 s; drops clients that fail to `pong` within 10 s

### `lib/vaultBroadcast.server.ts` (new)

```ts
import { EventEmitter } from 'events'
export const vaultBroadcast = new EventEmitter()
```

- `server.ts` subscribes → forwards events to WebSocket clients
- `/api/graph/rebuild/route.ts` imports and emits `'graph:rebuilt'` after subprocess success

### WebSocket Message Protocol

All payloads are JSON. Data flows server → client only (except pong).

| Direction | Message |
|-----------|---------|
| S → C | `{ type: "file:created",  path: "Patterns/foo.md", stem: "foo", noteType: "pattern" }` |
| S → C | `{ type: "file:deleted",  path: "Patterns/foo.md", stem: "foo" }` |
| S → C | `{ type: "file:modified", path: "Patterns/foo.md", stem: "foo" }` |
| S → C | `{ type: "graph:rebuilt" }` |
| S → C | `{ type: "ping" }` |
| C → S | `{ type: "pong" }` |

### `package.json` script changes

| Script | Before | After |
|--------|--------|-------|
| `dev` | `next dev -p 8030` | `tsx server.ts` |
| `start` | `next start -p 8030` | `node dist/server.js` |
| `build` | `next build` | `next build && tsc server.ts --outDir dist` |

New dependencies: `ws`, `chokidar` (runtime); `tsx`, `@types/ws` (dev).

---

## API Changes

### New: `GET /api/files`

- Walks `VAULT_ROOT` with the same exclusion list as chokidar
- Reads frontmatter (stem, path, type, title, tags) from each `.md` file via stdlib regex
- Returns `{ files: VaultFile[] }`

```ts
interface VaultFile {
  stem: string
  path: string       // relative to vault root, e.g. "Patterns/foo.md"
  noteType?: string  // frontmatter `type` field
  title?: string
  tags?: string[]
}
```

### Modified: `POST /api/note` (save)

Accepts optional `lastModified: number` (unix ms) in the request body.

- If `lastModified` is provided **and** `file mtime > lastModified` → do not save, return:
  ```json
  { "conflict": true, "serverContent": "...full file text..." }
  ```
- Otherwise: save and return `{ ok: true }` (unchanged behaviour)
- Force-save (conflict resolved by user): omit `lastModified` from the request body

### Modified: `POST /api/graph/rebuild`

After the `build_graph.py` subprocess completes successfully:
```ts
vaultBroadcast.emit('graph:rebuilt')
```

---

## Frontend

### New: `lib/useVaultFiles.ts`

```ts
function useVaultFiles(opts: {
  onNoteModified: (stem: string) => void
  onGraphRebuilt: () => void
}): {
  fileTree: Map<string, Map<string, VaultFile[]>>
  wsStatus: 'connecting' | 'connected' | 'disconnected'
}
```

- On mount: fetches `/api/files`, builds the `fileTree` map
- Opens WebSocket to `ws[s]://${window.location.host}/ws/vault`
- Event handlers:
  - `file:created` → insert `VaultFile`; create folder/subfolder entries if new
  - `file:deleted` → remove from tree; prune empty folders
  - `file:modified` → call `opts.onNoteModified(stem)`
  - `graph:rebuilt` → call `opts.onGraphRebuilt()`
  - `ping` → send `pong`
- Reconnection: exponential backoff, 1 s → 2 s → … → 30 s max, unlimited retries
- Cleans up WebSocket and chokidar subscription on unmount

### `page.tsx` changes

- Calls `useVaultFiles({ onNoteModified, onGraphRebuilt })`
- Passes `fileTree` (from `useVaultFiles`) to `FileExplorer`
- `onGraphRebuilt`: refetches `/graph.json?t=${Date.now()}` and updates graph state
- Passes `wsStatus` to `Toolbar`
- `onNoteModified(stem)`: if the stem is open in a tab → increment `refreshTrigger` for read-mode tabs; set `pendingConflictCheck: true` for edit-mode tabs

### `FileExplorer.tsx` changes

- `fileTree` prop type changes from `Map<string, Map<string, NoteNode[]>>` to `Map<string, Map<string, VaultFile[]>>`
- Color dot uses `vaultFile.noteType` instead of `node.type`
- All other visual behaviour unchanged

### `useVisualizerState.ts` changes

- Remove the `fileTree` memo that derived the tree from `graphData.nodes`
- Add `pendingConflictCheck` flag per tab in tab state
- `onNoteModified(stem)` implementation lives here

### Read-Mode Auto-Refresh (scroll preservation)

- `ReadingPane` receives `refreshTrigger: number` prop
- `useEffect` on `refreshTrigger`:
  1. Record `container.scrollTop`
  2. Fetch fresh content from `/api/note?stem=...`
  3. After content state updates, restore `scrollTop` in a follow-up effect

### `ReadingPane.tsx` save changes

- Tracks `loadedAt: number` (set when note content is fetched)
- On save: POST `{ stem, content, lastModified: loadedAt }`
- If response is `{ conflict: true, serverContent }`: open `ConflictDialog`

### New: `ConflictDialog.tsx`

Props: `myContent: string`, `serverContent: string`, `onResolve(resolved: string)`, `onCancel()`

Three resolution paths:
- **Take theirs** → `onResolve(serverContent)`
- **Keep mine** → `onResolve(myContent)` (force-save — POST without `lastModified`)
- **Merge** → opens unified editor pre-populated with a diff3 3-way merge; user edits; Confirm calls `onResolve(editedResult)`

Reuses `DiffViewer.tsx` for the side-by-side comparison panels.

### `Toolbar.tsx` changes

- Accepts `wsStatus` prop
- Adds a pulsing dot (right side, before existing controls):
  - 🟢 `connected` — solid green
  - 🟡 `connecting` — pulsing amber (CSS keyframe)
  - 🔴 `disconnected` — solid red
- Tooltip: "Vault sync connected / reconnecting… / disconnected"

---

## Files Created / Modified

| File | Change |
|------|--------|
| `server.ts` | **New** — custom Next.js + WebSocket server |
| `lib/vaultBroadcast.server.ts` | **New** — shared EventEmitter |
| `lib/useVaultFiles.ts` | **New** — real-time file tree hook |
| `components/ConflictDialog.tsx` | **New** — conflict resolution dialog |
| `app/api/files/route.ts` | **New** — initial vault scan endpoint |
| `app/api/note/route.ts` | **Modified** — conflict detection on POST |
| `app/api/graph/rebuild/route.ts` | **Modified** — emit `graph:rebuilt` after success |
| `app/page.tsx` | **Modified** — wire `useVaultFiles`, pass props |
| `lib/useVisualizerState.ts` | **Modified** — remove graph-derived fileTree, add conflict/refresh state |
| `components/FileExplorer.tsx` | **Modified** — accept `VaultFile[]` instead of `NoteNode[]` |
| `components/ReadingPane.tsx` | **Modified** — refreshTrigger, conflict detection on save |
| `components/Toolbar.tsx` | **Modified** — wsStatus dot |
| `package.json` | **Modified** — new deps, updated scripts |

---

## Error Handling

- WebSocket connection failure: retry silently with backoff; show red dot after 3 failed attempts
- `/api/files` fetch failure: fall back to empty file tree with a console warning; FileExplorer shows empty state
- Conflict response with no `serverContent`: show error toast, do not open ConflictDialog
- chokidar error events: log to server console, do not crash the process
- Graph rebuild failure: existing error handling unchanged; no `graph:rebuilt` event is emitted on failure

---

## Out of Scope

- Collaborative multi-user editing (multiple browser tabs seeing each other's edits in real time)
- Binary file watching (only `.md` files are tracked)
- Bidirectional WebSocket commands (client does not instruct the server to create/delete files)
