// app/api/files/route.test.ts
// ARC-016 / QA-004: pin the auth contract for the file-listing route. The
// handler walks the vault and returns every .md file with its frontmatter
// type — no path-traversal input (the `vault` param is allowlist-resolved
// inside resolveVault), so the matrix is the auth/CSRF pair plus the
// happy-path shape.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import { GET } from './route'
import { setupTmpHome, makeRequest } from '@/lib/__fixtures__/routeTestHelper'
import * as fs from 'fs'
import * as path from 'path'

describe('ARC-016 / files route — auth + happy path', () => {
  let setup: ReturnType<typeof setupTmpHome>

  beforeEach(() => {
    setup = setupTmpHome()
    fs.mkdirSync(path.join(setup.defaultVault, 'Patterns'), { recursive: true })
    fs.writeFileSync(
      path.join(setup.defaultVault, 'Patterns', 'a.md'),
      '---\ntype: pattern\n---\n# A\n',
    )
    fs.writeFileSync(
      path.join(setup.defaultVault, 'Patterns', 'b.md'),
      '---\ntype: pattern\n---\n# B\n',
    )
  })
  afterEach(() => setup.restore())

  it('returns 401 when VISUALIZER_TOKEN is set and Authorization is missing', async () => {
    process.env.VISUALIZER_TOKEN = 'test-secret'
    try {
      const res = await GET(makeRequest('/api/files'))
      expect(res.status).toBe(401)
    } finally {
      delete process.env.VISUALIZER_TOKEN
    }
  })

  it('rejects Sec-Fetch-Site: cross-site with 403', async () => {
    const res = await GET(
      makeRequest('/api/files', { headers: { 'sec-fetch-site': 'cross-site' } }),
    )
    expect(res.status).toBe(403)
  })

  it('returns the list of .md files with stem + noteType', async () => {
    const res = await GET(makeRequest('/api/files'))
    expect(res.status).toBe(200)
    const json = await res.json()
    const stems = json.files.map((f: { stem: string }) => f.stem).sort()
    expect(stems).toEqual(['a', 'b'])
    expect(json.files[0].noteType).toBe('pattern')
  })

  it('excludes dotfile directories like .git', async () => {
    fs.mkdirSync(path.join(setup.defaultVault, '.git'), { recursive: true })
    fs.writeFileSync(
      path.join(setup.defaultVault, '.git', 'config.md'),
      '# should not appear\n',
    )
    const res = await GET(makeRequest('/api/files'))
    const json = await res.json()
    expect(json.files.find((f: { stem: string }) => f.stem === 'config')).toBeUndefined()
  })

  it('rejects an unknown vault name with 400', async () => {
    const res = await GET(makeRequest('/api/files?vault=does-not-exist'))
    expect(res.status).toBe(400)
  })
})
