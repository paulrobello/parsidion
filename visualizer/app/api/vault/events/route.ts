// app/api/vault/events/route.ts
// SSE replacement for the retired `ws`-based /ws/vault endpoint (server.ts).
// Streams file:created / file:deleted / file:modified / graph:rebuilt events
// for a single vault to the client via EventSource.

import { NextRequest, NextResponse } from 'next/server'
import { watch, type FSWatcher } from 'chokidar'
import fs from 'fs'
import path from 'path'
import { vaultBroadcast } from '@/lib/vaultBroadcast.server'
import { resolveVault, VaultConfigError } from '@/lib/vaultResolver'
import { requireSameOrigin } from '@/lib/apiAuth'

const EXCLUDED_DIRS = new Set(['.obsidian', 'Templates', '.git', '.trash', 'TagsRoutes'])

function parseFrontmatterType(filePath: string): string | undefined {
  try {
    const content = fs.readFileSync(filePath, 'utf-8')
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

  watcher.on('add', (filePath: string) => {
    if (!filePath.endsWith('.md')) return
    const relPath = path.relative(vaultRoot, filePath)
    const stem = path.basename(filePath, '.md')
    const noteType = parseFrontmatterType(filePath)
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

export async function GET(req: NextRequest) {
  // SSE equivalent of the WS Origin check in server.ts: same-origin
  // EventSource requests send Sec-Fetch-Site: same-origin, so this doesn't
  // affect the app; a cross-site page's EventSource is rejected.
  const originError = requireSameOrigin(req)
  if (originError) return originError

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

  const stream = new ReadableStream({
    start(controller) {
      let closed = false

      function send(data: object): void {
        if (closed) return
        try {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(data)}\n\n`))
        } catch {
          // Controller already closed (client disconnected mid-write); ignore.
        }
      }

      acquireWatcher(vaultPath, send)

      const onRebuilt = () => send({ type: 'graph:rebuilt' })
      vaultBroadcast.on('graph:rebuilt', onRebuilt)

      req.signal.addEventListener('abort', () => {
        if (closed) return
        closed = true
        vaultBroadcast.off('graph:rebuilt', onRebuilt)
        releaseWatcher(vaultPath, send)
        try {
          controller.close()
        } catch {
          // Already closed.
        }
      })
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
}
