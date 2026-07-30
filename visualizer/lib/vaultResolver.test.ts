// lib/vaultResolver.test.ts
// ARC-016 / QA-004: this is the highest-value file in the repo with no test.
// vaultResolver.ts is the path-traversal defense for the visualizer API and
// the path resolver every route depends on. ARC-002 (silent data loss) is
// precisely the class of bug one test here would have caught — the contract
// was checked nowhere.
//
// Covers the seven cases flagged in ARC-016 step 1:
//   1. `..` traversal rejected (guardPath)
//   2. symlink escaping the vault rejected (guardPath)
//   3. VAULT_FORBIDDEN_PREFIXES enforced (validateVaultPath)
//   4. sibling directory (~/ParsidionVault-evil) rejected (startsWith check)
//   5. unknown vault name rejected by the allowlist (resolveVault)
//   6. realpathAllowingMissing: not-yet-created file inside vault OK, outside NOT
//   7. resolution precedence: explicit → .claude/vault → env → default
//
// vaultResolver.ts itself is owned by the 3b agent; this test file is the
// 3c half of ARC-016. The two layers compose: any code change to the resolver
// must keep these contracts intact.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import * as fs from 'fs'
import * as path from 'path'
import * as os from 'os'

import {
  resolveVault,
  guardPath,
  validateVaultPath,
  VaultConfigError,
  getDefaultVault,
  listNamedVaults,
  getVaultsConfigPath,
} from './vaultResolver'

let tmpHome: string
let originalHome: string | undefined
let originalVaultRoot: string | undefined
let originalXdg: string | undefined

function setup(): void {
  tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'vault-resolver-'))
  originalHome = process.env.HOME
  originalVaultRoot = process.env.VAULT_ROOT
  originalXdg = process.env.XDG_CONFIG_HOME
  process.env.HOME = tmpHome
  delete process.env.VAULT_ROOT
  delete process.env.XDG_CONFIG_HOME
}

