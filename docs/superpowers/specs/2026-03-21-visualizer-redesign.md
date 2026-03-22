# Vault Visualizer Redesign — Obsidian-Style Layout

**Date:** 2026-03-21
**Status:** Approved

## Overview

Redesign the vault visualizer from a graph-first app with a slide-in note sidebar into an Obsidian-style layout with a browse-only file explorer, tabbed reading pane, and a toggle between Read and Graph modes.

## Goals

- Primary interaction becomes browsing and reading notes (not the graph)
- File explorer sidebar for navigating the vault folder structure
- Full-width reading pane with tabbed multi-note support
- Graph mode accessible as a toggle, defaulting to local neighborhood view
- Single unified search replaces the current dual-search pattern

## Layout

### Three Zones

1. **Left sidebar** — resizable, collapsible file tree (browse-only)
2. **Toolbar** — hamburger toggle, tab bar, unified search, Read/Graph mode toggle
3. **Content area** — full-width, switches between reading pane and graph canvas

### Sidebar

- Browse-only folder tree rooted at the vault directory
- Folders show expand/collapse chevrons and note counts
- Files show as clickable items; the currently active note is highlighted with an indigo left border and background tint
- Header shows "Vault" label and total note count
- No search box — all search goes through the unified ⌘K in the toolbar
- Resizable via drag handle on the right edge; width persisted to localStorage
- Collapsible via ☰ hamburger button in the toolbar; collapsed state persisted
- Minimum width ~180px, maximum ~400px

### Toolbar

Fixed horizontal bar above the content area containing (left to right):

- **☰ hamburger** — toggles sidebar visibility
- **Tab bar** — horizontal tabs for open notes, each with a colored dot (by note type), title, and ✕ close button. Active tab has a distinct background and bottom border that blends into the content area. Inactive tabs are dimmer. Tabs are scrollable if they overflow.
- **⌘K search** — unified search input (right-aligned), 240px wide
- **Read/Graph toggle** — pill toggle button, right-aligned after search

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

### Tab Behavior

- Clicking a file in the sidebar opens it in the current active tab (replaces content)
- Cmd+click on a sidebar file opens in a new tab
- Tabs can be closed via ✕ button; closing the last tab shows an empty state
- Tab order is maintained; new tabs append to the right
- Active tab state (which note is open) persisted to localStorage
- Each tab has a colored dot matching the note's type (same color mapping as graph nodes)

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

- Existing draggable HUD panel preserved as-is, overlaying the graph canvas
- All current controls retained: similarity threshold, graph source toggle (semantic/wiki), overlay edges, node type filters, show daily toggle, hide isolated toggle, labels on hover, physics sliders (repulsion, gravity, cooling, edge strength, start temp, auto-stop), pause/run, reset
- Temperature bar at bottom of HUD

### Graph Interactions

- Click node → opens note in current tab (stays in Graph mode; node becomes highlighted)
- Cmd+click node → opens note in new tab
- Drag node → pins to canvas, reheats simulation (existing behavior)
- Hover node → shows label (existing behavior)
- Click background → deselects node
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
| `/` | Folder search | Prefix match on folder path |

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

### Loading

The file tree is built from the `graph.json` nodes array. Each node has a `folder` field — group nodes by folder to build the tree structure. Subfolders within folders are derived from node paths if available, otherwise flat per-folder listing.

### API Enhancement

The existing `/api/note?stem=<stem>` endpoint is sufficient for loading note content. No new API endpoints needed. The file tree is purely client-side from graph.json data.

## State Persistence (localStorage)

All UI state persisted with `vv:` prefix (existing convention):

| Key | Type | Default |
|-----|------|---------|
| `vv:sidebarWidth` | number | 240 |
| `vv:sidebarCollapsed` | boolean | false |
| `vv:openTabs` | string[] | [] |
| `vv:activeTab` | string | null |
| `vv:viewMode` | "read" \| "graph" | "read" |
| `vv:graphScope` | "local" \| "full" | "local" |
| (existing graph settings) | various | (unchanged) |

## Component Architecture

### New Components

| Component | Responsibility |
|-----------|---------------|
| `FileExplorer.tsx` | Sidebar file tree with folder expand/collapse, note selection, resize handle |
| `TabBar.tsx` | Horizontal tab strip with tab management (open, close, switch, reorder) |
| `ReadingPane.tsx` | Full-width markdown rendering for the active note |
| `UnifiedSearch.tsx` | ⌘K search input + dropdown with prefix-based filtering |
| `ViewToggle.tsx` | Read/Graph pill toggle button |
| `Toolbar.tsx` | Composes hamburger + TabBar + UnifiedSearch + ViewToggle |

### Modified Components

| Component | Changes |
|-----------|---------|
| `page.tsx` | New layout: sidebar + toolbar + content area; manages view mode state, open tabs, active note |
| `GraphCanvas.tsx` | Add local neighborhood mode (filter to N-hop subgraph from center node); add "Show Full Vault" button; node click opens tab instead of NotePanel |
| `HUDPanel.tsx` | No structural changes; may need z-index adjustment for new layout |

### Removed Components

| Component | Reason |
|-----------|--------|
| `NotePanel.tsx` | Replaced by full-width ReadingPane |
| `SearchBox.tsx` | Replaced by UnifiedSearch |

### Kept As-Is

| Component | Reason |
|-----------|--------|
| `TemperatureBar.tsx` | Still used in HUD |
| `lib/graph.ts` | Type definitions and data loading unchanged |
| `lib/sigma-colors.ts` | Node color mapping unchanged |
| `lib/useLocalStorage.ts` | Persistence hook unchanged |

## Graph Neighborhood Algorithm

For local neighborhood mode, given a center node and hop count N:

1. Start with the center node in a `visited` set
2. For each hop 1..N, find all nodes connected to the current `visited` set via visible edges
3. Add those nodes to `visited`
4. Filter the graph to only show nodes in `visited` and edges between them
5. Outer-hop nodes (hop N) render at reduced opacity to show the boundary

This is computed client-side from the Graphology graph instance. No server call needed.

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| ⌘K / Ctrl+K | Focus search |
| Esc | Close search dropdown / deselect graph node |
| ⌘W / Ctrl+W | Close active tab |
| ⌘[ / Ctrl+[ | Toggle sidebar |
| ⌘\\ / Ctrl+\\ | Toggle Read/Graph mode |

## Non-Goals

- Note editing (read-only viewer)
- Split/pane view (single content area only)
- Backlinks panel (use graph mode for link exploration)
- Note creation or deletion
- Server-side search (client-side from graph.json is sufficient for ~1000 notes)

## Migration from Current UI

The redesign replaces the current full-screen graph layout entirely. There is no incremental migration path — it's a full rewrite of `page.tsx` and the component tree. The existing `GraphCanvas.tsx` physics simulation and rendering logic is preserved but wrapped in the new layout with added neighborhood filtering.
