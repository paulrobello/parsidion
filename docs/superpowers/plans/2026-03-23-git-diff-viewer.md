# Git Diff Viewer Implementation Plan

> **Note:** Historical design document. Path references and project names may reflect the legacy `parsidion-cc` name (renamed to `parsidion` in v0.7.0). Do not edit this document.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a git diff viewer to the vault visualizer that shows version history for any note with syntax-highlighted side-by-side diffs, accessible from the ReadingPane toolbar and right-click context menus.

**Architecture:** A new `HistoryView` top-level component replaces `ReadingPane` when history mode is active. `useVisualizerState` gains `historyMode`/`historyNote` state and `openHistory`/`closeHistory` actions. Two new API routes (`/api/note/history`, `/api/note/diff`) shell out to git inside `VAULT_ROOT`. Client-side `parseDiff.ts` converts raw unified diff output into a structured hunk model for rendering in `DiffViewer`.

**Tech Stack:** Next.js 16.2 (App Router), TypeScript 5, React 19, Bun 1.1+, inline styles (no Tailwind classes in component logic), `diff` npm package for word-level diffing, `child_process.spawn` for git calls (same pattern as `/api/graph/rebuild`).

---

## File Map

### New files
| File | Responsibility |
|---|---|
| `visualizer/app/api/note/history/route.ts` | GET: run `git log` for a note, return commit list |
| `visualizer/app/api/note/diff/route.ts` | GET: run `git diff` between two commits, return raw unified diff |
| `visualizer/lib/parseDiff.ts` | Pure function: parse raw unified diff string → `DiffHunk[]` |
| `visualizer/lib/parseDiff.test.ts` | Unit tests for parseDiff (bun test) |
| `visualizer/components/DiffViewer.tsx` | Renders diff in UNIFIED / SPLIT / WORDS modes |
| `visualizer/components/CommitList.tsx` | Scrollable commit list with FROM/TO badge selection |
| `visualizer/components/HistoryView.tsx` | Full split-screen container: toolbar + CommitList + DiffViewer |

### Modified files
| File | Change |
|---|---|
| `visualizer/lib/useVisualizerState.ts` | Add `historyMode`, `historyNote`, `prevViewMode`, `openHistory`, `closeHistory` |
| `visualizer/app/page.tsx` | Render `HistoryView` instead of `ReadingPane` when `historyMode` is true; pass `openHistory` down |
| `visualizer/components/ReadingPane.tsx` | Add History (clock) button to toolbar; accept `onOpenHistory` prop |
| `visualizer/components/FileExplorer.tsx` | Add right-click context menu with "View History" on file rows; accept `onOpenHistory` prop |
| `visualizer/components/GraphCanvas.tsx` | Add right-click handler on nodes with "View History"; accept `onOpenHistory` prop |
| `visualizer/package.json` | Add `diff` + `@types/diff` dependencies |

---

## Task 1: Install `diff` package

**Files:**
- Modify: `visualizer/package.json`

- [ ] **Step 1: Install the package**

```bash
cd visualizer && bun add diff && bun add -d @types/diff
```

Expected: `diff` appears in `dependencies`, `@types/diff` in `devDependencies` in `package.json`.

- [ ] **Step 2: Verify TypeScript can see the types**

```bash
cd visualizer && bunx tsc --noEmit 2>&1 | head -5
```

Expected: no errors about `diff` module.

- [ ] **Step 3: Commit**

```bash
git add visualizer/package.json visualizer/bun.lock
git commit -m "chore(visualizer): add diff package for word-level diffing"
```

---

## Task 2: `lib/parseDiff.ts` — diff parser

**Files:**
- Create: `visualizer/lib/parseDiff.ts`
- Create: `visualizer/lib/parseDiff.test.ts`

This is a pure function — write the test first, then the implementation.

- [ ] **Step 1: Write the failing test**

Create `visualizer/lib/parseDiff.test.ts`:

```typescript
import { describe, it, expect } from 'bun:test'
import { parseDiff } from './parseDiff'

const SAMPLE_DIFF = `--- a/Patterns/my-note.md
+++ b/Patterns/my-note.md
@@ -1,4 +1,5 @@
 # My Note

-Old line one.
+New line one.
+Added line.
 Context line.
`

describe('parseDiff', () => {
  it('returns empty array for empty input', () => {
    expect(parseDiff('')).toEqual([])
  })

  it('parses a single hunk', () => {
    const hunks = parseDiff(SAMPLE_DIFF)
    expect(hunks).toHaveLength(1)
    expect(hunks[0].header).toBe('@@ -1,4 +1,5 @@')
  })

  it('strips file header lines (--- / +++)', () => {
    const hunks = parseDiff(SAMPLE_DIFF)
    const allContent = hunks.flatMap(h => h.lines.map(l => l.content))
    expect(allContent.some(c => c.startsWith('---') || c.startsWith('+++')))
      .toBe(false)
  })

  it('classifies line types correctly', () => {
    const lines = parseDiff(SAMPLE_DIFF)[0].lines
    const types = lines.map(l => l.type)
    expect(types).toEqual(['context', 'context', 'remove', 'add', 'add', 'context'])
  })

  it('assigns oldLineNo to context and remove lines', () => {
    const lines = parseDiff(SAMPLE_DIFF)[0].lines
    const removeLine = lines.find(l => l.type === 'remove')!
    expect(removeLine.oldLineNo).toBe(3)
    expect(removeLine.newLineNo).toBeNull()
  })

  it('assigns newLineNo to context and add lines', () => {
    const lines = parseDiff(SAMPLE_DIFF)[0].lines
    const addLine = lines.find(l => l.type === 'add')!
    expect(addLine.newLineNo).toBe(3)
    expect(addLine.oldLineNo).toBeNull()
  })

  it('counts additions and deletions', () => {
    const hunks = parseDiff(SAMPLE_DIFF)
    const all = hunks.flatMap(h => h.lines)
    expect(all.filter(l => l.type === 'add').length).toBe(2)
    expect(all.filter(l => l.type === 'remove').length).toBe(1)
  })
})
```

- [ ] **Step 2: Run the test — verify it fails**

```bash
cd visualizer && bun test lib/parseDiff.test.ts 2>&1 | head -20
```

Expected: error like `Cannot find module './parseDiff'`

- [ ] **Step 3: Implement `parseDiff.ts`**

Create `visualizer/lib/parseDiff.ts`:

