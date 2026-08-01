// lib/vaultResolver.parity.test.ts
// ENH-005: the TypeScript half of the shared vault-resolution parity contract.
//
// Reads the SAME fixture as the Python suite
// (tests/fixtures/parity/vault-resolution.json) and asserts resolveVault()
// agrees with every TypeScript-applicable vector. The Python half lives at
// tests/test_vault_resolver_parity.py. Neither language owns the test data;
// both consume it, so a vector added on one side forces an ack on the other.
//
// The two resolvers are NOT identical (see the fixture's $comment): Python is
// a 4-channel path-or-name resolver (explicit / cwd/.claude/vault /
// CLAUDE_VAULT / default); TypeScript is an allowlist resolver (named vaults
// or the default path) with a VAULT_ROOT default override. Vectors scoped to
// channels that only exist on one side carry `applies_to`, and this suite
// asserts every vector is either run or explicitly excluded -- no silent skips.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import * as fs from 'fs'
import * as path from 'path'
import * as os from 'os'

import { resolveVault, VaultConfigError } from './vaultResolver'

const FIXTURE = path.join(
  import.meta.dir,
  '..',
  '..',
  'tests',
  'fixtures',
  'parity',
  'vault-resolution.json',
)

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
  tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'parity-'))

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

describe('ENH-005 — vault-resolution parity (shared fixture)', () => {
  beforeEach(() => {
    savedEnv = {
      HOME: process.env.HOME,
      CLAUDE_VAULT: process.env.CLAUDE_VAULT,
      VAULT_ROOT: process.env.VAULT_ROOT,
      XDG_CONFIG_HOME: process.env.XDG_CONFIG_HOME,
    }
  })

  afterEach(() => {
    cleanup()
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
      ]),
    )
    // ARC-007: the four TS-only vectors are the inverse of the python-only
    // channel vectors above. They pin that the TS resolver does NOT read
    // cwd/.claude/vault, CLAUDE_VAULT, or use a VAULT_ROOT override the
    // Python side ignores. Adding these channels to TS later requires
    // updating this set.
  })

  for (const vec of tsVectors) {
    it(vec.name, () => {
      materialize(vec)
      const explicit = vec.explicit !== undefined && vec.explicit !== null
        ? subst(vec.explicit)
        : undefined
      if (vec.expect_error !== undefined) {
        expect(() => resolveVault(explicit ?? null)).toThrow(VaultConfigError)
      } else if (vec.expect !== undefined) {
        const result = resolveVault(explicit ?? null)
        // Resolve both sides so macOS /private prefixing and symlinks
        // (e.g. /etc -> /private/etc) don't make equal paths differ.
        const expected = subst(vec.expect)
        expect(path.resolve(result)).toBe(path.resolve(expected))
      } else {
        throw new Error(`vector ${vec.name} has neither expect nor expect_error`)
      }
    })
  }
})
