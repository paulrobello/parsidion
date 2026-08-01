import { describe, test, expect, afterEach } from 'bun:test'
import path from 'path'
import { spawnSync } from 'child_process'
import {
  runVaultSearch,
  ScriptMissingError,
  SearchBusyError,
  SearchFailedError,
} from './searchServer'

const FIXTURES = path.join(import.meta.dir, '__fixtures__', 'search')
const savedEnv = process.env.PARSIDION_SCRIPTS_DIR

// The first two tests spawn the search script through `uv`; skip them where uv
// isn't installed (the visualizer CI job intentionally installs no Python
// toolchain). The mock/error-path tests below still run — they expect the
// spawn to fail, which it does with or without uv.
const uvAvailable = spawnSync('uv', ['--version'], { stdio: 'ignore' }).status === 0

function useFixture(name: string) {
  process.env.PARSIDION_SCRIPTS_DIR = path.join(FIXTURES, name)
}

afterEach(() => {
  if (savedEnv === undefined) delete process.env.PARSIDION_SCRIPTS_DIR
  else process.env.PARSIDION_SCRIPTS_DIR = savedEnv
})

describe('runVaultSearch', () => {
  test.skipIf(!uvAvailable)('maps rows and strips the vault prefix from paths', async () => {
    useFixture('ok')
    const results = await runVaultSearch('/tmp/fakevault', 'hello world', 8)
    expect(results.length).toBe(2)
    expect(results[0].stem).toBe('note-alpha')
    expect(results[0].path).toBe('Patterns/note-alpha.md')
    expect(results[0].summary).toBe('hello world') // fixture echoes the query
    expect(results[0].tags).toEqual(['a', 'b'])
    expect(results[1].folder).toBe('Debugging')
  }, 20000)

  test.skipIf(!uvAvailable)('a leading-dash query survives as the positional argument', async () => {
    useFixture('ok')
    const results = await runVaultSearch('/tmp/fakevault', '-not a flag', 8)
    expect(results[0].summary).toBe('-not a flag')
  }, 20000)

  test('missing script rejects with ScriptMissingError', async () => {
    useFixture('nonexistent-dir')
    await expect(runVaultSearch('/tmp/v', 'q', 8))
      .rejects.toBeInstanceOf(ScriptMissingError)
  })

  test('nonzero exit rejects with SearchFailedError', async () => {
    useFixture('fail')
    await expect(runVaultSearch('/tmp/v', 'q', 8))
      .rejects.toBeInstanceOf(SearchFailedError)
  }, 20000)

  test('non-JSON output rejects with SearchFailedError', async () => {
    useFixture('badjson')
    await expect(runVaultSearch('/tmp/v', 'q', 8))
      .rejects.toBeInstanceOf(SearchFailedError)
  }, 20000)

  test('timeout kills the process and rejects with SearchFailedError', async () => {
    useFixture('slow')
    await expect(runVaultSearch('/tmp/v', 'q', 8, { timeoutMs: 250 }))
      .rejects.toBeInstanceOf(SearchFailedError)
  }, 20000)

  test('a third concurrent search rejects with SearchBusyError', async () => {
    useFixture('slow')
    const p1 = runVaultSearch('/tmp/v', 'q', 8)
    const p2 = runVaultSearch('/tmp/v', 'q', 8)
    await expect(runVaultSearch('/tmp/v', 'q', 8))
      .rejects.toBeInstanceOf(SearchBusyError)
    await Promise.allSettled([p1, p2])
  }, 20000)

  test('an aborted search kills the subprocess and frees its concurrency slot', async () => {
    useFixture('slow')
    const controller = new AbortController()
    const aborted = runVaultSearch('/tmp/v', 'q', 8, { signal: controller.signal })
    setTimeout(() => controller.abort(), 100)
    await expect(aborted).rejects.toBeInstanceOf(SearchFailedError)

    // If the abort had leaked the concurrency slot, one of these would
    // reject with SearchBusyError instead of running to completion.
    const results = await Promise.allSettled([
      runVaultSearch('/tmp/v', 'q', 8),
      runVaultSearch('/tmp/v', 'q', 8),
    ])
    for (const r of results) {
      if (r.status === 'rejected') expect(r.reason).not.toBeInstanceOf(SearchBusyError)
    }
  }, 20000)
})
