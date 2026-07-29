// lib/runScript.ts
// Shared subprocess wrapper extracted from searchServer.ts (ARC-036).
//
// Every server route that spawns an external process for a request —
// `note/diff` (git diff), `note/history` (git log), `graph/rebuild`
// (build_graph.py via uv), and `searchServer` (vault_search.py via uv) —
// has the same shape: timeout, abort-on-client-gone, capped stderr, and a
// rejection envelope that distinguishes "client gone" from "subprocess
// failed" from "subprocess produced bad output". Centralizing it here means
// a future route cannot quietly drop one of those (the prior bug: only
// searchServer capped stderr, so a runaway git diff on a huge vault could
// grow stderr without bound).
//
// Server-only: spawns child processes. ARC-041.
import 'server-only'

import { spawn } from 'child_process'

/** Raised when the AbortSignal associated with the request fires. */
export class ScriptAbortedError extends Error {}
/** Raised when the subprocess exceeds `timeoutMs`. */
export class ScriptTimeoutError extends Error {}
/** Raised when the subprocess exits non-zero (or the spawn itself failed). */
export class ScriptFailedError extends Error {
  constructor(
    message: string,
    /** Captured stderr (truncated to `maxBytes`). Useful for server-side logging. */
    readonly stderr: string,
    /** Exit code, when available. */
    readonly exitCode: number | null,
    /** Captured stdout at the point of failure. */
    readonly stdout: string = '',
  ) {
    super(message)
  }
}

export interface RunScriptOptions {
  /** Hard timeout in milliseconds. Defaults to 30s. */
  timeoutMs?: number
  /**
   * Maximum bytes of stderr to retain. Defaults to 64 KiB — matches the
   * pre-extraction SEC-014 limit. The stderr stream is still fully drained
   * (so the subprocess's pipe doesn't backpressure and block), only the
   * retained string is truncated.
   */
  maxBytes?: number
  /** When the signal aborts, the subprocess is SIGKILLed and the call rejects with ScriptAbortedError. */
  signal?: AbortSignal
  /** Working directory for the subprocess. */
  cwd?: string
  /**
   * Exit codes to treat as success (in addition to 0). `git diff` uses 1 to
   * mean "differences found" — pass `[1]` there to keep the success path.
   */
  successExitCodes?: readonly number[]
}

export interface RunScriptResult {
  stdout: string
  /** Truncated stderr (≤ maxBytes). */
  stderr: string
}

/** Default per-call timeout. 30s matches the pre-extraction SEARCH_TIMEOUT_MS. */
export const DEFAULT_TIMEOUT_MS = 30_000

/** Default stderr cap. 64 KiB matches the pre-extraction SEC-014 limit. */
export const DEFAULT_MAX_STDERR_BYTES = 64 * 1024

/**
 * Spawn a subprocess, capture its stdout/stderr (stderr capped), enforce a
 * timeout, and abort cleanly when the caller's signal fires. The subprocess
 * is always SIGKILLed on timeout or abort so it cannot hold resources after
 * the call has rejected.
 *
 * Resolves with `{stdout, stderr}` when the exit code is 0 (or appears in
 * `opts.successExitCodes`). Rejects with:
 *   - {@link ScriptAbortedError} when `signal.aborted` before or during the run
 *   - {@link ScriptTimeoutError} when `timeoutMs` elapses
 *   - {@link ScriptFailedError} for any other non-zero exit, spawn failure,
 *     or signal death. The error carries the partial stdout and truncated
 *     stderr so callers like `git diff` (exit 1 = "diff present") can recover
 *     the captured output without respawning.
 *
 * Caller is responsible for parsing stdout — this wrapper does not impose a
 * JSON shape, so it can back git diff / git log / uv run / anything else.
 */
export function runScript(
  cmd: string,
  args: readonly string[],
  opts: RunScriptOptions = {},
): Promise<RunScriptResult> {
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS
  const maxBytes = opts.maxBytes ?? DEFAULT_MAX_STDERR_BYTES
  const signal = opts.signal
  const successExit = opts.successExitCodes ?? []

  if (signal?.aborted) {
    return Promise.reject(new ScriptAbortedError('aborted before spawn'))
  }

  return new Promise<RunScriptResult>((resolve, reject) => {
    const proc = spawn(cmd, args, { stdio: ['ignore', 'pipe', 'pipe'], cwd: opts.cwd })

    let stdout = ''
    let stderr = ''
    let stderrBytes = 0
    let timedOut = false
    let aborted = false
    let settled = false

    const timer = setTimeout(() => {
      timedOut = true
      try { proc.kill('SIGKILL') } catch { /* already dead */ }
    }, timeoutMs)

    const onAbort = () => {
      aborted = true
      try { proc.kill('SIGKILL') } catch { /* already dead */ }
    }
    signal?.addEventListener('abort', onAbort)

    const cleanup = () => {
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
    }

    const settle = (fn: () => void) => {
      if (settled) return
      settled = true
      cleanup()
      fn()
    }

    proc.stdout?.on('data', (chunk: Buffer) => { stdout += chunk.toString() })
    proc.stderr?.on('data', (chunk: Buffer) => {
      const remaining = maxBytes - stderrBytes
      if (remaining > 0) {
        const slice = chunk.slice(0, remaining)
        stderr += slice.toString()
        stderrBytes += slice.length
      }
    })

    proc.on('close', code => {
      if (aborted) return settle(() => reject(new ScriptAbortedError('aborted')))
      if (timedOut) return settle(() => reject(new ScriptTimeoutError('timed out')))
      const codeNum = typeof code === 'number' ? code : -1
      if (code === 0 || successExit.includes(codeNum)) {
        return settle(() => resolve({ stdout, stderr }))
      }
      settle(() => reject(
        new ScriptFailedError(`exited ${code}`, stderr, code, stdout),
      ))
    })

    proc.on('error', err => {
      // Node emits 'error' after a failed spawn (ENOENT, EACCES, ...).
      // No 'close' will follow in that case.
      settle(() => reject(new ScriptFailedError(`spawn failed: ${err.message}`, stderr, null, stdout)))
    })
  })
}
