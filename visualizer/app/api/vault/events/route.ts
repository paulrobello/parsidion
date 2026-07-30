// app/api/vault/events/route.ts
// SSE replacement for the retired `ws`-based /ws/vault endpoint (server.ts).
// Streams file:created / file:deleted / file:modified / graph:rebuilt events
// for a single vault to the client via EventSource.

import { NextRequest, NextResponse } from 'next/server'
import { watch, type FSWatcher } from 'chokidar'
import fs from 'fs/promises'
import path from 'path'
import { vaultBroadcast } from '@/lib/vaultBroadcast.server'
import { resolveVault, VaultConfigError } from '@/lib/vaultResolver'
import { withApi } from '@/lib/apiAuth'

const EXCLUDED_DIRS = new Set(['.obsidian', 'Templates', '.git', '.trash', 'TagsRoutes'])

// QA-012: read the file via fs/promises so the event loop is not blocked
// inside the chokidar handler. The caller is `watcher.on('add', ...)` which
// does not await the result, so the broadcast fires asynchronously — fine
// for an SSE channel where ordering across files was never guaranteed.
async function parseFrontmatterType(filePath: string): Promise<string | undefined> {
  try {
    const content = await fs.readFile(filePath, 'utf-8')
    const match = content.match(/^---\n[\s\S]*?^type:\s*(.+)$/m)
    return match?.[1]?.trim()
  } catch {
    return undefined
  }
}

type Subscriber = (data: object) => void

interface WatcherEntry {
  watcher: FSWatcher
  refCount: number
  subscribers: Set<Subscriber>
}

// Module-level watcher registry — persists across requests within the same
// `next dev` / `next start` process. Reference-counted per active SSE
// connection so a watcher shared by multiple clients is only closed once its
// last subscriber disconnects (mirrors acquireWatcher/releaseWatcher from
// the retired server.ts custom server).
const watchers = new Map<string, WatcherEntry>()

function broadcastToVault(vaultPath: string, msg: object): void {
  const entry = watchers.get(vaultPath)
  if (!entry) return
  for (const send of entry.subscribers) send(msg)
}

