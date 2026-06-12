# Graph Features Design: 5 Visualizer Enhancements

> **Note:** Historical design document. Path references and project names may reflect the legacy `parsidion-cc` name (renamed to `parsidion` in v0.7.0). Do not edit this document.

**Date:** 2026-03-25
**Status:** Approved
**Scope:** Vault visualizer (Next.js + Sigma.js v3 + Graphology 0.26, port 3999)

## Overview

Implement five graph enhancement features in the vault visualizer:

1. Semantic Gradient Coloring
2. Node Sizing by Centrality
3. Graph Statistics Panel
4. Shortest Path Finder
5. Edge Density Reduction

No new npm packages are required. All algorithms are implemented with existing `graphology` + custom TypeScript.

---

## Files Modified

| File | Purpose |
|------|---------|
| `visualizer/lib/sigma-colors.ts` | Add `getSemanticEdgeColor()` gradient helper |
| `visualizer/lib/useVisualizerState.ts` | New state fields, `graphStats`, `nodeSizeMap` computation |
| `visualizer/components/GraphCanvas.tsx` | New props, refs, effects, path finder logic, context menu |
| `visualizer/components/HUDPanel.tsx` | Three new HUD sections; extended stats panel |
| `visualizer/app/page.tsx` | Wire new props through |

---

## Feature Designs

### 1. Semantic Gradient Coloring

**State:** `edgeColorMode: 'binary' | 'gradient'`
- Persisted to localStorage key `vv:edgeColorMode`
- Default: `'binary'` (existing behavior unchanged)

**Logic:**
New function signature in `sigma-colors.ts`:

```ts
export function getSemanticEdgeColor(
  weight: number,
  kind: 'wiki' | 'semantic',
  mode: 'binary' | 'gradient'
): string
```

- `binary` mode OR `kind === 'wiki'`: use existing binary colors (wiki = `rgba(123,97,255,0.35)`, semantic = weight-derived opacity). Wiki edges are **never** gradient-colored regardless of mode.
- `gradient` mode + `kind === 'semantic'`: HSL interpolation by weight in range [0.7, 1.0]:
  - w=0.7 → `hsl(220, 80%, 55%)` (blue)
  - w=1.0 → `hsl(0, 90%, 55%)` (red)
  - Formula: `hue = 220 * (1 - t)` where `t = clamp((w - 0.7) / 0.3, 0, 1)`

A `useEffect` in `GraphCanvas` reacts to `edgeColorMode` changes. It iterates `graphRef.current.edges()`, reads `kind` and `baseWeight` attributes from each edge, **skips wiki edges** (they keep their binary color always), and updates `color` + `originalColor` on semantic edges only. Calls `sigma.refresh()` after.

**HUD:** "Edge Color" toggle section (Binary / Gradient buttons, same style as Edge Source). Appears above the Similarity slider.

---

### 2. Node Sizing by Centrality

**State:** `nodeSizeMode: 'uniform' | 'incoming_links' | 'betweenness' | 'recency'`
- Persisted to localStorage key `vv:nodeSizeMode`
- Default: `'incoming_links'` (existing behavior unchanged)

**Sizing formulas:**
- `uniform`: fixed size 4
- `incoming_links`: existing `getNodeSize(incoming_links)` — `max(2, log(n+1)*2)`
- `recency`: `max(2, 10 - log(age_days + 1) * 1.5)` — newer = larger
  - `age_days = (Date.now() / 1000 - node.mtime) / 86400` (mtime is Unix seconds, a float)
- `betweenness`: normalized Brandes score scaled to range [2, 14]

**Betweenness computation:**
Brandes algorithm on wiki-only undirected edges, implemented in `useVisualizerState` as a `useMemo` **guarded by `nodeSizeMode === 'betweenness'`** (returns `null` otherwise). Runs on `graphData.edges` — the **full unfiltered** edge set from `graph.json`, not the live graphology graph instance (which reflects current filter state and would produce unstable scores as the user adjusts filters). Result stored in `nodeSizeMap: Map<string, number>` passed as prop to `GraphCanvas`.

