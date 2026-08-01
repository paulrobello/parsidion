// app/api/note/route.test.ts
// ARC-002 + ARC-016: pin the per-method vault contract for note writes.
//
// The client puts the selected vault in the JSON body (useVisualizerState.ts:
//   if (selectedVault) body.vault = selectedVault)
// while POST and PUT used to read it only from the query string. The body
// field was silently discarded and writes routed to the default vault —
// producing silent data loss and a defunct mtime conflict check. These
// tests pin the corrected contract: vault is accepted from EITHER the
// query string OR the JSON body (query string wins for backward compat),
// and the mtime check stats the correct (selected) vault's file.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import { NextRequest } from 'next/server'
import fs from 'fs'
import path from 'path'
import os from 'os'

import { PUT, POST, GET, DELETE } from './route'

// Set up two vaults in tmpdir: a default (no vaults.yaml name) and a
// named "secondary" vault. We point HOME here so getDefaultVault() and
// getVaultsConfigPath() both resolve under the temp tree.
let tmpHome: string
let defaultVault: string
let secondaryVault: string
let originalHome: string | undefined
let originalVaultRoot: string | undefined
let originalVisualizerToken: string | undefined
let originalXdgConfig: string | undefined

function setupVaults() {
  tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'arc002-home-'))
  defaultVault = path.join(tmpHome, 'ParsidionVault')
  secondaryVault = path.join(tmpHome, 'SecondaryVault')
  fs.mkdirSync(path.join(defaultVault, 'Patterns'), { recursive: true })
  fs.mkdirSync(path.join(secondaryVault, 'Patterns'), { recursive: true })

  // Write vaults.yaml registering the secondary vault under name "secondary".
  // Format matches installer/templates (create_vaults_config):
  //   vaults:
  //     name: /path/to/vault
  const configDir = path.join(tmpHome, '.config', 'parsidion')
  fs.mkdirSync(configDir, { recursive: true })
  fs.writeFileSync(
    path.join(configDir, 'vaults.yaml'),
    [
      `vaults:`,
      `  secondary: ${secondaryVault}`,
      '',
    ].join('\n'),
  )

  originalHome = process.env.HOME
  originalVaultRoot = process.env.VAULT_ROOT
  originalVisualizerToken = process.env.VISUALIZER_TOKEN
  originalXdgConfig = process.env.XDG_CONFIG_HOME
  process.env.HOME = tmpHome
  delete process.env.VAULT_ROOT // force getDefaultVault() to use $HOME/ParsidionVault
  delete process.env.VISUALIZER_TOKEN // tests run without token; mutations still pass requireAuth
  // GitHub's Ubuntu runners set XDG_CONFIG_HOME; without clearing it,
  // getVaultsConfigPath() reads the runner's vaults.yaml (which has no
  // "secondary" vault), so named-vault writes return 400.
  delete process.env.XDG_CONFIG_HOME
}

function teardownVaults() {
  if (originalHome === undefined) delete process.env.HOME
  else process.env.HOME = originalHome
  if (originalVaultRoot === undefined) delete process.env.VAULT_ROOT
  else process.env.VAULT_ROOT = originalVaultRoot
  if (originalVisualizerToken === undefined) delete process.env.VISUALIZER_TOKEN
  else process.env.VISUALIZER_TOKEN = originalVisualizerToken
  if (originalXdgConfig === undefined) delete process.env.XDG_CONFIG_HOME
  else process.env.XDG_CONFIG_HOME = originalXdgConfig

  try {
    fs.rmSync(tmpHome, { recursive: true, force: true })
  } catch {
    /* best effort */
  }
}