function createVaultWatcher(vaultRoot: string): FSWatcher {
  const watcher = watch(vaultRoot, {
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

  watcher.on('add', async (filePath: string) => {
    if (!filePath.endsWith('.md')) return
    const relPath = path.relative(vaultRoot, filePath)
    const stem = path.basename(filePath, '.md')
    const noteType = await parseFrontmatterType(filePath)
    broadcastToVault(vaultRoot, { type: 'file:created', path: relPath, stem, noteType })
  })

  watcher.on('unlink', (filePath: string) => {
    if (!filePath.endsWith('.md')) return
    broadcastToVault(vaultRoot, { type: 'file:deleted', path: path.relative(vaultRoot, filePath) })
  })

  watcher.on('change', (filePath: string) => {
    if (!filePath.endsWith('.md')) return
    broadcastToVault(vaultRoot, { type: 'file:modified', path: path.relative(vaultRoot, filePath) })
  })

  watcher.on('error', (err: unknown) => console.error('[vault/events]', vaultRoot, err))

  return watcher
}

function acquireWatcher(vaultPath: string, subscriber: Subscriber): void {
  let entry = watchers.get(vaultPath)
  if (!entry) {
    entry = { watcher: createVaultWatcher(vaultPath), refCount: 0, subscribers: new Set() }
    watchers.set(vaultPath, entry)
  }
  entry.refCount += 1
  entry.subscribers.add(subscriber)
}

function releaseWatcher(vaultPath: string, subscriber: Subscriber): void {
  const entry = watchers.get(vaultPath)
  if (!entry) return
  entry.subscribers.delete(subscriber)
  entry.refCount -= 1
  if (entry.refCount <= 0) {
    entry.watcher.close()
    watchers.delete(vaultPath)
  }
}

export const GET = withApi(async (req: NextRequest) => {
  // SEC-102: bearer token first — EventSource cannot set custom headers from
  // page script, but a non-browser client on the same network can open this
  // stream without one. The same-origin check below handles cross-site pages.
  // ARC-014: guards now run via `withApi` so this handler only contains its
  // own business logic.

  const vaultParam = req.nextUrl.searchParams.get('vault')

  // SEC-009: validate the vault before opening the stream.
  let vaultPath: string
  try {
    vaultPath = resolveVault(vaultParam)
  } catch (err) {
    if (err instanceof VaultConfigError) {
      console.warn('[vault/events] Rejected forbidden vault path:', vaultParam, '-', err.message)
      return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
    }
    console.error('[vault/events] Vault resolution error:', err)
    return NextResponse.json({ error: 'Failed to resolve vault' }, { status: 500 })
  }

  const encoder = new TextEncoder()

  // ARC-039: hoisted so the cancel() method can run the same cleanup the
  // abort handler does. `teardownRef` is assigned inside start() once the
  // closure variables it needs (controller, listeners, timers) exist.
  let teardownRef: (() => void) | null = null

  const stream = new ReadableStream({
    start(controller) {
      let closed = false
      // ARC-039: keepalive heartbeat. EventSource clients time out after a
      // short idle window on some proxies (notably nginx's
      // proxy_read_timeout, default 60s); a periodic `: keepalive\n\n`
      // comment frame resets that timer without triggering a client-side
      // message handler. The interval is well under the 60s proxy default.
      const KEEPALIVE_INTERVAL_MS = 15_000

      function send(data: object): void {
        if (closed) return
        try {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(data)}\n\n`))
        } catch {
          // Controller already closed (client disconnected mid-write); ignore.
        }
      }

      // ARC-015 step 5: forward the broadcast payload (which carries the
      // rebuilt vault path) so SSE subscribers scoped to a different vault
      // can no-op instead of refetching. The legacy payload (no argument)
      // is treated as "unknown vault, refetch" for backward compatibility.
      const onRebuilt = (payload?: { vault?: string }) => {
        if (payload && payload.vault && payload.vault !== vaultPath) return
        send({ type: 'graph:rebuilt', vault: payload?.vault ?? vaultPath })
      }

      // Defined later (after keepaliveTimer); the teardown closure captures
      // it by reference, so the `let` binding must exist first.
      let keepaliveTimer: ReturnType<typeof setInterval> | null = null

      function teardown(): void {
        if (closed) return
        closed = true
        if (keepaliveTimer !== null) clearInterval(keepaliveTimer)
        vaultBroadcast.off('graph:rebuilt', onRebuilt)
        releaseWatcher(vaultPath, send)
        try {
          controller.close()
        } catch {
          // Already closed.
        }
      }
      teardownRef = teardown

      acquireWatcher(vaultPath, send)
      vaultBroadcast.on('graph:rebuilt', onRebuilt)

      keepaliveTimer = setInterval(() => {
        if (closed) return
        try {
          // SSE comment frame — clients ignore it but the bytes reset
          // intermediary idle timers.
          controller.enqueue(encoder.encode(': keepalive\n\n'))
        } catch {
          // Controller closed mid-write; tear down so we don't keep firing.
          teardown()
        }
      }, KEEPALIVE_INTERVAL_MS)

      // Client disconnect (fetch/EventSource drop) — the canonical teardown
      // path. The cancel() hook below is the safety net for streams torn
      // down through other routes.
      req.signal.addEventListener('abort', teardown)
    },
    cancel() {
      // ARC-039: stream cancelled directly (e.g. by the runtime pulling the
      // body away). Release the chokidar watcher and the EventEmitter
      // listener so they don't leak across reconnects. Without this, a
      // teardown path that bypassed req.signal would accumulate watchers.
      teardownRef?.()
    },
  })

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  })
})