```typescript
export interface DiffLine {
  type: 'add' | 'remove' | 'context'
  content: string        // line text without the +/-/space prefix
  oldLineNo: number | null
  newLineNo: number | null
}

export interface DiffHunk {
  header: string         // e.g. "@@ -1,4 +1,5 @@"
  lines: DiffLine[]
}

/**
 * Parse a raw unified diff string into structured hunks.
 * Strips the --- / +++ file header lines.
 * Returns [] for empty or header-only input.
 */
export function parseDiff(raw: string): DiffHunk[] {
  const lines = raw.split('\n')
  const hunks: DiffHunk[] = []
  let current: DiffHunk | null = null
  let oldLine = 0
  let newLine = 0

  for (const line of lines) {
    // Skip file header lines
    if (line.startsWith('--- ') || line.startsWith('+++ ')) continue

    // Hunk header: @@ -oldStart,oldCount +newStart,newCount @@
    const hunkMatch = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/)
    if (hunkMatch) {
      current = { header: line.replace(/\s+@@.*$/, ' @@').trim(), lines: [] }
      // Normalise: keep just "@@ -L,N +L,N @@" part
      current.header = line.match(/^(@@ [^@]+ @@)/)?.[1] ?? line
      oldLine = parseInt(hunkMatch[1], 10)
      newLine = parseInt(hunkMatch[2], 10)
      hunks.push(current)
      continue
    }

    if (!current) continue

    if (line.startsWith('+')) {
      current.lines.push({ type: 'add', content: line.slice(1), oldLineNo: null, newLineNo: newLine++ })
    } else if (line.startsWith('-')) {
      current.lines.push({ type: 'remove', content: line.slice(1), oldLineNo: oldLine++, newLineNo: null })
    } else if (line.startsWith(' ') || line === '') {
      // context line (space prefix or empty — trailing newline)
      if (line.startsWith(' ')) {
        current.lines.push({ type: 'context', content: line.slice(1), oldLineNo: oldLine++, newLineNo: newLine++ })
      }
    }
    // Lines starting with '\' (e.g. "\ No newline at end of file") are ignored
  }

  return hunks
}

/** Count total additions and deletions across all hunks */
export function diffStats(hunks: DiffHunk[]): { additions: number; deletions: number } {
  let additions = 0
  let deletions = 0
  for (const hunk of hunks) {
    for (const line of hunk.lines) {
      if (line.type === 'add') additions++
      else if (line.type === 'remove') deletions++
    }
  }
  return { additions, deletions }
}
```

- [ ] **Step 4: Run the tests — verify they pass**

```bash
cd visualizer && bun test lib/parseDiff.test.ts
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add visualizer/lib/parseDiff.ts visualizer/lib/parseDiff.test.ts
git commit -m "feat(visualizer): add parseDiff utility with unit tests"
```

---

## Task 3: `/api/note/history` route

**Files:**
- Create: `visualizer/app/api/note/history/route.ts`

The pattern to follow is `/api/note/route.ts` (uses `getVaultRoot`, `findNote`, `guardPath`) and `/api/graph/rebuild/route.ts` (uses `spawn` for shell commands).

- [ ] **Step 1: Create the route**

Create `visualizer/app/api/note/history/route.ts`:

```typescript
import { NextRequest, NextResponse } from 'next/server'
import { spawn } from 'child_process'
import fs from 'fs'
import path from 'path'

function getVaultRoot() {
  return process.env.VAULT_ROOT || path.join(process.env.HOME || '~', 'ClaudeVault')
}

function findNote(dir: string, stemToFind: string): string | null {
  try {
    const entries = fs.readdirSync(dir, { withFileTypes: true })
    for (const entry of entries) {
      if (entry.name.startsWith('.')) continue
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        const found = findNote(full, stemToFind)
        if (found) return found
      } else if (entry.isFile() && entry.name.endsWith('.md')) {
        if (entry.name.replace(/\.md$/, '') === stemToFind) return full
      }
    }
  } catch { /* skip */ }
  return null
}

function guardPath(notePath: string, vaultRoot: string): boolean {
  return path.resolve(notePath).startsWith(path.resolve(vaultRoot) + path.sep)
}

export interface CommitEntry {
  hash: string
  shortHash: string
  date: string
  message: string
}

export async function GET(req: NextRequest) {
  const stem = req.nextUrl.searchParams.get('stem')
  if (!stem) return NextResponse.json({ error: 'stem required' }, { status: 400 })

  const vaultRoot = getVaultRoot()
  const notePath = findNote(vaultRoot, stem)
  if (!notePath) return NextResponse.json({ error: `Note not found: ${stem}` }, { status: 404 })

  if (!guardPath(notePath, vaultRoot)) {
    return NextResponse.json({ error: 'Path traversal rejected' }, { status: 403 })
  }

  // Check git is available
  const gitDir = path.join(vaultRoot, '.git')
  if (!fs.existsSync(gitDir)) {
    return NextResponse.json({ commits: [] })
  }

  const relPath = path.relative(vaultRoot, notePath)

  return new Promise<NextResponse>(resolve => {
    const proc = spawn('git', ['log', '--follow', '--format=%H|%ai|%s', '--', relPath], {
      cwd: vaultRoot,
      stdio: 'pipe',
    })

    let stdout = ''
    let stderr = ''
    proc.stdout?.on('data', (chunk: Buffer) => { stdout += chunk.toString() })
    proc.stderr?.on('data', (chunk: Buffer) => { stderr += chunk.toString() })

    proc.on('close', code => {
      if (code !== 0) {
        resolve(NextResponse.json({ error: `git log failed: ${stderr}` }, { status: 500 }))
        return
      }

      const commits: CommitEntry[] = stdout
        .split('\n')
        .filter(Boolean)
        .map(line => {
          const [hash, date, ...msgParts] = line.split('|')
          return {
            hash,
            shortHash: hash.slice(0, 7),
            date,
            message: msgParts.join('|'),
          }
        })

      resolve(NextResponse.json({ commits }))
    })

    proc.on('error', err => {
      resolve(NextResponse.json({ error: err.message }, { status: 500 }))
    })
  })
}
```

- [ ] **Step 2: Manually test the route**

With the dev server running (`bun run dev` from `visualizer/`), open:
```
http://localhost:3999/api/note/history?stem=vault-pipeline-architecture-overview
```

Expected: JSON with `commits` array. Each entry has `hash`, `shortHash`, `date`, `message`.
If vault has no git: `{ "commits": [] }`.

- [ ] **Step 3: Commit**

```bash
git add visualizer/app/api/note/history/route.ts
git commit -m "feat(visualizer): add /api/note/history route for git log"
```

---

## Task 4: `/api/note/diff` route

**Files:**
- Create: `visualizer/app/api/note/diff/route.ts`

- [ ] **Step 1: Create the route**

Create `visualizer/app/api/note/diff/route.ts`:

