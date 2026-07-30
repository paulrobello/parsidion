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
})
