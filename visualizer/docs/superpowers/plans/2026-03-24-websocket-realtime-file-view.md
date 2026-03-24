# WebSocket Real-Time File View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a WebSocket server that pushes vault filesystem events to the browser so `FileExplorer` updates in real time, open notes auto-refresh when modified externally, and save conflicts are surfaced with a resolution dialog.

**Architecture:** Replace `next dev` with a custom `server.ts` that bootstraps Next.js, attaches a `ws` WebSocket server on `/ws/vault`, and runs a `chokidar` watcher on `~/ClaudeVault/`. File events are broadcast to all connected clients; a shared `EventEmitter` singleton (`vaultBroadcast`) lets route handlers (e.g., graph rebuild) also push events. The frontend hook `useVaultFiles` fetches an initial file list then maintains it via WebSocket events. `FileExplorer` is migrated from `NoteNode[]` (from graph.json) to `VaultFile[]` (real-time). `ReadingPane` gains a `refreshTrigger` prop for auto-refresh and sends `lastModified` timestamps on save so the API can detect conflicts.

**Tech Stack:** Next.js 16.2 (App Router), `ws` (WebSocket server), `chokidar` (filesystem watcher), `tsx` (dev runner), `diff` (already installed, for conflict display).

**Spec:** `docs/superpowers/specs/2026-03-24-websocket-realtime-file-view-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `lib/vaultFile.ts` | **Create** | `VaultFile` interface (shared client+server type) |
| `lib/vaultBroadcast.server.ts` | **Create** | Global EventEmitter singleton for server→WS forwarding |
| `server.ts` | **Create** | Custom Next.js HTTP server + WebSocket + chokidar watcher |
| `tsconfig.server.json` | **Create** | TypeScript config for compiling `server.ts` to `dist/` |
| `app/api/files/route.ts` | **Create** | `GET /api/files` — initial vault scan returning `VaultFile[]` |
| `app/api/note/route.ts` | **Modify** | `POST` — accept `lastModified`, return conflict if mtime newer |
| `app/api/graph/rebuild/route.ts` | **Modify** | Emit `graph:rebuilt` to `vaultBroadcast` after success |
| `package.json` | **Modify** | Add deps; update `dev`/`start`/`build` scripts |
| `lib/useVaultFiles.ts` | **Create** | React hook: fetch initial files + WebSocket live updates |
| `lib/useVisualizerState.ts` | **Modify** | Update `saveNote` signature; add `invalidateNote`; remove `fileTree` memo |
| `components/ConflictDialog.tsx` | **Create** | Show diff of my vs server content; three resolution paths |
| `components/ReadingPane.tsx` | **Modify** | Track `loadedAt`; `refreshTrigger` prop; conflict on save; scroll restore |
| `components/FileExplorer.tsx` | **Modify** | Switch `NoteNode[]` → `VaultFile[]` throughout |
| `components/Toolbar.tsx` | **Modify** | Add `wsStatus` dot indicator |
| `app/page.tsx` | **Modify** | Wire `useVaultFiles`; pass real-time tree + wsStatus; handle events |

---

## Task 1: Add dependencies, VaultFile type, and tsconfig.server.json

**Files:**
- Create: `lib/vaultFile.ts`
- Create: `tsconfig.server.json`
- Modify: `package.json`

- [ ] **Step 1: Install runtime and dev dependencies**

```bash
cd visualizer
bun add ws chokidar
bun add -d tsx @types/ws
```

Expected: both appear in `package.json` dependencies/devDependencies.

- [ ] **Step 2: Create `lib/vaultFile.ts`**

```typescript
// lib/vaultFile.ts
// Shared type used by both the API route and client hook.
// Keep this file import-free so it is safe to use in both environments.

export interface VaultFile {
  /** Filename without extension — e.g. "foo" for "Patterns/foo.md" */
  stem: string
  /** Path relative to vault root — e.g. "Patterns/foo.md" */
  path: string
  /** Frontmatter `type` field, if present */
  noteType?: string
}
```

- [ ] **Step 3: Create `tsconfig.server.json`**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "CommonJS",
    "moduleResolution": "node",
    "outDir": "dist",
    "esModuleInterop": true,
    "strict": true,
    "skipLibCheck": true,
    "resolveJsonModule": true
  },
  "include": ["server.ts", "lib/vaultBroadcast.server.ts"]
}
```

- [ ] **Step 4: Verify TypeScript compiles without errors**

```bash
cd visualizer
bunx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add visualizer/lib/vaultFile.ts visualizer/tsconfig.server.json visualizer/package.json visualizer/bun.lock
git commit -m "chore(visualizer): add ws/chokidar deps, VaultFile type, tsconfig.server.json"
```

---

## Task 2: Create `lib/vaultBroadcast.server.ts`

**Files:**
- Create: `lib/vaultBroadcast.server.ts`

This module exports a global-scoped `EventEmitter` singleton. Using `global` ensures the same instance is shared between `server.ts` and Next.js route handlers regardless of which module bundler loaded them.

- [ ] **Step 1: Create the file**

```typescript
// lib/vaultBroadcast.server.ts
import { EventEmitter } from 'events'

// Use a global key so the same instance is shared across
// server.ts (loaded by tsx/node) and route handlers (loaded by Next.js).
const globalKey = Symbol.for('vaultBroadcast')

declare global {
  // eslint-disable-next-line no-var
  var [Symbol.for('vaultBroadcast')]: EventEmitter | undefined
}

if (!(global as Record<symbol, unknown>)[globalKey]) {
  (global as Record<symbol, unknown>)[globalKey] = new EventEmitter()
}

export const vaultBroadcast = (global as Record<symbol, unknown>)[globalKey] as EventEmitter
```

> **Note:** The Symbol.for key approach can be tricky with TypeScript. Use a string key instead if Symbol indexing causes type errors:

```typescript
// lib/vaultBroadcast.server.ts
import { EventEmitter } from 'events'

declare global {
  // eslint-disable-next-line no-var
  var __vaultBroadcast__: EventEmitter | undefined
}

if (!global.__vaultBroadcast__) {
  global.__vaultBroadcast__ = new EventEmitter()
}

export const vaultBroadcast: EventEmitter = global.__vaultBroadcast__
```

- [ ] **Step 2: Verify no TypeScript errors**

```bash
cd visualizer
bunx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add visualizer/lib/vaultBroadcast.server.ts
git commit -m "feat(visualizer): add vaultBroadcast singleton for server→WS event forwarding"
```

---

## Task 3: Create `GET /api/files` route

**Files:**
- Create: `app/api/files/route.ts`

Walks the vault, reads each `.md` file's frontmatter `type` field, and returns `VaultFile[]`. Uses the same exclusion list as the chokidar watcher in `server.ts`.

- [ ] **Step 1: Create `app/api/files/route.ts`**

