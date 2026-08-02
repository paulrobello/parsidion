// lib/vaultResolver.ts
// Shared vault resolution logic for all API routes.
// Server-side only (uses fs, path, child_process via runScript).
//
// ENH-009: resolution is now Python-canonical. resolveVault / getDefaultVault
// / listNamedVaults are thin async subprocess callers over the stdlib
// vault_resolve.py CLI (which wraps core.vault_path.resolve_vault_server), so
// the allowlist algorithm has exactly ONE implementation -- the former
// TypeScript reimplementation is gone (was QA-012 / ARC-007 / SEC-P001). A
// promise cache invalidated on vaults.yaml mtime means the subprocess runs at
// most once per vault name per server lifetime.
//
// guardPath / validateVaultPath / the realpath* helpers are NOT vault
// resolution -- they are HTTP-input containment checks (does this note path
// the client sent fall inside the already-resolved vault root?), so they stay
// here, untouched. Python never sees request paths.
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

import { findParsidionScript } from './scriptResolver'
import { runScript, ScriptFailedError } from './runScript'

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
 * Error thrown when a vault path resolves to a forbidden location, or when a
 * vault reference is not a known named/default vault.
 * SEC-001: mirrors Python VaultConfigError raised by _validate_vault_path()
 * / resolve_vault_server().
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
 * Defense in depth on the TypeScript side; canonical validation lives in
 * Python's resolve_vault_server() and runs on every delegated resolution.
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
 * Follows XDG Base Directory specification. Used to stat the file for cache
 * invalidation; the resolution itself is delegated to Python.
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
 * Best-effort fs.realpathSync—falls back to the input path if it doesn't exist
 * (or can't be read) rather than throwing.
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
 * ancestor, realpaths that, and reattaches the nonexistent remainder—so
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

// ---------------------------------------------------------------------------
// ENH-009: Python-canonical resolution. Everything below delegates to
// vault_resolve.py (which wraps core.vault_path.resolve_vault_server) so the
// resolution algorithm is single-sourced in Python.
// ---------------------------------------------------------------------------

/** Hard cap for the resolver subprocess. Resolution is cheap; 5s is generous. */
const RESOLVE_TIMEOUT_MS = 5_000

interface ServerVaultList {
  default: string
  named: NamedVault[]
}

/** Signature of the resolution inputs when the caches were last populated. */
let _cachedConfigSig: string | null = null
/** key `${name}` (empty string = default) -> resolved-path promise. */
const _resolveCache = new Map<string, Promise<string>>()
/** cached `--list` result promise. */
let _listPromise: Promise<ServerVaultList> | null = null

function yamlMtime(): number {
  try {
    return fs.statSync(getVaultsConfigPath()).mtimeMs
  } catch {
    return 0
  }
}

/**
 * Fingerprint of every input resolution can depend on: the vaults.yaml
 * mtime (named-vault set), HOME (default vault root), and VAULT_ROOT (default
 * override). The caches are dropped whenever this changes, so a changed HOME
 * or VAULT_ROOT never yields a stale cached path.
 */
function configSignature(): string {
  return `${yamlMtime()}:${process.env.HOME ?? ''}:${process.env.VAULT_ROOT ?? ''}`
}

/** Drop both caches when any resolution input changes. */
function invalidateIfStale(): void {
  const sig = configSignature()
  if (sig !== _cachedConfigSig) {
    _cachedConfigSig = sig
    _resolveCache.clear()
    _listPromise = null
  }
}

/**
 * Test-only: drop the resolution caches so cases that repoint HOME / rewrite
 * vaults.yaml start from a clean slate. Mirrors Python's
 * `resolve_vault.cache_clear()`. Not part of the public API.
 */
export function _clearResolutionCacheForTest(): void {
  _cachedConfigSig = null
  _resolveCache.clear()
  _listPromise = null
}

function scriptNotFound(): ScriptFailedError {
  return new ScriptFailedError(
    'vault_resolve.py not found. Install the parsidion skill or run the visualizer from the source repo.',
    '',
    null,
  )
}

/**
 * Spawn vault_resolve.py with the given args and return its trimmed stdout.
 * Exit code 1 is the script's contract for "not a known vault" (Python
 * VaultConfigError) and is re-thrown as the TS {@link VaultConfigError} so
 * routes map it to HTTP 400. Any other failure (missing script, timeout,
 * unexpected exit) surfaces as {@link ScriptFailedError} -> HTTP 500.
 */
async function runResolveScript(args: readonly string[]): Promise<string> {
  const script = findParsidionScript('vault_resolve.py')
  if (!script) throw scriptNotFound()
  try {
    const { stdout } = await runScript('uv', ['run', '--no-project', script, ...args], {
      timeoutMs: RESOLVE_TIMEOUT_MS,
    })
    return stdout.trim()
  } catch (err) {
    if (err instanceof ScriptFailedError && err.exitCode === 1) {
      throw new VaultConfigError(err.stderr.trim() || 'vault resolution failed')
    }
    throw err
  }
}

/**
 * Resolves a vault name or path to an absolute vault path by delegating to
 * Python's canonical resolver. Falls back to the default vault when no vault
 * is specified.
 *
 * @throws {VaultConfigError} If `vaultName` doesn't match a known vault, or
 *   the resolved path is under a forbidden prefix (validated in Python).
 */
export async function resolveVault(vaultName?: string | null): Promise<string> {
  invalidateIfStale()
  const key = vaultName ?? ''
  const cached = _resolveCache.get(key)
  if (cached) return cached

  const promise = runResolveScript(key ? [key] : [])
  // Don't cache rejections: a transient failure (e.g. timeout) should retry.
  promise.catch(() => {
    if (_resolveCache.get(key) === promise) _resolveCache.delete(key)
  })
  _resolveCache.set(key, promise)
  return promise
}

/**
 * Returns the default vault path without resolving a specific name, by
 * delegating to Python. (Reuses the default-vault cache entry.)
 */
export async function getDefaultVault(): Promise<string> {
  invalidateIfStale()
  return resolveVault(undefined)
}

async function loadVaultList(): Promise<ServerVaultList> {
  invalidateIfStale()
  if (_listPromise) return _listPromise

  const promise = (async () => {
    const script = findParsidionScript('vault_resolve.py')
    if (!script) throw scriptNotFound()
    try {
      const { stdout } = await runScript(
        'uv',
        ['run', '--no-project', script, '--list'],
        { timeoutMs: RESOLVE_TIMEOUT_MS },
      )
      const parsed = JSON.parse(stdout) as {
        default: string
        named: { name: string; path: string }[]
      }
      return {
        default: parsed.default,
        named: parsed.named.map(v => ({ name: v.name, path: v.path })),
      }
    } catch (err) {
      if (err instanceof ScriptFailedError && err.exitCode === 1) {
        throw new VaultConfigError(err.stderr.trim() || 'vault resolution failed')
      }
      throw err
    }
  })()

  _listPromise = promise
  promise.catch(() => {
    if (_listPromise === promise) _listPromise = null
  })
  return promise
}

/**
 * Lists named vaults from vaults.yaml by delegating to Python.
 */
export async function listNamedVaults(): Promise<NamedVault[]> {
  return (await loadVaultList()).named
}
