// lib/vaultStatsServer.ts
// Server-only helpers for vault stats + summarizer process control.
// Uses fs / child_process — must never be imported by client code.
//
// Liveness model: the summarizer writes ~/.claude/logs/parsidion-summarizer-progress.json
// during a run and deletes it on completion. We additionally record the spawned PID +
// exit code in parsidion-summarizer-visualizer.json so a crash (stale progress file, dead
// PID) is distinguishable from a long in-progress run, and so runs started from the CLI
// (no recorded PID) are still detected via progress-file freshness.
//
// ARC-041: structural server-only guard. See lib/vaultResolver.ts for the
// rationale; the same hazard applies here (spawns `uv`).
import 'server-only'

import { spawn } from 'child_process'
import fs from 'fs'
import os from 'os'
import path from 'path'
import { envWithoutClaudecode } from './env'
import { findParsidionScript } from './scriptResolver'
import { runScript, ScriptFailedError, ScriptTimeoutError } from './runScript'

const HOME = os.homedir()
export const SECURE_LOG_DIR = path.join(HOME, '.claude', 'logs')
const PROGRESS_FILE = path.join(SECURE_LOG_DIR, 'parsidion-summarizer-progress.json')
const STATE_FILE = path.join(SECURE_LOG_DIR, 'parsidion-summarizer-visualizer.json')
const LOG_FILE = path.join(SECURE_LOG_DIR, 'parsidion-summarizer-visualizer.log')

/** Count non-empty, JSON-parseable lines in {vault}/pending_summaries.jsonl.
 *  Mirrors vault_metrics.collect_pending — file is session_id-deduped at write
 *  time, so the line count is the queue length. Missing/unreadable → 0. */
export function countPendingSummaries(vaultPath: string): number {
  let content: string
  try {
    content = fs.readFileSync(path.join(vaultPath, 'pending_summaries.jsonl'), 'utf-8')
  } catch {
    return 0
  }
  let count = 0
  for (const line of content.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed) continue
    try {
      JSON.parse(trimmed)
      count++
    } catch {
      // skip malformed lines
    }
  }
  return count
}

/** True if a process with the given PID is currently alive (signal 0 probe). */
export function isPidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

interface SummarizerState {
  pid?: number
  startedAt?: string
  vault?: string
  finishedAt?: string
  exitCode?: number | null
}

function readState(): SummarizerState | null {
  try {
    return JSON.parse(fs.readFileSync(STATE_FILE, 'utf-8')) as SummarizerState
  } catch {
    return null
  }
}

function writeState(state: SummarizerState): void {
  try {
    fs.mkdirSync(SECURE_LOG_DIR, { recursive: true, mode: 0o700 })
    fs.writeFileSync(STATE_FILE, JSON.stringify(state) + '\n', { encoding: 'utf-8', mode: 0o600 })
  } catch {
    // best-effort — never throw
  }
}

export interface Progress {
  total: number
  processed: number
  written: number
  skipped: number
  errors: number
  current: string
  pct: string
}

function readProgress(): Progress | null {
  try {
    const data = JSON.parse(fs.readFileSync(PROGRESS_FILE, 'utf-8')) as Record<string, unknown>
    const total = Number(data.total ?? 0)
    const processed = Number(data.processed ?? 0)
    return {
      total,
      processed,
      written: Number(data.written ?? 0),
      skipped: Number(data.skipped ?? 0),
      errors: Number(data.errors ?? 0),
      current: typeof data.current === 'string' ? data.current : '',
      pct: total ? `${((processed / total) * 100).toFixed(1)}%` : '—',
    }
  } catch {
    return null
  }
}

/** Age of the progress file's `ts` field in seconds, or null if unreadable/absent. */
function progressAgeSec(): number | null {
  try {
    const data = JSON.parse(fs.readFileSync(PROGRESS_FILE, 'utf-8')) as { ts?: string }
    if (!data.ts) return null
    const ms = Date.now() - new Date(data.ts).getTime()
    return ms > 0 ? ms / 1000 : 0
  } catch {
    return null
  }
}

