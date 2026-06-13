# Vault Visualizer Redesign — Obsidian-Style Layout

> **Note:** Historical design document. Path references and project names may reflect the legacy `parsidion-cc` name (renamed to `parsidion` in v0.7.0). Do not edit this document.

**Date:** 2026-03-21
**Status:** Implemented
**Last Updated:** 2026-03-24

## Overview

The vault visualizer has been redesigned from a graph-first app with a slide-in note sidebar into an Obsidian-style layout with a browse-only file explorer, tabbed reading pane, and a toggle between Read and Graph modes.

**Note:** The implementation extends beyond the original spec to include note editing, version history, conflict detection, and real-time vault synchronization.

## Goals

- Primary interaction becomes browsing and reading notes (not the graph) — **Implemented**
- File explorer sidebar for navigating the vault folder structure — **Implemented**
- Full-width reading pane with tabbed multi-note support — **Implemented**
- Graph mode accessible as a toggle, defaulting to local neighborhood view — **Implemented**
- Single unified search replaces the current dual-search pattern — **Implemented**

## Table of Contents
- [Layout](#layout)
- [Read Mode](#read-mode)
  - [Reading Pane](#reading-pane)
  - [Note Editing](#note-editing-implementation-extension)
  - [History View](#history-view-implementation-extension)
  - [Tab Behavior](#tab-behavior)
- [Graph Mode](#graph-mode)
  - [Default: Local Neighborhood](#default-local-neighborhood)
  - [Full Vault Mode](#full-vault-mode)
  - [Graph Controls (HUD Panel)](#graph-controls-hud-panel)
  - [Force Simulation](#force-simulation-implementation-detail)
  - [Graph Interactions](#graph-interactions)
- [Unified Search](#unified-search-k)
- [File Tree Data](#file-tree-data)
- [State Persistence](#state-persistence-localstorage)
- [Component Architecture](#component-architecture)
- [Graph Neighborhood Algorithm](#graph-neighborhood-algorithm)
- [Keyboard Shortcuts](#keyboard-shortcuts)
- [Error & Empty States](#error--empty-states)
- [Real-Time Synchronization](#real-time-synchronization-implementation-extension)
- [Non-Goals](#non-goals)
- [Post-Implementation Additions](#post-implementation-additions)
- [Migration Notes](#migration-notes)
- [Related Documentation](#related-documentation)

## Layout

### Three Zones

1. **Left sidebar** — resizable, collapsible file tree (browse-only)
2. **Toolbar** — hamburger toggle, tab bar (with permanent Graph tab), unified search, new note button, WebSocket status indicator
3. **Content area** — full-width, switches between reading pane and graph canvas

### Sidebar

- Browse-only folder tree rooted at the vault directory
- Folders show expand/collapse chevrons and note counts
- Files show as clickable items; the currently active note is highlighted with an indigo left border and background tint
- Header shows "Vault" label and total note count
- No search box — all search goes through the unified ⌘K in the toolbar
- Resizable via drag handle on the right edge (4px transparent hit zone, `cursor: col-resize`, indigo highlight during drag); width persisted to localStorage
- Collapsible via ☰ hamburger button in the toolbar or ⌘B; collapsed state persisted
- Minimum width ~180px, maximum ~400px
- Auto-collapses below 768px viewport width
- **Right-click context menu** on notes: Open, View History, Delete
- Mobile/responsive beyond auto-collapse is out of scope

### Toolbar

Fixed horizontal bar above the content area containing (left to right):

- **☰ hamburger** — toggles sidebar visibility
- **Tab bar** — horizontal tabs for open notes, each with a colored dot (by note type), title, and ✕ close button. Active tab has a distinct background and bottom border that blends into the content area. Inactive tabs are dimmer. Tabs are scrollable if they overflow.
  - **Graph tab** — permanent tab that switches to graph mode (no close button)
  - **Note tabs** — one per open note, closable via ✕ button
- **WebSocket status indicator** — colored dot showing sync connection state (green=connected, amber=connecting, red=disconnected) with hover tooltip
- **New note button (+)** — opens dialog to create a new note
- **⌘K search** — unified search input (right-aligned), 240px wide

### Content Area

Fills all remaining space. In Read mode, renders the selected note's markdown. In Graph mode, renders the force-directed graph.

## Read Mode

### Reading Pane

- Full-width content area with centered column (max-width 720px, auto margins)
- Generous padding (32px vertical, 48px horizontal)
- Note header: type badge (colored by type), date, confidence level
- Title: large Oxanium heading
- Tags: horizontal list of pill badges below the title
- Body: rendered markdown (GitHub Flavored Markdown) using Syne font for body, JetBrains Mono for code blocks
- Wikilinks rendered in purple with dotted underline; clickable
- Click wikilink → replaces current tab content
- Cmd+click (or Ctrl+click) wikilink → opens in new tab
- Related section at the bottom shows wikilinks from the `related` frontmatter field

### Note Editing (Implementation Extension)

The reading pane includes full note editing capabilities:

- **Edit button** — enters edit mode (also ⌘E keyboard shortcut)
- **Edit/Preview toggle** — switch between raw markdown and rendered preview while editing
- **Frontmatter editor** — structured form for editing YAML frontmatter fields (date, type, tags, project, confidence, related, sources)
- **Conflict detection** — if the note was modified externally (via WebSocket notification), save shows a conflict dialog with diff viewer
- **Save/Cancel** — ⌘S to save, Escape to cancel
- **Delete button** — shows confirmation dialog, then deletes the note from the vault

### History View (Implementation Extension)

- **Version history** — accessible via "HISTORY" button in reading pane or right-click context menu
- **Commit list** — shows git commits that modified the note with timestamps and messages
- **Diff viewer** — side-by-side diff between selected commits
- **Restore** — ability to restore note content from a historical commit

### Tab Behavior

- Clicking a file in the sidebar opens it in the current active tab (replaces content)
- Cmd+click on a sidebar file opens in a new tab
- Tabs can be closed via ✕ button; closing the last tab shows an empty state
- Tab order is maintained; new tabs append to the right
- Active tab state (which note is open) persisted to localStorage
- Each tab has a colored dot matching the note's type (reuse `TYPE_COLORS` from `lib/sigma-colors.ts`)
- Maximum 20 open tabs; attempting to open more closes the oldest inactive tab
- **Graph tab** — permanent tab (no close button) that switches to graph mode; active when in graph view

## Graph Mode

### Default: Local Neighborhood

- When toggling to Graph mode, shows the currently active tab's note as the center node
- Displays nodes within 2 hops (configurable) of the center note
- Scope indicator bar (top-left) shows: center note name, hop count, node count
- "Show Full Vault ⤢" button next to scope indicator expands to full vault graph

### Full Vault Mode

- Shows all vault nodes (same as current visualizer behavior)
- Activated via "Show Full Vault" button or via config default
- Config option `graph.defaultScope: "local" | "full"` (default: `"local"`)

### Graph Controls (HUD Panel)

- Existing draggable HUD panel preserved, overlaying the graph canvas
- Only rendered when in Graph mode (hidden in Read mode)
- Default position offset below the toolbar (top: `var(--toolbar-height)` + 16px)
- All current controls retained: similarity threshold, graph source toggle (semantic/wiki), overlay edges, node type filters, show daily toggle, hide isolated toggle, labels on hover, physics sliders (repulsion, gravity, cooling, edge strength, start temp, auto-stop), pause/run, reset
- Temperature bar at bottom of HUD shows simulation energy level
- Collapsible via ⊟/⊞ button in header

### Force Simulation (Implementation Detail)

The implementation uses a **custom Newtonian force simulation** rather than ForceAtlas2:

- **Repulsion** — Coulomb-style between all visible node pairs (O(n²))
- **Gravity** — Pull toward center, scaled with repulsion
- **Edge attraction** — Weighted by edge weight × edgeWeightInfluence
- **Velocity damping** — 0.85 per frame
- **Temperature decay** — Exponential cooling based on `slowDown` parameter
- **Drag interaction** — Dragging a node floors temperature at 0.4 so neighbors keep reacting

### Neighborhood + Filter Interaction

The neighborhood subgraph is computed from the **wiki edges only** (semantic edges are too dense — 19K+ edges would reach ~70% of the graph in 2 hops). All edge types are still rendered for nodes within the neighborhood. HUD filters (type filters, similarity threshold, hide isolated, etc.) are applied to the resulting subgraph.

### Graph Interactions

- Click node → opens note in current tab and **switches to read mode** (node becomes highlighted in graph)
- Cmd+click node → opens note in new tab
- Drag node → pins to canvas, floors temperature at 0.4 to reheat simulation
- Hover node → shows label (if "Labels on Hover Only" enabled)
- Right-click node → context menu with "Open in Reading Pane" and "View History"
- Click background → deselects node, clears highlights
- Search result selection in Graph mode → flies camera to that node

## Unified Search (⌘K)

### Input Behavior

- Activated by clicking the search box or pressing ⌘K (Cmd+K / Ctrl+K)
- Focus shows a dropdown below the search box
- Content behind the dropdown dims slightly

### Search Modes (by prefix)

| Prefix | Mode | Matches |
|--------|------|---------|
| (none) | Title search | Fuzzy match on note titles |
| `#` | Tag search | Exact match on note tags |
| `/` | Folder search | Prefix match on vault-relative path (e.g., `/Patterns/fastapi` matches all notes under that subfolder) |

### Dropdown

- Shows up to 8 results
- Each result: colored dot (by type), title with match highlighting, folder path and tags in muted text
- Keyboard navigation: ↑↓ to move selection, ⏎ to open in current tab, ⌘⏎ to open in new tab, Esc to close
- Click a result to open in current tab; Cmd+click for new tab
- Footer shows keyboard shortcuts
- In Graph mode, opening a result also flies the camera to that node

### Data Source

Search operates against the pre-loaded `graph.json` node data (titles, tags, folders). No server round-trip needed.

## File Tree Data

### Graph Schema Change

Add a `path` field to `NoteNode` in `graph.json` containing the vault-relative path (e.g., `"Patterns/fastapi-middleware/basics.md"`). Update `build_graph.py` to emit this field from the `note_index` table. The `folder` field is retained for backward compatibility.

The `path` field enables:
- Building a proper nested file tree (one level of subfolders per vault conventions)
- Folder prefix search (`/Patterns/fastapi` matches notes in that subfolder)
- Resolving wikilinks that reference subfolder notes by short stem

### Tree Construction

The file tree is built client-side from `graph.json` nodes:
1. Parse each node's `path` to extract folder and optional subfolder
2. Group into a tree structure: `{ folder: { subfolder?: { notes[] } } }`
3. Sort folders alphabetically, notes alphabetically within each folder

### Wikilink Resolution

Wikilinks in note bodies (e.g., `[[basics]]`) are resolved to `NoteNode.id` using:
1. Exact match on `NoteNode.id` (most common case)
2. If no exact match, suffix match against node IDs (handles subfolder notes where the wikilink uses the short name)
3. Build a lookup map at load time: `Map<string, string>` mapping all possible stems to canonical node IDs

### API

The existing `/api/note?stem=<stem>` endpoint is sufficient for loading note content. No new API endpoints needed.

### Tab Content Caching

Note content fetched via `/api/note` is cached in a `Map<string, string>` keyed by stem. When switching between already-opened tabs, the cached content is used immediately (no loading spinner). Cache is cleared only on page reload.

## State Persistence (localStorage)

All UI state persisted with `vv:` prefix:

| Key | Type | Default |
|-----|------|---------|
| `vv:sidebarWidth` | number | 240 |
| `vv:sidebarCollapsed` | boolean | false |
| `vv:openTabs` | string[] | [] |
| `vv:activeTab` | string \| null | null |
| `vv:viewMode` | "read" \| "graph" | "read" |
| `vv:graphScope` | "local" \| "full" | "local" |
| `vv:expandedFolders` | string[] | [] |
| `vv:threshold` | number | 0.8 |
| `vv:graphSource` | "semantic" \| "wiki" | "semantic" |
| `vv:showOverlayEdges` | boolean | false |
| `vv:filterNodesBySimilarity` | boolean | false |
| `vv:activeTypes` | string[] | [all types except "daily"] |
| `vv:showDaily` | boolean | false |
| `vv:hideIsolated` | boolean | false |
| `vv:labelsOnHoverOnly` | boolean | false |
| `vv:scalingRatio` | number | 10 |
| `vv:gravity` | number | 1 |
| `vv:slowDown` | number | 0.5 |
| `vv:edgeWeightInfluence` | number | 2 |
| `vv:startTemperature` | number | 0.8 |
| `vv:stopThreshold` | number | 0.01 |

## Component Architecture

### Core Components

| Component | Responsibility |
|-----------|---------------|
| `FileExplorer.tsx` | Sidebar file tree with folder expand/collapse, note selection, resize handle, context menu |
| `TabBar.tsx` | Horizontal tab strip with permanent Graph tab + note tabs, tab management (open, close, switch) |
| `ReadingPane.tsx` | Full-width markdown rendering with edit mode, frontmatter editor, conflict detection |
| `UnifiedSearch.tsx` | ⌘K search input + dropdown with prefix-based filtering |
| `Toolbar.tsx` | Composes hamburger + TabBar + WebSocket status + New note button + UnifiedSearch |
| `GraphCanvas.tsx` | Force-directed graph with neighborhood mode, node interactions, custom physics simulation |
| `HUDPanel.tsx` | Draggable control panel for graph filters and physics settings |
| `TemperatureBar.tsx` | Energy indicator for running simulation |

### Additional Components (Implementation Extensions)

| Component | Responsibility |
|-----------|---------------|
| `HistoryView.tsx` | Git history browser with commit list and diff viewer |
| `DiffViewer.tsx` | Side-by-side diff rendering for historical versions |
| `CommitList.tsx` | List of git commits that modified a note |
| `NewNoteDialog.tsx` | Modal dialog for creating new notes with folder/type selection |
| `ConfirmDialog.tsx` | Generic confirmation dialog (used for delete) |
| `ConflictDialog.tsx` | Merge conflict resolution with diff preview |
| `FrontmatterEditor.tsx` | Structured form for YAML frontmatter fields |

### State Management

| Module | Responsibility |
|--------|---------------|
| `useVisualizerState.ts` | Central hook managing all UI state, tab operations, note CRUD, graph settings |
| `useVaultFiles.ts` | WebSocket connection for real-time vault sync, file tree construction |
| `useLocalStorage.ts` | Persistence hook for localStorage-backed state |

### Library Modules

| Module | Responsibility |
|--------|---------------|
| `lib/graph.ts` | Graph data types, edge filtering, graph.json loading |
| `lib/sigma-colors.ts` | Node color/size mapping by type and link count |
| `lib/frontmatter.ts` | YAML frontmatter parsing and serialization |
| `lib/vaultFile.ts` | VaultFile type definition for file tree entries |
| `lib/parseDiff.ts` | Git diff parsing for history view |

### API Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/api/note` | GET | Fetch note content by stem or path |
| `/api/note` | POST | Save note content with conflict detection |
| `/api/note` | PUT | Create new note |
| `/api/note` | DELETE | Delete note |
| `/api/note/history` | GET | Get git history for a note |
| `/api/note/diff` | GET | Get diff between two commits for a note |
| `/api/files` | GET | Get file tree (WebSocket fallback) |
| `/api/graph/rebuild` | POST | Rebuild graph.json from vault index |

### Removed Components

| Component | Reason |
|-----------|--------|
| `NotePanel.tsx` | Replaced by full-width ReadingPane |
| `SearchBox.tsx` | Replaced by UnifiedSearch |
| `ViewToggle.tsx` | Replaced by permanent Graph tab in TabBar |

## Graph Neighborhood Algorithm

For local neighborhood mode, given a center node and hop count N (default 2):

1. Pre-build wiki-edge adjacency list for O(1) neighbor lookup
2. Start with the center node in a `visited` set with distance 0
3. For each hop 1..N, find all nodes connected via wiki edges to the current frontier
4. Add those nodes to `visited` with their hop distance
5. Filter the graph to only show nodes in `visited` and edges between them
6. Outer-hop nodes (hop N) render at 0.4 opacity to show the boundary

**Important:** Uses wiki edges only for BFS traversal (semantic edges are too dense). All edge types are still rendered for nodes within the neighborhood.

This is computed client-side from the Graphology graph instance. No server call needed.

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| ⌘K / Ctrl+K | Focus search |
| Esc | Close search dropdown / cancel edit mode / deselect graph node |
| ⌘B / Ctrl+B | Toggle sidebar (matches VS Code/Obsidian) |
| ⌘\\ / Ctrl+\\ | Toggle between Read and Graph mode (via Graph tab) |
| ⌘E / Ctrl+E | Enter edit mode (when note is open) |
| ⌘S / Ctrl+S | Save note (when in edit mode) |

Note: `⌘W` is not used (conflicts with browser close-tab). Tabs are closed via the ✕ button only.

## Error & Empty States

- **graph.json fails to load**: Sidebar and toolbar render but are empty/disabled. Content area shows a centered error message with instructions to run `make graph`.
- **graph.json loads with zero nodes**: Sidebar shows empty tree. Content area shows "No notes in vault" message.
- **No open tabs (read mode)**: Content area shows a welcome/empty state: "Open a note from the sidebar or press ⌘K to search."
- **Note content fetch fails**: Tab shows an error inline: "Could not load note: {stem}".
- **Graph mode with no active note**: Shows the full vault graph regardless of `defaultScope` setting (no center node to compute neighborhood from).
- **Save conflict**: Shows conflict dialog with server content and options to resolve.
- **WebSocket disconnected**: Status indicator shows red dot with "Vault sync disconnected" tooltip.

## Real-Time Synchronization (Implementation Extension)

The visualizer maintains a WebSocket connection to the Next.js server for real-time vault sync:

- **File changes** — External edits to open notes trigger a refresh (or conflict warning if editing)
- **Note creation/deletion** — File tree updates automatically
- **graph.json rebuilds** — Graph data refreshes when `graph.json` is regenerated
- **Connection status** — Visual indicator in toolbar (green/amber/red dot)

## Non-Goals

- Split/pane view (single content area only)
- Backlinks panel (use graph mode for link exploration)
- Server-side search (client-side from graph.json is sufficient for ~1000 notes)
- Mobile/responsive layout beyond 768px auto-collapse

## Post-Implementation Additions

The following features were added during implementation beyond the original spec:

### Note Editing
- Full edit mode with markdown preview toggle
- Structured frontmatter editor
- Conflict detection with diff-based resolution
- Keyboard shortcuts for edit (⌘E) and save (⌘S)

### Version History
- Git-based version history for each note
- Side-by-side diff viewer
- Restore from historical versions

### Real-Time Sync
- WebSocket connection for live vault monitoring
- Automatic refresh on external changes
- Visual connection status indicator

### Note Creation
- New note dialog with folder/type selection
- Automatic graph.json rebuild after creation

### Note Deletion
- Delete with confirmation dialog
- Automatic tab cleanup after deletion

## Migration Notes

The redesign replaced the previous full-screen graph layout entirely. Key migration points:

1. **Layout change** — `page.tsx` now uses sidebar + toolbar + content area layout
2. **Tab system** — New tab management with persistent Graph tab
3. **Graph mode** — Accessed via Graph tab instead of ViewToggle
4. **Note panel removed** — Replaced by full-width ReadingPane
5. **Physics simulation** — Custom implementation replaces ForceAtlas2
6. **State centralization** — All state moved to `useVisualizerState` hook

## Related Documentation

- [DOCUMENTATION_STYLE_GUIDE.md](../../DOCUMENTATION_STYLE_GUIDE.md) — Documentation standards
- [../README.md](../README.md) — Superpowers documentation index
