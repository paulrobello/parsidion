// server.ts
import { createServer } from 'http'
import { parse } from 'url'
import next from 'next'
import { WebSocketServer, WebSocket } from 'ws'
import type { FSWatcher } from 'chokidar' with { "resolution-mode": "import" }
import fs from 'fs'
import path from 'path'
import { vaultBroadcast } from './lib/vaultBroadcast.server'
import { resolveVault, VaultConfigError } from './lib/vaultResolver'

const dev = process.env.NODE_ENV !== 'production'
const PORT = parseInt(process.env.PORT ?? '3999', 10)

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

// ── Bootstrap Next.js ────────────────────────────────────────────────────────

const app = next({ dev, hostname: 'localhost', port: PORT })
const handle = app.getRequestHandler()

app.prepare().then(async () => {
  const { watch } = await import('chokidar')
  // ── HTTP server ────────────────────────────────────────────────────────────

  const server = createServer((req, res) => {
    const parsedUrl = parse(req.url ?? '/', true)
    handle(req, res, parsedUrl)
  })

  // ── WebSocket server ───────────────────────────────────────────────────────

  const wss = new WebSocketServer({ noServer: true })

  // Track live clients with their vault subscription
  type AliveWS = WebSocket & { isAlive: boolean; vaultPath?: string }
  const clients = new Set<AliveWS>()

  // Track watchers by vault path (lazy creation)
  const watchers = new Map<string, FSWatcher>()

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

    watcher.on('error', (err: unknown) => console.error('[chokidar]', vaultRoot, err))

    return watcher
  }

  function getOrCreateWatcher(vaultPath: string): FSWatcher {
    let watcher = watchers.get(vaultPath)
    if (!watcher) {
      watcher = createVaultWatcher(vaultPath)
      watchers.set(vaultPath, watcher)
    }
    return watcher
  }

  function broadcastToVault(vaultPath: string, msg: object): void {
    const payload = JSON.stringify(msg)
    for (const ws of clients) {
      if (ws.vaultPath === vaultPath && ws.readyState === WebSocket.OPEN) {
        ws.send(payload)
      }
    }
  }

  function broadcast(msg: object): void {
    const payload = JSON.stringify(msg)
    for (const ws of clients) {
      if (ws.readyState === WebSocket.OPEN) ws.send(payload)
    }
  }

  // Only upgrade requests targeting /ws/vault; forward everything else to Next.js (HMR etc.)
  const nextUpgradeHandler = app.getUpgradeHandler()
  server.on('upgrade', (req, socket, head) => {
    const parsedUrl = parse(req.url ?? '/', true)
    if (parsedUrl.pathname === '/ws/vault') {
      // Extract vault from query params
      const vaultParam = parsedUrl.query.vault as string | undefined

      // SEC-001 / SEC-009: Validate the vault path before establishing the
      // WebSocket connection.  resolveVault() throws VaultConfigError for
      // forbidden paths.  Destroy the socket with a 400 response so the client
      // gets a clear rejection rather than an unhandled server crash.
      let vaultPath: string
      try {
        vaultPath = resolveVault(vaultParam)
      } catch (err) {
        if (err instanceof VaultConfigError) {
          console.warn('[ws/vault] Rejected forbidden vault path:', vaultParam, '-', err.message)
        } else {
          console.error('[ws/vault] Vault resolution error:', err)
        }
        // Send an HTTP 400 response and destroy the socket — this is the
        // standard pattern for rejecting a WebSocket upgrade before accepting it.
        socket.write('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n')
        socket.destroy()
        return
      }

      wss.handleUpgrade(req, socket as import('net').Socket, head, ws => {
        const aWs = ws as AliveWS
        aWs.vaultPath = vaultPath
        wss.emit('connection', ws)
      })
    } else {
      nextUpgradeHandler(req, socket, head)
    }
  })

  wss.on('connection', ws => {
    const aWs = ws as AliveWS
    aWs.isAlive = true
    clients.add(aWs)

    // Ensure watcher exists for this vault
    if (aWs.vaultPath) {
      getOrCreateWatcher(aWs.vaultPath)
    }

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

  // ── Forward vaultBroadcast events to WebSocket clients ────────────────────

  vaultBroadcast.on('graph:rebuilt', () => broadcast({ type: 'graph:rebuilt' }))

  // ── Initial vault watcher (default vault, for backward compatibility) ─────

  const defaultVaultPath = resolveVault()
  getOrCreateWatcher(defaultVaultPath)

  // ── Clean up on server close ───────────────────────────────────────────────

  server.on('close', () => {
    clearInterval(heartbeat)
    for (const watcher of watchers.values()) {
      watcher.close()
    }
  })

  // ── Start listening ────────────────────────────────────────────────────────

  server.listen(PORT, () => {
    console.info(`> Ready on http://localhost:${PORT}`)
  })
}).catch((err: Error) => {
  console.error('Failed to start server:', err)
  process.exit(1)
})