export interface SummarizerStatus {
  running: boolean
  pid?: number
  startedAt?: string
  finishedAt?: string
  exitCode?: number | null
  error?: string
  progress: Progress | null
}

export function getSummarizerStatus(): SummarizerStatus {
  const state = readState()
  const progress = readProgress()
  const pid = state?.pid
  const pidAlive = pid ? isPidAlive(pid) : false

  // Running if our PID is alive, or a progress file is fresh (external CLI run).
  let running = pidAlive
  if (!running && progress) {
    const age = progressAgeSec()
    if (age !== null && age < 120) running = true
  }

  let error: string | undefined
  if (state?.finishedAt && state.exitCode != null && state.exitCode !== 0) {
    error = `Summarizer exited with code ${state.exitCode}`
  }

  return {
    running,
    pid,
    startedAt: state?.startedAt,
    finishedAt: state?.finishedAt,
    exitCode: state?.exitCode,
    error,
    progress,
  }
}

export type SpawnResult = { started: true; pid: number } | { alreadyRunning: true }

// ---------------------------------------------------------------------------
// ENH-007: vault health report
// ---------------------------------------------------------------------------

/** One scored dimension in the vault health report. Mirrors the Python
 *  ``DimensionScore`` dataclass serialised by ``vault-stats --health --json``. */
export interface HealthDimension {
  name: string
  score: number
  weight: number
  detail: string
  /** Concrete command to run when unhealthy, or null when the dimension is healthy. */
  action: string | null
}

/** Shape returned by ``vault-stats --health --json``. */
export interface VaultHealthReport {
  vault: string
  overall: number
  grade: string
  dimensions: HealthDimension[]
  note_types: Record<string, number>
  warnings: string[]
}

/** Errors raised by getVaultHealth. Distinguished so the route handler can
 *  pick the right HTTP status (timeout vs. internal error vs. missing). */
export class HealthScriptMissingError extends Error {}
export class HealthReportFailedError extends Error {
  constructor(message: string, readonly stderr: string) {
    super(message)
  }
}

/** Run ``vault-stats --health --json`` against the given vault and return
 *  the parsed report. Subprocess pattern matches spawnSummarizer /
 *  runVaultSearch so the import and subprocess layers see the same code.
 *
 *  The metadata-quality scan is the expensive dimension on a large vault;
 *  pass ``fast=true`` to skip it (the dimension is reported with a neutral
 *  score and ``detail='skipped (--fast)'``).
 *
 *  SEC-030: the report spawns a subprocess with a 60 s budget, so
 *  concurrent callers share one in-flight run and completed results are
 *  reused for ``HEALTH_CACHE_TTL_MS`` — a page refresh storm cannot fork a
 *  subprocess per request. Failures are never cached. */
export function getVaultHealth(
  vaultPath: string,
  opts?: { fast?: boolean; timeoutMs?: number },
): Promise<VaultHealthReport> {
  const key = `${vaultPath}|${opts?.fast ? 'fast' : 'full'}`
  const now = Date.now()
  const hit = healthCache.get(key)
  if (hit && now - hit.at < HEALTH_CACHE_TTL_MS) {
    return hit.promise
  }
  const entry = {
    at: now,
    promise: runVaultHealth(vaultPath, opts),
  }
  healthCache.set(key, entry)
  entry.promise.catch(() => {
    // Evict on failure so the next call retries instead of replaying the
    // cached rejection for the rest of the TTL.
    if (healthCache.get(key) === entry) healthCache.delete(key)
  })
  return entry.promise
}

const HEALTH_CACHE_TTL_MS = 60_000

const healthCache = new Map<
  string,
  { at: number; promise: Promise<VaultHealthReport> }
>()