```typescript
// app/api/files/route.ts
import { NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import type { VaultFile } from '@/lib/vaultFile'

const EXCLUDED_DIRS = new Set(['.obsidian', 'Templates', '.git', '.trash', 'TagsRoutes'])

function getVaultRoot() {
  return process.env.VAULT_ROOT || path.join(process.env.HOME || '~', 'ClaudeVault')
}

function parseFrontmatterType(content: string): string | undefined {
  const match = content.match(/^---\n[\s\S]*?^type:\s*(.+)$/m)
  return match?.[1]?.trim()
}

function walkVault(dir: string, vaultRoot: string, results: VaultFile[]): void {
  let entries: fs.Dirent[]
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true })
  } catch {
    return
  }

  for (const entry of entries) {
    if (entry.name.startsWith('.')) continue
    const full = path.join(dir, entry.name)

    if (entry.isDirectory()) {
      if (EXCLUDED_DIRS.has(entry.name)) continue
      walkVault(full, vaultRoot, results)
    } else if (entry.isFile() && entry.name.endsWith('.md')) {
      const relPath = path.relative(vaultRoot, full)
      const stem = entry.name.replace(/\.md$/, '')
      let noteType: string | undefined
      try {
        const content = fs.readFileSync(full, 'utf-8')
        noteType = parseFrontmatterType(content)
      } catch { /* skip unreadable */ }
      results.push({ stem, path: relPath, noteType })
    }
  }
}

export async function GET() {
  const vaultRoot = getVaultRoot()
  const files: VaultFile[] = []
  walkVault(vaultRoot, vaultRoot, files)
  return NextResponse.json({ files })
}
```

- [ ] **Step 2: Test the endpoint manually**

> At this point `package.json` still uses `next dev -p 3999` (the script change is Task 6). Start the dev server normally:

Start dev server (`bun run dev`) and open a browser or run:
```bash
curl http://localhost:3999/api/files | head -c 500
```

Expected: JSON with `{ "files": [{ "stem": "...", "path": "...", "noteType": "..." }, ...] }`.

- [ ] **Step 3: Commit**

```bash
git add visualizer/app/api/files/route.ts
git commit -m "feat(visualizer): add GET /api/files for real-time vault scan"
```

---

## Task 4: Add conflict detection to `POST /api/note`

**Files:**
- Modify: `app/api/note/route.ts`

The POST handler now accepts an optional `lastModified` timestamp. If the file's current `mtime` is newer, it returns `{ conflict: true, serverContent }` instead of saving.

- [ ] **Step 1: Update the POST handler in `app/api/note/route.ts`**

Replace lines 50–71 (the entire `POST` function) with:

```typescript
export async function POST(req: NextRequest) {
  const body = await req.json()
  const { stem, content, lastModified } = body as {
    stem?: string
    content?: string
    lastModified?: number
  }
  if (!stem || content === undefined) {
    return NextResponse.json({ error: 'stem and content required' }, { status: 400 })
  }

  const vaultRoot = getVaultRoot()
  const notePath = findNote(vaultRoot, stem)
  if (!notePath) return NextResponse.json({ error: `Note not found: ${stem}` }, { status: 404 })

  if (!guardPath(notePath, vaultRoot)) {
    return NextResponse.json({ error: 'Path traversal rejected' }, { status: 403 })
  }

  // Conflict detection: if caller provided lastModified and the file
  // has been modified since then, return the current content instead of saving.
  if (lastModified !== undefined) {
    try {
      const stat = fs.statSync(notePath)
      if (stat.mtimeMs > lastModified) {
        const serverContent = fs.readFileSync(notePath, 'utf-8')
        return NextResponse.json({ conflict: true, serverContent })
      }
    } catch {
      // If stat fails, proceed with the save
    }
  }

  try {
    fs.writeFileSync(notePath, content, 'utf-8')
    return NextResponse.json({ ok: true })
  } catch {
    return NextResponse.json({ error: 'Failed to write note' }, { status: 500 })
  }
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd visualizer && bunx tsc --noEmit
```

- [ ] **Step 3: Test conflict detection manually**

> Still using `next dev` at this stage (Task 6 switches the server).

```bash
# Save a note, then modify the file directly, then try to save again with the old timestamp
curl -X POST http://localhost:3999/api/note \
  -H "Content-Type: application/json" \
  -d '{"stem":"some-note","content":"new content","lastModified":0}'
```

Expected: `{ "conflict": true, "serverContent": "...current file content..." }`

- [ ] **Step 4: Commit**

```bash
git add visualizer/app/api/note/route.ts
git commit -m "feat(visualizer): add conflict detection to POST /api/note"
```

---

## Task 5: Create `server.ts`

**Files:**
- Create: `server.ts` (in `visualizer/` root)

Custom Next.js HTTP server that also runs the WebSocket server and vault watcher.

- [ ] **Step 1: Create `server.ts`**

