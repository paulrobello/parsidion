// lib/env.ts
// SEC-P002: allowlist-based env filter for child processes spawned by the
// visualizer server. Mirrors the Python `_SAFE_ENV_KEYS` contract in
// skills/parsidion/scripts/core/vault_hooks.py so the same set of Anthropic
// auth / routing / proxy vars is forwarded on both runtimes, and every other
// variable (including `CLAUDECODE`, which trips Claude's nesting guard, and
// any unrelated secret the dev server happens to carry) is dropped.
//
// If you update the allowlist here, update `_SAFE_ENV_KEYS` in the Python
// twin as well — the parity is intentional.

/**
 * Env vars forwarded to summarizer / vault-stats subprocesses.
 *
 * Keep in sync with `_SAFE_ENV_KEYS` in
 * `skills/parsidion/scripts/core/vault_hooks.py`.
 */
const SAFE_ENV_KEYS: readonly string[] = [
  // Shell / locale / process basics
  'PATH',
  'HOME',
  'USER',
  'SHELL',
  'TERM',
  'LANG',
  'LC_ALL',
  'TMPDIR',
  // Anthropic API auth & routing
  'ANTHROPIC_API_KEY',
  'ANTHROPIC_AUTH_TOKEN',
  'ANTHROPIC_BASE_URL',
  'ANTHROPIC_CUSTOM_HEADERS',
  // Model pinning
  'ANTHROPIC_DEFAULT_HAIKU_MODEL',
  'ANTHROPIC_DEFAULT_SONNET_MODEL',
  'ANTHROPIC_DEFAULT_OPUS_MODEL',
  // API timeout
  'API_TIMEOUT_MS',
  // Network proxy
  'HTTPS_PROXY',
  'HTTP_PROXY',
  // par-mem code-memory daemon endpoint (URL, never a secret)
  'PARMEM_MCP_URL',
] as const

const SAFE_ENV_SET: ReadonlySet<string> = new Set(SAFE_ENV_KEYS)

/**
 * A minimal env shape: string keys → string-or-undefined values. Used instead
 * of `NodeJS.ProcessEnv` because Next.js augments the latter to require
 * `NODE_ENV`, which would force every caller and test fixture to set it.
 */
export type EnvRecord = Record<string, string | undefined>

/**
 * Return a filtered copy of the source env for child processes spawned by
 * the visualizer (summarizer, vault-stats). Only the vars in
 * `SAFE_ENV_KEYS` are forwarded; everything else — including `CLAUDECODE`
 * and any unrelated secret the parent process carries — is dropped.
 *
 * Mirrors `env_without_claudecode()` in
 * `skills/parsidion/scripts/core/vault_hooks.py`.
 *
 * Defaults to `process.env` so the common call site is
 * `spawn(..., { env: envWithoutClaudecode() })`.
 *
 * Returns `NodeJS.ProcessEnv` (with an internal cast) so the result can be
 * passed straight to `child_process.spawn`. Next.js augments that type to
 * require `NODE_ENV`, but Node's runtime imposes no such constraint and we
 * deliberately do not forward `NODE_ENV` to the subprocess.
 */
export function envWithoutClaudecode(source: EnvRecord = process.env): NodeJS.ProcessEnv {
  const filtered: EnvRecord = {}
  for (const key of Object.keys(source)) {
    if (SAFE_ENV_SET.has(key)) {
      const value = source[key]
      if (value !== undefined) {
        filtered[key] = value
      }
    }
  }
  return filtered as NodeJS.ProcessEnv
}

/** Exported for tests / introspection. Do not mutate at runtime. */
export const SAFE_ENV_KEYS_READONLY: readonly string[] = SAFE_ENV_KEYS
