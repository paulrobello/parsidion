# Git Diff Viewer — Design Spec

**Date:** 2026-03-23
**Project:** parsidion-cc visualizer
**Status:** Approved

---

## Overview

Add a git diff viewer to the web visualizer that lets users browse version history for any vault note and compare any two commits with syntax-highlighted diffs. Accessible via a toolbar button in the ReadingPane and right-click context menus on file explorer entries and graph nodes.

---

## Architecture

### Approach

A new top-level `HistoryView` component replaces `ReadingPane` when history mode is active. `useVisualizerState` gets a `historyMode` boolean and `historyNote` stem; `page.tsx` conditionally renders `HistoryView` or `ReadingPane` based on this flag.

### New Components

| Component | Purpose |
|---|---|
| `HistoryView` | Full split-screen container — commits left, diff right |
| `CommitList` | Scrollable list of commits with FROM/TO selection |
| `DiffViewer` | Renders diff in unified / side-by-side / word-diff modes |

### New API Routes

| Route | Method | Purpose |
|---|---|---|
| `/api/note/history` | GET `?stem=<stem>` | Returns git log for the note file |
| `/api/note/diff` | GET `?stem=<stem>&from=<hash>&to=<hash>` | Returns diff between two commits |

Both routes shell out to git inside `VAULT_ROOT`. Both routes must verify the resolved file path starts with `VAULT_ROOT` before executing any git command (path traversal protection, same pattern as the existing `guardPath()` in `/api/note/route.ts`).

### State additions to `useVisualizerState`

```typescript
historyMode: boolean
historyNote: string | null        // stem of note being viewed
openHistory: (stem: string) => void
closeHistory: () => void
```

`openHistory` saves the current `viewMode` into a `prevViewMode` field before setting `historyMode: true`. `closeHistory` restores `viewMode` from `prevViewMode` and clears `historyMode` and `historyNote`.

---

## UI Layout

### HistoryView (split-screen)

```
┌─ Toolbar (existing, 42px) ─────────────────────────────┐
├─────────────────────────────────────────────────────────┤
│  File Explorer  │  HistoryView                          │
│  (unchanged)    │                                       │
│                 │  ┌─ HistoryView toolbar ────────────┐ │
│                 │  │ [← Back]  note-stem — History    │ │
│                 │  │           [UNIFIED|SPLIT|WORDS]  │ │
│                 │  ├──────────────┬───────────────────┤ │
│                 │  │ Commit list  │ Diff viewer        │ │
│                 │  │ (240px)      │ (flex 1)           │ │
│                 │  │              │                    │ │
│                 │  │ [FROM] hash  │ +12  −5  file.md  │ │
│                 │  │ message      │ ──────────────     │ │
│                 │  │ time         │ old  │  new        │ │
│                 │  │              │      │             │ │
│                 │  │ [TO]  hash   │ line │  line       │ │
│                 │  │ message      │      │             │ │
│                 │  │ time         │      │             │ │
│                 │  │              │      │             │ │
│                 │  │ ○  hash      │      │             │ │
│                 │  │ ...          │      │             │ │
└─────────────────┴──┴──────────────┴───────────────────┘
```

### Commit List (left panel, 240px)

- Header: "COMMITS · N total"
- Each row: FROM/TO badge (blue/green when selected), short hash, commit message (truncated), relative timestamp
- Click behaviour:
  - Each commit row has two distinct clickable badges: **[FROM]** and **[TO]** (shown as small buttons on hover, always shown when that commit is selected)
  - Clicking **[FROM]** on any commit sets it as the FROM reference; clicking **[TO]** sets it as TO
  - FROM and TO cannot be the same commit — setting FROM to the current TO automatically clears TO (and vice versa)
  - Default on open: FROM = latest commit, TO = previous commit
- Scrollable, supports any number of commits

### Diff Viewer (right panel)

**Header strip:** `+N additions  −N deletions  filename.md`

**Three render modes (toggle in HistoryView toolbar, default: SPLIT):**

1. **UNIFIED** — single column, lines prefixed `+` / `-`, context lines in between. Line numbers on left.
2. **SPLIT** — two columns side by side: FROM left, TO right, aligned line-by-line. Line numbers on each side. Red background on removed lines (left), green background on added lines (right).
3. **WORDS** — full document shown with changed words highlighted inline. Red strikethrough for removed words, green for added words. No line-level coloring.

