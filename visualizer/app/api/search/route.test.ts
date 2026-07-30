// app/api/search/route.test.ts
// ARC-016 / QA-004: pin the auth + query-validation contract for the search
// route. The handler delegates to vault_search.py via runVaultSearch, so the
// matrix is auth + the q-length / top-N validation gates.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import { GET } from './route'
import { setupTmpHome, makeRequest } from '@/lib/__fixtures__/routeTestHelper'

describe('ARC-016 / search route — auth + query validation', () => {
  let setup: ReturnType<typeof setupTmpHome>

  beforeEach(() => {
    setup = setupTmpHome()
  })
  afterEach(() => setup.restore())

  it('returns 400 when q is missing', async () => {
    const res = await GET(makeRequest('/api/search'))
    expect(res.status).toBe(400)
  })

  it('returns 400 when q exceeds MAX_QUERY_LENGTH (512)', async () => {
    const long = 'a'.repeat(600)
    const res = await GET(makeRequest(`/api/search?q=${long}`))
    expect(res.status).toBe(400)
  })

  it('returns 401 when VISUALIZER_TOKEN is set and Authorization is missing', async () => {
    process.env.VISUALIZER_TOKEN = 'test-secret'
    try {
      const res = await GET(makeRequest('/api/search?q=python'))
      expect(res.status).toBe(401)
    } finally {
      delete process.env.VISUALIZER_TOKEN
    }
  })

  it('rejects Sec-Fetch-Site: cross-site with 403', async () => {
    const res = await GET(
      makeRequest('/api/search?q=python', {
        headers: { 'sec-fetch-site': 'cross-site' },
      }),
    )
    expect(res.status).toBe(403)
  })

  it('rejects an unknown vault name with 400', async () => {
    const res = await GET(makeRequest('/api/search?q=python&vault=nope'))
    expect(res.status).toBe(400)
  })
})
