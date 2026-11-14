// lib/vaultResolver.cache.test.ts
// ARC-209: proves the ENH-009 resolution memoization (vaultResolver.ts)
// spawns vault_resolve.py at most once while the config signature is
// stable, re-resolves after invalidation, dedupes concurrent callers, and
// does not cache rejections (a transient failure retries).
//
// Spawns are counted with a stub vault_resolve.py (lib/__fixtures__/resolve/)
// that appends to SPAWN_COUNTER — the same fixture-script pattern as
// __fixtures__/search/ — rather than child_process mocking, because bun's
// mock.module is process-wide across test files and would leak into
// runScript.test.ts.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import * as fs from 'fs'
import * as path from 'path'
import * as os from 'os'
import { spawnSync } from 'child_process'

import {
  resolveVault,
  getVaultsConfigPath,
  _clearResolutionCacheForTest,
} from './vaultResolver'
import { ScriptFailedError } from './runScript'

const FIXTURE_SCRIPTS_DIR = path.join(import.meta.dir, '__fixtures__', 'resolve')

// Spawning `uv run --no-project <stub>` needs the uv toolchain, same as the
// real-script delegation tests in vaultResolver.test.ts.
const uvAvailable =
  spawnSync('uv', ['--version'], { stdio: 'ignore' }).status === 0

let tmpHome = ''
let counterPath = ''
let originalHome: string | undefined
let originalXdg: string | undefined
let originalScriptsDir: string | undefined
let originalCounter: string | undefined

function spawnCount(): number {
  try {
    return fs.readFileSync(counterPath, 'utf8').length
  } catch {
    return 0
  }
}

function setup(): void {
  tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'vault-resolver-cache-'))
  originalHome = process.env.HOME
  originalXdg = process.env.XDG_CONFIG_HOME
  originalScriptsDir = process.env.PARSIDION_SCRIPTS_DIR
  originalCounter = process.env.SPAWN_COUNTER
  process.env.HOME = tmpHome
  delete process.env.XDG_CONFIG_HOME
  process.env.PARSIDION_SCRIPTS_DIR = FIXTURE_SCRIPTS_DIR
  counterPath = path.join(tmpHome, 'spawn_count')
  fs.rmSync(counterPath, { force: true })
  process.env.SPAWN_COUNTER = counterPath
  _clearResolutionCacheForTest()
}

function teardown(): void {
  if (originalHome === undefined) delete process.env.HOME
  else process.env.HOME = originalHome
  if (originalXdg === undefined) delete process.env.XDG_CONFIG_HOME
  else process.env.XDG_CONFIG_HOME = originalXdg
  if (originalScriptsDir === undefined) delete process.env.PARSIDION_SCRIPTS_DIR
  else process.env.PARSIDION_SCRIPTS_DIR = originalScriptsDir
  if (originalCounter === undefined) delete process.env.SPAWN_COUNTER
  else process.env.SPAWN_COUNTER = originalCounter
  _clearResolutionCacheForTest()
  try {
    fs.rmSync(tmpHome, { recursive: true, force: true })
  } catch {
    /* best effort */
  }
}

describe.skipIf(!uvAvailable)('ARC-209 — vault resolution memoization', () => {
  beforeEach(() => setup())
  afterEach(() => teardown())

  it('repeat calls within cache validity spawn the subprocess once', async () => {
    const first = await resolveVault(null)
    const second = await resolveVault(null)
    expect(second).toBe(first)
    expect(spawnCount()).toBe(1)
  })

  it('concurrent callers share one in-flight spawn (promise dedup)', async () => {
    const [a, b] = await Promise.all([resolveVault(null), resolveVault(null)])
    expect(b).toBe(a)
    expect(spawnCount()).toBe(1)
  })

  it('bumping vaults.yaml mtime invalidates the cache', async () => {
    await resolveVault(null)
    expect(spawnCount()).toBe(1)
    const cfg = getVaultsConfigPath()
    fs.mkdirSync(path.dirname(cfg), { recursive: true })
    fs.writeFileSync(cfg, 'default: x\n')
    const future = new Date(Date.now() + 5_000)
    fs.utimesSync(cfg, future, future)
    await resolveVault(null)
    expect(spawnCount()).toBe(2)
  })

  it('rejections are not cached — the next call retries', async () => {
    process.env.PARSIDION_STUB_FAIL = '1'
    await expect(resolveVault(null)).rejects.toBeInstanceOf(ScriptFailedError)
    delete process.env.PARSIDION_STUB_FAIL
    expect(await resolveVault(null)).toBe('/vault/stub\n'.trim())
    expect(spawnCount()).toBe(2)
  })
})
