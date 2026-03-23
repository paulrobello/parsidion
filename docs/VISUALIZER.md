# Vault Visualizer

An interactive web application for exploring and navigating a ClaudeVault knowledge base through dual-mode reading and graph visualization.

## Table of Contents
- [Overview](#overview)
- [Architecture](#architecture)
- [Features](#features)
  - [Read Mode](#read-mode)
  - [Graph Mode](#graph-mode)
  - [Multi-Tab Support](#multi-tab-support)
  - [File Explorer Sidebar](#file-explorer-sidebar)
  - [Unified Search](#unified-search)
  - [Keyboard Shortcuts](#keyboard-shortcuts)
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
- Dual-mode interface: Read (Markdown rendering) and Graph (force-directed visualization)
- Multi-tab note browsing with persistent state
- Unified search across titles, tags, and folders (⌘K)
- Interactive graph with per-node neighborhood and full-vault views
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
    end

    subgraph "Data"
        GJ[graph.json]
        API["/api/note?stem="]
        Vault[ClaudeVault Notes]
    end

    subgraph "Build Pipeline"
        Emb[embeddings.db]
        Builder[build_graph.py]
    end

    App --> Read
    App --> Graph
    App --> Search
    App --> Sidebar

    App -->|fetch on load| GJ
    Read -->|fetch on open| API
    API --> Vault

    Emb --> Builder
    Builder --> GJ

    style App fill:#e65100,stroke:#ff9800,stroke-width:3px,color:#ffffff
    style Graph fill:#0d47a1,stroke:#2196f3,stroke-width:2px,color:#ffffff
    style Read fill:#1b5e20,stroke:#4caf50,stroke-width:2px,color:#ffffff
    style Search fill:#4a148c,stroke:#9c27b0,stroke-width:2px,color:#ffffff
    style Sidebar fill:#37474f,stroke:#78909c,stroke-width:2px,color:#ffffff
    style GJ fill:#1a237e,stroke:#3f51b5,stroke-width:2px,color:#ffffff
    style API fill:#37474f,stroke:#78909c,stroke-width:2px,color:#ffffff
    style Vault fill:#37474f,stroke:#78909c,stroke-width:1px,color:#ffffff
    style Emb fill:#1a237e,stroke:#3f51b5,stroke-width:2px,color:#ffffff
    style Builder fill:#880e4f,stroke:#c2185b,stroke-width:2px,color:#ffffff
```

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | Next.js + React |
| Graph Rendering | Sigma.js (WebGL) |
| Graph Layout | Graphology + ForceAtlas2 |
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
    ViewToggle[ViewToggle.tsx]
    Sidebar[FileExplorer.tsx]
    ReadPane[ReadingPane.tsx]
    GraphCanvas[GraphCanvas.tsx]
    HUD[HUDPanel.tsx]
    TempBar[TemperatureBar.tsx]
    NewNote[NewNoteDialog.tsx]
    Confirm[ConfirmDialog.tsx]
    FmEditor[FrontmatterEditor.tsx]

    Page --> Toolbar
    Toolbar --> TabBar
    Toolbar --> Search
    Toolbar --> ViewToggle
    Page --> Sidebar
    Page --> ReadPane
    Page --> GraphCanvas
    Page --> NewNote
    ReadPane --> Confirm
    ReadPane --> FmEditor
    GraphCanvas --> HUD
    GraphCanvas --> TempBar

    style Page fill:#e65100,stroke:#ff9800,stroke-width:3px,color:#ffffff
    style Toolbar fill:#1b5e20,stroke:#4caf50,stroke-width:2px,color:#ffffff
    style GraphCanvas fill:#0d47a1,stroke:#2196f3,stroke-width:2px,color:#ffffff
    style HUD fill:#0d47a1,stroke:#2196f3,stroke-width:1px,color:#ffffff
    style Sidebar fill:#37474f,stroke:#78909c,stroke-width:2px,color:#ffffff
    style ReadPane fill:#1b5e20,stroke:#4caf50,stroke-width:1px,color:#ffffff
    style TabBar fill:#37474f,stroke:#78909c,stroke-width:1px,color:#ffffff
    style Search fill:#4a148c,stroke:#9c27b0,stroke-width:2px,color:#ffffff
    style ViewToggle fill:#37474f,stroke:#78909c,stroke-width:1px,color:#ffffff
    style TempBar fill:#37474f,stroke:#78909c,stroke-width:1px,color:#ffffff
    style NewNote fill:#880e4f,stroke:#c2185b,stroke-width:1px,color:#ffffff
    style Confirm fill:#880e4f,stroke:#c2185b,stroke-width:1px,color:#ffffff
    style FmEditor fill:#880e4f,stroke:#c2185b,stroke-width:1px,color:#ffffff
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
- **Inline editing**: toggle edit mode to modify note body and frontmatter
  - FrontmatterEditor provides structured editing of type, date, confidence, tags, project, sources, and related links with tag autocomplete from the graph
  - Save and delete operations via the note CRUD API

### Graph Mode

Interactive force-directed graph for exploring note relationships:

**Default (Local) View**
- Displays a 2-hop neighborhood around the currently active note
- Uses wiki edges (explicit wikilinks) for BFS traversal
- Shows semantic edges within the neighborhood

**Full Vault View**
- Toggle via "Show Full Vault ⤢" button in the HUD
- Renders all notes and edges simultaneously

**Visual Encoding**

| Element | Encoding |
|---------|---------|
| Node color | Note type (pattern, debugging, research, project, tool, language, framework, daily) |
| Node size | Incoming link count (logarithmic scale) |
| Wiki edge | Solid line — explicit wikilinks |
| Semantic edge | Solid line — embedding similarity above threshold |

**Interactions**
- Click node → opens note in current tab and highlights selection
- Drag node → pins position, reheats physics simulation
- Hover node → shows label (if labels-on-hover mode is active)

#### HUD Panel

Floating overlay in the bottom-left of the graph canvas.

**Display Controls**
- Semantic similarity threshold (0.0–1.0)
- Graph source: Semantic vs. Wiki
- Overlay edges (show opposite type at low opacity)
- Node type filter checkboxes
- Show daily notes toggle
- Hide isolated nodes toggle
- Labels on hover only toggle

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

- Up to 8 results shown per query
- Each result: colored type dot, title with match highlighting, folder path, tags
- Keyboard navigation: ↑↓ to move, ⏎ to open, ⌘⏎ for new tab
- Click → open in current tab; Cmd+click → new tab
- In Graph mode: opening a result flies camera to that node
- All data served from `graph.json` — no server round-trips

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| ⌘K / Ctrl+K | Focus search input |
| ⌘B / Ctrl+B | Toggle sidebar |
| ⌘\ / Ctrl+\ | Toggle Read / Graph mode |
| Esc | Close search dropdown or deselect graph node |
| ↑ ↓ (search) | Navigate results |
| ⏎ (search) | Open selected result |
| ⌘⏎ (search) | Open selected result in new tab |

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

The `graph.json` file is a pre-computed snapshot of vault relationships. Rebuild it whenever notes are added, removed, or embeddings are updated.

### Prerequisites

1. Vault must have embeddings built:
   ```bash
   uv run --no-project ~/.claude/skills/parsidion-cc/scripts/build_embeddings.py
   ```

2. Run the graph builder:
   ```bash
   make graph               # Exclude Daily notes (recommended)
   make graph-with-daily    # Include Daily folder notes
   ```

### Graph Builder Options

```bash
uv run scripts/build_graph.py [OPTIONS]

Options:
  --include-daily        Include Daily folder notes (excluded by default)
  --min-threshold FLOAT  Minimum cosine similarity for semantic edges (default: 0.70)
  --output PATH          Output path for graph.json
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
                       //   tool | language | framework | daily
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
  }
  nodes: NoteNode[]
  edges: GraphEdge[]
}
```

### API Route

**`GET /api/note?stem=<stem>`**

Returns the Markdown content for a note identified by its stem ID.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `stem` | string | Yes | Vault note stem (filename without extension) |

**Response (200):** JSON `{ content: string, path: string }` — raw Markdown and vault-relative path

**Response (404):** JSON error — note not found

**`POST /api/note`** — Update (overwrite) an existing note.
Body: `{ stem: string, content: string }`

**`PUT /api/note`** — Create a new note at a vault-relative path.
Body: `{ path: string, content: string }`. Returns 409 if the note already exists.

**`DELETE /api/note?stem=<stem>`** — Delete a note by stem.

**`POST /api/graph/rebuild`** — Trigger a server-side `build_graph.py` run to regenerate `graph.json`.

## State Management

All application state is managed by the `useVisualizerState` hook (`lib/useVisualizerState.ts`). State is split into categories:

**Tab / View State**

| Key | Type | Description |
|-----|------|-------------|
| `openTabs` | `string[]` | Array of open note stems |
| `activeTab` | `string \| null` | Currently displayed note stem |
| `viewMode` | `'read' \| 'graph'` | Current display mode |
| `graphScope` | `'local' \| 'full'` | Neighborhood vs. full vault |

**Sidebar State**

| Key | Type | Description |
|-----|------|-------------|
| `sidebarWidth` | `number` | Width in pixels (180–400) |
| `sidebarCollapsed` | `boolean` | Whether sidebar is hidden |

**Graph Controls** (all persisted to `localStorage` with `vv:` prefix)

| Key | Default | Description |
|-----|---------|-------------|
| `threshold` | `0.70` | Semantic similarity cutoff |
| `graphSource` | `'semantic'` | Primary edge type to display |
| `showOverlayEdges` | `false` | Show opposite edge type at low opacity |
| `activeTypes` | all types | Visible note type filters |
| `showDaily` | `false` | Show Daily folder notes |
| `hideIsolated` | `false` | Hide unconnected nodes |
| `labelsOnHoverOnly` | `true` | Only show labels on hover |
| `scalingRatio` | `2` | Node repulsion multiplier |
| `gravity` | `0.1` | Attraction to center |
| `slowDown` | `1` | Cooling rate |
| `edgeWeightInfluence` | `1` | Edge attraction multiplier |

**Computed State**

| Key | Description |
|-----|-------------|
| `fileTree` | Nested folder structure derived from nodes |
| `nodeMap` | `Map<stem, NoteNode>` for O(1) lookup |
| `stemLookup` | Wikilink resolution map (exact + fuzzy matching) |
| `stats` | Visible node/edge counts and average semantic score |
| `selectedNode` | Currently highlighted graph node |

## Graph Visualization Engine

### Physics Simulation

The ForceAtlas2 algorithm drives the layout:

```mermaid
stateDiagram-v2
    [*] --> Loading: fetch graph.json
    Loading --> Ready: build Graphology graph
    Ready --> Simulating: start ForceAtlas2
    Simulating --> Simulating: per-frame RAF loop
    Simulating --> Converged: temperature < stopThreshold
    Converged --> Simulating: drag node (reheat)
    Converged --> [*]: pause layout

    note right of Simulating
        Per frame:
        - apply FA2 iteration
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
| Physics | O(N) per iteration with velocity tracking |
| Content loading | Cached per-tab after first fetch |

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VAULT_ROOT` | `~/ClaudeVault` | Custom vault root path |

### Dev Server Port

The dev and production server runs on **port 3999** (configured in `package.json`).

### localStorage Persistence

All graph controls and UI layout are persisted to `localStorage` using the `vv:` prefix. Keys are listed in the [State Management](#state-management) section. Clear `localStorage` in browser DevTools to reset all settings to defaults.

## File Structure

```
parsidion-cc/
├── visualizer/                       # Next.js app root
│   ├── app/
│   │   ├── page.tsx                  # Main layout and state wiring
│   │   ├── layout.tsx                # HTML head, global styles
│   │   ├── api/note/route.ts         # Note CRUD API (GET, POST, PUT, DELETE)
│   │   └── api/graph/rebuild/route.ts  # Trigger graph.json rebuild (POST)
│   ├── components/
│   │   ├── GraphCanvas.tsx           # Sigma.js WebGL renderer
│   │   ├── HUDPanel.tsx              # Graph controls overlay
│   │   ├── FileExplorer.tsx          # Sidebar with folder tree
│   │   ├── ReadingPane.tsx           # Markdown renderer
│   │   ├── Toolbar.tsx               # Top bar with hamburger + tabs
│   │   ├── TabBar.tsx                # Scrollable tab strip
│   │   ├── UnifiedSearch.tsx         # ⌘K search input + dropdown
│   │   ├── ViewToggle.tsx            # Read / Graph pill toggle
│   │   ├── TemperatureBar.tsx        # Simulation energy indicator
│   │   ├── NewNoteDialog.tsx         # Dialog for creating new vault notes
│   │   └── ConfirmDialog.tsx         # Reusable confirmation prompt
│   ├── lib/
│   │   ├── graph.ts                  # Data types and fetch helpers
│   │   ├── useVisualizerState.ts     # Central state management hook
│   │   ├── sigma-colors.ts           # Note type → color mapping
│   │   └── useLocalStorage.ts        # localStorage persistence hook
│   ├── public/
│   │   └── graph.json                # Pre-computed graph (generated)
│   ├── package.json
│   ├── tsconfig.json
│   └── next.config.ts
│
├── scripts/
│   └── build_graph.py                # Graph data generator
│
└── Makefile                          # Build targets
```

## Related Documentation

- [Architecture Inventory](architecture-inventory.md) — Full system component map
- [Local Embeddings for Vault Semantic Search](../ClaudeVault/Research/local-embeddings-for-vault-semantic-search.md) — How embeddings are built
- [CLAUDE.md](../CLAUDE.md) — Project conventions and script reference
