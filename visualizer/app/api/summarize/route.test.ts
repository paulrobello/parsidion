// app/api/summarize/route.test.ts
// ARC-016 / QA-004: pin auth + Content-Type + happy path for the summarizer
// spawn route. This is a mutation (POST) so requireAuth's Content-Type check
// is also exercised.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import { POST } from './route'
import { setupTmpHome, makeRequest } from '@/lib/__fixtures__/routeTestHelper'

describe('ARC-016 / summarize route — auth + Content-Type (mutation)', () => {
  let setup: ReturnType<typeof setupTmpHome>

  beforeEach(() => {
    setup = setupTmpHome()
  })
  afterEach(() => setup.restore())

  it('returns 415 when Content-Type is not application/json', async () => {
    const res = await POST(
      makeRequest('/api/summarize', { method: 'POST' }),
    )
    expect(res.status).toBe(415)
  })

  it('returns 401 when VISUALIZER_TOKEN is set and Authorization is missing', async () => {
    process.env.VISUALIZER_TOKEN = 'test-secret'
    try {
      const res = await POST(
        makeRequest('/api/summarize', {
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
      makeRequest('/api/summarize', {
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
      makeRequest('/api/summarize?vault=does-not-exist', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
      }),
    )
    expect(res.status).toBe(400)
  })
})
