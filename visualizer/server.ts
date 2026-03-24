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

  watcher.on('error', (err: unknown) => console.error('[chokidar]', err))

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