```typescript
// server.ts
import { createServer } from 'http'
import { parse } from 'url'
import next from 'next'
import { WebSocketServer, WebSocket } from 'ws'
import chokidar from 'chokidar'
import fs from 'fs'
import path from 'path'
import { vaultBroadcast } from './lib/vaultBroadcast.server'

const dev = process.env.NODE_ENV !== 'production'
const PORT = parseInt(process.env.PORT ?? '3999', 10)

const EXCLUDED_DIRS = new Set(['.obsidian', 'Templates', '.git', '.trash', 'TagsRoutes'])

function getVaultRoot(): string {
  return process.env.VAULT_ROOT || path.join(process.env.HOME || '~', 'ClaudeVault')
}

function parseFrontmatterType(filePath: string): string | undefined {
  try {
    const content = fs.readFileSync(filePath, 'utf-8')
    const match = content.match(/^---\n[\s\S]*?^type:\s*(.+)$/m)
    return match?.[1]?.trim()
  } catch {
    return undefined
  }
}

// ── Bootstrap Next.js ────────────────────────────────────────────────────────

const app = next({ dev, hostname: 'localhost', port: PORT })
const handle = app.getRequestHandler()

app.prepare().then(() => {
  // ── HTTP server ────────────────────────────────────────────────────────────

  const server = createServer((req, res) => {
    const parsedUrl = parse(req.url ?? '/', true)
    handle(req, res, parsedUrl)
  })

  // ── WebSocket server ───────────────────────────────────────────────────────

  const wss = new WebSocketServer({ noServer: true })

  // Only upgrade requests targeting /ws/vault
  server.on('upgrade', (req, socket, head) => {
    if (req.url === '/ws/vault') {
      wss.handleUpgrade(req, socket as import('net').Socket, head, ws => {
        wss.emit('connection', ws)
      })
    } else {
      socket.destroy()
    }
  })

  // Track live clients
  type AliveWS = WebSocket & { isAlive: boolean }
  const clients = new Set<AliveWS>()

  wss.on('connection', ws => {
    const aWs = ws as AliveWS
    aWs.isAlive = true
    clients.add(aWs)

    aWs.on('message', data => {
      try {
        const msg = JSON.parse(data.toString()) as { type: string }
        if (msg.type === 'pong') aWs.isAlive = true
      } catch { /* ignore */ }
    })

    aWs.on('close', () => clients.delete(aWs))
    aWs.on('error', () => { aWs.terminate(); clients.delete(aWs) })
  })

  // Heartbeat — ping every 30 s; drop clients that miss it
  const heartbeat = setInterval(() => {
    for (const ws of clients) {
      if (!ws.isAlive) {
        ws.terminate()
        clients.delete(ws)
        continue
      }
      ws.isAlive = false
      ws.send(JSON.stringify({ type: 'ping' }))
    }
  }, 30_000)

  function broadcast(msg: object): void {
    const payload = JSON.stringify(msg)
    for (const ws of clients) {
      if (ws.readyState === WebSocket.OPEN) ws.send(payload)
    }
  }

  // ── Forward vaultBroadcast events to WebSocket clients ────────────────────
  // (route handlers emit here; e.g. graph rebuild emits 'graph:rebuilt')

  vaultBroadcast.on('graph:rebuilt', () => broadcast({ type: 'graph:rebuilt' }))

  // ── Vault filesystem watcher ───────────────────────────────────────────────

  const vaultRoot = getVaultRoot()

  const watcher = chokidar.watch(vaultRoot, {
    ignored: (filePath: string) => {
      const rel = path.relative(vaultRoot, filePath)
      const parts = rel.split(path.sep)
      // Exclude configured directories
      if (parts.some(p => EXCLUDED_DIRS.has(p))) return true
      // Exclude dot-files
      if (path.basename(filePath).startsWith('.')) return true
      // Only watch .md files (chokidar calls ignored on dirs too; allow dirs)
      const ext = path.extname(filePath)
      if (ext !== '' && ext !== '.md') return true
      return false
    },
    persistent: true,
    ignoreInitial: true,
    // Wait for the file write to finish before emitting
    awaitWriteFinish: { stabilityThreshold: 500, pollInterval: 100 },
  })

  watcher.on('add', (filePath: string) => {
    if (!filePath.endsWith('.md')) return
    const relPath = path.relative(vaultRoot, filePath)
    const stem = path.basename(filePath, '.md')
    const noteType = parseFrontmatterType(filePath)
    broadcast({ type: 'file:created', path: relPath, stem, noteType })
  })

  watcher.on('unlink', (filePath: string) => {
    if (!filePath.endsWith('.md')) return
    broadcast({ type: 'file:deleted', path: path.relative(vaultRoot, filePath) })
  })

  watcher.on('change', (filePath: string) => {
    if (!filePath.endsWith('.md')) return
    broadcast({ type: 'file:modified', path: path.relative(vaultRoot, filePath) })
  })

  watcher.on('error', (err: Error) => console.error('[chokidar]', err))

  // ── Clean up on server close ───────────────────────────────────────────────

  server.on('close', () => {
    clearInterval(heartbeat)
    watcher.close()
  })

  // ── Start listening ────────────────────────────────────────────────────────

  server.listen(PORT, () => {
    console.log(`> Ready on http://localhost:${PORT}`)
  })
}).catch((err: Error) => {
  console.error('Failed to start server:', err)
  process.exit(1)
})
```

- [ ] **Step 2: Commit**

```bash
git add visualizer/server.ts
git commit -m "feat(visualizer): add custom Next.js server with WebSocket + chokidar vault watcher"
```

---

## Task 6: Update `package.json` scripts and verify server starts

**Files:**
- Modify: `package.json`

- [ ] **Step 1: Update scripts in `package.json`**

Replace the `"scripts"` block:

```json
"scripts": {
  "dev": "tsx server.ts",
  "build": "next build && tsc --project tsconfig.server.json",
  "start": "node dist/server.js",
  "kill": "lsof -ti:3999 | xargs kill -9 2>/dev/null || true",
  "lint": "eslint"
},
```

- [ ] **Step 2: Kill any existing dev server and start the new one**

```bash
cd visualizer
bun run kill
bun run dev
```

Expected output:
```
> Ready on http://localhost:3999
```

Open a browser to `http://localhost:3999` — the visualizer should load normally.

- [ ] **Step 3: Verify WebSocket endpoint is reachable**

In the browser console:
```javascript
const ws = new WebSocket('ws://localhost:3999/ws/vault')
ws.onopen = () => console.log('connected')
ws.onmessage = e => console.log('msg:', e.data)
```

Expected: `connected` logged within 1 second.

- [ ] **Step 4: Verify vault events fire**

Create a test `.md` file in `~/ClaudeVault/Patterns/`:
```bash
echo "# Test" > ~/ClaudeVault/Patterns/ws-test-delete-me.md
```

Expected: browser console logs `msg: {"type":"file:created","path":"Patterns/ws-test-delete-me.md","stem":"ws-test-delete-me",...}`.

Delete it to clean up:
```bash
rm ~/ClaudeVault/Patterns/ws-test-delete-me.md
```

- [ ] **Step 5: Commit**

```bash
git add visualizer/package.json
git commit -m "feat(visualizer): switch dev/start scripts to custom server.ts"
```

---

## Task 7: Emit `graph:rebuilt` from `/api/graph/rebuild`

**Files:**
- Modify: `app/api/graph/rebuild/route.ts`

- [ ] **Step 1: Update the rebuild route**

Replace the entire file:

```typescript
// app/api/graph/rebuild/route.ts
import { NextResponse } from 'next/server'
import { spawn } from 'child_process'
import path from 'path'
import { vaultBroadcast } from '@/lib/vaultBroadcast.server'

export async function POST() {
  const repoRoot = path.join(process.cwd(), '..')
  const scriptPath = path.join(repoRoot, 'scripts', 'build_graph.py')

  return new Promise<NextResponse>(resolve => {
    const proc = spawn('uv', ['run', '--no-project', scriptPath], {
      cwd: repoRoot,
      stdio: 'pipe',
    })

    let stderr = ''
    proc.stderr?.on('data', (chunk: Buffer) => { stderr += chunk.toString() })

    proc.on('close', code => {
      if (code === 0) {
        vaultBroadcast.emit('graph:rebuilt')
        resolve(NextResponse.json({ ok: true }))
      } else {
        resolve(NextResponse.json(
          { error: `build_graph.py exited ${code}`, detail: stderr },
          { status: 500 }
        ))
      }
    })

    proc.on('error', err => {
      resolve(NextResponse.json({ error: err.message }, { status: 500 }))
    })
  })
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd visualizer && bunx tsc --noEmit
```

- [ ] **Step 3: Verify graph:rebuilt event fires**

With the dev server running and a WebSocket open in the browser console (from Task 6 Step 3), trigger a graph rebuild:
```bash
curl -X POST http://localhost:3999/api/graph/rebuild
```

Expected: browser console logs `msg: {"type":"graph:rebuilt"}` within a few seconds.

- [ ] **Step 4: Commit**

```bash
git add visualizer/app/api/graph/rebuild/route.ts
git commit -m "feat(visualizer): emit graph:rebuilt event via vaultBroadcast after graph rebuild"
```

---

## Task 8: Update `useVisualizerState.ts`

**Files:**
- Modify: `lib/useVisualizerState.ts`

Three changes:
1. Update `saveNote` signature to accept `lastModified?` and return conflict data
2. Add `invalidateNote(stem)` — clears content cache
3. Remove the `fileTree` memo (it will come from `useVaultFiles`)

> **Important:** After Task 8 removes `fileTree` from the return object, `page.tsx` will have a TypeScript error (`state.fileTree` does not exist) until Task 14 is applied. Tasks 8 through 14 should be treated as one atomic batch — apply all of them before running the dev server again to confirm end-to-end functionality.

- [ ] **Step 1: Update `saveNote` (lines 163–173)**

Replace:
```typescript
  // --- Save note content ---
  const saveNote = useCallback(async (stem: string, content: string): Promise<void> => {
    const res = await fetch('/api/note', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stem, content }),
    })
    const data = await res.json()
    if (data.error) throw new Error(data.error as string)
    contentCache.current.set(stem, content)
  }, [])
```

