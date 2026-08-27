// lib/vaultResolver.parity.test.ts
// ENH-005 / ENH-009: the TypeScript half of the shared vault-resolution
// contract.
//
// Reads the SAME fixture as the Python suite
// (tests/fixtures/parity/vault-resolution.json). Since ENH-009 the TypeScript
// resolver DELEGATES to Python's resolve_vault_server (via vault_resolve.py),
// so there is only one resolution implementation; this suite is now an
// end-to-end check that the delegated path produces every fixture vector's
// expected result. The Python half (the authoritative resolution-semantics
// suite) lives at tests/test_vault_resolver_parity.py.
//
// The vector loop spawns the real resolver through `uv`, so it is gated on uv
// being installed (the visualizer CI job installs no Python toolchain — same
// convention as searchServer.test.ts). The fixture-version and vector-
// accounting tests are pure and always run, pinning the applies_to contract.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import * as fs from 'fs'
import * as path from 'path'
import * as os from 'os'
import { spawnSync } from 'child_process'

import { resolveVault, VaultConfigError, _clearResolutionCacheForTest } from './vaultResolver'

const FIXTURE = path.join(
  import.meta.dir,
  '..',
  '..',
  'tests',
  'fixtures',
  'parity',
  'vault-resolution.json',
)

// Point findParsidionScript at the real scripts dir so vault_resolve.py (and
// its core.vault_path import) resolve deterministically.
const REAL_SCRIPTS_DIR = path.join(
  import.meta.dir,
  '..',
  '..',
  'skills',
  'parsidion',
  'scripts',
)
const uvAvailable =
  spawnSync('uv', ['--version'], { stdio: 'ignore' }).status === 0

interface Vector {
  name: string
  description?: string
  applies_to?: string[]
  explicit?: string | null
  cwd?: string
  cwd_marker?: string
  cwd_marker_content?: string
  env_HOME?: string
  env_CLAUDE_VAULT?: string | null
  env_VAULT_ROOT?: string | null
  vaults_yaml?: string
  mkdir?: string[]
  expect?: string
  expect_error?: string
}

interface Fixture {
  version: number
  vectors: Vector[]
}

const fixture: Fixture = JSON.parse(fs.readFileSync(FIXTURE, 'utf-8'))
const allVectors = fixture.vectors
const tsVectors = allVectors.filter(
  v => v.applies_to === undefined || v.applies_to.includes('typescript'),
)
const excludedFromTs = allVectors
  .filter(v => !(v.applies_to === undefined || v.applies_to.includes('typescript')))
  .map(v => v.name)

// State saved/restored around each vector -- the resolver reads env vars and
// the filesystem, so materialization must not leak between cases.
let savedEnv: Record<string, string | undefined> = {}
let tmpRoot: string

function subst<T>(value: T): T {
  if (typeof value === 'string') return value.replaceAll('{TMP}', tmpRoot) as T
  if (Array.isArray(value)) return value.map(subst) as T
  return value
}

function materialize(vec: Vector): void {
  // realpath so {TMP} substitution matches Python's .resolve() output on macOS
  // (the /var -> /private/var tmp symlink), the same /private-prefixing caveat
  // the Python parity suite notes.
  tmpRoot = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'parity-')))

  const home = subst(vec.env_HOME ?? '{TMP}')
  process.env.HOME = home

  // Clear every env var a vector can set, then apply the vector's value.
  delete process.env.CLAUDE_VAULT
  delete process.env.VAULT_ROOT
  delete process.env.XDG_CONFIG_HOME
  if (vec.env_CLAUDE_VAULT) process.env.CLAUDE_VAULT = subst(vec.env_CLAUDE_VAULT)
  if (vec.env_VAULT_ROOT) process.env.VAULT_ROOT = subst(vec.env_VAULT_ROOT)

  for (const d of vec.mkdir ?? []) {
    fs.mkdirSync(subst(d), { recursive: true })
  }

  if (vec.vaults_yaml) {
    const configDir = path.join(home, '.config', 'parsidion')
    fs.mkdirSync(configDir, { recursive: true })
    fs.writeFileSync(path.join(configDir, 'vaults.yaml'), subst(vec.vaults_yaml))
  }
}

