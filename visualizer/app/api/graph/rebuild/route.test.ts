// app/api/graph/rebuild/route.test.ts
// ARC-016 / QA-004: pin auth + Content-Type + script-presence for the graph
// rebuild route. Mutation (POST), shells out to build_graph.py.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import { POST } from './route'
import { setupTmpHome, makeRequest } from '@/lib/__fixtures__/routeTestHelper'

describe('ARC-016 / graph/rebuild route — auth + Content-Type (mutation)', () => {
  let setup: ReturnType<typeof setupTmpHome>

  beforeEach(() => {
    setup = setupTmpHome()
  })
  afterEach(() => setup.restore())

  it('returns 415 when Content-Type is not application/json', async () => {
    const res = await POST(makeRequest('/api/graph/rebuild', { method: 'POST' }))
    expect(res.status).toBe(415)
  })

  it('returns 401 when VISUALIZER_TOKEN is set and Authorization is missing', async () => {
    process.env.VISUALIZER_TOKEN = 'test-secret'
    try {
      const res = await POST(
        makeRequest('/api/graph/rebuild', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
        }),
      )
      expect(res.status).toBe(401)
    } finally {
      delete process.env.VISUALIZER_TOKEN
    }
  })

  it('rejects Sec-Fetch-Site: cross-site with 403', async () => {
    const res = await POST(
      makeRequest('/api/graph/rebuild', {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          'sec-fetch-site': 'cross-site',
        },
      }),
    )
    expect(res.status).toBe(403)
  })

  it('rejects an unknown vault name with 400', async () => {
    const res = await POST(
      makeRequest('/api/graph/rebuild?vault=does-not-exist', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
      }),
    )
    expect(res.status).toBe(400)
  })

  it('returns 400 when the resolved vault does not exist', async () => {
    // Remove the default vault so resolveVault succeeds but stat fails.
    // resolveVault allows the default-vault path even when the dir is absent
    // (so PUT can create notes), so the route's stat check is the gate.
    const fs = await import('fs')
    fs.rmSync(setup.defaultVault, { recursive: true, force: true })
    const res = await POST(
      makeRequest('/api/graph/rebuild', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
      }),
    )
    expect(res.status).toBe(400)
  })
})
