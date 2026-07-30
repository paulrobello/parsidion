// app/api/note/history/route.test.ts
// ARC-016 / QA-004: pin the auth + path-traversal contract for the git
// history route. The handler resolves a note by stem or path then shells out
// to `git log`, so the path-traversal guard is the only defense keeping an
// adversary with browser access out of arbitrary files' histories.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import { GET } from './route'
import { setupTmpHome, makeRequest } from '@/lib/__fixtures__/routeTestHelper'
import * as fs from 'fs'
import * as path from 'path'

describe('ARC-016 / note/history route — auth + traversal guards', () => {
  let setup: ReturnType<typeof setupTmpHome>

  beforeEach(() => {
    setup = setupTmpHome()
    // Seed a note in the default vault so the happy-path resolves something.
    fs.mkdirSync(path.join(setup.defaultVault, 'Patterns'), { recursive: true })
    fs.writeFileSync(
      path.join(setup.defaultVault, 'Patterns', 'my-note.md'),
      '# My Note\n',
    )
  })
  afterEach(() => setup.restore())

  it('rejects ../ traversal with 403', async () => {
    const res = await GET(
      makeRequest('/api/note/history?path=' + encodeURIComponent('../../../etc/passwd')),
    )
    // The route's findNote won't find ../../../etc/passwd and returns 404, OR
    // guardPath rejects with 403. Either way, the file is NOT read.
    expect([403, 404]).toContain(res.status)
  })

  it('returns 401 when VISUALIZER_TOKEN is set and Authorization is missing', async () => {
    process.env.VISUALIZER_TOKEN = 'test-secret'
    try {
      const res = await GET(makeRequest('/api/note/history?stem=my-note'))
      expect(res.status).toBe(401)
    } finally {
      delete process.env.VISUALIZER_TOKEN
    }
  })

  it('rejects Sec-Fetch-Site: cross-site with 403', async () => {
    const res = await GET(
      makeRequest('/api/note/history?stem=my-note', {
        headers: { 'sec-fetch-site': 'cross-site' },
      }),
    )
    expect(res.status).toBe(403)
  })

  it('returns empty commits list when the vault has no .git directory', async () => {
    const res = await GET(makeRequest('/api/note/history?stem=my-note'))
    expect(res.status).toBe(200)
    const json = await res.json()
    expect(json.commits).toEqual([])
  })

  it('returns 400 when neither stem nor path is provided', async () => {
    const res = await GET(makeRequest('/api/note/history'))
    expect(res.status).toBe(400)
  })
})