A `useEffect` in `GraphCanvas` reacts to `nodeSizeMap` or `nodeSizeMode` changes and updates all node sizes in `graphRef.current`, then calls `sigma.refresh()`. **Guard:** if `nodeSizeMode === 'incoming_links'` and `nodeSizeMap` is `null`, do nothing (sizes were already set correctly during init).

**HUD:** "Node Size" section with four toggle buttons. When `betweenness` is selected and `nodeSizeMap` is still `null`, display `"Computing…"` label.

---

### 3. Graph Statistics Panel

**State:** `graphStats` memoized in `useVisualizerState` alongside existing `stats`:

```ts
interface GraphStats {
  avgDegree: number
  maxDegree: number
  topHubs: Array<{ id: string; title: string; degree: number }>  // top 5
  density: number
  componentCount: number
}
```

**Computation:** Scoped to the **same `visibleNodes` set** already computed in `useVisualizerState.stats` (which accounts for `activeTypes`, `showDaily`, and the `filterNodesBySimilarity` filter). This ensures the stats panel reflects exactly what the user sees.

- `degree` counts wiki edges only (undirected), where both endpoints are in `visibleNodes`
- `density = wikiEdges / (n * (n - 1) / 2)` where n = `visibleNodes.size`
- `componentCount` via BFS on wiki adjacency restricted to `visibleNodes`
- `topHubs` sorted descending by degree, top 5

**HUD:** Collapsible "Graph Analysis" section below the existing stats row. Shows metrics + top-5 hub notes as clickable chips. Clicking a chip calls `canvasRef.current?.flyToNode(id)` — **uses the existing `canvasRef` prop already on `HUDPanel`**, no new `onFlyToNode` prop is needed.

---

### 4. Shortest Path Finder

**State:** Internal to `GraphCanvas` (refs, not useState, to avoid unnecessary re-renders):
- `pathSourceRef: React.MutableRefObject<string | null>`
- `pathNodesRef: React.MutableRefObject<Set<string>>`
- `pathEdgesRef: React.MutableRefObject<Set<string>>`

Also a `useState` for the toast message: `toastMsg: string | null`.

**UX flow:**
1. Right-click any node → context menu shows **"Set Path Origin"**
   - When `pathSourceRef.current !== null`: also shows **"Origin: \<stem\>"** (dimmed, non-clickable) + **"Find Path Here"** (highlighted)
2. "Find Path Here" runs BFS on wiki-only undirected adjacency (same algorithm as existing `neighborhoodInfo` in the same file). Builds `pathEdgesRef` by tracking graphology edge IDs along the BFS parent chain.
3. Path found: populate `pathNodesRef` / `pathEdgesRef`, clear `pathSourceRef`, `sigma.refresh()`, show toast.
4. Path not found: show toast `"No wiki-link path found"`, keep `pathSourceRef` set.
5. Background click: **extend the existing `clickStage` handler** (do not register a second one). Add `pathSourceRef.current = null; pathNodesRef.current = new Set(); pathEdgesRef.current = new Set()` before the existing `sigma.refresh()` call.

**nodeReducer / edgeReducer:** Path nodes render in `#FFD700` (yellow) at full size. Path edges render in `#FFD700` at `size: 3`. Path highlight takes precedence over the `highlightedNodesRef`/`highlightedEdgesRef` dim logic — check path sets before the dim check.

**Toast notification:** `toastMsg` React state rendered as an absolutely-positioned `<div>` at bottom-center of the graph container (inside the `return` JSX). Auto-dismiss via `useEffect` with a `setTimeout(clearToast, 4000)` — **always clear the prior timeout ref before setting a new one** (store timeout ID in a `useRef`). Clear timeout on component unmount.

---

### 5. Edge Density Reduction

**State:**
- `edgePruning: boolean` — localStorage `vv:edgePruning`, default `false`
- `edgePruningK: number` — localStorage `vv:edgePruningK`, default `8`

**Logic:** A `pruneEdges(edges: GraphEdge[], k: number): GraphEdge[]` function in `GraphCanvas.tsx` (co-located with the effects that use it). Applied as a post-filter step after `filterEdges()` **for primary edges only** — overlay edges are added in a separate block after pruning and are not passed through `pruneEdges()`.

