// app/api/vaults/route.test.ts
// ARC-016 / QA-004: pin auth + the default-vs-named-vault listing logic.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import { GET } from './route'
import { setupTmpHome, makeRequest } from '@/lib/__fixtures__/routeTestHelper'
import * as fs from 'fs'
import * as path from 'path'

describe('ARC-016 / vaults route — auth + listing', () => {
  let setup: ReturnType<typeof setupTmpHome>

  beforeEach(() => {
    setup = setupTmpHome()
  })
  afterEach(() => setup.restore())

  it('returns 401 when VISUALIZER_TOKEN is set and Authorization is missing', async () => {
    process.env.VISUALIZER_TOKEN = 'test-secret'
    try {
      const res = await GET(makeRequest('/api/vaults'))
      expect(res.status).toBe(401)
    } finally {
      delete process.env.VISUALIZER_TOKEN
    }
  })

  it('rejects Sec-Fetch-Site: cross-site with 403', async () => {
    const res = await GET(
      makeRequest('/api/vaults', { headers: { 'sec-fetch-site': 'cross-site' } }),
    )
    expect(res.status).toBe(403)
  })

  it('returns a synthetic "default" entry when no vaults.yaml is configured', async () => {
    const res = await GET(makeRequest('/api/vaults'))
    expect(res.status).toBe(200)
    const json = await res.json()
    expect(json.vaults.length).toBeGreaterThanOrEqual(1)
    expect(json.vaults[0].name).toBe('default')
    expect(json.vaults[0].isDefault).toBe(true)
    expect(json.defaultVault).toBe('default')
  })

  it('returns named vaults from vaults.yaml when configured', async () => {
    const configDir = path.join(setup.tmpHome, '.config', 'parsidion')
    fs.mkdirSync(configDir, { recursive: true })
    const named = path.join(setup.tmpHome, 'NamedVault')
    fs.mkdirSync(named, { recursive: true })
    fs.writeFileSync(
      path.join(configDir, 'vaults.yaml'),
      `vaults:\n  named: ${named}\n`,
    )
    const res = await GET(makeRequest('/api/vaults'))
    expect(res.status).toBe(200)
    const json = await res.json()
    const names = json.vaults.map((v: { name: string }) => v.name)
    expect(names).toContain('named')
  })
})
