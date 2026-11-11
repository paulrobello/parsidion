// app/api/vault/events/route.test.ts
// ENH-012: end-to-end integration test for the SSE vault-events route.
//
// Two tests, two determinism strategies (load-flaky SSE watcher fix):
//
// 1. Broadcast-driven delivery (fully deterministic). Opens the stream and
//    injects a `graph:rebuilt` event through lib/vaultBroadcast.server.ts —
//    the same EventEmitter the route subscribes to — so SSE framing, the
//    per-vault subscriber set, and vault scoping are exercised with zero
//    OS/file-watcher timing. Abort + closed-signal + second emit then proves
//    teardown removed the listener.
//
// 2. REAL chokidar watcher on a REAL temp vault, pinned to chokidar's
//    polling backend (CHOKIDAR_USEPOLLING, honored globally by chokidar v6)
//    so delivery no longer rides the macOS fs.watch/FSEvents backend, whose
//    warm-up gap after chokidar's `ready` silently loses writes (measured on
//    this toolchain: a write 0 ms after ready is lost, one at 300 ms
//    delivers, and the gap widens under host load). With polling, delivery
//    latency is bounded by the poll interval + awaitWriteFinish under any
//    load. The test still arms with an active canary probe — distinct .md
//    files written and read windows awaited (event-driven, no sleeps) until
//    the first canary frame proves the watcher delivers — because `ready`
//    alone is not an armed signal. Abort then awaits the route's
//    `vaultWatcherClosed` signal before the provoking write, so a slow
//    teardown under load cannot leak a late frame into the negative window.
//
// The only wall-clock numbers left are backstop caps inside readNextSSEData
// windows (plus awaitWriteFinish's real ~600 ms+ delivery latency that they
// absorb); no assertion depends on a blind sleep elapsing.
//
// The route handler's module-level watcher registry persists across requests
// within a single bun process; each test uses a fresh unique vault path (via
// mkdtemp inside setupTmpHome) so no two tests share a watcher entry, and
// the resolver cache is invalidated by the HOME change between tests. Tests
// mirror the route's registry key by calling `resolveVault()` under the same
// HOME before using the route's test-support signals.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import { NextRequest } from 'next/server'
import * as fs from 'fs'
import * as path from 'path'

import { GET, vaultWatcherReady, vaultWatcherClosed } from './route'
import { resolveVault } from '@/lib/vaultResolver'
import { vaultBroadcast } from '@/lib/vaultBroadcast.server'
import {
  setupTmpHome,
  readNextSSEData,
  type RouteTestSetup,
} from '@/lib/__fixtures__/routeTestHelper'

