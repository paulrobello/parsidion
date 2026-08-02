// lib/vaultResolver.test.ts
// ARC-016 / QA-004: this is the highest-value file in the repo with no test.
// vaultResolver.ts is the path-traversal defense for the visualizer API and
// the path resolver every route depends on. ARC-002 (silent data loss) is
// precisely the class of bug one test here would have caught — the contract
// was checked nowhere.
//
// Two layers of coverage:
//   - guardPath / validateVaultPath / getVaultsConfigPath are pure, server-side
//     path-traversal defenses. They are tested hermetically (no subprocess).
//   - resolveVault / getDefaultVault / listNamedVaults now DELEGATE to the
//     Python vault_resolve.py CLI (ENH-009). Their happy paths are exercised
//     end-to-end through the real script, gated on `uv` being installed (the
//     visualizer CI job intentionally installs no Python toolchain — same
//     convention as searchServer.test.ts). The error path (missing script) is
//     always covered.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import * as fs from 'fs'
import * as path from 'path'
import * as os from 'os'
import { spawnSync } from 'child_process'

import {
  resolveVault,
  guardPath,
  validateVaultPath,
  VaultConfigError,
  getDefaultVault,
  listNamedVaults,
  getVaultsConfigPath,
  _clearResolutionCacheForTest,
} from './vaultResolver'
import { ScriptFailedError } from './runScript'

// The real scripts dir (skills/parsidion/scripts) resolved from this test file
// (visualizer/lib). Pointing PARSIDION_SCRIPTS_DIR here makes
// findParsidionScript('vault_resolve.py') deterministic regardless of cwd or
// whether the skill is installed.
const REAL_SCRIPTS_DIR = path.join(
  import.meta.dir,
  '..',
  '..',
  'skills',
  'parsidion',
  'scripts',
)

// uv-gated tests need the Python toolchain the visualizer CI job omits.
const uvAvailable =
  spawnSync('uv', ['--version'], { stdio: 'ignore' }).status === 0

let tmpHome: string
// Python's resolver returns .resolve()'d paths, which on macOS resolves the
// /var -> /private/var tmp symlink. Build expected paths from the realpath so
// the comparison agrees (same /private-prefixing caveat the parity suite notes).
let realHome: string
let originalHome: string | undefined
let originalVaultRoot: string | undefined
let originalXdg: string | undefined
let originalScriptsDir: string | undefined

function setup(): void {
  tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'vault-resolver-'))
  realHome = fs.realpathSync(tmpHome)
  originalHome = process.env.HOME
  originalVaultRoot = process.env.VAULT_ROOT
  originalXdg = process.env.XDG_CONFIG_HOME
  originalScriptsDir = process.env.PARSIDION_SCRIPTS_DIR
  process.env.HOME = tmpHome
  process.env.PARSIDION_SCRIPTS_DIR = REAL_SCRIPTS_DIR
  delete process.env.VAULT_ROOT
  delete process.env.XDG_CONFIG_HOME
  // Each case repoints HOME / rewrites vaults.yaml; never inherit a cached
  // resolution from a prior case.
  _clearResolutionCacheForTest()
}

function teardown(): void {
  if (originalHome === undefined) delete process.env.HOME
  else process.env.HOME = originalHome
  if (originalVaultRoot === undefined) delete process.env.VAULT_ROOT
  else process.env.VAULT_ROOT = originalVaultRoot
  if (originalXdg === undefined) delete process.env.XDG_CONFIG_HOME
  else process.env.XDG_CONFIG_HOME = originalXdg
  if (originalScriptsDir === undefined) delete process.env.PARSIDION_SCRIPTS_DIR
  else process.env.PARSIDION_SCRIPTS_DIR = originalScriptsDir
  _clearResolutionCacheForTest()
  try {
    fs.rmSync(tmpHome, { recursive: true, force: true })
  } catch {
    /* best effort */
  }
}

