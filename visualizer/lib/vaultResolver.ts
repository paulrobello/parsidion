// lib/vaultResolver.ts
// Shared vault resolution logic for all API routes.
// Server-side only (uses fs, path).
//
// QA-012: This file duplicates the vault resolution logic from the Python
// vault_common.py:resolve_vault().  Both implementations must stay in sync.
// Long-term plan: serve vault resolution through the parsidion-mcp server
// so only the Python implementation is canonical.  See AUDIT.md [QA-012].

import fs from 'fs'
import path from 'path'

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
  const configDir = xdg
    ? path.join(xdg, 'parsidion')
    : path.join(process.env.HOME || '~', '.config', 'parsidion')
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
 * 3. Default vault (VAULT_ROOT env or ~/ClaudeVault)
 */
export function resolveVault(vaultName?: string | null): string {
  const home = process.env.HOME || '~'
  const defaultVault = process.env.VAULT_ROOT || path.join(home, 'ClaudeVault')

  if (!vaultName) {
    return defaultVault
  }

  // Try as named vault first
  const vaults = listNamedVaults()
  const named = vaults.find(v => v.name === vaultName)
  if (named) {
    return named.path
  }

  // Treat as path - expand ~ if present
  if (vaultName.startsWith('~')) {
    return path.join(home, vaultName.slice(1))
  }

  return vaultName
}

/**
 * Returns the default vault path without resolving a specific name.
 */
export function getDefaultVault(): string {
  const home = process.env.HOME || '~'
  return process.env.VAULT_ROOT || path.join(home, 'ClaudeVault')
}
