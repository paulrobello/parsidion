// app/api/summarizer/status/route.test.ts
// ARC-016 / QA-004: pin auth + the status-shape contract.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import { GET } from './route'
import { setupTmpHome, makeRequest } from '@/lib/__fixtures__/routeTestHelper'

describe('ARC-016 / summarizer/status route — auth + shape', () => {
  let setup: ReturnType<typeof setupTmpHome>

  beforeEach(() => {
    setup = setupTmpHome()
  })
  afterEach(() => setup.restore())

  it('returns 401 when VISUALIZER_TOKEN is set and Authorization is missing', async () => {
    process.env.VISUALIZER_TOKEN = 'test-secret'
    try {
      const res = await GET(makeRequest('/api/summarizer/status'))
      expect(res.status).toBe(401)
    } finally {
      delete process.env.VISUALIZER_TOKEN
    }
  })

  it('rejects Sec-Fetch-Site: cross-site with 403', async () => {
    const res = await GET(
      makeRequest('/api/summarizer/status', {
        headers: { 'sec-fetch-site': 'cross-site' },
      }),
    )
    expect(res.status).toBe(403)
  })

  it('returns a status object with pendingSummaries on the happy path', async () => {
    const res = await GET(makeRequest('/api/summarizer/status'))
    expect(res.status).toBe(200)
    const json = await res.json()
    expect(json).toHaveProperty('pendingSummaries')
    expect(typeof json.pendingSummaries).toBe('number')
  })

  it('rejects an unknown vault name with 400', async () => {
    const res = await GET(makeRequest('/api/summarizer/status?vault=does-not-exist'))
    expect(res.status).toBe(400)
  })
})