describe('ENH-012 / vault/events SSE route — broadcast delivery + watcher e2e', () => {
  let setup: RouteTestSetup

  beforeEach(() => {
    setup = setupTmpHome()
    // Pin the route's internal chokidar watcher to its polling backend for
    // the duration of each test (chokidar v6 honors these env vars
    // globally). Polling re-reads file state on a timer instead of riding
    // the OS backend, whose warm-up gap silently loses writes and widens
    // under host load, so delivery latency is bounded by interval +
    // awaitWriteFinish regardless of load. Production behavior is untouched:
    // the vars are set here and only inside this suite.
    process.env.CHOKIDAR_USEPOLLING = 'true'
    process.env.CHOKIDAR_INTERVAL = '100'
    // Seed a pre-existing note so the vault is non-empty (and so chokidar's
    // ignoreInitial scan has something to skip — confirming it does).
    fs.mkdirSync(path.join(setup.defaultVault, 'Patterns'), { recursive: true })
    fs.writeFileSync(path.join(setup.defaultVault, 'Patterns', 'seed.md'), '# seed\n')
  })
  afterEach(() => {
    delete process.env.CHOKIDAR_USEPOLLING
    delete process.env.CHOKIDAR_INTERVAL
    setup.restore()
  })

  async function openStream(): Promise<{
    ac: AbortController
    reader: ReadableStreamDefaultReader<Uint8Array>
    vaultPath: string
  }> {
    const ac = new AbortController()
    const req = new NextRequest('http://localhost:3999/api/vault/events', {
      signal: ac.signal,
    })
    const res = await GET(req)
    expect(res.status).toBe(200)
    expect(res.headers.get('Content-Type')).toBe('text/event-stream')
    // Mirror the route's registry key: same resolver, same env.
    const vaultPath = await resolveVault()
    return { ac, reader: res.body!.getReader(), vaultPath }
  }

  /**
   * Arm the real chokidar watcher with an active probe instead of a blind
   * sleep: write a distinct canary .md and await its `file:created` frame.
   * chokidar's `ready` alone is not an armed signal (writes inside the
   * backend's post-ready warm-up gap are lost), so the first delivered
   * canary frame is the armed proof. With the polling backend pinned, the
   * frame arrives within ~interval + awaitWriteFinish; the two attempts with
   * 3 s caps are a backstop for a slow first stat cycle under extreme load.
   * Returns the delivered canary frame path, or null if the watcher never
   * delivered within the caps.
   */
  async function awaitArmedWatcher(
    reader: ReadableStreamDefaultReader<Uint8Array>,
    vaultDir: string,
  ): Promise<string | null> {
    const windowCapsMs = [3000, 3000]
    for (let attempt = 0; attempt < windowCapsMs.length; attempt++) {
      const relPath = `Patterns/canary-arm-${attempt}.md`
      fs.writeFileSync(path.join(vaultDir, relPath), '# arming canary\n')
      const frame = (await readNextSSEData(reader, windowCapsMs[attempt])) as
        | { type?: string; path?: string }
        | null
      if (
        frame !== null &&
        frame.type === 'file:created' &&
        typeof frame.path === 'string' &&
        frame.path.startsWith('Patterns/canary-arm-')
      ) {
        return frame.path
      }
    }
    return null
  }

  it('delivers a graph:rebuilt frame from the broadcast bus and stops after abort', async () => {
    const { ac, reader, vaultPath } = await openStream()

    // EventEmitter delivery is synchronous: the frame is already enqueued by
    // the time emit() returns, so this read cannot miss.
    vaultBroadcast.emit('graph:rebuilt', { vault: vaultPath })
    const frame = (await readNextSSEData(reader, 2000)) as
      | { type?: string; vault?: string }
      | null
    expect(frame).not.toBeNull()
    expect(frame!.type).toBe('graph:rebuilt')
    expect(frame!.vault).toBe(vaultPath)

    ac.abort()
    await vaultWatcherClosed(vaultPath)

    // Teardown must have removed the bus listener: re-emitting must not
    // produce a frame. The stream is closed and the listener detached
    // synchronously before this emit, so null is deterministic here. The
    // reader is deliberately still locked — a released reader would make
    // this read trivially null and prove nothing.
    vaultBroadcast.emit('graph:rebuilt', { vault: vaultPath })
    const leaked = await readNextSSEData(reader, 1000)
    expect(leaked).toBeNull()

    try {
      reader.releaseLock()
    } catch {
      /* already released by the close */
    }
  }, 15_000)

  it('delivers a file:created frame for a real chokidar write, then stops after abort', async () => {
    const { ac, reader, vaultPath } = await openStream()

    // chokidar's own ready event: necessary (the scan has drained) but not
    // sufficient (backend warm-up continues past it) — the canary probe below
    // supplies the armed proof.
    await vaultWatcherReady(vaultPath)
    const armedPath = await awaitArmedWatcher(reader, setup.defaultVault)
    expect(armedPath).not.toBeNull()

    ac.abort()
    // Await the watcher's close completion before provoking a second write:
    // a slow teardown under load can no longer leak a late frame into the
    // negative window.
    await vaultWatcherClosed(vaultPath)

    // With the registry entry gone (broadcastToVault no-ops) and the chokidar
    // handle closed, this write cannot produce a frame: the stream is closed
    // (read → done). If teardown did NOT run (abort listener missing), the
    // watcher would still be live and this write would produce a frame.
    fs.writeFileSync(
      path.join(setup.defaultVault, 'Patterns', 'after-abort.md'),
      '# after\n',
    )
    const leaked = await readNextSSEData(reader, 1800)
    expect(leaked).toBeNull()
  }, 30_000)
})
