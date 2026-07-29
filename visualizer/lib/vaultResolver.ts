// lib/vaultResolver.ts
// Shared vault resolution logic for all API routes.
// Server-side only (uses fs, path).
//
// QA-012: This file duplicates the vault resolution logic from the Python
// vault_common.py:resolve_vault().  Both implementations must stay in sync.
// Long-term plan: serve vault resolution through the parsidion-mcp server
// so only the Python implementation is canonical.  See AUDIT.md [QA-012].
//
// ARC-041: `import 'server-only'` makes the server-only-ness structural
// rather than convention-enforced. Next.js swaps this import for a throw at
// bundle time when a Client Component graph pulls this file in, so dropping
// the `import type` keyword in a client component can no longer silently
// drag `fs` (or, transitively via route.ts files, `child_process`) into the
// browser bundle.
import 'server-only'

import fs from 'fs'
import os from 'os'
import path from 'path'

// SEC-001: Mirror Python's _VAULT_FORBIDDEN_PREFIXES from vault_path.py.
// Prevents resolveVault() from pointing the vault into system directories or
// the Claude config tree.  Resolved at module load time so home-dir expansion
// happens once.
const _home = os.homedir()
const VAULT_FORBIDDEN_PREFIXES: readonly string[] = [
  path.resolve(_home, '.claude'),
  path.resolve('/System'),
  path.resolve('/usr'),
  path.resolve('/bin'),
  path.resolve('/sbin'),
  path.resolve('/etc'),
  path.resolve(_home, 'Library'),
]

/**
 * Error thrown when a vault path resolves to a forbidden location.
 * SEC-001: mirrors Python VaultConfigError raised by _validate_vault_path().
 */
export class VaultConfigError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'VaultConfigError'
  }
}

/**
 * Throws VaultConfigError if `resolved` falls under a forbidden prefix.
 * SEC-001: mirrors Python _validate_vault_path() in vault_path.py.
 *
 * @param resolved - Fully resolved (path.resolve'd) vault path to validate.
 * @throws {VaultConfigError} If the path is under a forbidden prefix.
 */
export function validateVaultPath(resolved: string): void {
  for (const prefix of VAULT_FORBIDDEN_PREFIXES) {
    if (resolved === prefix || resolved.startsWith(prefix + path.sep)) {
      throw new VaultConfigError(
        `Vault path resolves to a forbidden location: ${resolved}`
      )
    }
  }
}

export interface NamedVault {
  name: string
  path: string
}

/**
 * Returns the path to the vaults.yaml config file.
 * Follows XDG Base Directory specification.
 */
export function getVaultsConfigPath(): string {
  const xdg = process.env.XDG_CONFIG_HOME
  const home = process.env.HOME || '~'
  const configBase = xdg || path.join(home, '.config')
  let configDir = path.join(configBase, 'parsidion')

  if (!fs.existsSync(configDir)) {
    const legacyCandidates = [
      path.join(configBase, 'parsidion-cc'),
      path.join(home, '.parsidion-cc'),
    ]
    const legacyDir = legacyCandidates.find(candidate => fs.existsSync(candidate))
    if (legacyDir) {
      configDir = legacyDir
    }
  }

  return path.join(configDir, 'vaults.yaml')
}

/**
 * Parses vaults.yaml and returns a list of named vaults.
 * Returns empty array if config doesn't exist or is invalid.
 */
export function listNamedVaults(): NamedVault[] {
  const configPath = getVaultsConfigPath()

  if (!fs.existsSync(configPath)) {
    return []
  }

  const content = fs.readFileSync(configPath, 'utf-8')
  const vaults: NamedVault[] = []
  const home = process.env.HOME || '~'

  let inVaultsSection = false

  for (const line of content.split('\n')) {
    const stripped = line.trim()

    // Skip empty lines and comments
    if (!stripped || stripped.startsWith('#')) {
      continue
    }

    // Detect start of vaults section
    if (stripped === 'vaults:') {
      inVaultsSection = true
      continue
    }

    // End of vaults section (unindented non-empty line)
    if (inVaultsSection && !line.startsWith(' ') && !line.startsWith('\t')) {
      break
    }

    // Parse vault entry: "name: path" or "name:" (with path on next line)
    if (inVaultsSection && stripped.includes(':')) {
      const colonIdx = stripped.indexOf(':')
      const name = stripped.slice(0, colonIdx).trim()
      let vaultPath = stripped.slice(colonIdx + 1).trim()

      // Remove quotes if present
      if ((vaultPath.startsWith('"') && vaultPath.endsWith('"')) ||
          (vaultPath.startsWith("'") && vaultPath.endsWith("'"))) {
        vaultPath = vaultPath.slice(1, -1)
      }

      if (name && vaultPath) {
        // Expand ~ to home directory
        const expandedPath = vaultPath.startsWith('~')
          ? path.join(home, vaultPath.slice(1))
          : vaultPath

        vaults.push({ name, path: expandedPath })
      }
    }
  }

  return vaults
}