With:
```typescript
  // --- Save note content ---
  const saveNote = useCallback(async (
    stem: string,
    content: string,
    lastModified?: number,
  ): Promise<{ conflict: true; serverContent: string } | { ok: true }> => {
    const res = await fetch('/api/note', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stem, content, lastModified }),
    })
    const data = await res.json() as { error?: string; conflict?: boolean; serverContent?: string; ok?: boolean }
    if (data.error) throw new Error(data.error)
    if (data.conflict && data.serverContent) {
      return { conflict: true, serverContent: data.serverContent }
    }
    // Only cache on successful save
    contentCache.current.set(stem, content)
    return { ok: true }
  }, [])
```

- [ ] **Step 2: Add `invalidateNote` after `saveNote`**

```typescript
  // --- Invalidate cached note (called when vault watcher detects external edit) ---
  const invalidateNote = useCallback((stem: string): void => {
    contentCache.current.delete(stem)
  }, [])
```

- [ ] **Step 3: Remove the `fileTree` memo (lines 273–292)**

Delete this entire block:
```typescript
  // --- File tree ---
  const fileTree = useMemo(() => {
    if (!graphData) return new Map<string, Map<string, NoteNode[]>>()
    const tree = new Map<string, Map<string, NoteNode[]>>()
    for (const node of graphData.nodes) {
      const parts = node.path.replace(/\.md$/, '').split('/')
      const folder = parts[0] || 'Root'
      const subfolder = parts.length > 2 ? parts[1] : ''
      if (!tree.has(folder)) tree.set(folder, new Map())
      const folderMap = tree.get(folder)!
      if (!folderMap.has(subfolder)) folderMap.set(subfolder, [])
      folderMap.get(subfolder)!.push(node)
    }
    for (const [, subMap] of tree) {
      for (const [, notes] of subMap) {
        notes.sort((a, b) => a.title.localeCompare(b.title))
      }
    }
    return tree
  }, [graphData])
```

- [ ] **Step 4: Update the return object**

In the `return {` block:
- Remove `fileTree,` from the return
- Add `saveNote, invalidateNote,` (saveNote was already there, just ensure the new signature is exported)
- The return line for content should now read:
  ```typescript
  fetchNoteContent, saveNote, deleteNote, createNote, resolveWikilink, nodeMap, invalidateNote,
  ```

- [ ] **Step 5: Verify TypeScript compiles**

```bash
cd visualizer && bunx tsc --noEmit
```

> You will see errors in `page.tsx` and `ReadingPane.tsx` because they still reference the old signatures — that's expected; those are fixed in later tasks.

- [ ] **Step 6: Commit**

```bash
git add visualizer/lib/useVisualizerState.ts
git commit -m "feat(visualizer): update saveNote for conflict detection; add invalidateNote; remove fileTree memo"
```

---

## Task 9: Create `lib/useVaultFiles.ts`

**Files:**
- Create: `lib/useVaultFiles.ts`

Client-side hook that fetches the initial vault file list and maintains it via WebSocket events.

- [ ] **Step 1: Create `lib/useVaultFiles.ts`**

