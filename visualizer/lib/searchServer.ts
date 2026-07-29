// lib/searchServer.ts — spawn vault_search.py for semantic search.
// Mirrors the graph-rebuild/vaultStatsServer subprocess pattern: parsidion's
// Python layer owns backend selection (par-mem daemon → embeddings fallback);
// this module never implements search or backend logic itself.
//
// ARC-041: structural server-only guard — this module spawns subprocesses.
// ARC-036: subprocess plumbing now delegates to lib/runScript.ts so the
// timeout / abort / stderr-cap behavior is shared with the git-spawning
// routes rather than re-implemented here.
import 'server-only'

import path from 'path'
import { findParsidionScript } from './scriptResolver'
import {
  runScript,
  ScriptAbortedError,
  ScriptTimeoutError,
  ScriptFailedError,
  DEFAULT_TIMEOUT_MS,
} from './runScript'

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

  const timeoutMs = opts?.timeoutMs ?? DEFAULT_TIMEOUT_MS
  // `--` terminates argparse option parsing so a query starting with `-`
  // cannot be read as a flag by vault_search.py.
  const args = [
    'run', '--no-project', scriptPath,
    '--vault', vaultPath, '--json', '--top', String(top),
    '--', query,
  ]

  inFlight++
  try {
    let stdout = ''
    let stderr = ''
    try {
      ({ stdout, stderr } = await runScript('uv', args, {
        timeoutMs,
        signal: opts?.signal,
      }))
    } catch (err) {
      if (err instanceof ScriptAbortedError) throw new SearchFailedError('semantic search aborted')
      if (err instanceof ScriptTimeoutError) throw new SearchFailedError('semantic search timed out')
      if (err instanceof ScriptFailedError) {
        // SEC-003: log stderr server-side only.
        console.error('[searchServer] vault_search.py', err.message, ':', err.stderr)
        throw new SearchFailedError(`vault_search.py ${err.message}`)
      }
      throw err
    }

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
