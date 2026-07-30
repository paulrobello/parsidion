// app/api/graph/delta/route.test.ts
// ARC-016 / QA-004: pin auth + the no-baseline full-refetch contract for the
// graph delta endpoint.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import { GET } from './route'
import { setupTmpHome, makeRequest } from '@/lib/__fixtures__/routeTestHelper'
import * as fs from 'fs'
import * as path from 'path'

describe('ARC-016 / graph/delta route — auth + baseline contract', () => {
  let setup: ReturnType<typeof setupTmpHome>

  beforeEach(() => {
    setup = setupTmpHome()
  })
  afterEach(() => setup.restore())

  it('returns 404 when graph.json is absent', async () => {
    const res = await GET(makeRequest('/api/graph/delta?since=anything'))
    expect(res.status).toBe(404)
  })

  it('returns 401 when VISUALIZER_TOKEN is set and Authorization is missing', async () => {
    process.env.VISUALIZER_TOKEN = 'test-secret'
    try {
      const res = await GET(makeRequest('/api/graph/delta?since=x'))
      expect(res.status).toBe(401)
    } finally {
      delete process.env.VISUALIZER_TOKEN
    }
  })

  it('rejects Sec-Fetch-Site: cross-site with 403', async () => {
    const res = await GET(
      makeRequest('/api/graph/delta?since=x', {
        headers: { 'sec-fetch-site': 'cross-site' },
      }),
    )
    expect(res.status).toBe(403)
  })

  it('returns full:true when no `since` baseline is provided', async () => {
    fs.writeFileSync(
      path.join(setup.defaultVault, 'graph.json'),
      JSON.stringify({
        meta: { generated: 't1' },
        nodes: [{ id: 'a' }],
        edges: [],
      }),
    )
    const res = await GET(makeRequest('/api/graph/delta'))
    expect(res.status).toBe(200)
    const json = await res.json()
    expect(json.full).toBe(true)
    expect(json.reason).toBe('missing since')
  })

  it('returns full:true when the `since` baseline is unknown', async () => {
    fs.writeFileSync(
      path.join(setup.defaultVault, 'graph.json'),
      JSON.stringify({
        meta: { generated: 'current' },
        nodes: [],
        edges: [],
      }),
    )
    const res = await GET(makeRequest('/api/graph/delta?since=unknown'))
    expect(res.status).toBe(200)
    const json = await res.json()
    expect(json.full).toBe(true)
    expect(json.reason).toBe('unknown since')
  })
})
