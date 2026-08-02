// app/api/vault/events/route.test.ts
// ENH-012: end-to-end integration test for the SSE vault-events route.
//
// Exercises a REAL chokidar watcher on a REAL temp vault: opens the stream,
// writes a new .md file into the vault, and asserts a `file:created` data
// frame arrives within the broadcast window. Then aborts the connection and
// asserts the watcher is torn down (no further frames after a second file
// write) — proving `req.signal` abort → `teardown` → `releaseWatcher`.
//
// The route handler's module-level watcher registry persists across requests
// within a single bun process; each test uses a fresh unique vault path (via
// mkdtemp inside setupTmpHome) so no two tests share a watcher entry, and
// the resolver cache is invalidated by the HOME change between tests.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import { NextRequest } from 'next/server'
import * as fs from 'fs'
import * as path from 'path'

import { GET } from './route'
import {
  setupTmpHome,
  readNextSSEData,
  type RouteTestSetup,
} from '@/lib/__fixtures__/routeTestHelper'

describe('ENH-012 / vault/events SSE route — end-to-end watcher', () => {
  let setup: RouteTestSetup

  beforeEach(() => {
    setup = setupTmpHome()
    // Seed a pre-existing note so the vault is non-empty (and so chokidar's
    // ignoreInitial scan has something to skip — confirming it does).
    fs.mkdirSync(path.join(setup.defaultVault, 'Patterns'), { recursive: true })
    fs.writeFileSync(path.join(setup.defaultVault, 'Patterns', 'seed.md'), '# seed\n')
  })
  afterEach(() => setup.restore())

  it('delivers a file:created frame when a new .md file is written to the vault', async () => {
    const ac = new AbortController()
    const req = new NextRequest('http://localhost:3999/api/vault/events', {
      signal: ac.signal,
    })
    const res = await GET(req)
    expect(res.status).toBe(200)
    expect(res.headers.get('Content-Type')).toBe('text/event-stream')

    const reader = res.body!.getReader()

    // Let chokidar finish its initial scan before adding a new file.
    // (ignoreInitial: true means existing files don't fire `add`, but the
    // watcher needs to be armed before the write to reliably catch it.)
    await new Promise(r => setTimeout(r, 400))

    const newFile = path.join(setup.defaultVault, 'Patterns', 'new-note.md')
    fs.writeFileSync(newFile, '# freshly written\n')

    // awaitWriteFinish.stabilityThreshold=500 + pollInterval=100 → the chokidar
    // `add` event fires ~600-700ms after the write settles. 5s window keeps
    // the test deterministic on slow CI without being wall-clock-exact.
    const frame = await readNextSSEData(reader, 5000)
    expect(frame).not.toBeNull()
    expect((frame as { type?: string }).type).toBe('file:created')
    expect((frame as { path?: string }).path).toBe('Patterns/new-note.md')

    ac.abort()
    // Allow watcher.close() (un-awaited inside the route) to settle so the
    // chokidar handle does not leak into the next test.
    await new Promise(r => setTimeout(r, 200))
    try {
      reader.releaseLock()
    } catch {
      /* already released by the close */
    }
  }, 15_000)

  it('stops broadcasting after the connection is aborted (watcher released)', async () => {
    const ac = new AbortController()
    const req = new NextRequest('http://localhost:3999/api/vault/events', {
      signal: ac.signal,
    })
    const res = await GET(req)
    expect(res.status).toBe(200)
    const reader = res.body!.getReader()

    // Write one file to confirm the stream is live, then abort.
    await new Promise(r => setTimeout(r, 400))
    fs.writeFileSync(
      path.join(setup.defaultVault, 'Patterns', 'before-abort.md'),
      '# before\n',
    )
    const first = await readNextSSEData(reader, 5000)
    expect(first).not.toBeNull()
    expect((first as { type?: string }).type).toBe('file:created')

    ac.abort()
    // Let teardown + watcher.close() settle before provoking a second write.
    await new Promise(r => setTimeout(r, 300))

    // Write a second file. If the watcher was released, no data frame arrives:
    // either the stream is closed (read → done) or it stays open with no
    // subscriber (broadcastToVault no-ops). Either way readNextSSEData returns
    // null. If teardown did NOT run (abort listener missing), the watcher
    // would still be live and this second write would produce a frame.
    fs.writeFileSync(
      path.join(setup.defaultVault, 'Patterns', 'after-abort.md'),
      '# after\n',
    )
    const leaked = await readNextSSEData(reader, 1800)
    expect(leaked).toBeNull()
  }, 20_000)
})
