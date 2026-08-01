// lib/env.test.ts
// SEC-P002: the visualizer spawns the summarizer / vault-stats subprocess
// with an env built from an allowlist (mirroring Python's `_SAFE_ENV_KEYS`),
// not the raw parent env. These tests pin the contract:
//   * `CLAUDECODE` (Claude nesting guard) is dropped.
//   * A secret-shaped variable the dev server happens to carry is dropped.
//   * The documented safe set is forwarded verbatim.

import { describe, it, expect } from 'bun:test'
import { envWithoutClaudecode, SAFE_ENV_KEYS_READONLY, type EnvRecord } from './env'

describe('envWithoutClaudecode', () => {
  it('drops CLAUDECODE (the Claude nesting guard)', () => {
    const env = envWithoutClaudecode({ CLAUDECODE: '1', PATH: '/usr/bin' })
    expect(env.CLAUDECODE).toBeUndefined()
    expect(env.PATH).toBe('/usr/bin')
  })

  it('drops an unrelated secret-shaped variable', () => {
    // A secret the dev server happens to carry must NOT leak into the child.
    // OPENAI_API_KEY is chosen because it is recognisable and is exactly the
    // kind of var a developer machine has alongside the Anthropic key.
    const env = envWithoutClaudecode({
      OPENAI_API_KEY: 'sk-leak-me-not',
      ANTHROPIC_API_KEY: 'sk-allowed',
    })
    expect(env.OPENAI_API_KEY).toBeUndefined()
    expect(env.ANTHROPIC_API_KEY).toBe('sk-allowed')
  })

  it('forwards the documented safe set (mirrors Python _SAFE_ENV_KEYS)', () => {
    const source: EnvRecord = {}
    for (const key of SAFE_ENV_KEYS_READONLY) {
      source[key] = `value-for-${key}`
    }
    source.CLAUDECODE = 'must-be-dropped'
    source.RANDOM_SECRET = 'must-be-dropped'

    const env = envWithoutClaudecode(source)

    for (const key of SAFE_ENV_KEYS_READONLY) {
      expect(env[key]).toBe(`value-for-${key}`)
    }
    expect(env.CLAUDECODE).toBeUndefined()
    expect(env.RANDOM_SECRET).toBeUndefined()
  })

  it('omits keys whose value is undefined', () => {
    const env = envWithoutClaudecode({ PATH: undefined, HOME: '/x' })
    expect('PATH' in env).toBe(false)
    expect(env.HOME).toBe('/x')
  })

  it('includes PATH and HOME by default (subprocess needs them)', () => {
    const env = envWithoutClaudecode({ PATH: '/bin', HOME: '/h' })
    expect(env.PATH).toBe('/bin')
    expect(env.HOME).toBe('/h')
  })

  it('does not mutate the source object', () => {
    const source: EnvRecord = { CLAUDECODE: '1', PATH: '/bin' }
    envWithoutClaudecode(source)
    expect(source.CLAUDECODE).toBe('1')
    expect(source.PATH).toBe('/bin')
  })
})
