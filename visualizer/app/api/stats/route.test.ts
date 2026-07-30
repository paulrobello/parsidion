// app/api/stats/route.test.ts
// ARC-016 / QA-004: stats route was the one route in the app that shipped
// with zero guards pre-SEC-102/SEC-118/QA-011. Pin the corrected auth matrix.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import { GET } from './route'
import { setupTmpHome, makeRequest } from '@/lib/__fixtures__/routeTestHelper'

describe('ARC-016 / stats route — auth (was the unguarded route)', () => {
  let setup: ReturnType<typeof setupTmpHome>

  beforeEach(() => {
    setup = setupTmpHome()
  })
  afterEach(() => setup.restore())

  it('returns 401 when VISUALIZER_TOKEN is set and Authorization is missing', async () => {
    process.env.VISUALIZER_TOKEN = 'test-secret'
    try {
      const res = await GET(makeRequest('/api/stats'))
      expect(res.status).toBe(401)
    } finally {
      delete process.env.VISUALIZER_TOKEN
    }
  })

  it('rejects Sec-Fetch-Site: cross-site with 403', async () => {
    const res = await GET(
      makeRequest('/api/stats', { headers: { 'sec-fetch-site': 'cross-site' } }),
    )
    expect(res.status).toBe(403)
  })

  it('returns pendingSummaries count on the happy path', async () => {
    const res = await GET(makeRequest('/api/stats'))
    expect(res.status).toBe(200)
    const json = await res.json()
    expect(json).toHaveProperty('pendingSummaries')
    expect(typeof json.pendingSummaries).toBe('number')
  })
})
