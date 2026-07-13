# Vault Visualizer

An interactive web application for exploring and navigating a ParsidionVault knowledge base through dual-mode reading and graph visualization.

## Table of Contents
- [Overview](#overview)
- [Architecture](#architecture)
- [Features](#features)
  - [Read Mode](#read-mode)
  - [Version History (Git Diff Viewer)](#version-history-git-diff-viewer)
  - [Graph Mode](#graph-mode)
  - [Multi-Tab Support](#multi-tab-support)
  - [File Explorer Sidebar](#file-explorer-sidebar)
  - [Unified Search](#unified-search)
  - [Keyboard Shortcuts](#keyboard-shortcuts)
  - [Real-Time Vault Sync](#real-time-vault-sync)
  - [Multi-Vault Support](#multi-vault-support)
- [Running the Visualizer](#running-the-visualizer)
- [Building Graph Data](#building-graph-data)
- [Data Model](#data-model)
- [State Management](#state-management)
- [Graph Visualization Engine](#graph-visualization-engine)
- [Configuration](#configuration)
- [File Structure](#file-structure)
- [Related Documentation](#related-documentation)

## Overview

**Purpose:** Provide a browser-based interface for reading, navigating, and visually exploring the vault knowledge graph — combining a hierarchical file browser with force-directed graph visualization powered by semantic embeddings and explicit wikilinks.

**Key Features:**
- Dual-mode interface: Read (Markdown rendering) and Graph (force-directed visualization), toggled via a permanent Graph tab in the tab bar
- **Version history viewer** — browse git commits for any note and compare any two with syntax-highlighted diffs
- Multi-tab note browsing with persistent state
- Unified search across titles, tags, and folders (⌘K)
- Interactive graph with per-node neighborhood and full-vault views
- Configurable node sizing (uniform, incoming links, betweenness centrality, recency) and edge coloring (binary opacity, gradient)
- Edge density pruning for large graphs
- Pre-built graph data from vault embeddings (no live queries)

**Requirements:**
- Bun runtime
- Vault with embeddings built (`build_embeddings.py`)
- `graph.json` built via `make graph`

## Architecture

### System Design

```mermaid
graph TB
    subgraph "Browser"
        App[Next.js App]
        Read[ReadingPane]
        Graph[GraphCanvas]
        Search[UnifiedSearch]
        Sidebar[FileExplorer]
        Conflict[ConflictDialog]
    end

    subgraph "Next.js Server (plain next dev/start)"
        SSE["/api/vault/events (SSE)"]
        Watcher[Chokidar Watcher - per vault, ref-counted]
        Broadcast[vaultBroadcast EventEmitter]
    end

    subgraph "Data"
        GJ[graph.json]
        API["/api/note?stem="]
        FilesAPI["/api/files"]
        HistAPI["/api/note/history"]
        DiffAPI["/api/note/diff"]
        Vault[ParsidionVault Notes]
        Git[Git Repo]
    end

    subgraph "Build Pipeline"
        Emb[embeddings.db]
        Builder[build_graph.py]
    end

    App --> Read
    App --> Graph
    App --> Search
    App --> Sidebar
    App --> History[HistoryView]
    Read --> Conflict

    SSE --> Watcher
    Watcher --> Vault
    SSE -->|text/event-stream: file:created/deleted/modified| App
    Broadcast -->|graph:rebuilt| SSE
    SSE -->|graph:rebuilt| App

    App -->|fetch on load| GJ
    Read -->|fetch on open| API
    Sidebar -->|fetch on mount| FilesAPI
    API --> Vault
    FilesAPI --> Vault
    HistAPI --> Git
    DiffAPI --> Git
    History -->|git log| HistAPI
    History -->|git diff| DiffAPI

    Emb --> Builder
    Builder --> GJ

    style App fill:#e65100,stroke:#ff9800,stroke-width:3px,color:#ffffff
    style Graph fill:#0d47a1,stroke:#2196f3,stroke-width:2px,color:#ffffff
    style Read fill:#1b5e20,stroke:#4caf50,stroke-width:2px,color:#ffffff
    style Search fill:#4a148c,stroke:#9c27b0,stroke-width:2px,color:#ffffff
    style Sidebar fill:#37474f,stroke:#78909c,stroke-width:2px,color:#ffffff
    style History fill:#006064,stroke:#00acc1,stroke-width:2px,color:#ffffff
    style Conflict fill:#b71c1c,stroke:#f44336,stroke-width:2px,color:#ffffff
    style SSE fill:#4a148c,stroke:#9c27b0,stroke-width:3px,color:#ffffff
    style Broadcast fill:#880e4f,stroke:#c2185b,stroke-width:2px,color:#ffffff
    style Watcher fill:#37474f,stroke:#78909c,stroke-width:2px,color:#ffffff
    style GJ fill:#1a237e,stroke:#3f51b5,stroke-width:2px,color:#ffffff
    style API fill:#37474f,stroke:#78909c,stroke-width:2px,color:#ffffff
    style FilesAPI fill:#37474f,stroke:#78909c,stroke-width:2px,color:#ffffff
    style HistAPI fill:#006064,stroke:#00acc1,stroke-width:1px,color:#ffffff
    style DiffAPI fill:#006064,stroke:#00acc1,stroke-width:1px,color:#ffffff
    style Vault fill:#37474f,stroke:#78909c,stroke-width:1px,color:#ffffff
    style Git fill:#311b92,stroke:#7c4dff,stroke-width:1px,color:#ffffff
    style Emb fill:#1a237e,stroke:#3f51b5,stroke-width:2px,color:#ffffff
    style Builder fill:#880e4f,stroke:#c2185b,stroke-width:2px,color:#ffffff
```

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | Next.js + React |
| Graph Rendering | Sigma.js (WebGL) |
| Graph Layout | Custom Newtonian force loop (`lib/useForceLayout.ts`) on a Graphology graph |
| Styling | Tailwind CSS |
| Runtime / Package Manager | Bun |
| Markdown Rendering | react-markdown + remark-gfm |

### Component Hierarchy

```mermaid
graph TD
    Page[app/page.tsx]
    Toolbar[Toolbar.tsx]
    TabBar[TabBar.tsx]
    Search[UnifiedSearch.tsx]
    WsIndicator[Sync Status Dot]
    VaultSel[VaultSelector.tsx]
    VaultStats[VaultStats.tsx]
    Sidebar[FileExplorer.tsx]
    ReadPane[ReadingPane.tsx]
    GraphCanvas[GraphCanvas.tsx]
    HUD[HUDPanel.tsx]
    TempBar[TemperatureBar.tsx]
    NewNote[NewNoteDialog.tsx]
    Confirm[ConfirmDialog.tsx]
    Conflict[ConflictDialog.tsx]
    FmEditor[FrontmatterEditor.tsx]
    HistView[HistoryView.tsx]
    CommitList[CommitList.tsx]
    DiffViewer[DiffViewer.tsx]

    Page --> Toolbar
    Toolbar --> TabBar
    Toolbar --> Search
    Toolbar --> WsIndicator
    Toolbar --> VaultSel
    Toolbar --> VaultStats
    Page --> Sidebar
    Page --> ReadPane
    Page --> GraphCanvas
    Page --> NewNote
    Page --> HistView
    ReadPane --> Confirm
    ReadPane --> Conflict
    ReadPane --> FmEditor
    GraphCanvas --> HUD
    GraphCanvas --> TempBar
    HistView --> CommitList
    HistView --> DiffViewer

    style Page fill:#e65100,stroke:#ff9800,stroke-width:3px,color:#ffffff
    style Toolbar fill:#1b5e20,stroke:#4caf50,stroke-width:2px,color:#ffffff
    style GraphCanvas fill:#0d47a1,stroke:#2196f3,stroke-width:2px,color:#ffffff
    style HUD fill:#0d47a1,stroke:#2196f3,stroke-width:1px,color:#ffffff
    style Sidebar fill:#37474f,stroke:#78909c,stroke-width:2px,color:#ffffff
    style ReadPane fill:#1b5e20,stroke:#4caf50,stroke-width:1px,color:#ffffff
    style TabBar fill:#37474f,stroke:#78909c,stroke-width:1px,color:#ffffff
    style Search fill:#4a148c,stroke:#9c27b0,stroke-width:2px,color:#ffffff
    style WsIndicator fill:#880e4f,stroke:#c2185b,stroke-width:1px,color:#ffffff
    style VaultSel fill:#37474f,stroke:#78909c,stroke-width:1px,color:#ffffff
    style VaultStats fill:#1b5e20,stroke:#4caf50,stroke-width:1px,color:#ffffff
    style TempBar fill:#37474f,stroke:#78909c,stroke-width:1px,color:#ffffff
    style NewNote fill:#880e4f,stroke:#c2185b,stroke-width:1px,color:#ffffff
    style Confirm fill:#880e4f,stroke:#c2185b,stroke-width:1px,color:#ffffff
    style Conflict fill:#b71c1c,stroke:#f44336,stroke-width:1px,color:#ffffff
    style FmEditor fill:#880e4f,stroke:#c2185b,stroke-width:1px,color:#ffffff
    style HistView fill:#006064,stroke:#00acc1,stroke-width:2px,color:#ffffff
    style CommitList fill:#006064,stroke:#00acc1,stroke-width:1px,color:#ffffff
    style DiffViewer fill:#006064,stroke:#00acc1,stroke-width:1px,color:#ffffff
```

## Features

### Read Mode

The default mode when opening a note. Provides a distraction-free reading experience:

- Centered column layout (max-width 720px) for comfortable reading
- Metadata header: note type badge, date, confidence level
- Tag pills display below the title
- Full GitHub Flavored Markdown (GFM) rendering
- Wikilinks (`[[stem]]`) rendered as clickable purple text
  - Click → open in current tab
  - Cmd+click → open in new tab
- Related notes section extracted from YAML frontmatter
- Linked Notes section: notes connected by wiki edges in `graph.json` (frontmatter `related:` plus par-mem in-body links when the [par-mem integration](PAR-MEM.md) is enabled) — undirected, deduplicated against the Related row
- **Inline editing**: toggle edit mode to modify note body and frontmatter
  - FrontmatterEditor provides structured editing of type, date, confidence, tags, project, sources, and related links with tag autocomplete from the graph
  - Save and delete operations via the note CRUD API

### Version History (Git Diff Viewer)

Browse the git commit history of any vault note and compare any two versions with syntax-highlighted diffs. Requires the vault to be a git repository.

**Entry Points — three ways to open history:**
- **ReadingPane toolbar** — click the `HISTORY` button (visible when viewing any note)
- **File Explorer right-click** — right-click any file item → "View History"
- **Graph node right-click** — right-click any node → "View History"

**Layout:**

```
┌─ Toolbar (← Back  stem — Version History  [UNIFIED|SPLIT|WORDS]) ──┐
├─────────────────────────────────────────────────────────────────────┤
│  CommitList (240px)    │  DiffViewer (flex 1)                       │
│                        │                                             │
│  COMMITS · N total     │  +12 additions  −5 deletions  note.md      │
│                        │  ─────────────────────────────────         │
│  [FROM] [TO] abc1234   │  old line  │  new line                     │
│  commit message        │  ...       │  ...                           │
│  3h ago · latest       │                                             │
│                        │                                             │
│  [FROM] [TO] def5678   │                                             │
│  commit message        │                                             │
│  1d ago                │                                             │
└────────────────────────┴─────────────────────────────────────────────┘
```

**Commit List:**
- Each row has independent **[FROM]** and **[TO]** badge buttons
- Clicking FROM sets the base revision; clicking TO sets the comparison target
- FROM and TO cannot be the same commit (setting one to the other's value auto-clears it)
- Defaults to FROM = latest commit, TO = previous commit on open
- Single-commit notes show "Only one version — no diff available"

**Diff Modes (toggle in toolbar):**

| Mode | Description |
|------|-------------|
| UNIFIED | Single column, `+`/`-`/space prefixes, line numbers on left |
| SPLIT | Two columns side by side (FROM left, TO right), aligned line pairs, red/green backgrounds |
| WORDS | Inline word-level diff — red strikethrough for removed words, green for added words |

Default mode is **SPLIT**.

**Edge cases:**
- No git history → "No version history found"
- Single commit → FROM shown read-only, diff panel shows message
- `from === to` → "Select two different commits to compare"
- Diff > 5000 lines → truncated with notice

### Graph Mode

Interactive force-directed graph for exploring note relationships:

**Default (Local) View**
- Displays a 2-hop neighborhood around the currently active note
- Uses wiki edges (explicit wikilinks) for BFS traversal
- Shows semantic edges within the neighborhood

**Full Vault View**
- Toggle via the "Show Full Vault ⤢" button in the top-right scope indicator (placed there to avoid overlapping the HUD)
- Renders all notes and edges simultaneously

**Visual Encoding**

| Element | Encoding |
|---------|---------|
| Node color | Note type (pattern, debugging, research, project, tool, language, framework, knowledge, daily) |
| Node size | Configurable: uniform, incoming link count (logarithmic), betweenness centrality, or recency (newer = larger) |
| Wiki edge | Solid line — explicit wikilinks |
| Semantic edge | Solid line — embedding similarity above threshold; color mode: binary (opacity) or gradient (blue to red) |

**Interactions**
- Click node → opens note in current tab and highlights selection
- Right-click node → context menu: "Open in Reading Pane" / "View History"
- Drag node → pins position, reheats physics simulation
- Hover node → shows label (if labels-on-hover mode is active)

#### HUD Panel

Floating overlay in the bottom-left of the graph canvas. Draggable via its title bar.

**Display Controls**
- Semantic similarity threshold (0.0–1.0)
- Graph source: Semantic vs. Wiki
- Overlay edges (show opposite type at low opacity)
- Node type filter checkboxes
- Show daily notes toggle
- Filter nodes by similarity toggle (show only nodes connected by semantic edges above threshold)
- Hide isolated nodes toggle
- Labels on hover only toggle

**Edge Color Mode**
- Binary — semantic edges use opacity-based gray; wiki edges use purple
- Gradient — semantic edges colored blue (weak) to red (strong) by similarity score

**Node Size Mode**
- Uniform — equal size for all nodes
- Links — sized by incoming wikilink count (logarithmic scale)
- Centrality — sized by betweenness centrality (computed via BFS; disabled for graphs exceeding 500 nodes)
- Recency — newer notes are larger, older notes are smaller

**Edge Density** (shown only when graph has >2000 edges)
- Toggle to enable per-node edge pruning
- Max edges per node slider (3–20, default 8)
- Keeps the K strongest connections per node to reduce visual clutter

**Physics Controls**
- Scaling ratio (node repulsion strength)
- Gravity (attraction to center)
- Slow down (cooling rate)
- Edge weight influence
- Start temperature
- Stop threshold
- Pause / Resume layout button
- Reset to defaults

**Statistics**
- Visible node count
- Visible edge count
- Average semantic similarity score
- Expandable detail panel: average degree, max degree, graph density, connected component count, top 5 hub nodes by degree, and a `body links` chip when `graph.json` carries `meta.parmem_body_links`

**Temperature Bar**
- Visual indicator of simulation energy (0 to 1.0)
- Hotter = nodes still moving; cooler = converging

### Multi-Tab Support

- Maximum 20 open tabs
- Each tab: colored type dot, note title, close button (✕)
- Active tab: highlighted with distinct background and bottom border
- Tabs scroll horizontally on overflow
- Tab state persisted to `localStorage`
- Stale stems auto-removed on load
- Switching tabs updates content immediately (cached)

### File Explorer Sidebar

- Nested folder structure (one level of subfolders)
- Expand/collapse folders via chevrons
- Notes sorted alphabetically within folders
- Active note highlighted (indigo left border + background tint)
- Clicking a note in Graph mode also flies camera to that node
- **Right-click context menu** on any file item: Open, View History, Delete
- Resizable via drag handle (180px–400px)
- Collapsible via hamburger button (☰) in toolbar
- Auto-collapses on mobile viewports (<768px)
- Width persisted to `localStorage`
- Note count shown in header

### Unified Search

Activated with **⌘K** — three modes selectable by prefix:

| Prefix | Mode | Description |
|--------|------|-------------|
| *(none)* | Title | Fuzzy match on note titles and stem IDs |
| `#tag` | Tag | Exact tag match |
| `/path` | Folder | Prefix match on vault-relative path |
| `?query` | Semantic | Meaning-based search via `vault_search.py` (par-mem backend when available, embeddings fallback) — debounced, shows note summaries |

- Up to 8 results shown per query
- Each result: colored type dot, title with match highlighting, folder path, tags
- Keyboard navigation: ↑↓ to move, ⏎ to open, ⌘⏎ for new tab
- Click → open in current tab; Cmd+click → new tab
- In Graph mode: opening a result flies camera to that node
- Lexical modes are served from `graph.json` with no server round-trips; `?semantic` calls `GET /api/search`
- Semantic mode requires the parsidion scripts to be installed (or a source checkout); when the backend is unavailable the dropdown shows an error row and the lexical modes keep working. Results are limited to notes present in `graph.json`.

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| ⌘K / Ctrl+K | Focus search input |
| ⌘B / Ctrl+B | Toggle sidebar |
| ⌘\ / Ctrl+\ | Switch to Graph mode |
| ⌘E / Ctrl+E | Enter edit mode (when viewing a note) |
| ⌘S / Ctrl+S | Save note (when editing) |
| Esc | Close search dropdown, cancel edit, or deselect graph node |
| ↑ ↓ (search) | Navigate results |
| ⏎ (search) | Open selected result |
| ⌘⏎ (search) | Open selected result in new tab |

> **Note:** Switching back to Read mode is done by clicking a note tab in the tab bar; there is no dedicated keyboard shortcut for Read mode.

### Real-Time Vault Sync

The visualizer maintains a Server-Sent Events (SSE) connection to the Next.js server for live vault updates. The app runs on plain `next dev` / `next start` — there is no custom server; the stream is a route handler at `app/api/vault/events/route.ts`.

**SSE Connection**
- Endpoint: `GET /api/vault/events?vault=<name>` (`text/event-stream`)
- Client opens it with the browser's `EventSource` API (in `lib/useVaultFiles.ts`)
- Reconnection is native to `EventSource` — the browser retries automatically with its own backoff; the client reports `connecting` while the stream is down and `connected` once `onopen` fires
- Same-origin guard (`requireSameOrigin`) is applied before the stream opens; the vault path is validated against `vaults.yaml`
- Connection status indicator in toolbar: green (connected), amber (connecting), red (disconnected)

**Server-Side Watcher**
- A reference-counted `chokidar` watcher is created per vault (module-level registry inside the route handler). It is shared across concurrent SSE connections for the same vault and closed only when its last subscriber disconnects.
- The watcher ignores the standard excluded dirs (`.obsidian`, `Templates`, `.git`, `.trash`, `TagsRoutes`), dot-files, and anything that is not a `.md` file, and waits for writes to settle (`awaitWriteFinish`) before emitting.
- `graph:rebuilt` events from `lib/vaultBroadcast.server.ts` (an EventEmitter) are forwarded to every subscriber — this is how clients learn that `graph.json` was regenerated.

**Live Updates (event payload)**
- `file:created` → note appears in FileExplorer immediately (no reload)
- `file:deleted` → note is removed from the sidebar instantly
- `file:modified` → modified note auto-refreshes in read mode (scroll position preserved)
- `graph:rebuilt` → clients refetch `graph.json` and reload the file list

**Conflict Detection**
- When saving a note that was modified externally, a `ConflictDialog` appears
- Three resolution options:
  1. **Take theirs** — use the server version
  2. **Keep mine** — overwrite with your edits
  3. **Merge** — manual editor with split/unified diff view

**External Modification Warning**
- If a note is modified externally while you are editing it, a warning appears
- Saving triggers conflict detection to prevent data loss

### Multi-Vault Support

The visualizer supports multiple isolated vaults, allowing you to switch between work, personal, or project-specific knowledge bases.

**Setup**

Create a vaults configuration file at `~/.config/parsidion/vaults.yaml`:

```yaml
vaults:
  work: ~/WorkVault
  personal: ~/PersonalVault
  team: ~/shared/team-vault
```

**Vault Selector**

- Dropdown in the toolbar (left of the vault-sync status indicator)
- Shows all configured vaults plus "default"
- Persists selection to localStorage (`vv:selectedVault`)
- Switching vaults clears the content cache and resets tabs

**Vault-Aware Components**

| Component | Behavior |
|-----------|----------|
| FileExplorer | Re-fetches file list from `/api/files?vault=name` |
| ReadingPane | Loads notes via `/api/note?vault=name&stem=...` |
| GraphCanvas | Graph data is vault-specific (separate `graph.json` per vault) |
| SSE stream | Reconnects to `/api/vault/events?vault=name` on switch |

**API Endpoints**

All API routes accept an optional `vault` query parameter:

| Endpoint | Vault Parameter |
|----------|-----------------|
| `GET /api/files?vault=<name>` | List files in specified vault |
| `GET /api/note?vault=<name>&stem=<stem>` | Read note from vault |
| `POST /api/note?vault=<name>` | Save note to vault |
| `GET /api/note/history?vault=<name>&stem=<stem>` | Git history for vault |
| `GET /api/note/diff?vault=<name>&...` | Git diff in vault |
| `GET /api/graph?vault=<name>` | Serve graph.json from vault root |
| `POST /api/graph/rebuild?vault=<name>` | Rebuild vault's graph.json |
| `GET /api/vault/events?vault=<name>` | SSE stream of file/create/modify/delete and `graph:rebuilt` events |
| `GET /api/vaults` | List available vaults |
| `GET /api/stats?vault=<name>` | Pending summary count for the vault |
| `POST /api/summarize?vault=<name>` | Spawn the summarizer subprocess for the vault (auth required) |
| `GET /api/summarizer/status?vault=<name>` | Live summarizer run progress (processed/written/skipped/errors, pct) |
| `GET /api/search?vault=<name>&q=<query>&top=<n>` | Semantic search via `vault_search.py` (spawned subprocess) |

**Fallback Behavior**

When no `vaults.yaml` exists or only one vault is configured:
- Vault selector is hidden in the toolbar
- All operations use the default vault (`~/ParsidionVault`, legacy `~/ClaudeVault` if it exists and `~/ParsidionVault` does not, or `VAULT_ROOT` environment variable)

## Running the Visualizer

### Development

```bash
# Install dependencies (first time only)
make visualizer-setup

# Build graph data from vault
make graph

# Start dev server (port 3999)
cd visualizer
bun dev
```

Open `http://localhost:3999` in your browser.

### Production

```bash
make build-visualizer         # Compile Next.js production build
cd visualizer && bun start    # Start production server on port 3999
make stop-visualizer          # Kill the process on port 3999
```

## Building Graph Data

The `graph.json` file is a pre-computed snapshot of vault relationships stored in the vault root (e.g. `~/ParsidionVault/graph.json`). Each vault has its own `graph.json`; the file is gitignored and rebuilt locally. Rebuild it whenever notes are added, removed, or embeddings are updated.

Frontmatter `related:` fields are the always-on source of wiki edges; when the optional [par-mem integration](PAR-MEM.md) is enabled, wiki edges also include par-mem's in-body `[[wikilinks]]`/markdown-link extraction (`--no-parmem` opts out) — edge kinds are unchanged either way (`kind: 'wiki'`).

### Prerequisites

1. Vault must have embeddings built:
   ```bash
   uv run --no-project ~/.claude/skills/parsidion/scripts/build_embeddings.py
   ```

2. Run the graph builder:
   ```bash
   make graph                        # Include Daily notes (default)
   uv run --no-project ~/.claude/skills/parsidion/scripts/build_graph.py --no-daily  # Exclude Daily folder notes
   ```

### Graph Builder Options

```bash
uv run --no-project ~/.claude/skills/parsidion/scripts/build_graph.py [OPTIONS]

Options:
  --include-daily        Include Daily folder notes (default; flag kept for backward compatibility)
  --no-daily             Exclude Daily folder notes
  --no-parmem            Skip par-mem in-body wiki-edge enrichment
  --min-threshold FLOAT  Minimum cosine similarity for semantic edges (default: 0.70)
  --output PATH          Output path for graph.json (default: {vault}/graph.json)
  --vault PATH           Custom vault root path
```

### Processing Pipeline

```mermaid
graph LR
    NI[note_index table]
    EM[note_embeddings table]
    Norm[L2-normalize vectors]
    Cos[Cosine similarity matrix]
    Filter[Filter by threshold]
    Wiki[Parse related fields]
    JSON[graph.json]

    NI --> Norm
    EM --> Norm
    Norm --> Cos
    Cos --> Filter
    Filter --> JSON
    Wiki --> JSON

    style NI fill:#1a237e,stroke:#3f51b5,stroke-width:2px,color:#ffffff
    style EM fill:#1a237e,stroke:#3f51b5,stroke-width:2px,color:#ffffff
    style Cos fill:#e65100,stroke:#ff9800,stroke-width:3px,color:#ffffff
    style Filter fill:#ff6f00,stroke:#ffa726,stroke-width:2px,color:#ffffff
    style Wiki fill:#4a148c,stroke:#9c27b0,stroke-width:2px,color:#ffffff
    style JSON fill:#1b5e20,stroke:#4caf50,stroke-width:2px,color:#ffffff
    style Norm fill:#37474f,stroke:#78909c,stroke-width:2px,color:#ffffff
```

## Data Model

### `NoteNode`

```typescript
{
  id: string           // Unique stem identifier
  title: string        // Display title
  type: string         // Note type: pattern | debugging | research | project |
                       //   tool | language | framework | knowledge | daily
  folder: string       // Top-level vault folder (e.g., "Patterns", "Daily")
  path: string         // Vault-relative path
  tags: string[]       // Note tags
  incoming_links: number  // Count of wiki links pointing to this note
  mtime: number        // File modification time (Unix timestamp)
}
```

### `GraphEdge`

```typescript
{
  s: string            // Source node stem
  t: string            // Target node stem
  w: number            // Weight: 0–1 for semantic, 1.0 for wiki
  kind: 'semantic' | 'wiki'
}
```

### `GraphData` (graph.json root)

```typescript
{
  meta: {
    generated: string        // ISO timestamp of build
    note_count: number
    edge_count: number
    min_semantic_threshold: number
    parmem_body_links?: number  // wiki edges added by par-mem body-link enrichment (absent when skipped/zero)
  }
  nodes: NoteNode[]
  edges: GraphEdge[]
}
```

### `VaultFile` (SSE events and /api/files)

```typescript
{
  stem: string         // Filename without extension — e.g. "foo" for "Patterns/foo.md"
  path: string         // Path relative to vault root — e.g. "Patterns/foo.md"
  noteType?: string    // Frontmatter `type` field, if present
}
```

### `WsStatus`

```typescript
type WsStatus = 'connecting' | 'connected' | 'disconnected'
```

### API Routes

**`GET /api/note?stem=<stem>`**

Returns the Markdown content for a note identified by its stem ID.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `stem` | string | Yes* | Vault note stem (*required if `path` not provided) |
| `path` | string | No | Vault-relative path (for disambiguation when multiple notes share the same stem) |
| `vault` | string | No | Vault name (from vaults.yaml) |

**Response (200):** JSON `{ content: string, path: string }` — raw Markdown and vault-relative path

**Response (404):** JSON error — note not found

**`POST /api/note`** — Update (overwrite) an existing note. Accepts `vault` query parameter.
Body: `{ stem: string, content: string, lastModified?: number }`
- If `lastModified` is provided, server checks for conflicts (409 if note was modified externally)
- Response (409): `{ conflict: true, serverContent: string }` — conflict detected
- Response (200): `{ ok: true }`

**`PUT /api/note`** — Create a new note at a vault-relative path.
Body: `{ path: string, content: string }`. Returns 409 if the note already exists.

**`DELETE /api/note?stem=<stem>`** — Delete a note by stem.

**`POST /api/graph/rebuild`** — Trigger a server-side `build_graph.py` run to regenerate `graph.json`. Broadcasts a `graph:rebuilt` event via `vaultBroadcast` to all connected SSE clients.

**`GET /api/graph`** — Serve the vault's `graph.json` file.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `vault` | string | No | Vault name (from vaults.yaml) |

**Response (200):** The `graph.json` file content (JSON).
**Response (404):** `graph.json` not found in the vault.

**`GET /api/vaults`** — List available vaults from `vaults.yaml`.

**Response (200):** `{ vaults: VaultInfo[], defaultVault: string }` where each `VaultInfo` has `{ name, path, isDefault }`.

**`GET /api/vault/events`** — Server-Sent Events stream of vault file changes for live sync. Replaces the retired `ws`-based `/ws/vault` endpoint.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `vault` | string | No | Vault name (from vaults.yaml) |

**Response (200):** `text/event-stream`. Each `data:` line is a JSON object with a `type` of `file:created`, `file:deleted`, `file:modified`, or `graph:rebuilt`. Same-origin guard applied before the stream opens; vault path is validated against `vaults.yaml`. A reference-counted `chokidar` watcher is created per vault and shared across concurrent connections.

**`GET /api/stats`** — Lightweight vault health probe used by the toolbar's `VaultStats` chip.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `vault` | string | No | Vault name (from vaults.yaml) |

**Response (200):** `{ pendingSummaries: number }` — count of entries in the vault's `pending_summaries.jsonl`.

**`POST /api/summarize`** — Spawn the Parsidion summarizer subprocess (`summarize_sessions.py`) for the vault. Auth-required (mutation route).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `vault` | string | No | Vault name (from vaults.yaml) |

**Response (200):** `{ started: true, pid: number }`. **Response (409):** `{ alreadyRunning: true }` — a summarizer is already running. **Response (400):** invalid vault or vault directory missing.

**`GET /api/summarizer/status`** — Live progress for a running summarizer (polled by `VaultStats` while a run is in flight).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `vault` | string | No | Vault name (from vaults.yaml) |

**Response (200):** `{ running: boolean, error?: string, progress: Progress | null, pendingSummaries: number }` where `Progress` is `{ total, processed, written, skipped, errors, current, pct }`.

**`GET /api/search?q=<query>`** — Semantic vault search. Spawns `vault_search.py --json` (par-mem daemon backend when available, embeddings fallback — see [PAR-MEM.md](PAR-MEM.md)).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `q` | string | Yes | Natural-language query (trimmed, max 512 chars) |
| `top` | number | No | Max results, clamped 1–20 (default 8) |
| `vault` | string | No | Vault name (from vaults.yaml) |

**Response (200):** `{ results: [{ stem, title, folder, path, tags, note_type, score, summary }], tookMs }` — `path` is vault-relative.
**Errors:** 400 invalid query/vault · 429 concurrent-search limit · 502 search failed · 503 `vault_search.py` not found.

**`GET /api/files`** — Returns the complete vault file tree.

**Response (200):** `{ files: VaultFile[] }` — flat array of all vault markdown files with stem, path, and noteType

**`GET /api/note/history?stem=<stem>`** — Returns the git commit log for a note.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `stem` | string | Yes* | Vault note stem (*required if `path` not provided) |
| `path` | string | No | Vault-relative path (for disambiguation when multiple notes share the same stem) |

**Response (200):** `{ commits: CommitEntry[] }` where each entry has `{ hash, shortHash, date, message }`. Returns `{ commits: [] }` when the vault has no git history (not an error).

**`GET /api/note/diff?stem=<stem>&from=<hash>&to=<hash>`** — Returns a unified diff between two commits.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `stem` | string | Yes* | Vault note stem (*required if `path` not provided) |
| `path` | string | No | Vault-relative path (for disambiguation when multiple notes share the same stem) |
| `from` | string | Yes | Base commit SHA (4–40 hex chars) |
| `to` | string | Yes | Target commit SHA, or `working` for the uncommitted working tree |

**Response (200):** `{ diff: string, truncated: boolean }` — raw unified diff text; `truncated` is true when the diff exceeded 5000 lines.

Both history routes path-traverse-protect with `guardPath()` (same pattern as `/api/note`) and validate SHA parameters against `/^[a-f0-9]{4,40}$|^working$/`.

## State Management

All application state is managed by the `useVisualizerState` hook (`lib/useVisualizerState.ts`) and the `useVaultFiles` hook (`lib/useVaultFiles.ts`). State is split into categories:

**Vault State**

| Key | Type | Description |
|-----|------|-------------|
| `selectedVault` | `string \| null` | Currently selected vault name (null = default) |
| `setSelectedVault(vault)` | callback | Switch vaults (clears cache and tabs on change) |

**Tab / View State**

| Key | Type | Description |
|-----|------|-------------|
| `openTabs` | `string[]` | Array of open note stems |
| `activeTab` | `string \| null` | Currently displayed note stem |
| `viewMode` | `'read' \| 'graph'` | Current display mode |
| `neighborhoodCenter` | `string \| null` | Stem at the center of the local 2-hop view; `null` means full vault |
| `isLayoutRunning` | `boolean` | Whether the force simulation is active (paused when leaving Graph mode) |

**History Mode State**

| Key | Type | Description |
|-----|------|-------------|
| `historyMode` | `boolean` | Whether the history viewer is active |
| `historyNote` | `string \| null` | Stem of the note whose history is being viewed |
| `historyPath` | `string \| null` | Vault-relative path for disambiguation |
| `openHistory(stem, path?)` | callback | Enters history mode; saves current `viewMode` for restoration |
| `closeHistory()` | callback | Exits history mode; restores `viewMode` to its pre-history value |

**Sidebar State**

| Key | Type | Description |
|-----|------|-------------|
| `sidebarWidth` | `number` | Width in pixels (180–400) |
| `sidebarCollapsed` | `boolean` | Whether sidebar is hidden |

**Content Cache & Sync**

| Key | Type | Description |
|-----|------|-------------|
| `contentCache` | `Map<string, string>` | In-memory cache of note content (stem/path → content) |
| `invalidateNote(stem, path?)` | callback | Evict cached content when external modification detected |
| `saveNote(stem, content, lastModified?, path?)` | callback | Save with optional conflict detection |
| `deleteNote(stem)` | callback | Delete a note by stem |
| `createNote(path, content)` | callback | Create a new note at a vault-relative path |

**Live-Sync State** (from `useVaultFiles`)

| Key | Type | Description |
|-----|------|-------------|
| `fileTree` | `VaultFileTree` | Nested Map<folder, Map<subfolder, VaultFile[]>> |
| `wsStatus` | `WsStatus` | SSE/EventSource connection state (type name retained for brevity) |
| `totalFiles` | `number` | Total vault note count |

**Graph Controls** (all persisted to `localStorage` with `vv:` prefix)

| Key | Default | Description |
|-----|---------|-------------|
| `threshold` | `0.80` | Semantic similarity cutoff |
| `graphSource` | `'semantic'` | Primary edge type to display |
| `showOverlayEdges` | `false` | Show opposite edge type at low opacity |
| `filterNodesBySimilarity` | `false` | Show only nodes connected by semantic edges above threshold |
| `activeTypes` | all types (excluding daily) | Visible note type filters |
| `showDaily` | `false` | Show Daily folder notes |
| `hideIsolated` | `false` | Hide unconnected nodes |
| `labelsOnHoverOnly` | `false` | Only show labels on hover |
| `edgeColorMode` | `'binary'` | Edge coloring: `binary` (opacity) or `gradient` (blue to red) |
| `nodeColorMode` | `'type'` | Node coloring: `type` (by note type) or `recency` (heat ramp) |
| `nodeSizeMode` | `'incoming_links'` | Node sizing: `uniform`, `incoming_links`, `betweenness`, or `recency` |
| `edgePruning` | `false` | Enable per-node edge pruning for dense graphs |
| `edgePruningK` | `8` | Max edges per node when pruning is enabled |
| `scalingRatio` | `10` | Node repulsion multiplier |
| `gravity` | `1` | Attraction to center (capped at 5) |
| `slowDown` | `0.5` | Cooling rate |
| `edgeWeightInfluence` | `2` | Edge attraction multiplier |
| `startTemperature` | `0.8` | Initial simulation energy |
| `stopThreshold` | `0.01` | Energy level below which layout pauses |

**Computed State**

| Key | Description |
|-----|-------------|
| `fileTree` | Nested folder structure derived from nodes |
| `nodeMap` | `Map<stem, NoteNode>` for O(1) lookup |
| `stemLookup` | Wikilink resolution map (exact + fuzzy matching) |
| `stats` | Visible node/edge counts and average semantic score |
| `graphStats` | Detailed graph metrics: average degree, max degree, top 5 hub nodes, graph density, connected component count |
| `selectedNode` | Currently highlighted graph node |
| `nodeSizeMap` | Betweenness centrality values (only when `nodeSizeMode` is `'betweenness'`; `null` otherwise) |
| `nodeSizeComputing` | Whether betweenness centrality is currently being computed |

## Graph Visualization Engine

### Physics Simulation

A custom Newtonian force loop drives the layout (see `lib/useForceLayout.ts`). It is **not** ForceAtlas2 — per frame it applies center gravity, pairwise Coulomb repulsion, Hooke attraction on edges, and velocity damping:

```mermaid
stateDiagram-v2
    [*] --> Loading: fetch graph.json
    Loading --> Ready: build Graphology graph
    Ready --> Simulating: start force loop
    Simulating --> Simulating: per-frame RAF loop
    Simulating --> Converged: temperature < stopThreshold
    Converged --> Simulating: drag node (reheat)
    Converged --> [*]: pause layout

    note right of Simulating
        Per frame:
        - gravity + repulsion + edge attraction
        - velocity damping + position update
        - decay temperature
        - update Sigma display
    end note
```

**Cooling:** Temperature decays per frame at `temp *= (1 - 0.002 * slowDown)`. At `slowDown=1`, convergence takes ~29 seconds at 60 fps; at `slowDown=5`, ~6 seconds.

### Neighborhood Computation

For local (2-hop) view:

1. Build adjacency list from wiki edges (O(E) setup, done once)
2. BFS from active note using wiki edges only
3. Collect all nodes within 2 hops
4. Include semantic edges between neighborhood nodes

> **Why wiki-only BFS?** Semantic edges form a dense graph (19K+ edges at 0.70 threshold). 2-hop semantic BFS reaches ~70% of the vault. Wiki edges reflect true structural relationships and produce useful, bounded neighborhoods.

### Performance

| Metric | Value |
|--------|-------|
| Tested vault size | 1000+ notes |
| Semantic edges at 0.70 threshold | ~19,000 |
| Rendering | WebGL via Sigma.js — ~1000 nodes at 60 fps |
| Physics | Custom Newtonian loop, O(N²) per iteration (pairwise repulsion) with velocity tracking |
| Content loading | Cached per-tab after first fetch |

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VAULT_ROOT` | `~/ParsidionVault` (legacy: `~/ClaudeVault`) | Custom vault root path |

### Dev Server Port

The dev and production server runs on **port 3999** (configured in `package.json`).

### localStorage Persistence

All graph controls and UI layout are persisted to `localStorage` using the `vv:` prefix. Keys are listed in the [State Management](#state-management) section. Clear `localStorage` in browser DevTools to reset all settings to defaults.

## File Structure

```
parsidion/
├── visualizer/                       # Next.js App Router root (plain next dev/start, no custom server)
│   ├── app/
│   │   ├── page.tsx                  # Main layout and state wiring
│   │   ├── layout.tsx                # HTML head, global styles
│   │   ├── api/note/route.ts            # Note CRUD API (GET, POST, PUT, DELETE)
│   │   ├── api/note/history/route.ts    # Git log for a note (GET)
│   │   ├── api/note/diff/route.ts       # Git diff between two commits (GET)
│   │   ├── api/files/route.ts           # Vault file tree (GET)
│   │   ├── api/vaults/route.ts          # List available vaults (GET)
│   │   ├── api/vault/events/route.ts    # SSE stream of file/create/modify/delete + graph:rebuilt (GET)
│   │   ├── api/graph/route.ts           # Serve graph.json from vault (GET)
│   │   ├── api/graph/rebuild/route.ts   # Trigger graph.json rebuild (POST)
│   │   ├── api/stats/route.ts           # Pending-summary count for VaultStats (GET)
│   │   ├── api/summarize/route.ts       # Spawn the summarizer subprocess (POST, auth)
│   │   └── api/summarizer/status/route.ts # Live summarizer run progress (GET)
│   ├── components/
│   │   ├── GraphCanvas.tsx           # Sigma.js WebGL renderer + node right-click menu
│   │   ├── HUDPanel.tsx              # Graph controls overlay (edge color, node color/size, density, physics)
│   │   ├── FileExplorer.tsx          # Sidebar with folder tree + right-click context menu
│   │   ├── ReadingPane.tsx           # Markdown renderer + HISTORY toolbar button
│   │   ├── HistoryView.tsx           # Split-screen git history viewer
│   │   ├── CommitList.tsx            # Scrollable commit list with FROM/TO selection
│   │   ├── DiffViewer.tsx            # Diff renderer (unified / split / words modes)
│   │   ├── Toolbar.tsx               # Top bar: tabs + vault selector + VaultStats + sync dot + new note
│   │   ├── VaultSelector.tsx         # Multi-vault dropdown switcher
│   │   ├── VaultStats.tsx            # PEND / NOTES chips; triggers + monitors summarizer runs
│   │   ├── TabBar.tsx                # Scrollable tab strip with permanent Graph tab
│   │   ├── UnifiedSearch.tsx         # ⌘K search input + dropdown
│   │   ├── TemperatureBar.tsx        # Simulation energy indicator
│   │   ├── NewNoteDialog.tsx         # Dialog for creating new vault notes
│   │   ├── ConfirmDialog.tsx         # Reusable confirmation prompt
│   │   ├── ConflictDialog.tsx        # Edit conflict resolution (take theirs / keep mine / merge)
│   │   ├── FrontmatterEditor.tsx     # Structured YAML frontmatter editor
│   │   └── ViewToggle.tsx            # (unused) Legacy Read/Graph mode toggle — replaced by TabBar Graph tab
│   ├── lib/
│   │   ├── graph.ts                  # Data types and fetch helpers
│   │   ├── useVisualizerState.ts     # Central state management hook (incl. vault, history, graph controls)
│   │   ├── useVaultFiles.ts          # SSE / EventSource hook for real-time vault sync
│   │   ├── useForceLayout.ts         # Custom Newtonian physics loop (gravity + repulsion + edge attraction + damping)
│   │   ├── useForceLayout.test.ts    # Unit tests for the physics loop
│   │   ├── useGraphReducers.ts       # Sigma node/edge reducers and neighborhood computation
│   │   ├── useFocusTrap.ts           # Focus-trap hook used by accessible modal dialogs
│   │   ├── vaultFile.ts              # VaultFile type (shared client/server)
│   │   ├── vaultResolver.ts          # Multi-vault path resolution (server-side, with forbidden-prefix guard)
│   │   ├── vaultBroadcast.server.ts  # Global EventEmitter for server-side graph:rebuilt events
│   │   ├── vaultStatsServer.ts       # Summarizer spawn/status + pending-summary counting (server-side)
│   │   ├── graphDelta.ts             # Graph diff/merge helpers for incremental updates
│   │   ├── apiAuth.ts                # Shared auth + same-origin guards for mutating/SSE routes
│   │   ├── parseDiff.ts              # Client-side unified diff parser (DiffHunk, DiffLine)
│   │   ├── parseDiff.test.ts         # Unit tests for parseDiff
│   │   ├── sigma-colors.ts           # Note type → color mapping, edge coloring, node sizing constants
│   │   ├── sigma-colors.test.ts      # Unit tests for sigma-colors
│   │   ├── sigma-renderers.ts        # Custom Sigma label/hover renderers
│   │   ├── frontmatter.ts            # Frontmatter parse/serialize helpers
│   │   ├── frontmatter.test.ts       # Unit tests for frontmatter
│   │   └── useLocalStorage.ts        # localStorage persistence hook
│   ├── public/
│   │   └── (static assets only — graph.json lives in the vault, not here)
│   ├── package.json
│   ├── tsconfig.json
│   └── next.config.ts                # Security headers (X-Frame-Options, X-Content-Type-Options, Referrer-Policy)
│
│
└── Makefile                          # Build targets
```

## Related Documentation

- [Architecture](ARCHITECTURE.md) — Full system component map
- [Embeddings](EMBEDDINGS.md) — How embeddings are built and evaluated
- [CLAUDE.md](../CLAUDE.md) — Project conventions and script reference
