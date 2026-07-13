import { describe, test, expect, afterEach } from 'bun:test'
import path from 'path'
import { findParsidionScript } from './scriptResolver'

const FIXTURES = path.join(import.meta.dir, '__fixtures__', 'search')
const savedEnv = process.env.PARSIDION_SCRIPTS_DIR

afterEach(() => {
  if (savedEnv === undefined) delete process.env.PARSIDION_SCRIPTS_DIR
  else process.env.PARSIDION_SCRIPTS_DIR = savedEnv
})

describe('findParsidionScript', () => {
  test('env override resolves within the override dir', () => {
    process.env.PARSIDION_SCRIPTS_DIR = path.join(FIXTURES, 'ok')
    expect(findParsidionScript('vault_search.py'))
      .toBe(path.join(FIXTURES, 'ok', 'vault_search.py'))
  })

  test('env override does NOT fall through when the script is missing', () => {
    process.env.PARSIDION_SCRIPTS_DIR = path.join(FIXTURES, 'ok')
    expect(findParsidionScript('definitely-not-a-script.py')).toBeNull()
  })
})