```ts
function pruneEdges(edges: GraphEdge[], k: number): GraphEdge[] {
  const perNode = new Map<string, GraphEdge[]>()
  for (const e of edges) {
    if (!perNode.has(e.s)) perNode.set(e.s, [])
    if (!perNode.has(e.t)) perNode.set(e.t, [])
    perNode.get(e.s)!.push(e)
    perNode.get(e.t)!.push(e)
  }
  const kept = new Set<GraphEdge>()
  for (const [, nodeEdges] of perNode) {
    nodeEdges.sort((a, b) => b.w - a.w)
    nodeEdges.slice(0, k).forEach(e => kept.add(e))
  }
  return edges.filter(e => kept.has(e))
}
```

**Reactivity:** `edgePruning` and `edgePruningK` must be added to the dependency array of the `threshold/graphSource/data` update effect (lines ~750–787 in `GraphCanvas.tsx`). This effect calls `graph.clearEdges()` and rebuilds, so adding them as deps is sufficient to trigger a rebuild when pruning is toggled or K is changed.

The `data`-only init effect (guarded by `}, [data])`) also applies pruning on first mount when `edgePruning` is true — it reads `edgePruning`/`edgePruningK` via refs (`edgePruningRef`, `edgePruningKRef`) updated by their own `useEffect`s (same pattern as `edgeWeightInfluenceRef`).

Physics layout uses the pruned edge set automatically.

**HUD:** "Edge Density" section, rendered only when `graphData.meta.edge_count > 2000` (uses the **total** edge count from the raw graph metadata, not the filtered `edgeCount` from `stats`). If pruning is active but the section is hidden (e.g., user switched to wiki-only source where edgeCount dropped), pruning stays active silently. Contains:
- Toggle checkbox labeled "Reduce Edge Density"
- "Max edges/node" slider (range 3–20, step 1), shown only when toggle is on
- Tip: "Keeps the K strongest connections per node — reduces visual clutter on dense graphs."

---

## Prop Interface Changes

### GraphCanvas new props
```ts
edgeColorMode: 'binary' | 'gradient'
nodeSizeMode: 'uniform' | 'incoming_links' | 'betweenness' | 'recency'
nodeSizeMap: Map<string, number> | null
edgePruning: boolean
edgePruningK: number
```

### HUDPanel new props
```ts
edgeColorMode: 'binary' | 'gradient'
onEdgeColorModeChange: (mode: 'binary' | 'gradient') => void
nodeSizeMode: 'uniform' | 'incoming_links' | 'betweenness' | 'recency'
onNodeSizeModeChange: (mode: 'uniform' | 'incoming_links' | 'betweenness' | 'recency') => void
nodeSizeComputing: boolean
graphStats: GraphStats | null
edgePruning: boolean
onToggleEdgePruning: () => void
edgePruningK: number
onEdgePruningKChange: (k: number) => void
totalEdgeCount: number   // graphData.meta.edge_count for HUD visibility logic
```

Note: `onFlyToNode` is NOT added. Hub chip clicks in HUDPanel use the existing `canvasRef` prop.

### useVisualizerState return additions
```ts
edgeColorMode, setEdgeColorMode,
nodeSizeMode, setNodeSizeMode,
nodeSizeMap,                       // Map<string, number> | null
nodeSizeComputing,                 // boolean (true while betweenness is computing)
edgePruning, toggleEdgePruning,
edgePruningK, setEdgePruningK,
graphStats,                        // GraphStats | null
```

---

## Implementation Order

1. `sigma-colors.ts` — add `getSemanticEdgeColor(weight, kind, mode)`
2. `useVisualizerState.ts` — add all new state + `graphStats` + `nodeSizeMap` + Brandes algorithm
3. `GraphCanvas.tsx` — add props + refs + all feature effects + shortest path + context menu + toast
4. `HUDPanel.tsx` — add three new sections + extended stats
5. `app/page.tsx` — wire new props

Features 1, 2, 3, 5 can be implemented in parallel after step 2. Feature 4 is independent of 1/2/3/5.
