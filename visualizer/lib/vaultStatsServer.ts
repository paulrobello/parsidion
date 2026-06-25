// lib/vaultStatsServer.ts
// Server-only helpers for vault stats + summarizer process control.
// Uses fs / child_process — must never be imported by client code.
//
// Liveness model: the summarizer writes ~/.claude/logs/parsidion-summarizer-progress.json
// during a run and deletes it on completion. We additionally record the spawned PID +
// exit code in parsidion-summarizer-visualizer.json so a crash (stale progress file, dead
// PID) is distinguishable from a long in-progress run, and so runs started from the CLI
// (no recorded PID) are still detected via progress-file freshness.
import { spawn } from 'child_process'
import fs from 'fs'
import os from 'os'
import path from 'path'

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

/** Locate a parsidion script by filename — installed skill dir first, then the
 *  source repo (app lives at <repo>/visualizer/, scripts at <repo>/skills/parsidion/scripts/). */
export function findParsidionScript(name: string): string | null {
  const installed = path.join(HOME, '.claude', 'skills', 'parsidion', 'scripts', name)
  if (fs.existsSync(installed)) return installed
  const source = path.join(process.cwd(), '..', 'skills', 'parsidion', 'scripts', name)
  if (fs.existsSync(source)) return source
  return null
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

/** Spawn the summarizer detached (non-blocking) against the given vault.
 *  Strips CLAUDECODE so the claude-cli backend works even if the dev server
 *  inherited it. Returns immediately; the child survives the request. */
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

  const env: NodeJS.ProcessEnv = { ...process.env }
  delete env.CLAUDECODE

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
