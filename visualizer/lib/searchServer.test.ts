import { describe, test, expect, afterEach } from 'bun:test'
import path from 'path'
import {
  runVaultSearch,
  ScriptMissingError,
  SearchBusyError,
  SearchFailedError,
} from './searchServer'

const FIXTURES = path.join(import.meta.dir, '__fixtures__', 'search')
const savedEnv = process.env.PARSIDION_SCRIPTS_DIR

function useFixture(name: string) {
  process.env.PARSIDION_SCRIPTS_DIR = path.join(FIXTURES, name)
}

afterEach(() => {
  if (savedEnv === undefined) delete process.env.PARSIDION_SCRIPTS_DIR
  else process.env.PARSIDION_SCRIPTS_DIR = savedEnv
})

describe('runVaultSearch', () => {
  test('maps rows and strips the vault prefix from paths', async () => {
    useFixture('ok')
    const results = await runVaultSearch('/tmp/fakevault', 'hello world', 8)
    expect(results.length).toBe(2)
    expect(results[0].stem).toBe('note-alpha')
    expect(results[0].path).toBe('Patterns/note-alpha.md')
    expect(results[0].summary).toBe('hello world') // fixture echoes the query
    expect(results[0].tags).toEqual(['a', 'b'])
    expect(results[1].folder).toBe('Debugging')
  }, 20000)

  test('a leading-dash query survives as the positional argument', async () => {
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
})
