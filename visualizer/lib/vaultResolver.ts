// lib/vaultResolver.ts
// Shared vault resolution logic for all API routes.
// Server-side only (uses fs, path).
//
// QA-012: This file duplicates the vault resolution logic from the Python
// vault_common.py:resolve_vault().  Both implementations must stay in sync.
// Long-term plan: serve vault resolution through the parsidion-mcp server
// so only the Python implementation is canonical.  See AUDIT.md [QA-012].

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
 * Resolution order:
 * 1. Named vault from vaults.yaml
 * 2. Treat as path directly
 * 3. Default vault (VAULT_ROOT env, ~/ParsidionVault, or legacy ~/ClaudeVault)
 *
 * SEC-001: After resolution the path is validated against
 * VAULT_FORBIDDEN_PREFIXES.  Throws VaultConfigError for forbidden paths.
 *
 * @throws {VaultConfigError} If the resolved path is under a forbidden prefix.
 */
export function resolveVault(vaultName?: string | null): string {
  const home = process.env.HOME || _home

  let resolved: string

  if (!vaultName) {
    resolved = getDefaultVault()
  } else {
    // Try as named vault first
    const vaults = listNamedVaults()
    const named = vaults.find(v => v.name === vaultName)
    if (named) {
      resolved = named.path
    } else if (vaultName.startsWith('~')) {
      // Treat as path - expand ~ if present
      resolved = path.join(home, vaultName.slice(1))
    } else {
      resolved = vaultName
    }
  }

  // SEC-001: Validate the fully-resolved path against the forbidden-prefix list.
  validateVaultPath(path.resolve(resolved))
  return resolved
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