```typescript
import { NextRequest, NextResponse } from 'next/server'
import { spawn } from 'child_process'
import fs from 'fs'
import path from 'path'

function getVaultRoot() {
  return process.env.VAULT_ROOT || path.join(process.env.HOME || '~', 'ClaudeVault')
}

function findNote(dir: string, stemToFind: string): string | null {
  try {
    const entries = fs.readdirSync(dir, { withFileTypes: true })
    for (const entry of entries) {
      if (entry.name.startsWith('.')) continue
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        const found = findNote(full, stemToFind)
        if (found) return found
      } else if (entry.isFile() && entry.name.endsWith('.md')) {
        if (entry.name.replace(/\.md$/, '') === stemToFind) return full
      }
    }
  } catch { /* skip */ }
  return null
}

function guardPath(notePath: string, vaultRoot: string): boolean {
  return path.resolve(notePath).startsWith(path.resolve(vaultRoot) + path.sep)
}

const MAX_DIFF_LINES = 5000

export async function GET(req: NextRequest) {
  const stem = req.nextUrl.searchParams.get('stem')
  const from = req.nextUrl.searchParams.get('from')
  const to = req.nextUrl.searchParams.get('to')

  if (!stem || !from || !to) {
    return NextResponse.json({ error: 'stem, from, and to are required' }, { status: 400 })
  }

  // Validate SHAs: alphanumeric only (short or full) or the sentinel "working"
  const shaPattern = /^[a-f0-9]{4,40}$|^working$/
  if (!shaPattern.test(from) || !shaPattern.test(to)) {
    return NextResponse.json({ error: 'Invalid commit reference' }, { status: 400 })
  }

  const vaultRoot = getVaultRoot()
  const notePath = findNote(vaultRoot, stem)
  if (!notePath) return NextResponse.json({ error: `Note not found: ${stem}` }, { status: 404 })

  if (!guardPath(notePath, vaultRoot)) {
    return NextResponse.json({ error: 'Path traversal rejected' }, { status: 403 })
  }

  const relPath = path.relative(vaultRoot, notePath)

  // Build git args:
  // Normal:       git diff <from> <to> -- <relPath>
  // Working tree: git diff <from> -- <relPath>  (no second SHA)
  const gitArgs = to === 'working'
    ? ['diff', from, '--', relPath]
    : ['diff', from, to, '--', relPath]

  return new Promise<NextResponse>(resolve => {
    const proc = spawn('git', gitArgs, { cwd: vaultRoot, stdio: 'pipe' })

    let stdout = ''
    let stderr = ''
    proc.stdout?.on('data', (chunk: Buffer) => { stdout += chunk.toString() })
    proc.stderr?.on('data', (chunk: Buffer) => { stderr += chunk.toString() })

    proc.on('close', code => {
      // git diff exits 0 (no diff) or 1 (has diff) — both are success
      if (code !== 0 && code !== 1) {
        resolve(NextResponse.json({ error: `git diff failed: ${stderr}` }, { status: 500 }))
        return
      }

      // Truncate very large diffs
      const lines = stdout.split('\n')
      let diff = stdout
      let truncated = false
      if (lines.length > MAX_DIFF_LINES) {
        diff = lines.slice(0, MAX_DIFF_LINES).join('\n')
        truncated = true
      }

      resolve(NextResponse.json({ diff, truncated }))
    })

    proc.on('error', err => {
      resolve(NextResponse.json({ error: err.message }, { status: 500 }))
    })
  })
}
```

- [ ] **Step 2: Manually test the route**

First get a real commit hash from the history route, then:
```
http://localhost:3999/api/note/diff?stem=vault-pipeline-architecture-overview&from=<hash>&to=working
```

Expected: JSON with `diff` string containing unified diff output.

- [ ] **Step 3: Commit**

```bash
git add visualizer/app/api/note/diff/route.ts
git commit -m "feat(visualizer): add /api/note/diff route for git diff between commits"
```

---

## Task 5: `useVisualizerState` — history state

**Files:**
- Modify: `visualizer/lib/useVisualizerState.ts`

- [ ] **Step 1: Add history state fields and actions**

In `useVisualizerState.ts`, after the `viewMode` / `graphScope` lines (around line 29), add:

```typescript
// --- History mode state ---
const [historyMode, setHistoryMode] = useState(false)
const [historyNote, setHistoryNote] = useState<string | null>(null)
const [prevViewMode, setPrevViewMode] = useState<'read' | 'graph'>('read')
```

Then add `openHistory` and `closeHistory` callbacks after the `switchTab` callback:

```typescript
const openHistory = useCallback((stem: string) => {
  setPrevViewMode(viewMode)
  setHistoryNote(stem)
  setHistoryMode(true)
}, [viewMode])

const closeHistory = useCallback(() => {
  setHistoryMode(false)
  setHistoryNote(null)
  setViewMode(prevViewMode)
}, [prevViewMode, setViewMode])
```

- [ ] **Step 2: Add to the return object**

In the `return { ... }` at the bottom of `useVisualizerState`, add after `viewMode, setViewMode, graphScope, setGraphScope,`:

```typescript
historyMode, historyNote, openHistory, closeHistory,
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd visualizer && bunx tsc --noEmit 2>&1 | head -20
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add visualizer/lib/useVisualizerState.ts
git commit -m "feat(visualizer): add history mode state to useVisualizerState"
```

---

## Task 6: `DiffViewer` component

**Files:**
- Create: `visualizer/components/DiffViewer.tsx`

This is a pure display component. It takes `hunks`, `mode`, and `filename` as props and renders the appropriate view.

- [ ] **Step 1: Create `DiffViewer.tsx`**

Create `visualizer/components/DiffViewer.tsx`:

```typescript
'use client'

import { useMemo } from 'react'
import { diffWords } from 'diff'
import type { DiffHunk, DiffLine } from '@/lib/parseDiff'
import { diffStats } from '@/lib/parseDiff'

export type DiffMode = 'unified' | 'split' | 'words'

interface Props {
  hunks: DiffHunk[]
  mode: DiffMode
  filename: string
  truncated?: boolean
}

const COLORS = {
  bg: '#060810',
  addBg: 'rgba(76,175,80,0.12)',
  removeBg: 'rgba(239,68,68,0.12)',
  addText: '#4CAF50',
  removeText: '#ef4444',
  contextText: '#666',
  lineNoText: '#333',
  headerBg: '#0a0e1a',
  headerBorder: '#111827',
  divider: '#111827',
  addLineNo: '#1a3a20',
  removeLineNo: '#5a2020',
}

function LineNo({ n, color }: { n: number | null; color: string }) {
  return (
    <span style={{ color, minWidth: 28, textAlign: 'right', paddingRight: 8, userSelect: 'none', flexShrink: 0 }}>
      {n ?? ''}
    </span>
  )
}

function UnifiedView({ hunks }: { hunks: DiffHunk[] }) {
  return (
    <div style={{ fontFamily: 'monospace', fontSize: 11 }}>
      {hunks.map((hunk, hi) => (
        <div key={hi}>
          <div style={{ padding: '2px 8px', background: '#0a0e1a', color: '#4b6bfb', fontSize: 10 }}>
            {hunk.header}
          </div>
          {hunk.lines.map((line, li) => {
            const isAdd = line.type === 'add'
            const isRemove = line.type === 'remove'
            return (
              <div key={li} style={{
                display: 'flex', gap: 8, padding: '1px 8px',
                background: isAdd ? COLORS.addBg : isRemove ? COLORS.removeBg : 'transparent',
              }}>
                <LineNo n={line.oldLineNo} color={isRemove ? COLORS.removeLineNo : COLORS.lineNoText} />
                <LineNo n={line.newLineNo} color={isAdd ? COLORS.addLineNo : COLORS.lineNoText} />
                <span style={{ color: isAdd ? '#4CAF50' : isRemove ? '#ef4444' : '#555', minWidth: 10 }}>
                  {isAdd ? '+' : isRemove ? '-' : ' '}
                </span>
                <span style={{ color: isAdd ? COLORS.addText : isRemove ? COLORS.removeText : COLORS.contextText, flex: 1, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                  {line.content}
                </span>
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}

function SplitView({ hunks }: { hunks: DiffHunk[] }) {
  // Pair up old/new lines per hunk for side-by-side display
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', fontFamily: 'monospace', fontSize: 11 }}>
      {/* Column headers */}
      <div style={{ padding: '3px 8px', background: COLORS.headerBg, color: '#555', fontSize: 9, borderBottom: `1px solid ${COLORS.headerBorder}`, borderRight: `1px solid ${COLORS.divider}` }}>
        FROM
      </div>
      <div style={{ padding: '3px 8px', background: COLORS.headerBg, color: '#555', fontSize: 9, borderBottom: `1px solid ${COLORS.headerBorder}` }}>
        TO
      </div>

      {hunks.map((hunk, hi) => {
        // Build paired rows: removes paired with adds, contexts shown on both sides
        const oldLines: (DiffLine | null)[] = []
        const newLines: (DiffLine | null)[] = []

        let ri = 0
        const removes = hunk.lines.filter(l => l.type === 'remove')
        const adds = hunk.lines.filter(l => l.type === 'add')

        // Simple pairing: interleave removes and adds, then context
        // Process lines in order, tracking remove/add runs
        let i = 0
        while (i < hunk.lines.length) {
          const line = hunk.lines[i]
          if (line.type === 'context') {
            oldLines.push(line)
            newLines.push(line)
            i++
          } else {
            // Collect a run of removes and adds
            const run: DiffLine[] = []
            while (i < hunk.lines.length && hunk.lines[i].type !== 'context') {
              run.push(hunk.lines[i++])
            }
            const runRemoves = run.filter(l => l.type === 'remove')
            const runAdds = run.filter(l => l.type === 'add')
            const maxLen = Math.max(runRemoves.length, runAdds.length)
            for (let j = 0; j < maxLen; j++) {
              oldLines.push(runRemoves[j] ?? null)
              newLines.push(runAdds[j] ?? null)
            }
          }
        }
        void ri; void removes; void adds

        return (
          <div key={hi} style={{ display: 'contents' }}>
            {/* Hunk header spans both columns */}
            <div style={{ padding: '2px 8px', background: '#0a0e1a', color: '#4b6bfb', fontSize: 10, gridColumn: '1 / -1' }}>
              {hunk.header}
            </div>
            {oldLines.map((old, li) => {
              const nw = newLines[li]
              return [
                // Old (left)
                <div key={`old-${li}`} style={{
                  display: 'flex', gap: 6, padding: '1px 8px',
                  background: old?.type === 'remove' ? COLORS.removeBg : 'transparent',
                  borderRight: `1px solid ${COLORS.divider}`,
                }}>
                  <LineNo n={old?.oldLineNo ?? null} color={old?.type === 'remove' ? COLORS.removeLineNo : COLORS.lineNoText} />
                  <span style={{
                    color: old?.type === 'remove' ? COLORS.removeText : COLORS.contextText,
                    flex: 1, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                  }}>
                    {old?.content ?? ''}
                  </span>
                </div>,
                // New (right)
                <div key={`new-${li}`} style={{
                  display: 'flex', gap: 6, padding: '1px 8px',
                  background: nw?.type === 'add' ? COLORS.addBg : 'transparent',
                }}>
                  <LineNo n={nw?.newLineNo ?? null} color={nw?.type === 'add' ? COLORS.addLineNo : COLORS.lineNoText} />
                  <span style={{
                    color: nw?.type === 'add' ? COLORS.addText : COLORS.contextText,
                    flex: 1, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                  }}>
                    {nw?.content ?? ''}
                  </span>
                </div>,
              ]
            })}
          </div>
        )
      })}
    </div>
  )
}

function WordsView({ hunks }: { hunks: DiffHunk[] }) {
  // Show full document with word-level highlighting on changed lines
  // Pair consecutive remove+add lines and apply diffWords
  return (
    <div style={{ fontFamily: 'monospace', fontSize: 11 }}>
      {hunks.map((hunk, hi) => {
        const rows: React.ReactNode[] = []
        let i = 0
        while (i < hunk.lines.length) {
          const line = hunk.lines[i]
          if (line.type === 'context') {
            rows.push(
              <div key={i} style={{ display: 'flex', gap: 8, padding: '1px 8px' }}>
                <LineNo n={line.newLineNo} color={COLORS.lineNoText} />
                <span style={{ color: COLORS.contextText, whiteSpace: 'pre-wrap' }}>{line.content}</span>
              </div>
            )
            i++
          } else {
            // Collect run
            const removes: DiffLine[] = []
            const adds: DiffLine[] = []
            while (i < hunk.lines.length && hunk.lines[i].type !== 'context') {
              if (hunk.lines[i].type === 'remove') removes.push(hunk.lines[i])
              else adds.push(hunk.lines[i])
              i++
            }
            // Render paired remove/add with word diff
            const maxLen = Math.max(removes.length, adds.length)
            for (let j = 0; j < maxLen; j++) {
              const rem = removes[j]
              const add = adds[j]
              if (rem && add) {
                // Word diff this pair
                const parts = diffWords(rem.content, add.content)
                rows.push(
                  <div key={`add-${i}-${j}`} style={{ display: 'flex', gap: 8, padding: '1px 8px', background: COLORS.addBg }}>
                    <LineNo n={add.newLineNo} color={COLORS.addLineNo} />
                    <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                      {parts.map((part, pi) => (
                        <span key={pi} style={{
                          color: part.added ? COLORS.addText : part.removed ? 'transparent' : COLORS.contextText,
                          textDecoration: part.removed ? 'line-through' : 'none',
                          display: part.removed ? 'none' : 'inline',
                        }}>
                          {part.value}
                        </span>
                      ))}
                    </span>
                  </div>
                )
              } else if (rem) {
                rows.push(
                  <div key={`rem-${i}-${j}`} style={{ display: 'flex', gap: 8, padding: '1px 8px', background: COLORS.removeBg }}>
                    <LineNo n={rem.oldLineNo} color={COLORS.removeLineNo} />
                    <span style={{ color: COLORS.removeText, textDecoration: 'line-through', whiteSpace: 'pre-wrap' }}>{rem.content}</span>
                  </div>
                )
              } else if (add) {
                rows.push(
                  <div key={`add2-${i}-${j}`} style={{ display: 'flex', gap: 8, padding: '1px 8px', background: COLORS.addBg }}>
                    <LineNo n={add.newLineNo} color={COLORS.addLineNo} />
                    <span style={{ color: COLORS.addText, whiteSpace: 'pre-wrap' }}>{add.content}</span>
                  </div>
                )
              }
            }
          }
        }
        return (
          <div key={hi}>
            <div style={{ padding: '2px 8px', background: '#0a0e1a', color: '#4b6bfb', fontSize: 10 }}>
              {hunk.header}
            </div>
            {rows}
          </div>
        )
      })}
    </div>
  )
}

export function DiffViewer({ hunks, mode, filename, truncated }: Props) {
  const { additions, deletions } = useMemo(() => diffStats(hunks), [hunks])
  const isEmpty = hunks.length === 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', background: COLORS.bg }}>
      {/* Stats header */}
      <div style={{
        padding: '4px 12px', background: '#060810',
        borderBottom: `1px solid ${COLORS.headerBorder}`,
        display: 'flex', gap: 16, alignItems: 'center', flexShrink: 0,
      }}>
        <span style={{ color: '#4CAF50', fontSize: 10 }}>+{additions} additions</span>
        <span style={{ color: '#ef4444', fontSize: 10 }}>−{deletions} deletions</span>
        <span style={{ color: '#555', fontSize: 10, marginLeft: 'auto' }}>{filename}</span>
        {truncated && <span style={{ color: '#FFC107', fontSize: 9 }}>diff truncated at 5000 lines</span>}
      </div>

      {/* Diff body */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {isEmpty ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 120, color: '#555', fontSize: 12, fontFamily: 'monospace' }}>
            No differences between selected commits
          </div>
        ) : mode === 'unified' ? (
          <UnifiedView hunks={hunks} />
        ) : mode === 'split' ? (
          <SplitView hunks={hunks} />
        ) : (
          <WordsView hunks={hunks} />
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd visualizer && bunx tsc --noEmit 2>&1 | grep -v 'node_modules' | head -20
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add visualizer/components/DiffViewer.tsx
git commit -m "feat(visualizer): add DiffViewer component with unified/split/words modes"
```

