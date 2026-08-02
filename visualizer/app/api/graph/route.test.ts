// app/api/graph/route.test.ts
// ARC-016 / QA-004: pin the auth contract for the streamed graph.json route.
// The route is owned by ARC-015 (streaming + ETag); this test pins the auth
// + 404 behavior.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import { GET } from './route'
import { setupTmpHome, makeRequest } from '@/lib/__fixtures__/routeTestHelper'
import * as fs from 'fs'
import * as path from 'path'

describe('ARC-016 / graph route — auth + 404 + happy path', () => {
  let setup: ReturnType<typeof setupTmpHome>

  beforeEach(() => {
    setup = setupTmpHome()
  })
  afterEach(() => setup.restore())

  it('returns 404 when graph.json is absent', async () => {
    const res = await GET(makeRequest('/api/graph'))
    expect(res.status).toBe(404)
  })

  it('returns 401 when VISUALIZER_TOKEN is set and Authorization is missing', async () => {
    process.env.VISUALIZER_TOKEN = 'test-secret'
    try {
      const res = await GET(makeRequest('/api/graph'))
      expect(res.status).toBe(401)
    } finally {
      delete process.env.VISUALIZER_TOKEN
    }
  })

  it('rejects Sec-Fetch-Site: cross-site with 403', async () => {
    const res = await GET(
      makeRequest('/api/graph', { headers: { 'sec-fetch-site': 'cross-site' } }),
    )
    expect(res.status).toBe(403)
  })

  it('streams graph.json with ETag + Content-Type when present', async () => {
    const graph = { meta: { generated: 'test', noteCount: 0 }, nodes: [], edges: [] }
    fs.writeFileSync(
      path.join(setup.defaultVault, 'graph.json'),
      JSON.stringify(graph),
    )
    const res = await GET(makeRequest('/api/graph'))
    expect(res.status).toBe(200)
    expect(res.headers.get('Content-Type')).toBe('application/json')
    expect(res.headers.get('ETag')).not.toBeNull()
    const body = await res.text()
    expect(body).toBe(JSON.stringify(graph))
  })

  it('returns 304 when If-None-Match matches the current ETag', async () => {
    fs.writeFileSync(
      path.join(setup.defaultVault, 'graph.json'),
      JSON.stringify({ nodes: [], edges: [] }),
    )
    const first = await GET(makeRequest('/api/graph'))
    const etag = first.headers.get('ETag')!
    const second = await GET(
      makeRequest('/api/graph', { headers: { 'If-None-Match': etag } }),
    )
    expect(second.status).toBe(304)
  })

  // ENH-012: the 304 path must carry the ETag and an empty body so cache
  // validators round-trip, and a content change (new size + mtime) must
  // produce a different ETag that no longer 304s against the stale value.
  it('304 response carries the ETag header and an empty body', async () => {
    const body = JSON.stringify({ nodes: [{ id: 'a' }], edges: [] })
    fs.writeFileSync(path.join(setup.defaultVault, 'graph.json'), body)
    const first = await GET(makeRequest('/api/graph'))
    const etag = first.headers.get('ETag')!
    const second = await GET(
      makeRequest('/api/graph', { headers: { 'If-None-Match': etag } }),
    )
    expect(second.status).toBe(304)
    // The validator must round-trip on the 304 so the next request can
    // revalidate against it.
    expect(second.headers.get('ETag')).toBe(etag)
    // No body on a 304 — the client reuses its cached copy.
    const text = await second.text()
    expect(text).toBe('')
  })

  it('returns 200 with a new, different ETag after graph.json content changes', async () => {
    fs.writeFileSync(
      path.join(setup.defaultVault, 'graph.json'),
      JSON.stringify({ nodes: [], edges: [] }),
    )
    const first = await GET(makeRequest('/api/graph'))
    const oldEtag = first.headers.get('ETag')!
    expect(first.status).toBe(200)

    // Rewrite with larger content (different size → different ETag; bump
    // mtime too so makeEtag's mtimeMs component also changes). Waiting a few
    // ms keeps the mtime strictly greater on filesystems with coarse mtimes.
    await new Promise(r => setTimeout(r, 20))
    fs.writeFileSync(
      path.join(setup.defaultVault, 'graph.json'),
      JSON.stringify({ nodes: [{ id: 'a' }, { id: 'b' }, { id: 'c' }], edges: [] }),
    )

    // The stale client revalidates with the old ETag and must get the new body.
    const second = await GET(
      makeRequest('/api/graph', { headers: { 'If-None-Match': oldEtag } }),
    )
    expect(second.status).toBe(200)
    const newEtag = second.headers.get('ETag')!
    expect(newEtag).not.toBe(oldEtag)
    // JSON.stringify emits no spaces; the new content must round-trip intact.
    const text = await second.text()
    expect(text).toContain('"id":"c"')
  })
})