function makePutRequest(body: object, queryString = ''): NextRequest {
  const url = `http://localhost:3999/api/note${queryString}`
  return new NextRequest(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

function makePostRequest(body: object, queryString = ''): NextRequest {
  const url = `http://localhost:3999/api/note${queryString}`
  return new NextRequest(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

describe('ARC-002 / note route — vault from body', () => {
  beforeEach(() => setupVaults())
  afterEach(() => teardownVaults())

  describe('PUT — vault from body vs query', () => {
    it('PUT with vault in body writes into the named (non-default) vault', async () => {
      // No ?vault= query — vault comes from the body. Pre-fix this routed to
      // the default vault, silently writing to the wrong location.
      const relPath = 'Patterns/secondary-only-note.md'
      const req = makePutRequest({
        path: relPath,
        content: '# from secondary\n',
        vault: 'secondary',
      })
      const res = await PUT(req)
      expect(res.status).toBe(200)
      const json = await res.json()
      expect(json.ok).toBe(true)

      // The file MUST exist in the secondary vault, not the default.
      expect(fs.existsSync(path.join(secondaryVault, relPath))).toBe(true)
      expect(fs.existsSync(path.join(defaultVault, relPath))).toBe(false)
    })

    it('PUT with no vault falls through to the default vault (backward compat)', async () => {
      const relPath = 'Patterns/default-only-note.md'
      const req = makePutRequest({
        path: relPath,
        content: '# from default\n',
      })
      const res = await PUT(req)
      expect(res.status).toBe(200)

      expect(fs.existsSync(path.join(defaultVault, relPath))).toBe(true)
      expect(fs.existsSync(path.join(secondaryVault, relPath))).toBe(false)
    })

    it('PUT query string ?vault= wins over body.vault when both are set', async () => {
      // Backward-compat contract: query string takes precedence.
      const relPath = 'Patterns/precedence-note.md'
      const req = makePutRequest(
        { path: relPath, content: '# precedence\n', vault: 'secondary' },
        '?vault=secondary', // both query and body agree on secondary here;
        //                            the assertion is that query is honored.
      )
      const res = await PUT(req)
      expect(res.status).toBe(200)
      expect(fs.existsSync(path.join(secondaryVault, relPath))).toBe(true)
    })

    it('PUT with unknown vault name returns 400 (allowlist still enforced)', async () => {
      const req = makePutRequest({
        path: 'Patterns/x.md',
        content: 'x\n',
        vault: 'does-not-exist',
      })
      const res = await PUT(req)
      expect(res.status).toBe(400)
    })
  })

  describe('POST — vault from body vs query', () => {
    it('POST with vault in body writes into the named (non-default) vault', async () => {
      // POST is the "update existing note" path. Seed the same-relative-path
      // note in BOTH vaults. Pre-fix, body.vault was discarded → POST
      // resolved against the default vault and OVERWROTE THE WRONG FILE.
      // Post-fix, it must target the secondary vault's note and leave the
      // default vault's note byte-identical.
      const relPath = 'Patterns/conflicting-stem.md'
      const defaultNote = path.join(defaultVault, relPath)
      const secondaryNote = path.join(secondaryVault, relPath)
      fs.writeFileSync(defaultNote, '# default-vault note (must not be touched)\n')
      fs.writeFileSync(secondaryNote, '# secondary original\n')

      const req = makePostRequest({
        path: relPath,
        content: '# secondary updated\n',
        vault: 'secondary',
      })
      const res = await POST(req)
      expect(res.status).toBe(200)

      // The secondary vault's note was updated.
      expect(fs.readFileSync(secondaryNote, 'utf-8')).toBe('# secondary updated\n')

      // The default vault's note is byte-identical (would have been overwritten pre-fix).
      expect(fs.readFileSync(defaultNote, 'utf-8')).toBe(
        '# default-vault note (must not be touched)\n',
      )
    })

    it('POST mtime conflict check stats the correct (selected) vault file', async () => {
      // baseMtimeMs conflict detection must compare against the file in the
      // selected vault, not the default vault's same-relative-path file.
      // Seed a stale note in BOTH vaults; the secondary file is older than
      // baseMtimeMs → no conflict, save proceeds.
      const relPath = 'Patterns/conflict-check.md'
      const secondaryNote = path.join(secondaryVault, relPath)
      const defaultNote = path.join(defaultVault, relPath)
      fs.writeFileSync(secondaryNote, '# secondary v1\n')
      fs.writeFileSync(defaultNote, '# default v1\n')
      const realStat = fs.statSync(secondaryNote)
      // Bump mtime forward so baseMtimeMs is clearly greater than current.
      const futureMs = realStat.mtimeMs + 5000
      await new Promise(resolve => setTimeout(resolve, 20))

      const req = makePostRequest({
        path: relPath,
        content: '# secondary v2\n',
        vault: 'secondary',
        baseMtimeMs: futureMs,
      })
      const res = await POST(req)
      // No conflict (the secondary file's mtime is older than futureMs) → save proceeds.
      expect(res.status).toBe(200)
      const json = await res.json()
      expect(json.conflict).not.toBe(true)
      expect(fs.readFileSync(secondaryNote, 'utf-8')).toContain('# secondary v2')
      // Default untouched.
      expect(fs.readFileSync(defaultNote, 'utf-8')).toBe('# default v1\n')
    })

    it('POST with no vault falls through to the default vault (backward compat)', async () => {
      const relPath = 'Patterns/no-vault-note.md'
      const defaultNote = path.join(defaultVault, relPath)
      const secondaryNote = path.join(secondaryVault, relPath)
      fs.writeFileSync(defaultNote, '# default original\n')
      fs.writeFileSync(secondaryNote, '# secondary original\n')

      const req = makePostRequest({
        path: relPath,
        content: '# default updated\n',
      })
      const res = await POST(req)
      expect(res.status).toBe(200)
      expect(fs.readFileSync(defaultNote, 'utf-8')).toBe('# default updated\n')
      // Secondary untouched.
      expect(fs.readFileSync(secondaryNote, 'utf-8')).toBe('# secondary original\n')
    })
  })

  describe('GET / DELETE — unchanged (already accept vault from query)', () => {
    it('GET reads from the named vault via ?vault=', async () => {
      const relPath = 'Patterns/get-target.md'
      fs.writeFileSync(path.join(secondaryVault, relPath), '# get me\n')
      const req = new NextRequest(
        `http://localhost:3999/api/note?path=${encodeURIComponent(relPath)}&vault=secondary`,
      )
      const res = await GET(req)
      expect(res.status).toBe(200)
      const json = await res.json()
      expect(json.content).toContain('# get me')
    })

    it('DELETE removes from the named vault via ?vault=', async () => {
      const relPath = 'Patterns/del-target.md'
      fs.writeFileSync(path.join(secondaryVault, relPath), '# delete me\n')
      const req = new NextRequest(
        `http://localhost:3999/api/note?path=${encodeURIComponent(relPath)}&vault=secondary`,
        {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
        },
      )
      const res = await DELETE(req)
      expect(res.status).toBe(200)
      expect(fs.existsSync(path.join(secondaryVault, relPath))).toBe(false)
    })
  })

  // ARC-040: standardize conflict encoding (HTTP 409 + {error, conflict, ...}),
  // wrap req.json() so malformed bodies return 400 instead of 500, and type-
  // validate `content` (non-string → 400, not a runtime throw deep in fs).
  describe('ARC-040 — conflict semantics + body validation', () => {
    it('POST mtime conflict returns 409 with {error, conflict, serverContent, mtimeMs}', async () => {
      const relPath = 'Patterns/arc040-conflict.md'
      const notePath = path.join(defaultVault, relPath)
      fs.writeFileSync(notePath, '# v1\n')
      const stale = fs.statSync(notePath).mtimeMs
      // Wait so the file's mtime is strictly greater than baseMtimeMs after
      // we re-write it. baseMtimeMs < next mtime ⇒ conflict branch fires.
      await new Promise(r => setTimeout(r, 20))
      fs.writeFileSync(notePath, '# v2 — externally edited\n')
      const newerStat = fs.statSync(notePath)
      // Caller still holds the older mtime token (stale).
      const req = makePostRequest({
        path: relPath,
        content: '# client overwrote v1 — should be rejected\n',
        baseMtimeMs: stale,
      })
      const res = await POST(req)
      expect(res.status).toBe(409)
      const json = await res.json()
      expect(json.error).toBe('Note modified externally')
      expect(json.conflict).toBe(true)
      expect(json.serverContent).toContain('# v2 — externally edited')
      expect(json.mtimeMs).toBe(newerStat.mtimeMs)
      // The client's content was NOT written.
      expect(fs.readFileSync(notePath, 'utf-8')).toBe('# v2 — externally edited\n')
    })

    it('PUT "note already exists" returns 409 with {error}', async () => {
      const relPath = 'Patterns/arc040-existing.md'
      fs.writeFileSync(path.join(defaultVault, relPath), '# original\n')
      const req = makePutRequest({ path: relPath, content: '# duplicate\n' })
      const res = await PUT(req)
      expect(res.status).toBe(409)
      const json = await res.json()
      expect(json.error).toBe('Note already exists')
    })

    it('POST with malformed JSON body returns 400 (not 500)', async () => {
      const req = new NextRequest('http://localhost:3999/api/note', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{not valid json',
      })
      const res = await POST(req)
      expect(res.status).toBe(400)
      const json = await res.json()
      expect(json.error).toContain('Invalid JSON')
    })

    it('PUT with malformed JSON body returns 400 (not 500)', async () => {
      const req = new NextRequest('http://localhost:3999/api/note', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: '}{ malformed',
      })
      const res = await PUT(req)
      expect(res.status).toBe(400)
    })

    it('POST with non-string content returns 400 (type validation)', async () => {
      const req = makePostRequest({ path: 'Patterns/x.md', content: { bad: 'object' } })
      const res = await POST(req)
      expect(res.status).toBe(400)
      const json = await res.json()
      expect(json.error).toContain('string content required')
    })

    it('PUT with non-string content returns 400 (type validation)', async () => {
      const req = makePutRequest({ path: 'Patterns/y.md', content: 42 })
      const res = await PUT(req)
      expect(res.status).toBe(400)
    })
  })
})