---

## Task 7: `CommitList` component

**Files:**
- Create: `visualizer/components/CommitList.tsx`

- [ ] **Step 1: Create `CommitList.tsx`**

Create `visualizer/components/CommitList.tsx`:

```typescript
'use client'

import type { CommitEntry } from '@/app/api/note/history/route'

interface Props {
  commits: CommitEntry[]
  fromHash: string | null
  toHash: string | null
  onSetFrom: (hash: string) => void
  onSetTo: (hash: string) => void
}

function relativeTime(isoDate: string): string {
  const diff = Date.now() - new Date(isoDate).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  if (days < 30) return `${days}d ago`
  return new Date(isoDate).toLocaleDateString()
}

export function CommitList({ commits, fromHash, toHash, onSetFrom, onSetTo }: Props) {
  const isSingleCommit = commits.length <= 1

  return (
    <div style={{
      width: 240, background: '#080b16',
      borderRight: '1px solid #1a2040',
      display: 'flex', flexDirection: 'column',
      overflow: 'hidden', flexShrink: 0,
    }}>
      {/* Header */}
      <div style={{
        padding: '6px 10px', borderBottom: '1px solid #111827', flexShrink: 0,
        color: '#555', fontSize: 9, letterSpacing: '1px',
        fontFamily: "'JetBrains Mono', monospace",
      }}>
        COMMITS · {commits.length} total
      </div>

      {/* List */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '6px 8px' }}>
        {commits.length === 0 && (
          <div style={{ color: '#555', fontSize: 11, padding: 8, fontFamily: 'monospace' }}>
            No version history found.
          </div>
        )}

        {commits.map((commit, idx) => {
          const isFrom = commit.hash === fromHash
          const isTo = commit.hash === toHash

          return (
            <div key={commit.hash} style={{
              padding: '6px 8px', borderRadius: 4, marginBottom: 4,
              border: `1px solid ${isFrom ? '#1e3a5f' : isTo ? '#2d4a1e' : 'transparent'}`,
              background: isFrom ? 'rgba(75,107,251,0.08)' : isTo ? 'rgba(76,175,80,0.06)' : 'transparent',
              fontFamily: 'monospace',
            }}>
              {/* FROM/TO badges row */}
              <div style={{ display: 'flex', gap: 4, marginBottom: 3, alignItems: 'center' }}>
                <button
                  onClick={() => { if (commit.hash !== toHash) onSetFrom(commit.hash) }}
                  disabled={commit.hash === toHash}
                  style={{
                    padding: '1px 5px', borderRadius: 2, fontSize: 8, cursor: 'pointer',
                    background: isFrom ? '#1e3a5f' : 'rgba(75,107,251,0.1)',
                    color: isFrom ? '#4b6bfb' : '#555',
                    border: `1px solid ${isFrom ? '#4b6bfb' : '#1a2040'}`,
                    fontWeight: isFrom ? 700 : 400,
                    opacity: commit.hash === toHash ? 0.3 : 1,
                  }}
                >
                  FROM
                </button>
                <button
                  onClick={() => { if (!isSingleCommit && commit.hash !== fromHash) onSetTo(commit.hash) }}
                  disabled={isSingleCommit || commit.hash === fromHash}
                  style={{
                    padding: '1px 5px', borderRadius: 2, fontSize: 8, cursor: 'pointer',
                    background: isTo ? '#2d4a1e' : 'rgba(76,175,80,0.1)',
                    color: isTo ? '#4CAF50' : '#555',
                    border: `1px solid ${isTo ? '#4CAF50' : '#1a2040'}`,
                    fontWeight: isTo ? 700 : 400,
                    opacity: (isSingleCommit || commit.hash === fromHash) ? 0.3 : 1,
                  }}
                >
                  TO
                </button>
                <span style={{ color: '#555', fontSize: 9, marginLeft: 'auto', fontFamily: 'monospace' }}>
                  {commit.shortHash}
                </span>
              </div>

              {/* Commit message */}
              <div style={{
                color: (isFrom || isTo) ? '#aaa' : '#666',
                fontSize: 10, marginBottom: 2,
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
              }}>
                {commit.message || '(no message)'}
              </div>

              {/* Timestamp */}
              <div style={{ color: '#444', fontSize: 9 }}>
                {relativeTime(commit.date)}
                {idx === 0 && <span style={{ color: '#555', marginLeft: 6 }}>· latest</span>}
              </div>
            </div>
          )
        })}

        {isSingleCommit && commits.length === 1 && (
          <div style={{ color: '#555', fontSize: 10, padding: '4px 8px', fontFamily: 'monospace' }}>
            Only one version — no diff available.
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify TypeScript**

```bash
cd visualizer && bunx tsc --noEmit 2>&1 | grep -v 'node_modules' | head -20
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add visualizer/components/CommitList.tsx
git commit -m "feat(visualizer): add CommitList component with FROM/TO badge selection"
```

---

## Task 8: `HistoryView` component

**Files:**
- Create: `visualizer/components/HistoryView.tsx`

This wires together `CommitList` and `DiffViewer`, fetches data from the two new API routes, and owns the diff mode toggle.

- [ ] **Step 1: Create `HistoryView.tsx`**

Create `visualizer/components/HistoryView.tsx`:

```typescript
'use client'