All modes: monospace font, scrollable, respects the existing dark sci-fi color scheme (`#0C0F1E` background, `#ef4444` deletions, `#4CAF50` additions, `#555` context).

---

## API Design

### GET `/api/note/history?stem=<stem>`

Runs:
```bash
git log --follow --format="%H|%ai|%s" -- <resolved_filepath>
```
inside `VAULT_ROOT`.

Response:
```typescript
interface CommitEntry {
  hash: string       // full SHA
  shortHash: string  // first 7 chars
  date: string       // ISO 8601
  message: string    // commit subject
}
// Returns: { commits: CommitEntry[] }
```

Returns empty `commits: []` if the file has no git history (not an error).

### GET `/api/note/diff?stem=<stem>&from=<hash>&to=<hash>`

- `from` and `to` are full or short git SHAs. The special value `working` for `to` means the current on-disk file (uncommitted working tree).
- Git commands used:
  - Normal case (both SHAs): `git diff <from> <to> -- <filepath>`
  - Working tree case (`to=working`): `git diff <from> -- <filepath>` (no second SHA; diffs committed state vs working tree)
- Returns raw unified diff string; parsing into hunks happens client-side.
- The file header lines (`--- a/...`, `+++ b/...`) are included in the raw output. The client-side parser strips them before building the `DiffHunk[]` model.

Response:
```typescript
{ diff: string }   // raw unified diff output
```

---

## Entry Points (Triggering History Mode)

### 1. ReadingPane toolbar button

Add a clock/history icon button to the existing toolbar row in `ReadingPane`. On click: `openHistory(activeStem)`.

### 2. File Explorer right-click context menu

Add a context menu to file items in `FileExplorer`. Right-clicking a file shows:
- Open
- **View History** → `openHistory(stem)`
- Delete

### 3. Graph node right-click

Add a right-click handler to `GraphCanvas` nodes. Shows same context menu:
- Open in Reading Pane
- **View History** → `openHistory(stem)`

---

## Diff Parsing (Client-side)

Parse the raw unified diff string into a structured hunk model for rendering:

```typescript
interface DiffLine {
  type: 'add' | 'remove' | 'context'
  content: string
  oldLineNo: number | null
  newLineNo: number | null
}

interface DiffHunk {
  header: string   // @@ -L,N +L,N @@
  lines: DiffLine[]
}
```

For WORDS mode, apply a secondary word-level diff on changed line pairs using the [`diff`](https://www.npmjs.com/package/diff) npm package (`diffWords` function). This package is already used in similar Next.js projects and provides Myers-based word diffing with no extra setup. Install with `bun add diff` + `bun add -d @types/diff`.

---

## Error & Edge Cases

| Scenario | Handling |
|---|---|
| File not in git history | Show "No version history found" empty state |
| Git not available | Show "Git not available in vault" with instructions |
| Single commit | FROM is auto-selected and shown read-only. TO selection is disabled. Show "Only one version — no diff available." The diff panel shows the full file content (no `+`/`-` lines) as a reference view. |
| Binary or very large diff | Cap at 5000 lines with "diff truncated" notice |
| `from === to` | Show "Select two different commits to compare" |
| VAULT_ROOT not a git repo | API returns `{ commits: [] }`, UI shows empty state |

---

## Files to Create / Modify

### New files
- `visualizer/app/api/note/history/route.ts`
- `visualizer/app/api/note/diff/route.ts`
- `visualizer/components/HistoryView.tsx`
- `visualizer/components/CommitList.tsx`
- `visualizer/components/DiffViewer.tsx`
- `visualizer/lib/parseDiff.ts`

### Modified files
- `visualizer/lib/useVisualizerState.ts` — add `historyMode`, `historyNote`, `openHistory`, `closeHistory`
- `visualizer/app/page.tsx` — render `HistoryView` instead of `ReadingPane` when `historyMode` is true
- `visualizer/components/ReadingPane.tsx` — add History button to toolbar
- `visualizer/components/FileExplorer.tsx` — add right-click context menu
- `visualizer/components/GraphCanvas.tsx` — add right-click handler on nodes

---

## Out of Scope

- Restoring / reverting a note to a previous version (read-only history view only)
- Diffing across branches
- Showing diffs for notes outside `VAULT_ROOT`
- Authentication / permissions (single-user local tool)
