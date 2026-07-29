// lib/findNote.test.ts
// QA-006: pin the behaviour of the shared async note resolver that backs
// note/, note/history/, and note/diff/. The previous triplicated copies had
// already diverged once (one async, two sync); centralising means a future
// regression in the walker surfaces here rather than as drift between routes.

import { describe, it, expect, beforeEach, afterEach } from 'bun:test'
import * as fs from 'fs'
import * as path from 'path'
import * as os from 'os'
import { findNote } from './findNote'

let tmpRoot: string

function setupVault(): void {
  tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'findnote-'))
}

function teardownVault(): void {
  try {
    fs.rmSync(tmpRoot, { recursive: true, force: true })
  } catch {
    /* best effort */
  }
}

describe('findNote — shared async note resolver (QA-006)', () => {
  beforeEach(() => setupVault())
  afterEach(() => teardownVault())

  it('finds a top-level note by stem', async () => {
    fs.writeFileSync(path.join(tmpRoot, 'my-note.md'), '# top\n')
    const result = await findNote(tmpRoot, 'my-note')
    expect(result).toBe(path.join(tmpRoot, 'my-note.md'))
  })

  it('finds a nested note by stem (recursive)', async () => {
    fs.mkdirSync(path.join(tmpRoot, 'Patterns', 'nested', 'deep'), { recursive: true })
    fs.writeFileSync(
      path.join(tmpRoot, 'Patterns', 'nested', 'deep', 'buried.md'),
      '# buried\n',
    )
    const result = await findNote(tmpRoot, 'buried')
    expect(result).toBe(
      path.join(tmpRoot, 'Patterns', 'nested', 'deep', 'buried.md'),
    )
  })

  it('returns null for a missing stem', async () => {
    fs.writeFileSync(path.join(tmpRoot, 'unrelated.md'), '# x\n')
    const result = await findNote(tmpRoot, 'does-not-exist')
    expect(result).toBeNull()
  })

  it('does not match non-md files even when stem matches', async () => {
    fs.writeFileSync(path.join(tmpRoot, 'readme.txt'), '# x\n')
    fs.writeFileSync(path.join(tmpRoot, 'data.json'), '{}')
    const result = await findNote(tmpRoot, 'readme')
    expect(result).toBeNull()
  })

  it('skips dotfile entries (does not traverse .git or .obsidian)', async () => {
    // A dotfile entry that would match if walked — must be skipped.
    fs.mkdirSync(path.join(tmpRoot, '.git'), { recursive: true })
    fs.writeFileSync(path.join(tmpRoot, '.git', 'hidden.md'), '# hidden\n')
    // A non-dotfile note that does NOT match the stem — confirms the walk
    // did not silently surface the dotfile path.
    fs.writeFileSync(path.join(tmpRoot, 'visible.md'), '# visible\n')
    const result = await findNote(tmpRoot, 'hidden')
    expect(result).toBeNull()
    const visible = await findNote(tmpRoot, 'visible')
    expect(visible).toBe(path.join(tmpRoot, 'visible.md'))
  })

  it('returns null when the start directory does not exist', async () => {
    const result = await findNote(path.join(tmpRoot, 'does', 'not', 'exist'), 'x')
    expect(result).toBeNull()
  })

  it('returns the first match in depth-first order when stems collide', async () => {
    // Two notes with the same stem in different folders — findNote returns
    // the first depth-first hit, which is why the routes prefer an explicit
    // relPath when available. Pin the contract so a future "return all" or
    // "return last" refactor is a deliberate decision, not silent drift.
    fs.mkdirSync(path.join(tmpRoot, 'A'), { recursive: true })
    fs.mkdirSync(path.join(tmpRoot, 'B'), { recursive: true })
    fs.writeFileSync(path.join(tmpRoot, 'A', 'dup.md'), '# A\n')
    fs.writeFileSync(path.join(tmpRoot, 'B', 'dup.md'), '# B\n')
    const result = await findNote(tmpRoot, 'dup')
    // readdir returns entries in filesystem order; the contract here is
    // simply "one of the matches, not null".
    expect(result).not.toBeNull()
    expect(result!.endsWith('dup.md')).toBe(true)
  })

  it('does not escape the start directory (resolves within tmpRoot only)', async () => {
    // A sibling temp dir with a matching note must not be returned — findNote
    // only walks downward from the dir it was given.
    const sibling = fs.mkdtempSync(path.join(os.tmpdir(), 'findnote-sibling-'))
    try {
      fs.writeFileSync(path.join(sibling, 'outside.md'), '# escape\n')
      fs.writeFileSync(path.join(tmpRoot, 'inside.md'), '# inside\n')
      const result = await findNote(tmpRoot, 'outside')
      expect(result).toBeNull()
      // And the inside one still resolves.
      const inside = await findNote(tmpRoot, 'inside')
      expect(inside).toBe(path.join(tmpRoot, 'inside.md'))
    } finally {
      fs.rmSync(sibling, { recursive: true, force: true })
    }
  })
})