function teardown(): void {
  if (originalHome === undefined) delete process.env.HOME
  else process.env.HOME = originalHome
  if (originalVaultRoot === undefined) delete process.env.VAULT_ROOT
  else process.env.VAULT_ROOT = originalVaultRoot
  if (originalXdg === undefined) delete process.env.XDG_CONFIG_HOME
  else process.env.XDG_CONFIG_HOME = originalXdg
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
  // Case 5: unknown vault name rejected by the allowlist
  // -------------------------------------------------------------------------
  describe('resolveVault — allowlist enforcement', () => {
    it('rejects an unknown vault name not in vaults.yaml', () => {
      expect(() => resolveVault('does-not-exist')).toThrow(VaultConfigError)
    })

    it('rejects an arbitrary filesystem path (not just a name)', () => {
      // Pre-SEC-001, any non-forbidden path resolved. Now: arbitrary paths
      // are rejected outright; only named entries (or the default) are allowed.
      expect(() => resolveVault('/')).toThrow(VaultConfigError)
      expect(() => resolveVault('~')).toThrow(VaultConfigError)
    })

    it('resolves a named vault that IS registered in vaults.yaml', () => {
      const named = path.join(tmpHome, 'MyNamedVault')
      fs.mkdirSync(named, { recursive: true })
      // Write a vaults.yaml under $XDG_CONFIG_HOME or $HOME/.config/parsidion
      const configDir = path.join(tmpHome, '.config', 'parsidion')
      fs.mkdirSync(configDir, { recursive: true })
      fs.writeFileSync(
        path.join(configDir, 'vaults.yaml'),
        `vaults:\n  my-named: ${named}\n`,
      )
      expect(resolveVault('my-named')).toBe(named)
    })

    it('falls back to the default vault when no name is given', () => {
      // Default is $HOME/ParsidionVault (no legacy ClaudeVault present here).
      expect(resolveVault(null)).toBe(path.join(tmpHome, 'ParsidionVault'))
      expect(resolveVault(undefined)).toBe(path.join(tmpHome, 'ParsidionVault'))
      expect(resolveVault('')).toBe(path.join(tmpHome, 'ParsidionVault'))
    })
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
  // Case 7: resolution precedence — explicit → .claude/vault → env → default
  // -------------------------------------------------------------------------
  describe('resolution precedence (ARC-016 step 1 final case)', () => {
    it('VAULT_ROOT env var beats HOME-based default', () => {
      process.env.VAULT_ROOT = path.join(tmpHome, 'EnvVault')
      expect(getDefaultVault()).toBe(path.join(tmpHome, 'EnvVault'))
    })

    it('HOME-based default selects ParsidionVault when neither legacy nor env set', () => {
      expect(getDefaultVault()).toBe(path.join(tmpHome, 'ParsidionVault'))
    })

    it('falls back to legacy ClaudeVault when ParsidionVault does not exist', () => {
      // Create ClaudeVault but NOT ParsidionVault — default flips to legacy.
      fs.mkdirSync(path.join(tmpHome, 'ClaudeVault'), { recursive: true })
      expect(getDefaultVault()).toBe(path.join(tmpHome, 'ClaudeVault'))
    })

    it('prefers ParsidionVault over legacy ClaudeVault when both exist', () => {
      fs.mkdirSync(path.join(tmpHome, 'ParsidionVault'), { recursive: true })
      fs.mkdirSync(path.join(tmpHome, 'ClaudeVault'), { recursive: true })
      expect(getDefaultVault()).toBe(path.join(tmpHome, 'ParsidionVault'))
    })

    it('resolveVault(unknown) does not consult VAULT_ROOT — allowlist still enforced', () => {
      // VAULT_ROOT changes the *default*; it does not add to the allowlist.
      // So an unknown name must still be rejected even with VAULT_ROOT set.
      process.env.VAULT_ROOT = path.join(tmpHome, 'EnvVault')
      expect(() => resolveVault('still-unknown')).toThrow(VaultConfigError)
    })

    it('resolveVault allows the default-vault PATH to be passed explicitly', () => {
      // The allowlist also accepts the default vault's own path (with ~ expansion),
      // so a client that knows the resolved path can pass it directly.
      const def = path.join(tmpHome, 'ParsidionVault')
      fs.mkdirSync(def, { recursive: true })
      expect(resolveVault(def)).toBe(def)
    })
  })

  // -------------------------------------------------------------------------
  // vaults.yaml parsing
  // -------------------------------------------------------------------------
  describe('listNamedVaults — vaults.yaml parsing', () => {
    it('returns empty when vaults.yaml is absent', () => {
      expect(listNamedVaults()).toEqual([])
    })

    it('returns empty when vaults.yaml has no vaults section', () => {
      const configDir = path.join(tmpHome, '.config', 'parsidion')
      fs.mkdirSync(configDir, { recursive: true })
      fs.writeFileSync(
        path.join(configDir, 'vaults.yaml'),
        '# nothing here\ndefault: ~/ParsidionVault\n',
      )
      expect(listNamedVaults()).toEqual([])
    })

    it('parses named vaults with ~ expansion', () => {
      const configDir = path.join(tmpHome, '.config', 'parsidion')
      fs.mkdirSync(configDir, { recursive: true })
      fs.writeFileSync(
        path.join(configDir, 'vaults.yaml'),
        [
          'vaults:',
          '  primary: ~/PrimaryVault',
          '  secondary: /tmp/SecondaryVault',
          '  quoted: "~/QuotedVault"',
          '',
        ].join('\n'),
      )
      const result = listNamedVaults()
      const names = result.map(v => v.name)
      expect(names).toEqual(['primary', 'secondary', 'quoted'])
      const primary = result.find(v => v.name === 'primary')!
      expect(primary.path).toBe(path.join(tmpHome, 'PrimaryVault'))
      const quoted = result.find(v => v.name === 'quoted')!
      expect(quoted.path).toBe(path.join(tmpHome, 'QuotedVault'))
    })

    it('getVaultsConfigPath honors XDG_CONFIG_HOME', () => {
      process.env.XDG_CONFIG_HOME = path.join(tmpHome, 'xdg')
      const p = getVaultsConfigPath()
      expect(p.startsWith(path.join(tmpHome, 'xdg'))).toBe(true)
    })

    it('getVaultsConfigPath falls back to legacy parsidion-cc dir', () => {
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