/**
 * Resolves a vault name or path to an absolute vault path.
 * Falls back to the default vault if no vault is specified.
 *
 * Resolution is an allowlist: `vaultName` must match either a named vault
 * from vaults.yaml or the default vault. Arbitrary filesystem paths (e.g.
 * "/", "~", "$HOME/Documents") are rejected outright, even when they don't
 * hit VAULT_FORBIDDEN_PREFIXES — previously this was a denylist that let any
 * non-forbidden path through, so any string with `vault=` in it (like the
 * bare home directory) resolved and could be walked.
 *
 * The fully-resolved path is additionally validated against
 * VAULT_FORBIDDEN_PREFIXES as defense in depth (e.g. local misconfiguration
 * of VAULT_ROOT).
 *
 * @throws {VaultConfigError} If `vaultName` doesn't match a known vault, or
 *   the resolved path is under a forbidden prefix.
 */
export function resolveVault(vaultName?: string | null): string {
  const home = process.env.HOME || _home

  if (!vaultName) {
    const resolved = getDefaultVault()
    validateVaultPath(path.resolve(resolved))
    return resolved
  }

  const named = listNamedVaults().find(v => v.name === vaultName)
  if (named) {
    validateVaultPath(path.resolve(named.path))
    return named.path
  }

  // Allow the default vault to be referenced by its own path (expanding ~
  // the same way named-vault paths are expanded), not just by omitting the
  // vault name.
  const expanded = vaultName.startsWith('~') ? path.join(home, vaultName.slice(1)) : vaultName
  const defaultVault = getDefaultVault()
  if (path.resolve(expanded) === path.resolve(defaultVault)) {
    validateVaultPath(path.resolve(defaultVault))
    return defaultVault
  }

  throw new VaultConfigError(`Unknown vault: ${vaultName}`)
}

/**
 * Best-effort fs.realpathSync — falls back to the input path if it doesn't
 * exist (or can't be read) rather than throwing.
 */
function realpathOrSelf(p: string): string {
  try {
    return fs.realpathSync(p)
  } catch {
    return p
  }
}

/**
 * Resolves `p` to its real (symlink-free) path. For paths that don't exist
 * yet (e.g. a note being created via PUT), walks up to the nearest existing
 * ancestor, realpaths that, and reattaches the nonexistent remainder — so
 * symlinks in the existing portion of the path are still caught while the
 * not-yet-created target itself doesn't need to exist.
 */
function realpathAllowingMissing(p: string): string {
  let target = path.resolve(p)
  const missingSuffix: string[] = []

  while (true) {
    try {
      const real = fs.realpathSync(target)
      return missingSuffix.length > 0 ? path.join(real, ...missingSuffix) : real
    } catch {
      const parent = path.dirname(target)
      if (parent === target) return target // hit filesystem root; give up
      missingSuffix.unshift(path.basename(target))
      target = parent
    }
  }
}

/**
 * Returns true if `notePath` is strictly inside `vaultRoot`.
 * SEC-012: Shared path-traversal guard extracted from route files to avoid
 * copy-paste drift.  All route files import and call this instead of defining
 * their own `guardPath` helper.
 *
 * Resolves real (symlink-free) paths before the containment check, so a
 * symlink inside the vault that points outside of it (e.g. carried in via a
 * shared/git-synced vault) can't be used to escape the vault root.
 *
 * @param notePath  - Absolute path to the note or file being accessed.
 * @param vaultRoot - Absolute vault root path.
 */
export function guardPath(notePath: string, vaultRoot: string): boolean {
  const resolvedRoot = realpathOrSelf(path.resolve(vaultRoot))
  const resolved = realpathAllowingMissing(notePath)
  return resolved === resolvedRoot || resolved.startsWith(resolvedRoot + path.sep)
}

/**
 * Returns the default vault path without resolving a specific name.
 */
export function getDefaultVault(): string {
  const home = process.env.HOME || '~'

  if (process.env.VAULT_ROOT) {
    return process.env.VAULT_ROOT
  }

  const current = path.join(home, 'ParsidionVault')
  const legacy = path.join(home, 'ClaudeVault')
  if (fs.existsSync(legacy) && !fs.existsSync(current)) {
    return legacy
  }
  return current
}
