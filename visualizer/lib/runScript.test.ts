// lib/runScript.test.ts
// ARC-036: covers the shared subprocess wrapper. Uses bun (which ships on
// PATH in this repo) and `/bin/echo` / `/bin/sh` so the tests are hermetic
// and do not need a fixture script.
import { describe, test, expect } from 'bun:test'
import {
  runScript,
  ScriptAbortedError,
  ScriptTimeoutError,
  ScriptFailedError,
} from './runScript'

describe('runScript', () => {
  test('captures stdout and treats exit 0 as success', async () => {
    const { stdout, stderr } = await runScript('bun', ['--eval', 'process.stdout.write("hi"); process.exit(0)'])
    expect(stdout).toBe('hi')
    expect(stderr).toBe('')
  })

  test('honors successExitCodes for non-zero success (git diff exit 1)', async () => {
    // Emulate `git diff` exiting 1 with diff text on stdout.
    const { stdout } = await runScript(
      'bun',
      ['--eval', 'process.stdout.write("diff content"); process.exit(1)'],
      { successExitCodes: [0, 1] },
    )
    expect(stdout).toBe('diff content')
  })

  test('ScriptFailedError carries exit code, stdout, and capped stderr on unguarded non-zero', async () => {
    try {
      await runScript('bun', ['--eval', 'process.stdout.write("partial-out"); process.stderr.write("err-msg"); process.exit(3)'])
      throw new Error('should have rejected')
    } catch (err) {
      expect(err).toBeInstanceOf(ScriptFailedError)
      const e = err as ScriptFailedError
      expect(e.exitCode).toBe(3)
      expect(e.stdout).toBe('partial-out')
      expect(e.stderr).toBe('err-msg')
    }
  })

  test('rejects with ScriptTimeoutError when timeoutMs elapses', async () => {
    // Sleep 500ms; timeout 50ms.
    try {
      await runScript('bun', ['--eval', 'setTimeout(() => process.exit(0), 500)'], { timeoutMs: 50 })
      throw new Error('should have timed out')
    } catch (err) {
      expect(err).toBeInstanceOf(ScriptTimeoutError)
    }
  })

  test('rejects with ScriptAbortedError when signal is already aborted', async () => {
    const ac = new AbortController()
    ac.abort()
    try {
      await runScript('bun', ['--eval', 'process.exit(0)'], { signal: ac.signal })
      throw new Error('should have rejected')
    } catch (err) {
      expect(err).toBeInstanceOf(ScriptAbortedError)
    }
  })

  test('rejects with ScriptAbortedError when signal fires mid-run', async () => {
    const ac = new AbortController()
    const p = runScript(
      'bun',
      ['--eval', 'setTimeout(() => process.exit(0), 5000)'],
      { signal: ac.signal, timeoutMs: 10_000 },
    )
    // Abort after 30ms — well before the 5s sleep exits.
    setTimeout(() => ac.abort(), 30)
    try {
      await p
      throw new Error('should have rejected')
    } catch (err) {
      expect(err).toBeInstanceOf(ScriptAbortedError)
    }
  })

  test('caps stderr retention at maxBytes but still resolves on exit 0', async () => {
    // Emit 2 KiB of stderr; cap at 64 bytes.
    const big = 'x'.repeat(2048)
    const { stderr } = await runScript(
      'bun',
      ['--eval', `process.stderr.write(${JSON.stringify(big)}); process.exit(0)`],
      { maxBytes: 64 },
    )
    expect(stderr.length).toBe(64)
    expect(stderr).toBe('x'.repeat(64))
  })

  test('ScriptFailedError on ENOENT (missing executable)', async () => {
    try {
      await runScript('/nonexistent-binary-for-test', [])
      throw new Error('should have rejected')
    } catch (err) {
      expect(err).toBeInstanceOf(ScriptFailedError)
      expect((err as ScriptFailedError).exitCode).toBeNull()
    }
  })
})