```typescript
// lib/useVaultFiles.ts
'use client'

import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import type { VaultFile } from '@/lib/vaultFile'

export type WsStatus = 'connecting' | 'connected' | 'disconnected'
export type VaultFileTree = Map<string, Map<string, VaultFile[]>>

interface Opts {
  /** Called when a vault .md file is modified externally. Receives the vault-relative path. */
  onNoteModified: (notePath: string) => void
  /** Called when the server emits graph:rebuilt — caller should refetch graph.json. */
  onGraphRebuilt: () => void
}

function buildTree(files: VaultFile[]): VaultFileTree {
  const tree: VaultFileTree = new Map()
  for (const file of files) {
    const parts = file.path.replace(/\.md$/, '').split('/')
    const folder = parts[0] || 'Root'
    const subfolder = parts.length > 2 ? parts[1] : ''
    if (!tree.has(folder)) tree.set(folder, new Map())
    const folderMap = tree.get(folder)!
    if (!folderMap.has(subfolder)) folderMap.set(subfolder, [])
    folderMap.get(subfolder)!.push(file)
  }
  for (const [, subMap] of tree) {
    for (const [, notes] of subMap) {
      notes.sort((a, b) => a.stem.localeCompare(b.stem))
    }
  }
  return tree
}

export function useVaultFiles(opts: Opts): {
  fileTree: VaultFileTree
  wsStatus: WsStatus
  totalFiles: number
} {
  const [files, setFiles] = useState<VaultFile[]>([])
  const [wsStatus, setWsStatus] = useState<WsStatus>('connecting')

  // Stable refs for callbacks — avoids stale closures in WS handler
  const onNoteModifiedRef = useRef(opts.onNoteModified)
  const onGraphRebuiltRef = useRef(opts.onGraphRebuilt)
  useEffect(() => { onNoteModifiedRef.current = opts.onNoteModified }, [opts.onNoteModified])
  useEffect(() => { onGraphRebuiltRef.current = opts.onGraphRebuilt }, [opts.onGraphRebuilt])

  const wsRef = useRef<WebSocket | null>(null)
  const retryDelayRef = useRef(1_000)
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef = useRef(true)

  // Fetch initial file list once on mount
  useEffect(() => {
    fetch('/api/files')
      .then(r => r.json())
      .then((data: { files?: VaultFile[] }) => {
        if (mountedRef.current) setFiles(data.files ?? [])
      })
      .catch(err => console.warn('[useVaultFiles] /api/files failed:', err))
  }, [])

  const connect = useCallback(() => {
    if (!mountedRef.current) return
    setWsStatus('connecting')

    const protocol = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = typeof window !== 'undefined' ? window.location.host : 'localhost:3999'
    const ws = new WebSocket(`${protocol}//${host}/ws/vault`)
    wsRef.current = ws

    ws.onopen = () => {
      if (!mountedRef.current) return
      retryDelayRef.current = 1_000 // reset backoff on successful connect
      setWsStatus('connected')
    }

    ws.onmessage = (event: MessageEvent<string>) => {
      if (!mountedRef.current) return
      try {
        const msg = JSON.parse(event.data) as {
          type: string
          path?: string
          stem?: string
          noteType?: string
        }
        switch (msg.type) {
          case 'ping':
            ws.send(JSON.stringify({ type: 'pong' }))
            break

          case 'file:created':
            if (msg.path && msg.stem) {
              setFiles(prev => {
                // Deduplicate by path
                if (prev.some(f => f.path === msg.path)) return prev
                return [...prev, { stem: msg.stem!, path: msg.path!, noteType: msg.noteType }]
              })
            }
            break

          case 'file:deleted':
            if (msg.path) {
              setFiles(prev => prev.filter(f => f.path !== msg.path))
            }
            break

          case 'file:modified':
            if (msg.path) {
              onNoteModifiedRef.current(msg.path)
            }
            break

          case 'graph:rebuilt':
            onGraphRebuiltRef.current()
            break
        }
      } catch { /* ignore malformed messages */ }
    }

    ws.onclose = () => {
      if (!mountedRef.current) return
      wsRef.current = null
      setWsStatus('disconnected')
      const delay = retryDelayRef.current
      retryDelayRef.current = Math.min(delay * 2, 30_000)
      retryTimerRef.current = setTimeout(connect, delay)
    }

    ws.onerror = () => ws.close()
  }, []) // stable — no deps change after mount

  useEffect(() => {
    mountedRef.current = true
    connect()
    return () => {
      mountedRef.current = false
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  const fileTree = useMemo(() => buildTree(files), [files])

  return { fileTree, wsStatus, totalFiles: files.length }
}
```

> **Note:** No `path` import is needed in this hook — all splits use plain string operations (`file.path.replace(/\.md$/, '').split('/')`), which are safe in the browser.

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd visualizer && bunx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add visualizer/lib/useVaultFiles.ts
git commit -m "feat(visualizer): add useVaultFiles hook for real-time vault file tree via WebSocket"
```

---

## Task 10: Update `FileExplorer.tsx` to use `VaultFile`

**Files:**
- Modify: `components/FileExplorer.tsx`

Replace all references to `NoteNode` with `VaultFile`. The visual structure is identical — only the type and field names change.

- [ ] **Step 1: Replace the import and Props type**

Replace lines 1–18:
```typescript
'use client'

import { useRef, useCallback, useEffect, useState } from 'react'
import { useLocalStorage } from '@/lib/useLocalStorage'
import type { VaultFile } from '@/lib/vaultFile'
import { getNodeColor } from '@/lib/sigma-colors'

interface Props {
  fileTree: Map<string, Map<string, VaultFile[]>>
  activeTab: string | null
  onSelectNote: (stem: string, newTab: boolean) => void
  onOpenHistory: (stem: string) => void
  onDeleteNote?: (stem: string) => void
  width: number
  onWidthChange: (w: number) => void
  collapsed: boolean
  totalNotes: number
}
```

- [ ] **Step 2: Update `NoteItem` (lines 237–281)**

Replace with:
```typescript
function NoteItem({ file, isActive, indent, onSelect, onContextMenu }: {
  file: VaultFile
  isActive: boolean
  indent: number
  onSelect: (stem: string, newTab: boolean) => void
  onContextMenu: (stem: string, x: number, y: number) => void
}) {
  return (
    <div
      onClick={(e) => onSelect(file.stem, e.metaKey || e.ctrlKey)}
      onContextMenu={(e) => {
        e.preventDefault()
        onContextMenu(file.stem, e.clientX, e.clientY)
      }}
      style={{
        padding: `4px 10px 4px ${indent}px`,
        fontSize: 10, cursor: 'pointer',
        color: isActive ? '#e8e8f0' : '#8892a8',
        background: isActive ? 'rgba(99,102,241,0.12)' : 'transparent',
        borderLeft: isActive ? '2px solid #6366f1' : '2px solid transparent',
        borderRadius: isActive ? '0 3px 3px 0' : 0,
        display: 'flex', alignItems: 'center', gap: 5,
        overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis',
        transition: 'background 0.1s, color 0.1s',
      }}
      onMouseEnter={e => {
        if (!isActive) {
          e.currentTarget.style.background = 'rgba(255,255,255,0.04)'
          e.currentTarget.style.color = '#c0c8d8'
        }
      }}
      onMouseLeave={e => {
        if (!isActive) {
          e.currentTarget.style.background = 'transparent'
          e.currentTarget.style.color = '#8892a8'
        }
      }}
    >
      <span style={{ color: getNodeColor(file.noteType ?? 'pattern'), fontSize: 7, flexShrink: 0 }}>●</span>
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {file.stem}.md
      </span>
    </div>
  )
}
```

- [ ] **Step 3: Update all `NoteItem` usages in the render body**

In the JSX, there are two `NoteItem` usages (inside subfolder and root-level loops). Update each from:
```tsx
<NoteItem
  key={note.id}
  note={note}
  isActive={note.id === activeTab}
  indent={38}   // or 24
  onSelect={onSelectNote}
  onContextMenu={(stem, x, y) => setContextMenu({ stem, x, y })}
/>
```
To:
```tsx
<NoteItem
  key={file.path}
  file={file}
  isActive={file.stem === activeTab}
  indent={38}   // or 24
  onSelect={onSelectNote}
  onContextMenu={(stem, x, y) => setContextMenu({ stem, x, y })}
/>
```

(Also rename the loop variable `note` → `file` in the `.map()` calls.)

- [ ] **Step 4: Verify TypeScript compiles**

```bash
cd visualizer && bunx tsc --noEmit
```

> Expect errors in `page.tsx` (still passes `NoteNode[]` to `fileTree`) — fixed in Task 14.

- [ ] **Step 5: Commit**

```bash
git add visualizer/components/FileExplorer.tsx
git commit -m "feat(visualizer): migrate FileExplorer from NoteNode to VaultFile for real-time tree"
```

---

## Task 11: Create `ConflictDialog.tsx`

**Files:**
- Create: `components/ConflictDialog.tsx`

Shows when the server detects that a file was externally modified since the user started editing. Three options: take server version, keep user version (force-save), or manually merge.

- [ ] **Step 1: Create `components/ConflictDialog.tsx`**

```typescript
// components/ConflictDialog.tsx
'use client'

import { useState, useMemo } from 'react'
import { createPatch } from 'diff'
import { DiffViewer } from './DiffViewer'
import type { DiffMode } from './DiffViewer'
import { parseDiff } from '@/lib/parseDiff'

interface Props {
  stem: string
  myContent: string
  serverContent: string
  /** Called with the resolved content the user wants to save (force-save, no lastModified). */
  onResolve: (resolved: string) => void
  onCancel: () => void
}

export function ConflictDialog({ stem, myContent, serverContent, onResolve, onCancel }: Props) {
  const [view, setView] = useState<'diff' | 'merge'>('diff')
  const [diffMode, setDiffMode] = useState<DiffMode>('split')
  const [mergeText, setMergeText] = useState(myContent)

  // Compute a unified diff: server content → my content
  const unifiedDiff = createPatch(
    `${stem}.md`,
    serverContent,
    myContent,
    'Server version',
    'Your version',
  )

  // Parse into DiffHunk[] for DiffViewer
  const hunks = useMemo(() => parseDiff(unifiedDiff), [unifiedDiff])

  const overlayStyle: React.CSSProperties = {
    position: 'fixed', inset: 0,
    background: 'rgba(0,0,0,0.7)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    zIndex: 2000,
    fontFamily: "'JetBrains Mono', monospace",
  }

  const dialogStyle: React.CSSProperties = {
    background: '#0a0e1a',
    border: '1px solid #ef4444',
    borderRadius: 8,
    width: '90vw', maxWidth: 900,
    maxHeight: '80vh',
    display: 'flex', flexDirection: 'column',
    boxShadow: '0 8px 32px rgba(0,0,0,0.8)',
    overflow: 'hidden',
  }

  return (
    <div style={overlayStyle} onClick={onCancel}>
      <div style={dialogStyle} onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div style={{
          padding: '12px 16px',
          borderBottom: '1px solid #1e293b',
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <span style={{ color: '#ef4444', fontSize: 14 }}>⚠</span>
          <span style={{ color: '#e8e8f0', fontSize: 12, fontWeight: 600 }}>
            Edit conflict — {stem}.md was modified externally
          </span>
          <span style={{ flex: 1 }} />
          {/* Diff mode toggle (only visible when view='diff') */}
          {view === 'diff' && (
            <>
              <button
                onClick={() => setDiffMode('split')}
                style={{
                  background: diffMode === 'split' ? 'rgba(99,102,241,0.15)' : 'none',
                  border: '1px solid #334155', borderRadius: 4,
                  color: diffMode === 'split' ? '#818cf8' : '#6b7a99',
                  cursor: 'pointer', padding: '2px 8px', fontSize: 10,
                }}
              >
                Split
              </button>
              <button
                onClick={() => setDiffMode('unified')}
                style={{
                  background: diffMode === 'unified' ? 'rgba(99,102,241,0.15)' : 'none',
                  border: '1px solid #334155', borderRadius: 4,
                  color: diffMode === 'unified' ? '#818cf8' : '#6b7a99',
                  cursor: 'pointer', padding: '2px 8px', fontSize: 10,
                }}
              >
                Unified
              </button>
            </>
          )}
          {/* View toggle */}
          <button
            onClick={() => setView('diff')}
            style={{
              background: view === 'diff' ? 'rgba(239,68,68,0.15)' : 'none',
              border: '1px solid #334155', borderRadius: 4,
              color: view === 'diff' ? '#ef4444' : '#6b7a99',
              cursor: 'pointer', padding: '2px 8px', fontSize: 10,
            }}
          >
            Diff
          </button>
          <button
            onClick={() => { setView('merge'); setMergeText(myContent) }}
            style={{
              background: view === 'merge' ? 'rgba(239,68,68,0.15)' : 'none',
              border: '1px solid #334155', borderRadius: 4,
              color: view === 'merge' ? '#ef4444' : '#6b7a99',
              cursor: 'pointer', padding: '2px 8px', fontSize: 10,
            }}
          >
            Merge
          </button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflow: 'auto' }}>
          {view === 'diff' ? (
            <DiffViewer hunks={hunks} mode={diffMode} filename={`${stem}.md`} />
          ) : (
            <div style={{ padding: 12, display: 'flex', flexDirection: 'column', height: '100%' }}>
              <div style={{ color: '#9ca3af', fontSize: 10, marginBottom: 6 }}>
                Edit the merged result below. Your content is pre-loaded; incorporate changes from the diff view.
              </div>
              <textarea
                value={mergeText}
                onChange={e => setMergeText(e.target.value)}
                spellCheck={false}
                style={{
                  flex: 1, resize: 'none',
                  background: '#0a0f1e', border: '1px solid #334155', borderRadius: 4,
                  color: '#e8e8f0', fontFamily: "'JetBrains Mono', monospace",
                  fontSize: 12, lineHeight: 1.7, padding: 12, outline: 'none',
                  minHeight: 300,
                }}
              />
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{
          padding: '10px 16px',
          borderTop: '1px solid #1e293b',
          display: 'flex', gap: 8, justifyContent: 'flex-end',
        }}>
          <button
            onClick={onCancel}
            style={{
              background: 'none', border: '1px solid #334155', borderRadius: 5,
              color: '#6b7a99', cursor: 'pointer', padding: '4px 14px', fontSize: 11,
            }}
          >
            Cancel
          </button>
          <button
            onClick={() => onResolve(serverContent)}
            style={{
              background: 'rgba(99,102,241,0.15)', border: '1px solid rgba(99,102,241,0.3)',
              borderRadius: 5, color: '#818cf8', cursor: 'pointer',
              padding: '4px 14px', fontSize: 11,
            }}
          >
            Take theirs
          </button>
          <button
            onClick={() => onResolve(myContent)}
            style={{
              background: 'rgba(245,158,11,0.15)', border: '1px solid rgba(245,158,11,0.3)',
              borderRadius: 5, color: '#f59e0b', cursor: 'pointer',
              padding: '4px 14px', fontSize: 11,
            }}
          >
            Keep mine
          </button>
          {view === 'merge' && (
            <button
              onClick={() => onResolve(mergeText)}
              style={{
                background: 'rgba(0,255,200,0.15)', border: '1px solid rgba(0,255,200,0.3)',
                borderRadius: 5, color: '#00FFC8', cursor: 'pointer',
                padding: '4px 14px', fontSize: 11, fontWeight: 600,
              }}
            >
              Confirm Merge
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
```

> `DiffViewer` accepts `hunks: DiffHunk[]`, `mode: DiffMode`, `filename: string`, `truncated?: boolean`. Always call `parseDiff(unifiedDiffString)` first to convert the raw string from `createPatch` into `DiffHunk[]`.

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd visualizer && bunx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add visualizer/components/ConflictDialog.tsx
git commit -m "feat(visualizer): add ConflictDialog for external edit conflict resolution"
```

---

## Task 12: Update `ReadingPane.tsx`

**Files:**
- Modify: `components/ReadingPane.tsx`

Four changes:
1. Track `loadedAt` — timestamp when note content was last fetched
2. `refreshTrigger` prop — when incremented and not in edit mode, refetch content + restore scroll
3. On save, pass `lastModified: loadedAt` and handle `{ conflict, serverContent }` response
4. Update `onSave` prop type to match new signature

- [ ] **Step 1: Add new imports and update Props interface**

Add to imports:
```typescript
import { ConflictDialog } from './ConflictDialog'
```

The existing import on line 3 is:
```typescript
import { useState, useEffect, useCallback, useTransition } from 'react'
```
Replace it with (add `useRef`):
```typescript
import { useState, useEffect, useCallback, useTransition, useRef } from 'react'
```

Replace the Props interface (lines 13–21):
```typescript
interface Props {
  node: NoteNode | null
  fetchContent: (stem: string) => Promise<string>
  onNavigate: (stem: string, newTab: boolean) => void
  onSave: (stem: string, content: string, lastModified: number) => Promise<{ conflict: true; serverContent: string } | { ok: true }>
  onDelete: (stem: string) => Promise<void>
  onOpenHistory: (stem: string) => void
  nodes: NoteNode[]
  /** Increment to trigger a content refresh in read mode (preserves scroll). */
  refreshTrigger?: number
}
```

Update function signature:
```typescript
export function ReadingPane({ node, fetchContent, onNavigate, onSave, onDelete, onOpenHistory, nodes, refreshTrigger = 0 }: Props) {
```

- [ ] **Step 2: Add state and refs for loadedAt, scroll, conflict, and external-modification indicator**

After the existing `useState` declarations (after line 35), add:
```typescript
  const [loadedAt, setLoadedAt] = useState(0)
  const [conflictData, setConflictData] = useState<{ serverContent: string } | null>(null)
  const [externallyModified, setExternallyModified] = useState(false)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const savedScrollRef = useRef(0)
```

- [ ] **Step 3: Update the content-fetch effect to set `loadedAt` (lines 37–50)**

Replace:
```typescript
  useEffect(() => {
    if (!node) return
    setIsEditing(false)
    let cancelled = false
    startTransition(async () => {
      try {
        const c = await fetchContent(node.id)
        if (!cancelled) { setContent(c); setError(null) }
      } catch (e) {
        if (!cancelled) setError((e as Error).message)
      }
    })
    return () => { cancelled = true }
  }, [node, fetchContent])
```

With:
```typescript
  useEffect(() => {
    if (!node) return
    setIsEditing(false)
    let cancelled = false
    startTransition(async () => {
      try {
        const c = await fetchContent(node.id)
        if (!cancelled) {
          setContent(c)
          setLoadedAt(Date.now())
          setError(null)
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message)
      }
    })
    return () => { cancelled = true }
  }, [node, fetchContent])
```

- [ ] **Step 4: Add refreshTrigger effect (after the existing node effect)**

```typescript
  // Respond to external vault modifications (triggered by vault watcher via WebSocket).
  // Cache was already invalidated by the caller; fetchContent will go to the network.
  useEffect(() => {
    if (refreshTrigger === 0 || !node) return
    if (isEditing) {
      // In edit mode: don't overwrite the user's draft — show a warning instead.
      setExternallyModified(true)
      return
    }
    // In read mode: auto-refresh and restore scroll.
    savedScrollRef.current = scrollContainerRef.current?.scrollTop ?? 0
    let cancelled = false
    startTransition(async () => {
      try {
        const c = await fetchContent(node.id)
        if (!cancelled) {
          setContent(c)
          setLoadedAt(Date.now())
          setError(null)
        }
      } catch { /* ignore refresh errors */ }
    })
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshTrigger])

  // Restore scroll position after content refreshes in read mode
  useEffect(() => {
    if (savedScrollRef.current > 0 && scrollContainerRef.current) {
      scrollContainerRef.current.scrollTop = savedScrollRef.current
      savedScrollRef.current = 0
    }
  }, [content])
```

- [ ] **Step 4b: Add the "modified externally" warning banner to the edit toolbar**

In the edit mode JSX (the toolbar row with "Editing: ..." and Save/Cancel buttons), add this banner immediately after the title span and before the `saveError` span:

```tsx
{externallyModified && (
  <span style={{ color: '#f59e0b', fontFamily: "'JetBrains Mono', monospace", fontSize: 10, flexShrink: 0 }}>
    ⚠ modified externally — save to see conflict
  </span>
)}
```

Also clear `externallyModified` at the start of `handleCancelEdit`:
```typescript
const handleCancelEdit = useCallback(() => {
  setIsEditing(false)
  setPreviewMode(false)
  setSaveError(null)
  setExternallyModified(false)  // add this line
}, [])
```

And clear it on successful save (in `handleSave`, after `setIsEditing(false)`):
```typescript
setExternallyModified(false)
```

- [ ] **Step 5: Update `handleSave` to pass `lastModified` and handle conflicts**

Replace lines 79–94:
```typescript
  const handleSave = useCallback(async () => {
    if (!node || !editFields) return
    setIsSaving(true)
    setSaveError(null)
    try {
      const fullContent = serializeFrontmatter(editFields, editBody)
      const result = await onSave(node.id, fullContent, loadedAt)
      if ('conflict' in result && result.conflict) {
        setConflictData({ serverContent: result.serverContent })
        return
      }
      setContent(fullContent)
      setLoadedAt(Date.now())
      setIsEditing(false)
      setPreviewMode(false)
    } catch (e) {
      setSaveError((e as Error).message)
    } finally {
      setIsSaving(false)
    }
  }, [node, editFields, editBody, onSave, loadedAt])
```

- [ ] **Step 6: Add ConflictDialog render and `handleConflictResolve` callback**

After the `handleConfirmDelete` callback, add:
```typescript
  const handleConflictResolve = useCallback(async (resolved: string) => {
    if (!node) return
    setConflictData(null)
    setIsSaving(true)
    setSaveError(null)
    try {
      // Force-save: no lastModified so the server always writes
      await onSave(node.id, resolved, 0)
      setContent(resolved)
      setLoadedAt(Date.now())
      setIsEditing(false)
      setPreviewMode(false)
    } catch (e) {
      setSaveError((e as Error).message)
    } finally {
      setIsSaving(false)
    }
  }, [node, onSave])
```

> **Note on force-save:** Passing `lastModified: 0` means the server will always find `mtime > 0` (true), which would cause another conflict! Instead, pass `undefined` by omitting the parameter. Fix the route to treat `lastModified === undefined` as "no check". Since the API accepts `lastModified?: number`, simply do not send it:

Actually, re-examine the conflict check in Task 4:
```typescript
if (lastModified !== undefined) { ... }
```
Passing `lastModified: 0` would trigger the check. Passing `undefined` skips it. Update `handleConflictResolve` to pass `undefined` (not `0`):
```typescript
// Force-save by omitting lastModified — the server skips the conflict check
await onSave(node.id, resolved, undefined as unknown as number)
```

Better — change the `onSave` signature to `lastModified?: number`:
```typescript
// In Props interface:
onSave: (stem: string, content: string, lastModified?: number) => Promise<...>
// In handleConflictResolve:
await onSave(node.id, resolved)  // omit lastModified entirely
```

Update the Props interface accordingly.

- [ ] **Step 7: Add scroll container ref to the read-mode outer div**

Find the read-mode return (line ~290):
```tsx
return (
  <div style={{ flex: 1, overflow: 'auto', padding: '32px 48px', fontFamily: "'Syne', sans-serif" }}>
```

Add the ref:
```tsx
return (
  <div ref={scrollContainerRef} style={{ flex: 1, overflow: 'auto', padding: '32px 48px', fontFamily: "'Syne', sans-serif" }}>
```

- [ ] **Step 8: Render ConflictDialog when conflict is set**

Inside the read-mode return, before the closing `</div>`, add:
```tsx
      {conflictData && node && (
        <ConflictDialog
          stem={node.id}
          myContent={serializeFrontmatter(editFields ?? {}, editBody)}
          serverContent={conflictData.serverContent}
          onResolve={handleConflictResolve}
          onCancel={() => setConflictData(null)}
        />
      )}
```

> **Note:** `conflictData` is only set while `isEditing` is true, so `editFields` and `editBody` are populated. Still guard defensively with `editFields ?? {}`.

Wait — at the moment `conflictData` is set, we're in the edit branch (isEditing = true), not the read-mode branch. The ConflictDialog should be rendered in the edit view. Find the Save button in the edit view (around line 204) and add the ConflictDialog after the closing edit view div:

```tsx
      {conflictData && node && (
        <ConflictDialog
          stem={node.id}
          myContent={serializeFrontmatter(editFields!, editBody)}
          serverContent={conflictData.serverContent}
          onResolve={handleConflictResolve}
          onCancel={() => setConflictData(null)}
        />
      )}
    </div>  // close the edit view outer div
  )
}
```

- [ ] **Step 9: Verify TypeScript compiles**

```bash
cd visualizer && bunx tsc --noEmit
```

- [ ] **Step 10: Commit**

```bash
git add visualizer/components/ReadingPane.tsx
git commit -m "feat(visualizer): add refreshTrigger, loadedAt tracking, and conflict resolution to ReadingPane"
```

---

## Task 13: Add `wsStatus` indicator to `Toolbar.tsx`

**Files:**
- Modify: `components/Toolbar.tsx`

- [ ] **Step 1: Add `wsStatus` to Props and add the indicator**

Replace the Props interface (lines 9–21):
```typescript
import type { WsStatus } from '@/lib/useVaultFiles'

interface Props {
  onToggleSidebar: () => void
  tabs: string[]
  activeTab: string | null
  nodeMap: Map<string, NoteNode>
  onSwitchTab: (stem: string) => void
  onCloseTab: (stem: string) => void
  nodes: NoteNode[]
  onSearchSelect: (stem: string, newTab: boolean) => void
  viewMode: 'read' | 'graph'
  onViewModeChange: (mode: 'read' | 'graph') => void
  onNewNote: () => void
  wsStatus: WsStatus
}
```

Update function signature to destructure `wsStatus`:
```typescript
export function Toolbar({
  onToggleSidebar,
  tabs, activeTab, nodeMap, onSwitchTab, onCloseTab,
  nodes, onSearchSelect,
  viewMode, onViewModeChange,
  onNewNote,
  wsStatus,
}: Props) {
```

- [ ] **Step 2: Add the status dot and CSS animation**

Add the pulsing animation to `globals.css` (or inline via a `<style>` tag):

In `app/globals.css`, add:
```css
@keyframes vault-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
```

In the Toolbar JSX, inside the right-side controls `<div>` (before the `+` button), add:

```tsx
        {/* WebSocket status indicator */}
        <div
          title={
            wsStatus === 'connected' ? 'Vault sync connected' :
            wsStatus === 'connecting' ? 'Vault sync reconnecting…' :
            'Vault sync disconnected'
          }
          style={{
            width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
            background:
              wsStatus === 'connected' ? '#10b981' :
              wsStatus === 'connecting' ? '#f59e0b' : '#ef4444',
            animation: wsStatus === 'connecting' ? 'vault-pulse 1.2s ease-in-out infinite' : 'none',
          }}
        />
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd visualizer && bunx tsc --noEmit
```

- [ ] **Step 4: Commit**

```bash
git add visualizer/components/Toolbar.tsx visualizer/app/globals.css
git commit -m "feat(visualizer): add WebSocket status dot to Toolbar"
```

---

## Task 14: Wire everything in `page.tsx`

**Files:**
- Modify: `app/page.tsx`

This is the final wiring task. Changes:
1. Import and call `useVaultFiles`
2. Add `noteRefreshTrigger` state and `handleNoteModified` callback
3. `onGraphRebuilt` triggers the existing graph.json refetch
4. Pass real-time `fileTree` (from `useVaultFiles`) instead of `state.fileTree` to `FileExplorer`
5. Pass `totalNotes` from `useVaultFiles.totalFiles`
6. Pass `wsStatus` to `Toolbar`
7. Pass `refreshTrigger` to `ReadingPane`
8. Update `onSave` prop to match new `saveNote` signature

- [ ] **Step 1: Add imports**

```typescript
import { useVaultFiles } from '@/lib/useVaultFiles'
import path from 'path'
```

- [ ] **Step 2: Add `noteRefreshTrigger` state**

After the existing state declarations (after `pendingOpenStem`):
```typescript
const [noteRefreshTrigger, setNoteRefreshTrigger] = useState(0)
```

- [ ] **Step 3: Add `handleNoteModified` and `handleGraphRebuilt` callbacks**

```typescript
const handleNoteModified = useCallback((notePath: string) => {
  // Extract stem from vault-relative path (e.g. "Patterns/foo.md" → "foo")
  const stem = notePath.replace(/\.md$/, '').split('/').pop() ?? notePath
  // Note: invalidateNote is keyed by stem. If two notes share the same stem
  // (e.g. Patterns/foo.md and Debugging/foo.md), both cache entries are cleared.
  // This is a known limitation of the stem-keyed content cache (pre-existing).
  state.invalidateNote(stem)
  if (stem === state.activeTab) {
    setNoteRefreshTrigger(n => n + 1)
  }
}, [state])

const handleGraphRebuilt = useCallback(async () => {
  try {
    const fresh = await fetch(`/graph.json?t=${Date.now()}`).then(r => r.json()) as GraphData
    setGraphData(fresh)
  } catch (err) {
    console.warn('[page] graph.json refetch failed:', err)
  }
}, [])
```

- [ ] **Step 4: Call `useVaultFiles`**

After `const state = useVisualizerState(graphData)`, add:
```typescript
const { fileTree, wsStatus, totalFiles } = useVaultFiles({
  onNoteModified: handleNoteModified,
  onGraphRebuilt: handleGraphRebuilt,
})
```

- [ ] **Step 5: Update `FileExplorer` props**

In the JSX, find the `<FileExplorer ... />` usage (around line 177) and change:
- `fileTree={state.fileTree}` → `fileTree={fileTree}`
- `totalNotes={graphData.nodes.length}` → `totalNotes={totalFiles}`

- [ ] **Step 6: Update `Toolbar` props**

Add `wsStatus={wsStatus}` to the `<Toolbar ... />` usage.

- [ ] **Step 7: Update `ReadingPane` props**

Add `refreshTrigger={noteRefreshTrigger}` to the `<ReadingPane ... />` usage.

Update `onSave`:
```typescript
onSave={state.saveNote}
```
This already works because `state.saveNote` now matches the new signature.

- [ ] **Step 8: Remove `path` import if it causes browser issues**

The `path` module import in step 1 is only needed for `handleNoteModified`. If bundler warnings appear, replace:
```typescript
const stem = notePath.replace(/\.md$/, '').split('/').pop() ?? notePath
```
(no `path` import needed — the split is just string manipulation).

- [ ] **Step 9: Verify TypeScript compiles with zero errors**

```bash
cd visualizer && bunx tsc --noEmit
```

Expected: zero type errors.

- [ ] **Step 10: End-to-end smoke test**

Start the dev server:
```bash
bun run dev
```

Open `http://localhost:3999`. Verify:
1. **File tree loads** — `FileExplorer` shows all vault notes (may take 1–2 s for initial fetch)
2. **Status dot is green** in toolbar
3. **Create a note** in Obsidian or with `echo "# Test" > ~/ClaudeVault/Patterns/live-test.md`
   - The note should appear in `FileExplorer` within ~1 s
4. **Modify an open note externally** — open a note in read mode, then edit it in Obsidian
   - The reading pane should refresh with the new content
5. **Conflict detection** — open a note in edit mode, modify the file externally via CLI, then click Save in the visualizer
   - `ConflictDialog` should appear with the diff
6. **Clean up** — `rm ~/ClaudeVault/Patterns/live-test.md`

- [ ] **Step 11: Commit**

```bash
git add visualizer/app/page.tsx
git commit -m "feat(visualizer): wire useVaultFiles for real-time file tree, refresh, and graph reload"
```

---

## Task 15: Final verification and changelog

- [ ] **Step 1: Run lint**

```bash
cd visualizer && bun run lint
```

Fix any reported issues.

- [ ] **Step 2: Verify production build compiles**

```bash
cd visualizer && bun run build
```

Expected: Next.js build succeeds; `tsc --project tsconfig.server.json` compiles `server.ts` to `dist/server.js`.

- [ ] **Step 3: Commit any lint fixes**

```bash
git add -A
git commit -m "chore(visualizer): fix lint issues after WebSocket real-time feature"
```

- [ ] **Step 4: Update VISUALIZER.md or CHANGELOG.md if it exists**

Check `visualizer/VISUALIZER.md` or repo-root changelog for update patterns and add a brief entry for this feature.

---

## Known Limitations

- Notes created outside the visualizer appear in `FileExplorer` in real time, but **cannot be opened** until `graph.json` is rebuilt (because `openNote` requires the note to exist in `nodeMap`). This is a pre-existing constraint and out of scope for this feature.
- The "Merge" option in `ConflictDialog` is a manual merge — it pre-populates the user's content and shows a diff side panel. It does not perform automated 3-way merge (we lack the base/original version for that).
- WebSocket reconnection is silent; the status dot turns amber during reconnection. No toast or modal interrupts the user.
