// app/api/graph/route.ts
import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import fsPromises from 'fs/promises'
import path from 'path'
import { resolveVault, VaultConfigError } from '@/lib/vaultResolver'
import { withApi } from '@/lib/apiAuth'

/**
 * Strong validator for graph.json: mtime (ms) + size (bytes). Both come from
 * a single fs.stat call so they cannot drift apart. The mtime alone is
 * insufficient because two rebuilds within the same filesystem tick (e.g.
 * `touch` then no-op rebuild) can produce identical mtimes with different
 * contents — adding size rules that out without depending on the file body.
 */
function makeEtag(mtimeMs: number, size: number): string {
  // Quoted hex so it round-trips through If-None-Match unchanged.
  return `"${Math.round(mtimeMs).toString(16)}-${size.toString(16)}"`
}

export const GET = withApi(async (req: NextRequest) => {
  const vault = req.nextUrl.searchParams.get('vault')
  let vaultPath: string
  try {
    vaultPath = await resolveVault(vault)
  } catch (err) {
    if (err instanceof VaultConfigError) {
      return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
    }
    return NextResponse.json({ error: 'Failed to resolve vault' }, { status: 500 })
  }
  const graphPath = path.join(vaultPath, 'graph.json')

  // QA-012: stat the file via fs/promises so the event loop is not blocked.
  // (ARC-015 already streams the body; this finishes the async-fs cleanup.)
  let stat: fs.Stats
  try {
    stat = await fsPromises.stat(graphPath)
  } catch (err) {
    // SEC-120: log the absolute path server-side only; the client gets a
    // generic message that does not leak the vault's filesystem location.
    console.error('[graph] stat failed for', graphPath, err)
    return NextResponse.json(
      { error: 'graph.json not found in vault' },
      { status: 404 },
    )
  }

  const etag = makeEtag(stat.mtimeMs, stat.size)

  // ARC-015 step 2: 304 short-circuit. `If-None-Match` is set by browsers
  // automatically when a previous response carried an ETag. We compare the
  // full token, not a weak prefix — the validator is strong (mtime+size), so
  // byte-equivalence holds.
  const ifNoneMatch = req.headers.get('if-none-match')
  if (ifNoneMatch !== null && ifNoneMatch === etag) {
    return new NextResponse(null, {
      status: 304,
      headers: {
        ETag: etag,
        'Cache-Control': 'no-cache',
      },
    })
  }

  // ARC-015 step 1: stream the file. The 47.5 MB graph.json was previously
  // read fully into a JS string on every request; with 5,563 nodes / 376,060
  // edges that tied up ~95 MB of string memory per concurrent request.
  // createReadStream pumps the file through a Node ReadStream; we adapt it
  // to a Web ReadableStream so Next's Response/NextResponse accepts it as a
  // body without materializing the whole file in memory.
  const nodeStream = fs.createReadStream(graphPath)
  const webStream = new ReadableStream<Uint8Array>({
    start(controller) {
      nodeStream.on('data', (chunk: Buffer) => {
        // Copy into a transferable Uint8Array so the chunk is not a shared
        // Node Buffer (which Response bodies do not accept on every runtime).
        controller.enqueue(new Uint8Array(chunk))
      })
      nodeStream.on('end', () => controller.close())
      nodeStream.on('error', err => controller.error(err))
    },
    cancel(reason) {
      // Client disconnected — stop pumping so we don't hold the FD.
      nodeStream.destroy(typeof reason === 'object' && reason instanceof Error ? reason : undefined)
    },
  })
  return new NextResponse(webStream, {
    status: 200,
    headers: {
      'Content-Type': 'application/json',
      'Content-Length': String(stat.size),
      ETag: etag,
      // `no-cache` (not `no-store`): the body is cacheable but every re-use
      // must revalidate via If-None-Match, which is exactly what gets the 304.
      'Cache-Control': 'no-cache',
    },
  })
})