function cleanup(): void {
  if (tmpRoot) {
    try {
      fs.rmSync(tmpRoot, { recursive: true, force: true })
    } catch {
      /* best effort */
    }
  }
}

describe('ENH-005 / ENH-009 — vault-resolution parity (shared fixture)', () => {
  beforeEach(() => {
    savedEnv = {
      HOME: process.env.HOME,
      CLAUDE_VAULT: process.env.CLAUDE_VAULT,
      VAULT_ROOT: process.env.VAULT_ROOT,
      XDG_CONFIG_HOME: process.env.XDG_CONFIG_HOME,
      PARSIDION_SCRIPTS_DIR: process.env.PARSIDION_SCRIPTS_DIR,
    }
    process.env.PARSIDION_SCRIPTS_DIR = REAL_SCRIPTS_DIR
    _clearResolutionCacheForTest()
  })

  afterEach(() => {
    cleanup()
    _clearResolutionCacheForTest()
    for (const [k, v] of Object.entries(savedEnv)) {
      if (v === undefined) delete (process.env as Record<string, string | undefined>)[k]
      else (process.env as Record<string, string | undefined>)[k] = v
    }
  })

  it('fixture parses with the expected version', () => {
    // The Python suite cross-checks this against gen_parity_fixtures.py's
    // VECTORS_VERSION; the TS side just pins the current value so a bump
    // is caught here too.
    expect(fixture.version).toBe(1)
    expect(allVectors.length).toBeGreaterThan(0)
  })

  it('no vector is silently skipped on the TypeScript side', () => {
    // Every vector must either run here or be recorded as python-only.
    // A new python-only vector forces a deliberate ack via this set.
    const ran = new Set(tsVectors.map(v => v.name))
    const excluded = new Set(excludedFromTs)
    const all = new Set(allVectors.map(v => v.name))
    expect(new Set([...ran, ...excluded])).toEqual(all)

    expect(new Set(excludedFromTs)).toEqual(
      new Set([
        'explicit-overrides-claude-vault-python',
        'explicit-overrides-cwd-marker-python',
        'cwd-marker-overrides-claude-vault-python',
        'claude-vault-overrides-default-python',
        'cwd-marker-empty-falls-through-python',
        'cwd-marker-missing-falls-through-python',
        'cwd-marker-arbitrary-falls-through-python',
        'claude-vault-arbitrary-falls-through-python',
        'claude-vault-forbidden-falls-through-python',
        'claude-vault-env-beats-config-default-python',
      ]),
    )
    // ARC-007: the four TS-only vectors are the inverse of the python-only
    // channel vectors above. They pin that the server resolver does NOT read
    // cwd/.claude/vault, CLAUDE_VAULT, and DOES honor a VAULT_ROOT override
    // the Python full-resolver ignores. Adding these channels later requires
    // updating this set.
  })

  for (const vec of tsVectors) {
    it.skipIf(!uvAvailable)(vec.name, async () => {
      materialize(vec)
      const explicit = vec.explicit !== undefined && vec.explicit !== null
        ? subst(vec.explicit)
        : undefined
      if (vec.expect_error !== undefined) {
        await expect(resolveVault(explicit ?? null)).rejects.toBeInstanceOf(
          VaultConfigError,
        )
      } else if (vec.expect !== undefined) {
        const result = await resolveVault(explicit ?? null)
        // Resolve both sides so macOS /private prefixing and symlinks
        // (e.g. /etc -> /private/etc) don't make equal paths differ.
        const expected = subst(vec.expect)
        expect(path.resolve(result)).toBe(path.resolve(expected))
      } else {
        throw new Error(`vector ${vec.name} has neither expect nor expect_error`)
      }
    }, 20000)
  }
})