async function runVaultHealth(
  vaultPath: string,
  opts?: { fast?: boolean; timeoutMs?: number },
): Promise<VaultHealthReport> {
  const script = findParsidionScript('vault_stats.py')
  if (!script) {
    throw new HealthScriptMissingError('vault_stats.py not found. Install parsidion or run from the source repo.')
  }
  const args = [
    'run', '--no-project', script,
    '--vault', vaultPath,
    '--health', '--json',
  ]
  if (opts?.fast) args.push('--fast')

  let stdout = ''
  let stderr = ''
  try {
    ({ stdout, stderr } = await runScript('uv', args, {
      timeoutMs: opts?.timeoutMs ?? 60_000,
    }))
  } catch (err) {
    if (err instanceof ScriptTimeoutError) {
      throw new HealthReportFailedError('vault health timed out', '')
    }
    if (err instanceof ScriptFailedError) {
      // SEC-003: log stderr server-side only.
      console.error('[vaultStatsServer] vault_stats.py', err.message, ':', err.stderr)
      throw new HealthReportFailedError(`vault_stats.py ${err.message}`, err.stderr)
    }
    throw err
  }

  try {
    return JSON.parse(stdout) as VaultHealthReport
  } catch {
    if (stderr) console.error('[vaultStatsServer] vault_stats.py produced invalid JSON; stderr:', stderr)
    throw new HealthReportFailedError('invalid JSON from vault_stats.py', stderr)
  }
}

/** Spawn the summarizer detached (non-blocking) against the given vault.
 *  Forwards only the env allowlist (SEC-P002: mirrors Python `_SAFE_ENV_KEYS`)
 *  so CLAUDECODE and any unrelated secret the dev server carries are dropped.
 *  Returns immediately; the child survives the request. */
export function spawnSummarizer(vaultPath: string): SpawnResult {
  if (getSummarizerStatus().running) return { alreadyRunning: true }

  const script = findParsidionScript('summarize_sessions.py')
  if (!script) {
    throw new Error('summarize_sessions.py not found. Install parsidion or run from the source repo.')
  }

  try {
    fs.mkdirSync(SECURE_LOG_DIR, { recursive: true, mode: 0o700 })
  } catch {
    // ignore — best-effort
  }

  // Append child stdout/stderr to a log file for debugging.
  let outFd: number | undefined
  try {
    outFd = fs.openSync(LOG_FILE, 'a')
  } catch {
    outFd = undefined
  }

  // SEC-P002: forward only the allowlisted env (mirrors Python
  // `_SAFE_ENV_KEYS`). Drops CLAUDECODE and any unrelated secret the dev
  // server happens to carry. See lib/env.ts.
  const env = envWithoutClaudecode()

  const proc = spawn(
    'uv',
    ['run', '--no-project', script, '--vault', vaultPath],
    {
      detached: true,
      stdio: ['ignore', outFd ?? 'ignore', outFd ?? 'ignore'],
      env,
    },
  )
  proc.unref()
  // spawn assigns the pid synchronously; guard purely to satisfy the type.
  if (proc.pid === undefined) {
    throw new Error('Failed to obtain summarizer PID')
  }
  const pid: number = proc.pid

  // The child inherits a dup of outFd at spawn; the parent may close its copy.
  if (outFd !== undefined) {
    try {
      fs.closeSync(outFd)
    } catch {
      // ignore
    }
  }

  const startedAt = new Date().toISOString()
  writeState({ pid, startedAt, vault: vaultPath })

  const markDone = (code: number | null) => {
    try {
      fs.appendFileSync(LOG_FILE, `[visualizer] summarizer pid ${pid} exited code=${code}\n`)
    } catch {
      // ignore
    }
    writeState({ pid, startedAt, vault: vaultPath, finishedAt: new Date().toISOString(), exitCode: code })
  }
  proc.on('close', code => markDone(code))
  proc.on('error', err => {
    try {
      fs.appendFileSync(LOG_FILE, `[visualizer] summarizer pid ${pid} spawn error: ${err.message}\n`)
    } catch {
      // ignore
    }
    markDone(-1)
  })

  return { started: true, pid }
}