describe('ARC-016 / QA-004 — vaultResolver.ts path-traversal defense', () => {
  beforeEach(() => setup())
  afterEach(() => teardown())

  // -------------------------------------------------------------------------
  // Case 3: VAULT_FORBIDDEN_PREFIXES enforced
  // -------------------------------------------------------------------------
  describe('validateVaultPath — VAULT_FORBIDDEN_PREFIXES', () => {
    // vaultResolver.ts caches `_home = os.homedir()` at module load time and
    // builds VAULT_FORBIDDEN_PREFIXES from it. Setting HOME dynamically does
    // not change the cached value, so home-relative prefix tests must use the
    // real os.homedir() rather than tmpHome.
    it('rejects the Claude config tree (~/.claude)', () => {
      expect(() => validateVaultPath(path.join(os.homedir(), '.claude'))).toThrow(
        VaultConfigError,
      )
    })

    it('rejects a path INSIDE ~/.claude (prefix + path.sep)', () => {
      expect(() =>
        validateVaultPath(path.join(os.homedir(), '.claude', 'skills', 'evil')),
      ).toThrow(VaultConfigError)
    })

    it('rejects /System, /usr, /bin, /sbin, /etc', () => {
      for (const p of ['/System', '/usr', '/bin', '/sbin', '/etc']) {
        expect(() => validateVaultPath(p)).toThrow(VaultConfigError)
      }
    })

    it('rejects ~/Library', () => {
      expect(() => validateVaultPath(path.join(os.homedir(), 'Library'))).toThrow(
        VaultConfigError,
      )
    })

    it('passes a normal vault path', () => {
      const vault = path.join(tmpHome, 'MyVault')
      expect(() => validateVaultPath(vault)).not.toThrow()
    })

    it('passes a path that merely PREFIX-MATCHES a forbidden entry without a separator', () => {
      // /etc is forbidden; /etc-vault is not (no path.sep between). This
      // pins the startsWith(prefix + path.sep) discipline — a naive
      // startsWith(prefix) check would have rejected /etc-vault too, which
      // would be a regression.
      expect(() => validateVaultPath('/etc-vault')).not.toThrow()
    })
  })

  // -------------------------------------------------------------------------
  // Case 4: sibling directory rejected
  // -------------------------------------------------------------------------
  describe('guardPath — sibling directory (~/ParsidionVault-evil)', () => {
    it('rejects a sibling of the vault root that prefix-matches its name', () => {
      const vault = path.join(tmpHome, 'ParsidionVault')
      const evil = path.join(tmpHome, 'ParsidionVault-evil')
      fs.mkdirSync(vault, { recursive: true })
      fs.mkdirSync(evil, { recursive: true })
      // guardPath(notePath, vaultRoot) — evil must not be considered inside vault
      // even though it PREFIX-MATCHES without a separator.
      expect(guardPath(path.join(evil, 'secret.md'), vault)).toBe(false)
    })
  })

  // -------------------------------------------------------------------------
  // Case 1: `..` traversal rejected
  // -------------------------------------------------------------------------
  describe('guardPath — `..` traversal', () => {
    it('rejects a path that escapes via ..', () => {
      const vault = path.join(tmpHome, 'ParsidionVault')
      fs.mkdirSync(vault, { recursive: true })
      const escape = path.join(vault, '..', 'escape.md')
      expect(guardPath(escape, vault)).toBe(false)
    })

    it('accepts a path strictly inside the vault', () => {
      const vault = path.join(tmpHome, 'ParsidionVault')
      const note = path.join(vault, 'Patterns', 'my-note.md')
      fs.mkdirSync(path.dirname(note), { recursive: true })
      fs.writeFileSync(note, '# x\n')
      expect(guardPath(note, vault)).toBe(true)
    })

    it('accepts the vault root itself', () => {
      const vault = path.join(tmpHome, 'ParsidionVault')
      fs.mkdirSync(vault, { recursive: true })
      expect(guardPath(vault, vault)).toBe(true)
    })
  })

  // -------------------------------------------------------------------------
  // Case 2: symlink escaping the vault rejected
  // -------------------------------------------------------------------------
  describe('guardPath — symlink escape', () => {
    it('rejects an in-vault symlink whose target is outside the vault', () => {
      const vault = path.join(tmpHome, 'ParsidionVault')
      const outside = path.join(tmpHome, 'outside.txt')
      const link = path.join(vault, 'link.md')
      fs.mkdirSync(vault, { recursive: true })
      fs.writeFileSync(outside, 'secret\n')
      try {
        fs.symlinkSync(outside, link)
        // realpathSync resolves the symlink → outside; guardPath must reject
        expect(guardPath(link, vault)).toBe(false)
      } catch (err) {
        // Some sandboxes disallow symlinks; skip rather than fail in that env.
        if ((err as NodeJS.ErrnoException).code === 'EPERM') return
        throw err
      }
    })

    it('accepts a symlink whose target is inside the vault', () => {
      const vault = path.join(tmpHome, 'ParsidionVault')
      const real = path.join(vault, 'real.md')
      const link = path.join(vault, 'link.md')
      fs.mkdirSync(vault, { recursive: true })
      fs.writeFileSync(real, '# x\n')
      try {
        fs.symlinkSync(real, link)
        expect(guardPath(link, vault)).toBe(true)
      } catch (err) {
        if ((err as NodeJS.ErrnoException).code === 'EPERM') return
        throw err
      }
    })
  })

  // -------------------------------------------------------------------------
  // Cases 5 & 7 + listNamedVaults: resolution now delegates to Python's
  // vault_resolve.py (ENH-009). Happy paths run end-to-end through the real
  // script where uv is available; the missing-script error path always runs.
  // -------------------------------------------------------------------------
  describe('resolveVault — Python delegation (ENH-009)', () => {
    // The unknown-name / arbitrary-path rejections need the Python script to
    // run (it emits exit 1 -> VaultConfigError). The missing-script path does
    // not reach uv, so it always runs.
    it.skipIf(!uvAvailable)(
      'rejects an unknown vault name with VaultConfigError',
      async () => {
        await expect(resolveVault('does-not-exist')).rejects.toBeInstanceOf(
          VaultConfigError,
        )
      },
      20000,
    )

    it.skipIf(!uvAvailable)(
      'rejects an arbitrary filesystem path (not just a name)',
      async () => {
        await expect(resolveVault('/')).rejects.toBeInstanceOf(VaultConfigError)
        await expect(resolveVault('~')).rejects.toBeInstanceOf(VaultConfigError)
      },
      20000,
    )

    it('rejects when the resolver script is missing (no Python toolchain)', async () => {
      // Point at an empty dir so findParsidionScript returns null. This runs
      // everywhere (CI included) — it exercises the scriptNotFound path that
      // does not need uv.
      process.env.PARSIDION_SCRIPTS_DIR = path.join(tmpHome, 'no-scripts-here')
      await expect(resolveVault(null)).rejects.toBeInstanceOf(ScriptFailedError)
    })

    it.skipIf(!uvAvailable)(
      'resolves the default vault when no name is given',
      async () => {
        // Default is $HOME/ParsidionVault (no legacy ClaudeVault present here).
        expect(await resolveVault(null)).toBe(path.join(realHome, 'ParsidionVault'))
        expect(await resolveVault(undefined)).toBe(path.join(realHome, 'ParsidionVault'))
        expect(await resolveVault('')).toBe(path.join(realHome, 'ParsidionVault'))
      },
      20000,
    )

    it.skipIf(!uvAvailable)(
      'resolves a named vault registered in vaults.yaml',
      async () => {
        const named = path.join(tmpHome, 'MyNamedVault')
        fs.mkdirSync(named, { recursive: true })
        const configDir = path.join(tmpHome, '.config', 'parsidion')
        fs.mkdirSync(configDir, { recursive: true })
        fs.writeFileSync(
          path.join(configDir, 'vaults.yaml'),
          `vaults:\n  my-named: ${named}\n`,
        )
        // Python .resolve()s the registered path, so compare to the realpath.
        expect(await resolveVault('my-named')).toBe(path.join(realHome, 'MyNamedVault'))
      },
      20000,
    )
  })

  describe('resolution precedence & listing (ENH-009)', () => {
    it.skipIf(!uvAvailable)(
      'VAULT_ROOT env var beats HOME-based default',
      async () => {
        process.env.VAULT_ROOT = path.join(realHome, 'EnvVault')
        expect(await getDefaultVault()).toBe(path.join(realHome, 'EnvVault'))
      },
      20000,
    )

    it.skipIf(!uvAvailable)(
      'HOME-based default selects ParsidionVault when neither legacy nor env set',
      async () => {
        expect(await getDefaultVault()).toBe(path.join(realHome, 'ParsidionVault'))
      },
      20000,
    )

    it.skipIf(!uvAvailable)(
      'falls back to legacy ClaudeVault when ParsidionVault does not exist',
      async () => {
        fs.mkdirSync(path.join(tmpHome, 'ClaudeVault'), { recursive: true })
        expect(await getDefaultVault()).toBe(path.join(realHome, 'ClaudeVault'))
      },
      20000,
    )

    it.skipIf(!uvAvailable)(
      'resolveVault(unknown) does not consult VAULT_ROOT — allowlist still enforced',
      async () => {
        // VAULT_ROOT changes the *default*; it does not add to the allowlist.
        process.env.VAULT_ROOT = path.join(tmpHome, 'EnvVault')
        await expect(resolveVault('still-unknown')).rejects.toBeInstanceOf(
          VaultConfigError,
        )
      },
      20000,
    )

    it.skipIf(!uvAvailable)(
      'listNamedVaults returns vaults registered in vaults.yaml',
      async () => {
        const configDir = path.join(tmpHome, '.config', 'parsidion')
        fs.mkdirSync(configDir, { recursive: true })
        fs.writeFileSync(
          path.join(configDir, 'vaults.yaml'),
          ['vaults:', '  primary: ~/PrimaryVault', '  secondary: /tmp/SecondaryVault', ''].join(
            '\n',
          ),
        )
        const result = await listNamedVaults()
        expect(result.map(v => v.name)).toEqual(['primary', 'secondary'])
      },
      20000,
    )
  })

  // -------------------------------------------------------------------------
  // Case 6: realpathAllowingMissing
  // -------------------------------------------------------------------------
  describe('guardPath — not-yet-created note inside the vault', () => {
    it('accepts a missing file that WOULD be inside the vault if created', () => {
      // This is the PUT create-note path: the file doesn't exist yet but we
      // still need to allow it. realpathAllowingMissing walks up to the nearest
      // existing ancestor, realpaths that, and reattaches the missing suffix.
      const vault = path.join(tmpHome, 'ParsidionVault')
      const note = path.join(vault, 'Patterns', 'new-note.md')
      fs.mkdirSync(vault, { recursive: true })
      fs.mkdirSync(path.join(vault, 'Patterns'), { recursive: true })
      expect(guardPath(note, vault)).toBe(true)
    })

    it('rejects a missing file that would be OUTSIDE the vault if created', () => {
      // Adversary creates a deep path under /; the not-yet-existing segments
      // are still on the wrong side of the vault boundary once the ancestor
      // realpath is computed.
      const vault = path.join(tmpHome, 'ParsidionVault')
      const escape = path.join(vault, '..', '..', 'evil.md')
      fs.mkdirSync(vault, { recursive: true })
      expect(guardPath(escape, vault)).toBe(false)
    })
  })

  // -------------------------------------------------------------------------
  // vaults.yaml config-path resolution (pure; no Python)
  // -------------------------------------------------------------------------
  describe('getVaultsConfigPath — XDG resolution', () => {
    it('honors XDG_CONFIG_HOME', () => {
      process.env.XDG_CONFIG_HOME = path.join(tmpHome, 'xdg')
      const p = getVaultsConfigPath()
      expect(p.startsWith(path.join(tmpHome, 'xdg'))).toBe(true)
    })

    it('falls back to legacy parsidion-cc dir', () => {
      // When XDG is unset and ~/.config/parsidion doesn't exist but
      // ~/.config/parsidion-cc does, use the legacy dir.
      const legacy = path.join(tmpHome, '.config', 'parsidion-cc')
      fs.mkdirSync(legacy, { recursive: true })
      delete process.env.XDG_CONFIG_HOME
      const p = getVaultsConfigPath()
      expect(p.startsWith(legacy)).toBe(true)
    })
  })
})
