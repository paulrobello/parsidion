// app/api/graph/rebuild/route.ts
import { NextRequest, NextResponse } from 'next/server'
import path from 'path'
import fs from 'fs/promises'
import { vaultBroadcast } from '@/lib/vaultBroadcast.server'
import { resolveVault, VaultConfigError } from '@/lib/vaultResolver'
import { withApi } from '@/lib/apiAuth'
import { findParsidionScript } from '@/lib/scriptResolver'
import { runScript, ScriptFailedError } from '@/lib/runScript'

// ARC-015 step 5: broadcast includes the resolved vault path so SSE clients
// scoped to a different vault can ignore the rebuild instead of refetching.

// SEC-030: one build_graph run per vault at a time. Concurrent POSTs raced
// on graph.json (both builds writing the same output path); a second caller
// now awaits the in-flight run's promise instead of forking its own.
const rebuildInFlight = new Map<string, Promise<void>>()

export const POST = withApi(async (req: NextRequest) => {
  const vault = req.nextUrl.searchParams.get('vault')

  // SEC-005: Validate vault path before passing it to the subprocess.
  // SEC-001 forbidden-prefix check is enforced inside resolveVault().
  let vaultPath: string
  try {
    vaultPath = await resolveVault(vault)
  } catch (err) {
    if (err instanceof VaultConfigError) {
      return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
    }
    return NextResponse.json({ error: 'Failed to resolve vault' }, { status: 500 })
  }

  // SEC-005: Verify the resolved path exists and is a directory before spawning.
  // QA-012: stat via fs/promises so the event loop is not blocked.
  try {
    const stat = await fs.stat(vaultPath)
    if (!stat.isDirectory()) {
      return NextResponse.json({ error: 'Vault directory not found' }, { status: 400 })
    }
  } catch {
    return NextResponse.json({ error: 'Vault directory not found' }, { status: 400 })
  }

  const scriptPath = findParsidionScript('build_graph.py')
  if (!scriptPath) {
    return NextResponse.json(
      { error: 'build_graph.py not found. Install parsidion or run from the source repo.' },
      { status: 500 }
    )
  }

  const outputPath = path.join(vaultPath, 'graph.json')
  const args = ['run', '--no-project', scriptPath, '--vault', vaultPath, '--output', outputPath]

  // ARC-036: shared subprocess wrapper — timeout, abort-on-client-disconnect,
  // capped stderr. Replaces a hand-rolled spawn here. The build can take long
  // on a large vault, so allow up to 5 minutes; aborts when the client closes
  // the POST connection (req.signal).
  // SEC-030: join (or start) the single in-flight run for this vault.
  let build = rebuildInFlight.get(vaultPath)
  if (!build) {
    build = runScript('uv', args, {
      signal: req.signal,
      timeoutMs: 5 * 60_000,
    }).then(() => undefined)
    rebuildInFlight.set(vaultPath, build)
    const owned = build
    owned.catch(() => {}).finally(() => {
      if (rebuildInFlight.get(vaultPath) === owned) rebuildInFlight.delete(vaultPath)
    })
  }
  try {
    await build
  } catch (err) {
    if (err instanceof ScriptFailedError) {
      console.error('[graph/rebuild] build_graph.py', err.message, ':', err.stderr)
      return NextResponse.json(
        { error: `Graph rebuild failed (exit code ${err.exitCode})` },
        { status: 500 },
      )
    }
    console.error('[graph/rebuild] error:', err)
    return NextResponse.json(
      { error: 'Graph rebuild failed (timeout or client aborted)' },
      { status: 500 },
    )
  }

  // ARC-015 step 5: include the resolved vault in the broadcast payload so
  // tabs on a different vault can ignore the rebuild instead of refetching.
  vaultBroadcast.emit('graph:rebuilt', { vault: vaultPath })
  return NextResponse.json({ ok: true })
}, { mutation: true })