import { useState, useEffect, useCallback } from 'react'
import { CommitList } from './CommitList'
import { DiffViewer } from './DiffViewer'
import type { DiffMode } from './DiffViewer'
import { parseDiff } from '@/lib/parseDiff'
import type { CommitEntry } from '@/app/api/note/history/route'
import type { NoteNode } from '@/lib/graph'

interface Props {
  stem: string
  node: NoteNode | null
  onClose: () => void
}

const BUTTON_BASE: React.CSSProperties = {
  padding: '2px 7px', fontSize: 9, cursor: 'pointer', border: 'none',
  fontFamily: "'JetBrains Mono', monospace",
}

export function HistoryView({ stem, node, onClose }: Props) {
  const [commits, setCommits] = useState<CommitEntry[]>([])
  const [loadingCommits, setLoadingCommits] = useState(true)
  const [commitsError, setCommitsError] = useState<string | null>(null)

  const [fromHash, setFromHash] = useState<string | null>(null)
  const [toHash, setToHash] = useState<string | null>(null)

  const [rawDiff, setRawDiff] = useState<string>('')
  const [truncated, setTruncated] = useState(false)
  const [loadingDiff, setLoadingDiff] = useState(false)
  const [diffError, setDiffError] = useState<string | null>(null)

  const [diffMode, setDiffMode] = useState<DiffMode>('split')

  // Load commit history on mount
  useEffect(() => {
    setLoadingCommits(true)
    setCommitsError(null)
    fetch(`/api/note/history?stem=${encodeURIComponent(stem)}`)
      .then(r => r.json())
      .then(data => {
        if (data.error) { setCommitsError(data.error); return }
        const list: CommitEntry[] = data.commits ?? []
        setCommits(list)
        // Default: FROM = latest (index 0), TO = previous (index 1)
        if (list.length >= 2) {
          setFromHash(list[0].hash)
          setToHash(list[1].hash)
        } else if (list.length === 1) {
          setFromHash(list[0].hash)
          setToHash(null)
        }
      })
      .catch(e => setCommitsError((e as Error).message))
      .finally(() => setLoadingCommits(false))
  }, [stem])

  // Fetch diff whenever from/to changes
  useEffect(() => {
    if (!fromHash || !toHash) { setRawDiff(''); return }
    setLoadingDiff(true)
    setDiffError(null)
    fetch(`/api/note/diff?stem=${encodeURIComponent(stem)}&from=${encodeURIComponent(fromHash)}&to=${encodeURIComponent(toHash)}`)
      .then(r => r.json())
      .then(data => {
        if (data.error) { setDiffError(data.error); return }
        setRawDiff(data.diff ?? '')
        setTruncated(data.truncated ?? false)
      })
      .catch(e => setDiffError((e as Error).message))
      .finally(() => setLoadingDiff(false))
  }, [stem, fromHash, toHash])

  const hunks = parseDiff(rawDiff)
  const filename = node?.path.split('/').pop() ?? `${stem}.md`

  const handleSetFrom = useCallback((hash: string) => {
    setFromHash(hash)
    // If new FROM matches current TO, clear TO
    if (hash === toHash) setToHash(null)
  }, [toHash])

  const handleSetTo = useCallback((hash: string) => {
    setToHash(hash)
    if (hash === fromHash) setFromHash(null)
  }, [fromHash])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', fontFamily: "'JetBrains Mono', monospace" }}>

      {/* HistoryView toolbar */}
      <div style={{
        height: 34, background: '#0a0e1a', borderBottom: '1px solid #1a2040',
        display: 'flex', alignItems: 'center', padding: '0 12px', gap: 10, flexShrink: 0,
      }}>
        <button onClick={onClose} style={{
          ...BUTTON_BASE,
          background: 'none', border: '1px solid #1a2040', borderRadius: 3,
          color: '#00FFC8',
        }}>
          ← Back
        </button>
        <span style={{ color: '#888', fontSize: 10 }}>{stem}</span>
        <span style={{ color: '#333', fontSize: 10 }}>—</span>
        <span style={{ color: '#4b6bfb', fontSize: 10 }}>Version History</span>

        <div style={{ flex: 1 }} />

        {/* Diff mode toggle */}
        <div style={{
          display: 'flex', gap: 1,
          background: '#0C0F1E', border: '1px solid #1a2040', borderRadius: 4, overflow: 'hidden',
        }}>
          {(['unified', 'split', 'words'] as DiffMode[]).map(m => (
            <button key={m} onClick={() => setDiffMode(m)} style={{
              ...BUTTON_BASE,
              background: diffMode === m ? '#4b6bfb' : 'transparent',
              color: diffMode === m ? '#0C0F1E' : '#555',
            }}>
              {m.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {/* Body: commit list + diff */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>

        {/* Left: commit list */}
        {loadingCommits ? (
          <div style={{ width: 240, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#555', fontSize: 11, background: '#080b16', borderRight: '1px solid #1a2040' }}>
            Loading history...
          </div>
        ) : commitsError ? (
          <div style={{ width: 240, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#ef4444', fontSize: 11, padding: 12, background: '#080b16', borderRight: '1px solid #1a2040', textAlign: 'center' }}>
            {commitsError}
          </div>
        ) : (
          <CommitList
            commits={commits}
            fromHash={fromHash}
            toHash={toHash}
            onSetFrom={handleSetFrom}
            onSetTo={handleSetTo}
          />
        )}

        {/* Right: diff viewer */}
        <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          {!fromHash || !toHash ? (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#555', fontSize: 12 }}>
              {commits.length <= 1 ? 'Only one version — no diff available.' : 'Select FROM and TO commits to compare.'}
            </div>
          ) : loadingDiff ? (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#555', fontSize: 12 }}>
              Loading diff...
            </div>
          ) : diffError ? (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#ef4444', fontSize: 12, padding: 24, textAlign: 'center' }}>
              {diffError}
            </div>
          ) : (
            <DiffViewer hunks={hunks} mode={diffMode} filename={filename} truncated={truncated} />
          )}
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify TypeScript**

```bash
cd visualizer && bunx tsc --noEmit 2>&1 | grep -v 'node_modules' | head -20
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add visualizer/components/HistoryView.tsx
git commit -m "feat(visualizer): add HistoryView split-screen component"
```

---

## Task 9: Wire `HistoryView` into `page.tsx`

**Files:**
- Modify: `visualizer/app/page.tsx`

- [ ] **Step 1: Import `HistoryView`**

At the top of `page.tsx`, add after the other component imports:

```typescript
import { HistoryView } from '@/components/HistoryView'
```

- [ ] **Step 2: Replace the content area conditional render**

Note: `state.nodeMap` already exists in `useVisualizerState` (it is built from `graphData.nodes` and returned as part of the state object). Task 5 only adds the history-specific fields — `nodeMap` requires no changes.

Find this block in `page.tsx` (around line 194):

```typescript
{state.viewMode === 'read' ? (
  <ReadingPane
    node={state.activeNode}
    fetchContent={state.fetchNoteContent}
    onNavigate={handleNavigate}
    onSave={state.saveNote}
    onDelete={handleDelete}
    nodes={graphData.nodes}
  />
) : (
```

Replace with:

```typescript
{state.historyMode && state.historyNote ? (
  <HistoryView
    stem={state.historyNote}
    node={state.nodeMap.get(state.historyNote) ?? null}
    onClose={state.closeHistory}
  />
) : state.viewMode === 'read' ? (
  <ReadingPane
    node={state.activeNode}
    fetchContent={state.fetchNoteContent}
    onNavigate={handleNavigate}
    onSave={state.saveNote}
    onDelete={handleDelete}
    onOpenHistory={state.openHistory}
    nodes={graphData.nodes}
  />
) : (
```

- [ ] **Step 3: Pass `onOpenHistory` and `onDeleteNote` to `FileExplorer`**

Find the `<FileExplorer` block in `page.tsx`. Add these props:

```typescript
onOpenHistory={state.openHistory}
onDeleteNote={handleDelete}
```

- [ ] **Step 4: Verify TypeScript (expect prop errors — will fix in next tasks)**

```bash
cd visualizer && bunx tsc --noEmit 2>&1 | grep -v 'node_modules' | head -20
```

Expected: errors about `onOpenHistory` prop not existing on `ReadingPane` and `FileExplorer` — that's fine, fix in Tasks 10 and 11.

- [ ] **Step 5: Defer `page.tsx` commit — continue to Task 10**

Do NOT commit `page.tsx` yet. TypeScript errors remain until Tasks 10, 11, and 12 add the `onOpenHistory` prop to `ReadingPane`, `FileExplorer`, and `GraphCanvas`. Commit `page.tsx` as part of Task 12 Step 7 (the final commit that also adds `onOpenHistory` to `GraphCanvas`).

---

## Task 10: `ReadingPane` — add History button

**Files:**
- Modify: `visualizer/components/ReadingPane.tsx`

- [ ] **Step 1: Add `onOpenHistory` to the Props interface**

Find the `interface Props` in `ReadingPane.tsx`:

```typescript
interface Props {
  node: NoteNode | null
  fetchContent: (stem: string) => Promise<string>
  onNavigate: (stem: string, newTab: boolean) => void
  onSave: (stem: string, content: string) => Promise<void>
  onDelete: (stem: string) => Promise<void>
  nodes: NoteNode[]
}
```

Replace with:

```typescript
interface Props {
  node: NoteNode | null
  fetchContent: (stem: string) => Promise<string>
  onNavigate: (stem: string, newTab: boolean) => void
  onSave: (stem: string, content: string) => Promise<void>
  onDelete: (stem: string) => Promise<void>
  onOpenHistory: (stem: string) => void
  nodes: NoteNode[]
}
```

- [ ] **Step 2: Destructure the new prop**

Find the function signature:
```typescript
export function ReadingPane({ node, fetchContent, onNavigate, onSave, onDelete, nodes }: Props) {
```
Replace with:
```typescript
export function ReadingPane({ node, fetchContent, onNavigate, onSave, onDelete, onOpenHistory, nodes }: Props) {
```

- [ ] **Step 3: Add the History button to the toolbar**

Search for the toolbar area in `ReadingPane.tsx` — look for the Edit/Save/Delete buttons in the toolbar. After the delete button (or at the end of the toolbar button row), add a History button. Find the section with buttons like "EDIT" or "SAVE" and add alongside:

```typescript
{!isEditing && node && (
  <button
    onClick={() => onOpenHistory(node.id)}
    title="Version History"
    style={{
      background: 'none', border: '1px solid #1a2040', borderRadius: 3,
      color: '#888', cursor: 'pointer', padding: '2px 8px', fontSize: 10,
      fontFamily: "'JetBrains Mono', monospace",
    }}
  >
    HISTORY
  </button>
)}
```

Place this button after the existing toolbar buttons but before the closing tag of the toolbar div.

- [ ] **Step 4: Verify TypeScript**

```bash
cd visualizer && bunx tsc --noEmit 2>&1 | grep -v 'node_modules' | head -20
```

Expected: `ReadingPane` errors resolve. Only `FileExplorer` error may remain.

- [ ] **Step 5: Commit ReadingPane only (page.tsx commits in Task 12)**

```bash
git add visualizer/components/ReadingPane.tsx
git commit -m "feat(visualizer): add History button to ReadingPane toolbar"
```

---

## Task 11: `FileExplorer` — right-click context menu

**Files:**
- Modify: `visualizer/components/FileExplorer.tsx`

- [ ] **Step 1: Add `onOpenHistory` and `onDeleteNote` to `FileExplorer` Props**

Find the `interface Props` in `FileExplorer.tsx` and add:

```typescript
onOpenHistory: (stem: string) => void
onDeleteNote?: (stem: string) => void   // optional — used for context menu Delete
```

Also add to the destructured params in the function signature.

- [ ] **Step 2: Add context menu state**

Inside the `FileExplorer` function, add state for the context menu:

```typescript
const [contextMenu, setContextMenu] = useState<{ stem: string; x: number; y: number } | null>(null)
```

Also add an `import { useState } from 'react'` if not already imported (it has `useRef, useCallback, useEffect` — add `useState` to that import).

- [ ] **Step 3: Add right-click handler on file items**

Find where individual file items are rendered in the JSX (look for `onClick={() => onSelectNote(node.id, ...)}` — this is on a `div` or similar element for each file). Add `onContextMenu` to that element:

```typescript
onContextMenu={(e) => {
  e.preventDefault()
  setContextMenu({ stem: node.id, x: e.clientX, y: e.clientY })
}}
```

- [ ] **Step 4: Add context menu JSX and close-on-click-outside**

Add a `useEffect` to dismiss the context menu on any click:

```typescript
useEffect(() => {
  if (!contextMenu) return
  const dismiss = () => setContextMenu(null)
  window.addEventListener('click', dismiss)
  return () => window.removeEventListener('click', dismiss)
}, [contextMenu])
```

Add context menu JSX at the end of the FileExplorer return, before the closing `</div>`:

```typescript
{contextMenu && (
  <div
    style={{
      position: 'fixed', left: contextMenu.x, top: contextMenu.y,
      background: '#0a0e1a', border: '1px solid #1a2040', borderRadius: 4,
      zIndex: 1000, minWidth: 140, boxShadow: '0 4px 16px rgba(0,0,0,0.6)',
      fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
    }}
    onClick={e => e.stopPropagation()}
  >
    <div
      style={{ padding: '6px 12px', cursor: 'pointer', color: '#ccc' }}
      onMouseEnter={e => (e.currentTarget.style.background = '#1a2040')}
      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
      onClick={() => { onSelectNote(contextMenu.stem, false); setContextMenu(null) }}
    >
      Open
    </div>
    <div
      style={{ padding: '6px 12px', cursor: 'pointer', color: '#00FFC8' }}
      onMouseEnter={e => (e.currentTarget.style.background = '#1a2040')}
      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
      onClick={() => { onOpenHistory(contextMenu.stem); setContextMenu(null) }}
    >
      View History
    </div>
    {onDeleteNote && (
      <div
        style={{ padding: '6px 12px', cursor: 'pointer', color: '#ef4444', borderTop: '1px solid #1a2040' }}
        onMouseEnter={e => (e.currentTarget.style.background = '#1a2040')}
        onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
        onClick={() => { onDeleteNote(contextMenu.stem); setContextMenu(null) }}
      >
        Delete
      </div>
    )}
  </div>
)}
```

- [ ] **Step 5: Verify TypeScript**

```bash
cd visualizer && bunx tsc --noEmit 2>&1 | grep -v 'node_modules' | head -20
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add visualizer/components/FileExplorer.tsx
git commit -m "feat(visualizer): add right-click context menu with View History to FileExplorer"
```

---

## Task 12: `GraphCanvas` — right-click on nodes

**Files:**
- Modify: `visualizer/components/GraphCanvas.tsx`

GraphCanvas uses Sigma.js. Sigma fires a `rightClickNode` event on the sigma instance.

- [ ] **Step 1: Add `onOpenHistory` to GraphCanvas Props**

Search for the `interface` or `type` defining `GraphCanvas` props near the top of `GraphCanvas.tsx`. Add:

```typescript
onOpenHistory?: (stem: string) => void
```

Also destructure it in the component function.

- [ ] **Step 2: Add context menu state**

At the top of the GraphCanvas function body, add:

```typescript
const [nodeContextMenu, setNodeContextMenu] = useState<{ stem: string; x: number; y: number } | null>(null)
```

Add `useState` to the React import if not already there.

- [ ] **Step 3: Check the existing `clickNode` event shape in `GraphCanvas.tsx`**

Before writing the `rightClickNode` handler, read `visualizer/components/GraphCanvas.tsx` and find the existing `sigma.on('clickNode', ...)` handler. Note the exact type of the event object (especially how `x`/`y` are accessed and whether a `preventSigmaDefault` or similar method exists). The `rightClickNode` event uses the same shape. Adapt the code below to match what you observe.

- [ ] **Step 4: Register `rightClickNode` on the Sigma instance**

Find the place in `GraphCanvas.tsx` where Sigma event listeners are registered (same block as `clickNode`). Add:

```typescript
sigma.on('rightClickNode', (e: { node: string; event: MouseEvent & { preventSigmaDefault?: () => void } }) => {
  e.event.preventDefault()           // suppress browser right-click menu
  e.event.preventSigmaDefault?.()    // suppress Sigma's default if it exists
  const rect = (e.event.target as HTMLElement)?.closest('canvas')?.getBoundingClientRect()
  const x = e.event.clientX
  const y = e.event.clientY
  setNodeContextMenu({ stem: e.node, x, y })
})
```

**Note:** The `e.event.clientX/clientY` approach works when Sigma exposes a native `MouseEvent`. If the codebase wraps it differently (check the `clickNode` handler you read in Step 3), use the same coordinate extraction pattern already in use.

Also add a click-outside dismiss handler in the same useEffect (or a separate one):

```typescript
const dismissMenu = () => setNodeContextMenu(null)
sigma.on('clickStage', dismissMenu)
```

- [ ] **Step 5: Add context menu JSX**

At the end of the GraphCanvas component's return, add the same context menu pattern as FileExplorer:

```typescript
{nodeContextMenu && (
  <div
    style={{
      position: 'fixed', left: nodeContextMenu.x, top: nodeContextMenu.y,
      background: '#0a0e1a', border: '1px solid #1a2040', borderRadius: 4,
      zIndex: 1000, minWidth: 160, boxShadow: '0 4px 16px rgba(0,0,0,0.6)',
      fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
    }}
    onClick={e => e.stopPropagation()}
  >
    <div
      style={{ padding: '6px 12px', cursor: 'pointer', color: '#ccc' }}
      onMouseEnter={e => (e.currentTarget.style.background = '#1a2040')}
      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
      onClick={() => { onNodeClick(nodeContextMenu.stem, false); setNodeContextMenu(null) }}
    >
      Open in Reading Pane
    </div>
    {onOpenHistory && (
      <div
        style={{ padding: '6px 12px', cursor: 'pointer', color: '#00FFC8' }}
        onMouseEnter={e => (e.currentTarget.style.background = '#1a2040')}
        onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
        onClick={() => { onOpenHistory(nodeContextMenu.stem); setNodeContextMenu(null) }}
      >
        View History
      </div>
    )}
  </div>
)}
```

- [ ] **Step 6: Pass `onOpenHistory` from `page.tsx` to `GraphCanvas`**

In `page.tsx`, find `<GraphCanvas` and add:

```typescript
onOpenHistory={state.openHistory}
```

- [ ] **Step 7: Verify TypeScript — all errors should now be resolved**

```bash
cd visualizer && bunx tsc --noEmit 2>&1 | grep -v 'node_modules' | head -20
```

Expected: no errors. All `onOpenHistory` props are now wired through `ReadingPane`, `FileExplorer`, and `GraphCanvas`.

- [ ] **Step 8: Commit all remaining wiring together**

```bash
git add visualizer/components/GraphCanvas.tsx visualizer/components/FileExplorer.tsx visualizer/app/page.tsx
git commit -m "feat(visualizer): add right-click View History context menu to GraphCanvas nodes; wire page.tsx"
```

---

## Task 13: End-to-end smoke test

**No new files.** Verify the full feature works together.

- [ ] **Step 1: Start the dev server**

```bash
cd visualizer && bun run dev
```

Open `http://localhost:3999`

- [ ] **Step 2: Test ReadingPane History button**

1. Open any note in Reading mode
2. Click the `HISTORY` button in the toolbar
3. Verify: HistoryView appears with commit list on the left, diff on the right
4. Verify: FROM/TO default to latest two commits with diff shown
5. Verify: Toggle UNIFIED / SPLIT / WORDS modes — all render correctly
6. Verify: Click FROM/TO badges on different commits — diff updates
7. Click `← Back` — verify returns to read mode

- [ ] **Step 3: Test FileExplorer right-click**

1. Right-click any file in the sidebar
2. Verify context menu appears with "Open" and "View History"
3. Click "View History" — verify HistoryView opens for that note

- [ ] **Step 4: Test GraphCanvas right-click**

1. Switch to Graph mode
2. Right-click any node
3. Verify context menu with "Open in Reading Pane" and "View History"
4. Click "View History" — verify HistoryView opens

- [ ] **Step 5: Test edge cases**

- Note with no git history: verify "No version history found." message
- Note with single commit: verify "Only one version — no diff available." and TO badge is disabled
- Click the `← Back` button: verify returns to the previous view mode (read or graph)
- Open a different note via a tab while in HistoryView: HistoryView remains open for the current `historyNote` stem — this is expected. To view another note's history, use Back first, then open the other note's history.

- [ ] **Step 6: Run unit tests one final time**

```bash
cd visualizer && bun test lib/parseDiff.test.ts
```

Expected: all tests pass.

- [ ] **Step 7: Final commit**

```bash
git add -A
git commit -m "feat(visualizer): git diff viewer — end-to-end integration complete"
```
