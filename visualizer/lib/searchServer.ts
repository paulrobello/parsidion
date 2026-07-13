// lib/searchServer.ts — spawn vault_search.py for semantic search.
// Mirrors the graph-rebuild/vaultStatsServer subprocess pattern: parsidion's
// Python layer owns backend selection (par-mem daemon → embeddings fallback);
// this module never implements search or backend logic itself.
import { spawn } from 'child_process'
import path from 'path'
import { findParsidionScript } from './scriptResolver'

export interface SemanticSearchResult {
  stem: string
  title: string
  folder: string
  /** Vault-relative note path. */
  path: string
  tags: string[]
  note_type: string
  score: number
  summary: string
}

export class ScriptMissingError extends Error {}
export class SearchBusyError extends Error {}
export class SearchFailedError extends Error {}

// SEC-014: cap stderr accumulation (same limit as the rebuild route).
const MAX_STDERR_BYTES = 64 * 1024
const SEARCH_TIMEOUT_MS = 30_000
const MAX_CONCURRENT_SEARCHES = 2

let inFlight = 0

export async function runVaultSearch(
  vaultPath: string,
  query: string,
  top: number,
  opts?: { timeoutMs?: number; signal?: AbortSignal },
): Promise<SemanticSearchResult[]> {
  const scriptPath = findParsidionScript('vault_search.py')
  if (!scriptPath) throw new ScriptMissingError('vault_search.py not found')
  if (opts?.signal?.aborted) {
    throw new SearchFailedError('semantic search aborted')
  }
  if (inFlight >= MAX_CONCURRENT_SEARCHES) {
    throw new SearchBusyError('too many concurrent searches')
  }

  const timeoutMs = opts?.timeoutMs ?? SEARCH_TIMEOUT_MS
  const signal = opts?.signal
  // `--` terminates argparse option parsing so a query starting with `-`
  // cannot be read as a flag by vault_search.py.
  const args = [
    'run', '--no-project', scriptPath,
    '--vault', vaultPath, '--json', '--top', String(top),
    '--', query,
  ]

  inFlight++
  let stderr = ''
  try {
    const stdout = await new Promise<string>((resolve, reject) => {
      const proc = spawn('uv', args, { stdio: ['ignore', 'pipe', 'pipe'] })
      let out = ''
      let stderrBytes = 0
      let timedOut = false
      let aborted = false
      const timer = setTimeout(() => {
        timedOut = true
        proc.kill('SIGKILL')
      }, timeoutMs)

      // Client is gone — kill the subprocess so it doesn't hold a concurrency
      // slot for up to SEARCH_TIMEOUT_MS after the response is moot.
      const onAbort = () => {
        aborted = true
        proc.kill('SIGKILL')
      }
      signal?.addEventListener('abort', onAbort)

      proc.stdout?.on('data', (chunk: Buffer) => { out += chunk.toString() })
      proc.stderr?.on('data', (chunk: Buffer) => {
        const remaining = MAX_STDERR_BYTES - stderrBytes
        if (remaining > 0) {
          const slice = chunk.slice(0, remaining)
          stderr += slice.toString()
          stderrBytes += slice.length
        }
      })

      proc.on('close', code => {
        clearTimeout(timer)
        signal?.removeEventListener('abort', onAbort)
        if (aborted) return reject(new SearchFailedError('semantic search aborted'))
        if (timedOut) return reject(new SearchFailedError('semantic search timed out'))
        if (code !== 0) {
          // SEC-003: log stderr server-side only.
          console.error('[searchServer] vault_search.py exited', code, ':', stderr)
          return reject(new SearchFailedError(`vault_search.py exited ${code}`))
        }
        resolve(out)
      })

      proc.on('error', err => {
        clearTimeout(timer)
        signal?.removeEventListener('abort', onAbort)
        reject(new SearchFailedError(`spawn failed: ${err.message}`))
      })
    })

    let rows: unknown
    try {
      rows = JSON.parse(stdout)
    } catch {
      if (stderr) console.error('[searchServer] vault_search.py produced invalid JSON; stderr:', stderr)
      throw new SearchFailedError('invalid JSON from vault_search.py')
    }
    if (!Array.isArray(rows)) {
      if (stderr) console.error('[searchServer] vault_search.py returned non-array JSON; stderr:', stderr)
      throw new SearchFailedError('unexpected JSON shape from vault_search.py')
    }
    return rows.map(r => mapRow(r as Record<string, unknown>, vaultPath))
  } finally {
    inFlight--
  }
}

function mapRow(r: Record<string, unknown>, vaultPath: string): SemanticSearchResult {
  const abs = typeof r.path === 'string' ? r.path : ''
  const rel = abs.startsWith(vaultPath + path.sep)
    ? abs.slice(vaultPath.length + 1)
    : abs
  return {
    stem: String(r.stem ?? ''),
    title: String(r.title ?? r.stem ?? ''),
    folder: String(r.folder ?? ''),
    path: rel,
    tags: Array.isArray(r.tags) ? r.tags.map(String) : [],
    note_type: String(r.note_type ?? ''),
    score: typeof r.score === 'number' ? r.score : 0,
    summary: String(r.summary ?? ''),
  }
}
