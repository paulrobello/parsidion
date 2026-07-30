// app/api/note/diff/route.test.ts
// ARC-016 / QA-004: pin the auth + path-traversal contract for the git diff
// route. Same shape as note/history but with the additional from/to SHA
// validation the diff handler carries.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import { GET } from './route'
import { setupTmpHome, makeRequest } from '@/lib/__fixtures__/routeTestHelper'
import * as fs from 'fs'
import * as path from 'path'

describe('ARC-016 / note/diff route — auth + traversal + SHA validation', () => {
  let setup: ReturnType<typeof setupTmpHome>

  beforeEach(() => {
    setup = setupTmpHome()
    fs.mkdirSync(path.join(setup.defaultVault, 'Patterns'), { recursive: true })
    fs.writeFileSync(
      path.join(setup.defaultVault, 'Patterns', 'my-note.md'),
      '# My Note\n',
    )
  })
  afterEach(() => setup.restore())

  it('rejects ../ traversal with 403 or 404', async () => {
    const res = await GET(
      makeRequest(
        '/api/note/diff?path=' +
          encodeURIComponent('../../../etc/passwd') +
          '&from=abc123&to=def456',
      ),
    )
    expect([403, 404]).toContain(res.status)
  })

  it('returns 401 when VISUALIZER_TOKEN is set and Authorization is missing', async () => {
    process.env.VISUALIZER_TOKEN = 'test-secret'
    try {
      const res = await GET(
        makeRequest('/api/note/diff?stem=my-note&from=abc123&to=def456'),
      )
      expect(res.status).toBe(401)
    } finally {
      delete process.env.VISUALIZER_TOKEN
    }
  })

  it('rejects Sec-Fetch-Site: cross-site with 403', async () => {
    const res = await GET(
      makeRequest('/api/note/diff?stem=my-note&from=abc123&to=def456', {
        headers: { 'sec-fetch-site': 'cross-site' },
      }),
    )
    expect(res.status).toBe(403)
  })

  it('returns 400 when from/to are missing', async () => {
    const res = await GET(makeRequest('/api/note/diff?stem=my-note'))
    expect(res.status).toBe(400)
  })

  it('returns 400 when from/to SHAs contain non-hex characters', async () => {
    const res = await GET(
      makeRequest('/api/note/diff?stem=my-note&from=ZZZZ&to=def456'),
    )
    expect(res.status).toBe(400)
  })

  it('accepts the sentinel "working" as the `to` SHA', async () => {
    // No .git directory means git diff will fail server-side, but the route
    // must get past the SHA validation to even attempt the diff. So the
    // response here is either 500 (no .git) or a successful diff — the
    // 400-validation pass is what we are pinning.
    const res = await GET(
      makeRequest('/api/note/diff?stem=my-note&from=abc123&to=working'),
    )
    expect(res.status).not.toBe(400) // SHA validation passed
  })
})
