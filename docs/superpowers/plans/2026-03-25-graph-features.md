# Graph Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add five interactive features to the vault graph visualizer: semantic gradient edge coloring, node sizing by centrality, a graph statistics panel, a shortest path finder, and edge density reduction.

**Architecture:** Each feature follows the established ref-based pattern in `GraphCanvas.tsx` (state synced to refs for low-latency access inside the physics loop and Sigma reducers), with control state persisted to localStorage via `useLocalStorage` in `useVisualizerState.ts`, and UI in `HUDPanel.tsx`. No new npm packages are needed.

**Tech Stack:** Next.js 16, React 19, Sigma.js v3, Graphology 0.26, TypeScript, Tailwind CSS v4, Bun

---

## File Map

| File | Role |
|------|------|
| `visualizer/lib/sigma-colors.ts` | New `getSemanticEdgeColor(weight, kind, mode)` helper |
| `visualizer/lib/useVisualizerState.ts` | New state: `edgeColorMode`, `nodeSizeMode`, `nodeSizeMap`, `nodeSizeComputing`, `edgePruning`, `edgePruningK`, `graphStats`; Brandes algorithm |
| `visualizer/components/GraphCanvas.tsx` | New props + refs for all features; `pruneEdges()`; path finder BFS; toast; `nodeReducer`/`edgeReducer` path highlight; context menu items; `nodeSizeMode` applied in init |
| `visualizer/components/HUDPanel.tsx` | Three new sections: Edge Color, Node Size, Edge Density; extended stats with Graph Analysis |
| `visualizer/app/page.tsx` | Wire all new props from state to GraphCanvas and HUDPanel |

---

## Task 1: Add `getSemanticEdgeColor` to sigma-colors.ts

**Files:**
- Modify: `visualizer/lib/sigma-colors.ts`

- [ ] **Step 1: Add the color function**

Replace the entire file with:

```ts
export const TYPE_COLORS: Record<string, string> = {
  pattern:   '#6366f1',
  debugging: '#ef4444',
  research:  '#10b981',
  project:   '#0ea5e9',
  tool:      '#f59e0b',
  language:  '#a855f7',
  framework: '#f97316',
  daily:     '#4b5563',
}

export function getNodeColor(type: string): string {
  return TYPE_COLORS[type] ?? '#6b7280'
}

export function getNodeSize(incomingLinks: number): number {
  return Math.max(2, Math.log(incomingLinks + 1) * 2)
}

export type EdgeColorMode = 'binary' | 'gradient'
export type NodeSizeMode = 'uniform' | 'incoming_links' | 'betweenness' | 'recency'

/**
 * Returns the color for an edge.
 * Wiki edges always use binary coloring regardless of mode.
 * Semantic edges: binary = opacity-based gray, gradient = blue→red by weight.
 */
export function getSemanticEdgeColor(
  weight: number,
  kind: 'wiki' | 'semantic',
  mode: EdgeColorMode
): string {
  if (kind === 'wiki') return 'rgba(123,97,255,0.35)'
  if (mode === 'binary') return `rgba(150,150,160,${Math.min(0.45, weight * 0.5)})`
  // gradient: HSL blue (220°) → red (0°) for weight in [0.7, 1.0]
  const t = Math.max(0, Math.min(1, (weight - 0.7) / 0.3))
  const hue = Math.round(220 * (1 - t))
  return `hsl(${hue}, 80%, 55%)`
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd visualizer && bun run lint
```

Expected: no errors on `sigma-colors.ts`

- [ ] **Step 3: Commit**

```bash
git add visualizer/lib/sigma-colors.ts
git commit -m "feat(visualizer): add getSemanticEdgeColor gradient helper"
```

---

## Task 2: Feature 1 — Semantic Gradient Coloring

**Files:**
- Modify: `visualizer/lib/useVisualizerState.ts`
- Modify: `visualizer/components/GraphCanvas.tsx`
- Modify: `visualizer/components/HUDPanel.tsx`
- Modify: `visualizer/app/page.tsx`

### 2a: State in useVisualizerState

- [ ] **Step 1: Add import and state**

Replace the existing `import { TYPE_COLORS } from '@/lib/sigma-colors'` at the top of `useVisualizerState.ts` with:

```ts
import { TYPE_COLORS, EdgeColorMode, NodeSizeMode } from '@/lib/sigma-colors'
```

Add after `const [selectedNode, setSelectedNode] = useState<string | null>(null)`:

```ts
const [edgeColorMode, setEdgeColorMode] = useLocalStorage<EdgeColorMode>('vv:edgeColorMode', 'binary')
```

Add to the return object (alongside `selectedNode, setSelectedNode`):

```ts
edgeColorMode, setEdgeColorMode,
```

### 2b: GraphCanvas — ref, effect, init/update usage

- [ ] **Step 2: Add prop and ref to GraphCanvas**

In the `Props` interface, add after `filterNodesBySimilarity`:

```ts
edgeColorMode: EdgeColorMode
```

In the destructuring at the top of the component, add `edgeColorMode`.

Add import at top of file:

```ts
import { getNodeColor, getNodeSize, getSemanticEdgeColor } from '@/lib/sigma-colors'
import type { EdgeColorMode } from '@/lib/sigma-colors'
```

(Replace the existing `import { getNodeColor, getNodeSize } from '@/lib/sigma-colors'`)

Add ref near other refs (after `thresholdRef`):

```ts
const edgeColorModeRef = useRef(edgeColorMode)
```

Add sync effect (after the `useEffect(() => { thresholdRef.current = threshold }, [threshold])` line):

```ts
useEffect(() => { edgeColorModeRef.current = edgeColorMode }, [edgeColorMode])
```

- [ ] **Step 3: Update edge color assignment in the init effect**

In the `init` async function, find the line that computes `col` for primary edges (around line 352):

```ts
const col = edge.kind === 'wiki' ? 'rgba(123,97,255,0.35)' : `rgba(150,150,160,${Math.min(0.45, edge.w * 0.5)})`
```

Replace with:

```ts
const col = getSemanticEdgeColor(edge.w, edge.kind, edgeColorModeRef.current)
```

Also update the overlay edge `col` for the semantic overlay case (around line 368) — leave wiki overlay unchanged:

```ts
const col = overlayKind === 'wiki' ? 'rgba(123,97,255,0.18)' : 'rgba(150,150,160,0.18)'
```

(Overlay edges are always dimmed, no gradient needed — leave this line unchanged.)

- [ ] **Step 4: Update edge color in the threshold/source/data update effect**

Find the update effect (starts around line 750 with `graph.clearEdges()`). Find the line:

```ts
const col = edge.kind === 'wiki' ? 'rgba(123,97,255,0.35)' : `rgba(150,150,160,${Math.min(0.45, edge.w * 0.5)})`
```

Replace with:

```ts
const col = getSemanticEdgeColor(edge.w, edge.kind, edgeColorModeRef.current)
```

- [ ] **Step 5: Add effect to live-update edge colors when mode changes**

Add this effect after the `useEffect(() => { edgeColorModeRef.current = edgeColorMode }, [edgeColorMode])` sync effect:

```ts
useEffect(() => {
  const graph = graphRef.current
  const sigma = sigmaRef.current
  if (!graph || !sigma) return
  ;(graph.edges() as string[]).forEach((e: string) => {
    if (graph.getEdgeAttribute(e, 'overlay')) return
    const kind = graph.getEdgeAttribute(e, 'kind') as 'wiki' | 'semantic'
    if (kind === 'wiki') return
    const baseWeight = graph.getEdgeAttribute(e, 'baseWeight') as number
    const col = getSemanticEdgeColor(baseWeight, kind, edgeColorMode)
    graph.setEdgeAttribute(e, 'color', col)
    graph.setEdgeAttribute(e, 'originalColor', col)
  })
  sigma.refresh()
}, [edgeColorMode])
```

### 2c: HUDPanel — Edge Color section

- [ ] **Step 6: Add prop to HUDPanel**

In `HUDPanel.tsx`, add to the `Props` interface:

```ts
edgeColorMode: EdgeColorMode
onEdgeColorModeChange: (mode: EdgeColorMode) => void
```

Add the import at top:

```ts
import type { EdgeColorMode } from '@/lib/sigma-colors'
```

Add to the destructuring in `HUDPanel({...})`.

- [ ] **Step 7: Add Edge Color section to HUD**

Add this block immediately after the stats row `</div>` block (before the Threshold section):

```tsx
{/* Edge Color */}
<div>
  <div style={{ display: 'flex', alignItems: 'center', marginBottom: 5 }}>
    <span style={{ color: '#6B7A99', textTransform: 'uppercase', letterSpacing: '0.06em', fontSize: 10 }}>Edge Color</span>
    <Tip text="Binary: semantic edges use opacity. Gradient: blue (weak) → red (strong) by similarity score." />
  </div>
  <div style={{ display: 'flex', gap: 4 }}>
    {(['binary', 'gradient'] as const).map(m => (
      <button
        key={m}
        onClick={() => onEdgeColorModeChange(m)}
        style={{
          flex: 1, padding: '4px 0', borderRadius: 4, border: '1px solid',
          borderColor: edgeColorMode === m ? '#00FFC8' : 'rgba(255,255,255,0.08)',
          background: edgeColorMode === m ? 'rgba(0,255,200,0.12)' : 'transparent',
          color: edgeColorMode === m ? '#00FFC8' : '#6B7A99',
          cursor: 'pointer', fontSize: 10, fontFamily: 'Oxanium, sans-serif',
          textTransform: 'capitalize', transition: 'all 0.15s',
        }}
      >
        {m}
      </button>
    ))}
  </div>
</div>
```

### 2d: Wire through page.tsx

- [ ] **Step 8: Pass new props in page.tsx**

In `<GraphCanvas ... />`, add:

```tsx
edgeColorMode={state.edgeColorMode}
```

In `<HUDPanel ... />`, add:

```tsx
edgeColorMode={state.edgeColorMode}
onEdgeColorModeChange={state.setEdgeColorMode}
```

- [ ] **Step 9: Verify lint passes**

```bash
cd visualizer && bun run lint
```

Expected: no errors

- [ ] **Step 10: Manual smoke test**

Start the server: `cd visualizer && bun run dev`
Open http://localhost:3999, switch to Graph view. In HUD, switch Edge Color from "binary" to "gradient". Semantic edges should turn blue→red. Switch back: edges return to gray. Wiki edges (in wiki mode) should be unaffected.

- [ ] **Step 11: Commit**

```bash
git add visualizer/lib/useVisualizerState.ts visualizer/components/GraphCanvas.tsx visualizer/components/HUDPanel.tsx visualizer/app/page.tsx
git commit -m "feat(visualizer): add semantic gradient edge coloring"
```

---

## Task 3: Feature 5 — Edge Density Reduction

**Files:**
- Modify: `visualizer/lib/useVisualizerState.ts`
- Modify: `visualizer/components/GraphCanvas.tsx`
- Modify: `visualizer/components/HUDPanel.tsx`
- Modify: `visualizer/app/page.tsx`

### 3a: State

- [ ] **Step 1: Add state in useVisualizerState**

Add after `edgeColorMode`:

```ts
const [edgePruning, setEdgePruning] = useLocalStorage('vv:edgePruning', false)
const [edgePruningK, setEdgePruningK] = useLocalStorage('vv:edgePruningK', 8)
const toggleEdgePruning = useCallback(() => setEdgePruning(s => !s), [setEdgePruning])
```

Add to the return object:

```ts
edgePruning, toggleEdgePruning, edgePruningK, setEdgePruningK,
```

### 3b: GraphCanvas — pruneEdges helper + refs + usage

- [ ] **Step 2: Add pruneEdges as a module-level function**

First, update the existing `import type { GraphData, GraphSource } from '@/lib/graph'` at the top of `GraphCanvas.tsx` to:

```ts
import type { GraphData, GraphEdge, GraphSource } from '@/lib/graph'
```

Then add this function **before** the `GraphCanvas` component definition (after the `COOL_FACTOR` constant):

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

(`GraphEdge` is now covered by the import update above — do not add a second standalone import.)

- [ ] **Step 3: Add props, refs, and sync effects**

In the `Props` interface, add:

```ts
edgePruning: boolean
edgePruningK: number
```

In destructuring, add `edgePruning, edgePruningK`.

Add refs after `dataRef`:

```ts
const edgePruningRef = useRef(edgePruning)
const edgePruningKRef = useRef(edgePruningK)
```

Add sync effects (near other ref-sync effects):

```ts
useEffect(() => { edgePruningRef.current = edgePruning }, [edgePruning])
useEffect(() => { edgePruningKRef.current = edgePruningK }, [edgePruningK])
```

- [ ] **Step 4: Apply pruning in the init effect**

In the `init` async function, find `const edges = filterEdges(data.edges, graphSource, threshold)` and change to:

```ts
let edges = filterEdges(data.edges, graphSource, threshold)
if (edgePruningRef.current) edges = pruneEdges(edges, edgePruningKRef.current)
```

(The overlay block that follows is a separate `if (showOverlayEdgesRef.current)` — leave that unchanged; overlays are never pruned.)

- [ ] **Step 5: Apply pruning in the update effect and add deps**

In the threshold/source/data update effect (~line 750), find:

```ts
const edges = filterEdges(data.edges, graphSource, threshold)
```

Change to:

```ts
let edges = filterEdges(data.edges, graphSource, threshold)
if (edgePruningRef.current) edges = pruneEdges(edges, edgePruningKRef.current)
```

Find the effect's dependency array (the closing `}, [threshold, graphSource, data, reheat]`) and add the new deps:

```ts
// Note: edgePruning/edgePruningK are in the dep array intentionally — unlike edgeWeightInfluence
// (which updates weights on existing edges and therefore only needs a ref), pruning requires a
// full edge rebuild via graph.clearEdges(). The effect must re-run when pruning toggles or K
// changes, so these must be real deps rather than ref-only values.
}, [threshold, graphSource, data, reheat, edgePruning, edgePruningK])
```

### 3c: HUDPanel — Edge Density section

- [ ] **Step 6: Add props to HUDPanel**

Add to `Props` interface:

```ts
edgePruning: boolean
onToggleEdgePruning: () => void
edgePruningK: number
onEdgePruningKChange: (k: number) => void
totalEdgeCount: number
```

Add to destructuring.

- [ ] **Step 7: Add Edge Density section**

Add this block **before** the Force Layout section (before the `borderTop` divider div), but after the "Labels on Hover Only" label:

```tsx
{/* Edge Density — only shown for dense graphs */}
{totalEdgeCount > 2000 && (
  <div>
    <div style={{ display: 'flex', alignItems: 'center', marginBottom: 5 }}>
      <span style={{ color: '#6B7A99', textTransform: 'uppercase', letterSpacing: '0.06em', fontSize: 10 }}>Edge Density</span>
      <Tip text="Keeps the K strongest connections per node — reduces visual clutter on dense graphs." />
    </div>
    <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
      <input
        type="checkbox" checked={edgePruning} onChange={onToggleEdgePruning}
        style={{ accentColor: '#00FFC8', width: 14, height: 14 }}
      />
      <span style={{ color: '#6B7A99', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
        Reduce Edge Density
      </span>
    </label>
    {edgePruning && (
      <div style={{ marginTop: 6 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
          <span style={{ color: '#6B7A99', fontSize: 10 }}>Max edges/node</span>
          <span style={{ color: '#00FFC8', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>{edgePruningK}</span>
        </div>
        <input
          type="range" min={3} max={20} step={1} value={edgePruningK}
          onChange={e => onEdgePruningKChange(parseInt(e.target.value))}
          style={{ width: '100%', accentColor: '#00FFC8', cursor: 'pointer' }}
        />
      </div>
    )}
  </div>
)}
```

### 3d: Wire through page.tsx

- [ ] **Step 8: Pass new props**

In `<GraphCanvas ... />`, add:

```tsx
edgePruning={state.edgePruning}
edgePruningK={state.edgePruningK}
```

In `<HUDPanel ... />`, add:

```tsx
edgePruning={state.edgePruning}
onToggleEdgePruning={state.toggleEdgePruning}
edgePruningK={state.edgePruningK}
onEdgePruningKChange={state.setEdgePruningK}
totalEdgeCount={graphData.meta.edge_count}
```

- [ ] **Step 9: Verify and test**

```bash
cd visualizer && bun run lint
```

Manual test: Switch to Graph view, full-vault mode. If edge count > 2000, "Edge Density" section appears in HUD. Toggle "Reduce Edge Density" — edges thin out noticeably. Adjust slider — more/fewer edges. Physics settles to a less cluttered graph.

- [ ] **Step 10: Commit**

```bash
git add visualizer/lib/useVisualizerState.ts visualizer/components/GraphCanvas.tsx visualizer/components/HUDPanel.tsx visualizer/app/page.tsx
git commit -m "feat(visualizer): add edge density reduction (top-K per node pruning)"
```

---

## Task 4: Feature 2 — Node Sizing by Centrality

**Files:**
- Modify: `visualizer/lib/useVisualizerState.ts`
- Modify: `visualizer/components/GraphCanvas.tsx`
- Modify: `visualizer/components/HUDPanel.tsx`
- Modify: `visualizer/app/page.tsx`

### 4a: State + Brandes algorithm in useVisualizerState

- [ ] **Step 1: Add NodeSizeMode state**

`NodeSizeMode` is already exported from `sigma-colors.ts` (added in Task 1) and imported via the line from Task 2 Step 1:
```ts
import { TYPE_COLORS, EdgeColorMode, NodeSizeMode } from '@/lib/sigma-colors'
```

Add state after `edgeColorMode`:

```ts
const [nodeSizeMode, setNodeSizeMode] = useLocalStorage<NodeSizeMode>('vv:nodeSizeMode', 'incoming_links')
```

- [ ] **Step 2: Add betweenness computation as a standalone function**

Add this function **outside** the hook (at module level, below the `SIM_DEFAULTS` const):

```ts
function computeBetweenness(nodes: string[], wikiAdj: Map<string, string[]>): Map<string, number> {
  const bc = new Map<string, number>()
  for (const n of nodes) bc.set(n, 0)

  for (const s of nodes) {
    const stack: string[] = []
    const pred = new Map<string, string[]>()
    for (const n of nodes) pred.set(n, [])
    const sigma = new Map<string, number>()
    for (const n of nodes) sigma.set(n, 0)
    sigma.set(s, 1)
    const dist = new Map<string, number>()
    for (const n of nodes) dist.set(n, -1)
    dist.set(s, 0)
    const queue: string[] = [s]

    while (queue.length > 0) {
      const v = queue.shift()!
      stack.push(v)
      for (const w of (wikiAdj.get(v) ?? [])) {
        if (dist.get(w) === -1) {
          queue.push(w)
          dist.set(w, dist.get(v)! + 1)
        }
        if (dist.get(w) === dist.get(v)! + 1) {
          sigma.set(w, sigma.get(w)! + sigma.get(v)!)
          pred.get(w)!.push(v)
        }
      }
    }

    const delta = new Map<string, number>()
    for (const n of nodes) delta.set(n, 0)
    while (stack.length > 0) {
      const w = stack.pop()!
      for (const v of (pred.get(w) ?? [])) {
        const ratio = (sigma.get(v)! / sigma.get(w)!) * (1 + delta.get(w)!)
        delta.set(v, delta.get(v)! + ratio)
      }
      if (w !== s) bc.set(w, bc.get(w)! + delta.get(w)!)
    }
  }

  // Normalize to [2, 14]
  let maxVal = 0
  for (const v of bc.values()) if (v > maxVal) maxVal = v
  if (maxVal === 0) maxVal = 1
  const result = new Map<string, number>()
  for (const [id, val] of bc) result.set(id, 2 + (val / maxVal) * 12)
  return result
}
```

- [ ] **Step 3: Add nodeSizeMap state with async computation**

Add after the `nodeSizeMode` state line:

```ts
const [nodeSizeMap, setNodeSizeMap] = useState<Map<string, number> | null>(null)
const [nodeSizeComputing, setNodeSizeComputing] = useState(false)

useEffect(() => {
  if (nodeSizeMode !== 'betweenness' || !graphData) {
    setNodeSizeMap(null)
    setNodeSizeComputing(false)
    return
  }
  setNodeSizeComputing(true)
  setNodeSizeMap(null)
  // Defer to next tick so "Computing…" label renders before the blocking work
  const id = setTimeout(() => {
    const nodes = graphData.nodes.map(n => n.id)
    const adj = new Map<string, string[]>()
    for (const n of nodes) adj.set(n, [])
    for (const e of graphData.edges) {
      if (e.kind !== 'wiki') continue
      adj.get(e.s)?.push(e.t)
      adj.get(e.t)?.push(e.s)
    }
    const result = computeBetweenness(nodes, adj)
    setNodeSizeMap(result)
    setNodeSizeComputing(false)
  }, 0)
  return () => clearTimeout(id)
}, [nodeSizeMode, graphData])
```

- [ ] **Step 4: Add to return object**

```ts
nodeSizeMode, setNodeSizeMode,
nodeSizeMap,
nodeSizeComputing,
```

### 4b: GraphCanvas — apply sizing

- [ ] **Step 5: Add imports and props**

`NodeSizeMode` is canonical in `sigma-colors.ts` (added in Task 1). Update the sigma-colors import in `GraphCanvas.tsx` (already modified in Task 2 Step 2) to also include `NodeSizeMode`:

```ts
import { getNodeColor, getNodeSize, getSemanticEdgeColor } from '@/lib/sigma-colors'
import type { EdgeColorMode, NodeSizeMode } from '@/lib/sigma-colors'
```

In the `Props` interface, add:

```ts
nodeSizeMode: NodeSizeMode
nodeSizeMap: Map<string, number> | null
```

- [ ] **Step 6: Add refs**

```ts
const nodeSizeModeRef = useRef(nodeSizeMode)
const nodeSizeMapRef = useRef(nodeSizeMap)
```

Add sync effects:

```ts
useEffect(() => { nodeSizeModeRef.current = nodeSizeMode }, [nodeSizeMode])
useEffect(() => { nodeSizeMapRef.current = nodeSizeMap }, [nodeSizeMap])
```

- [ ] **Step 7: Apply correct sizing during init**

In the `init` async function, find:

```ts
graph.addNode(node.id, {
  label: node.title,
  color: getNodeColor(node.type),
  size: getNodeSize(node.incoming_links),
```

Replace with:

```ts
const nsMode = nodeSizeModeRef.current
const nsMap = nodeSizeMapRef.current
let nodeSize: number
if (nsMode === 'uniform') {
  nodeSize = 4
} else if (nsMode === 'betweenness' && nsMap) {
  nodeSize = nsMap.get(node.id) ?? getNodeSize(node.incoming_links)
} else if (nsMode === 'recency') {
  const ageDays = (Date.now() / 1000 - node.mtime) / 86400
  nodeSize = Math.max(2, 10 - Math.log(ageDays + 1) * 1.5)
} else {
  nodeSize = getNodeSize(node.incoming_links)
}
graph.addNode(node.id, {
  label: node.title,
  color: getNodeColor(node.type),
  size: nodeSize,
```

- [ ] **Step 8: Add effect to live-update node sizes**

Add after the `nodeSizeMap` sync effect:

```ts
useEffect(() => {
  const graph = graphRef.current
  const sigma = sigmaRef.current
  const d = dataRef.current
  if (!graph || !sigma || !d) return
  // Skip while betweenness is still computing — the computation effect will re-trigger this
  if (nodeSizeMode === 'betweenness' && nodeSizeMap === null) return
  const nodeDataMap = new Map(d.nodes.map(n => [n.id, n]))
  ;(graph.nodes() as string[]).forEach((nodeId: string) => {
    const nd = nodeDataMap.get(nodeId)
    if (!nd) return
    let size: number
    if (nodeSizeMode === 'uniform') {
      size = 4
    } else if (nodeSizeMode === 'betweenness') {
      size = nodeSizeMap?.get(nodeId) ?? getNodeSize(nd.incoming_links)
    } else if (nodeSizeMode === 'recency') {
      const ageDays = (Date.now() / 1000 - nd.mtime) / 86400
      size = Math.max(2, 10 - Math.log(ageDays + 1) * 1.5)
    } else {
      size = getNodeSize(nd.incoming_links)
    }
    graph.setNodeAttribute(nodeId, 'size', size)
  })
  sigma.refresh()
}, [nodeSizeMode, nodeSizeMap])
```

### 4c: HUDPanel — Node Size section

- [ ] **Step 9: Add props**

Add `NodeSizeMode` to the sigma-colors import in `HUDPanel.tsx` (already has `EdgeColorMode` from Task 2 Step 6):

```ts
import type { EdgeColorMode, NodeSizeMode } from '@/lib/sigma-colors'
```

Add to `Props`:

```ts
nodeSizeMode: NodeSizeMode
onNodeSizeModeChange: (mode: NodeSizeMode) => void
nodeSizeComputing: boolean
```

Add to destructuring.

- [ ] **Step 10: Add Node Size section**

Add after the Edge Color section:

```tsx
{/* Node Size */}
<div>
  <div style={{ display: 'flex', alignItems: 'center', marginBottom: 5 }}>
    <span style={{ color: '#6B7A99', textTransform: 'uppercase', letterSpacing: '0.06em', fontSize: 10 }}>Node Size</span>
    <Tip text="Uniform: equal size. Links: by incoming wikilinks. Betweenness: by graph centrality. Recency: newer notes larger." />
    {nodeSizeComputing && (
      <span style={{ marginLeft: 6, color: '#f59e0b', fontSize: 9, fontFamily: 'JetBrains Mono, monospace' }}>Computing…</span>
    )}
  </div>
  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
    {([
      ['uniform',       'Uniform'],
      ['incoming_links','Links'],
      ['betweenness',   'Centrality'],
      ['recency',       'Recency'],
    ] as const).map(([mode, label]) => (
      <button
        key={mode}
        onClick={() => onNodeSizeModeChange(mode)}
        style={{
          flex: '1 0 auto', padding: '4px 0', borderRadius: 4, border: '1px solid',
          borderColor: nodeSizeMode === mode ? '#00FFC8' : 'rgba(255,255,255,0.08)',
          background: nodeSizeMode === mode ? 'rgba(0,255,200,0.12)' : 'transparent',
          color: nodeSizeMode === mode ? '#00FFC8' : '#6B7A99',
          cursor: 'pointer', fontSize: 10, fontFamily: 'Oxanium, sans-serif',
          transition: 'all 0.15s',
        }}
      >
        {label}
      </button>
    ))}
  </div>
</div>
```

### 4d: Wire through page.tsx

- [ ] **Step 11: Pass new props**

In `<GraphCanvas ... />`, add:

```tsx
nodeSizeMode={state.nodeSizeMode}
nodeSizeMap={state.nodeSizeMap}
```

In `<HUDPanel ... />`, add:

```tsx
nodeSizeMode={state.nodeSizeMode}
onNodeSizeModeChange={state.setNodeSizeMode}
nodeSizeComputing={state.nodeSizeComputing}
```

- [ ] **Step 12: Verify and test**

```bash
cd visualizer && bun run lint
```

Manual test: Switch Node Size to "Uniform" — all nodes same size. "Recency" — recent notes are larger. "Centrality" — "Computing…" briefly appears, then hub notes grow noticeably larger. "Links" — returns to default.

- [ ] **Step 13: Commit**

```bash
git add visualizer/lib/useVisualizerState.ts visualizer/components/GraphCanvas.tsx visualizer/components/HUDPanel.tsx visualizer/app/page.tsx
git commit -m "feat(visualizer): add node sizing by centrality (uniform/links/betweenness/recency)"
```

---

## Task 5: Feature 3 — Graph Statistics Panel

**Files:**
- Modify: `visualizer/lib/useVisualizerState.ts`
- Modify: `visualizer/components/HUDPanel.tsx`
- Modify: `visualizer/app/page.tsx`

### 5a: graphStats in useVisualizerState

- [ ] **Step 1: Add GraphStats interface and computation**

Add the interface near the top of `useVisualizerState.ts` (after the imports):

```ts
export interface GraphStats {
  avgDegree: number
  maxDegree: number
  topHubs: Array<{ id: string; title: string; degree: number }>
  density: number
  componentCount: number
}
```

Add this `useMemo` after the existing `stats` memo (reuse the same `visibleNodes` computation pattern):

```ts
const graphStats = useMemo<GraphStats | null>(() => {
  if (!graphData) return null

  // Same visibility logic as stats — scoped to the same visible node set
  const qualifying = (filterNodesBySimilarity && graphSource === 'wiki')
    ? new Set(graphData.edges.filter(e => e.kind === 'semantic' && e.w >= threshold).flatMap(e => [e.s, e.t]))
    : null
  const visibleNodes = new Set(
    graphData.nodes
      .filter(n => (showDaily || n.folder !== 'Daily') && activeTypes.has(n.type) && (!qualifying || qualifying.has(n.id)))
      .map(n => n.id)
  )

  // Degree from wiki edges (undirected)
  const degree = new Map<string, number>()
  for (const n of visibleNodes) degree.set(n, 0)
  let wikiEdgeCount = 0
  for (const e of graphData.edges) {
    if (e.kind !== 'wiki') continue
    if (!visibleNodes.has(e.s) || !visibleNodes.has(e.t)) continue
    degree.set(e.s, (degree.get(e.s) ?? 0) + 1)
    degree.set(e.t, (degree.get(e.t) ?? 0) + 1)
    wikiEdgeCount++
  }

  const n = visibleNodes.size
  const degrees = [...degree.values()]
  const total = degrees.reduce((s, d) => s + d, 0)
  const avgDegree = n > 0 ? total / n : 0
  const maxDegree = n > 0 ? Math.max(...degrees) : 0
  const density = n > 1 ? wikiEdgeCount / (n * (n - 1) / 2) : 0

  // Top 5 hubs
  const nodeIdToTitle = new Map(graphData.nodes.map(nd => [nd.id, nd.title]))
  const topHubs = [...degree.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5)
    .map(([id, deg]) => ({ id, title: nodeIdToTitle.get(id) ?? id, degree: deg }))

  // Connected components via BFS
  const wikiAdj = new Map<string, string[]>()
  for (const nd of visibleNodes) wikiAdj.set(nd, [])
  for (const e of graphData.edges) {
    if (e.kind !== 'wiki') continue
    if (!visibleNodes.has(e.s) || !visibleNodes.has(e.t)) continue
    wikiAdj.get(e.s)!.push(e.t)
    wikiAdj.get(e.t)!.push(e.s)
  }
  const visited = new Set<string>()
  let componentCount = 0
  for (const start of visibleNodes) {
    if (visited.has(start)) continue
    componentCount++
    const queue = [start]
    while (queue.length > 0) {
      const curr = queue.shift()!
      if (visited.has(curr)) continue
      visited.add(curr)
      for (const nb of (wikiAdj.get(curr) ?? [])) {
        if (!visited.has(nb)) queue.push(nb)
      }
    }
  }

  return { avgDegree, maxDegree, topHubs, density, componentCount }
}, [graphData, threshold, graphSource, activeTypes, showDaily, filterNodesBySimilarity])
```

Add to return object:

```ts
graphStats,
```

### 5b: HUDPanel — Graph Analysis section

- [ ] **Step 2: Add props**

Add to `Props`:

```ts
graphStats: import('@/lib/useVisualizerState').GraphStats | null
```

Or, since `GraphStats` is exported, add the import to `HUDPanel.tsx`:

```ts
import type { GraphStats } from '@/lib/useVisualizerState'
```

And add to `Props`:

```ts
graphStats: GraphStats | null
```

Add to destructuring.

- [ ] **Step 3: Add Graph Analysis collapsible section**

Add a new local state for the section collapse at the top of the `HUDPanel` component (after `const [collapsed, setCollapsed] = useState(false)`):

```ts
const [statsExpanded, setStatsExpanded] = useState(false)
```

Replace the existing stats block:

```tsx
{/* Stats */}
<div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
  {[
    { label: 'nodes', value: nodeCount },
    { label: 'edges', value: edgeCount },
    { label: 'avg sim', value: avgScore.toFixed(2) },
  ].map(s => (
    ...
  ))}
</div>
```

With:

```tsx
{/* Stats */}
<div>
  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
    {[
      { label: 'nodes', value: nodeCount },
      { label: 'edges', value: edgeCount },
      { label: 'avg sim', value: avgScore.toFixed(2) },
    ].map(s => (
      <div key={s.label} style={{ background: 'rgba(0,255,200,0.05)', border: '1px solid rgba(0,255,200,0.1)', borderRadius: 4, padding: '3px 7px', flex: '1 0 auto', textAlign: 'center' }}>
        <div style={{ color: '#00FFC8', fontWeight: 600, fontSize: 13 }}>{s.value}</div>
        <div style={{ color: '#6B7A99', fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{s.label}</div>
      </div>
    ))}
  </div>

  {/* Graph Analysis collapsible */}
  {graphStats && (
    <div style={{ marginTop: 6 }}>
      <button
        onClick={() => setStatsExpanded(s => !s)}
        style={{
          width: '100%', background: 'none', border: 'none', cursor: 'pointer',
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '2px 0', color: '#6B7A99', fontSize: 9,
          textTransform: 'uppercase', letterSpacing: '0.06em', fontFamily: 'Oxanium, sans-serif',
        }}
      >
        <span>Graph Analysis</span>
        <span>{statsExpanded ? '▲' : '▼'}</span>
      </button>
      {statsExpanded && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginTop: 5 }}>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {[
              { label: 'avg deg', value: graphStats.avgDegree.toFixed(1) },
              { label: 'max deg', value: graphStats.maxDegree },
              { label: 'density', value: (graphStats.density * 100).toFixed(2) + '%' },
              { label: 'components', value: graphStats.componentCount },
            ].map(s => (
              <div key={s.label} style={{ background: 'rgba(123,97,255,0.06)', border: '1px solid rgba(123,97,255,0.15)', borderRadius: 4, padding: '3px 7px', flex: '1 0 auto', textAlign: 'center' }}>
                <div style={{ color: '#7B61FF', fontWeight: 600, fontSize: 12 }}>{s.value}</div>
                <div style={{ color: '#6B7A99', fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{s.label}</div>
              </div>
            ))}
          </div>
          <div>
            <div style={{ color: '#6B7A99', fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Top Hubs</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              {graphStats.topHubs.map(hub => (
                <button
                  key={hub.id}
                  onClick={() => canvasRef.current?.flyToNode(hub.id)}
                  style={{
                    background: 'rgba(0,255,200,0.04)', border: '1px solid rgba(0,255,200,0.1)',
                    borderRadius: 3, padding: '3px 7px', cursor: 'pointer',
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    fontFamily: 'Oxanium, sans-serif',
                  }}
                >
                  <span style={{ color: '#A0B8C8', fontSize: 10, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 160 }}>
                    {hub.title}
                  </span>
                  <span style={{ color: '#00FFC8', fontSize: 10, marginLeft: 6, flexShrink: 0 }}>{hub.degree}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )}
</div>
```

### 5c: Wire through page.tsx

- [ ] **Step 4: Pass graphStats prop**

In `<HUDPanel ... />`, add:

```tsx
graphStats={state.graphStats}
```

- [ ] **Step 5: Verify and test**

```bash
cd visualizer && bun run lint
```

Manual test: In Graph view HUD, click "Graph Analysis ▼". Stats appear: avg degree, max degree, density %, component count. Top hubs listed — clicking a hub chip flies the camera to that node.

- [ ] **Step 6: Commit**

```bash
git add visualizer/lib/useVisualizerState.ts visualizer/components/HUDPanel.tsx visualizer/app/page.tsx
git commit -m "feat(visualizer): add graph statistics panel (degree, density, components, top hubs)"
```

---

## Task 6: Feature 4 — Shortest Path Finder

**Files:**
- Modify: `visualizer/components/GraphCanvas.tsx`

This feature is entirely self-contained in GraphCanvas (path state, BFS, context menu, toast).

### 6a: Path refs and toast state

- [ ] **Step 1: Add path refs and toast state**

Add after `const [nodeContextMenu, setNodeContextMenu] = useState<...>` (line ~94):

```ts
const pathSourceRef = useRef<string | null>(null)
const pathNodesRef = useRef<Set<string>>(new Set())
const pathEdgesRef = useRef<Set<string>>(new Set())
const [toastMsg, setToastMsg] = useState<string | null>(null)
const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
```

Add `showToast` helper after the refs:

```ts
const showToast = useCallback((msg: string) => {
  if (toastTimerRef.current) clearTimeout(toastTimerRef.current)
  setToastMsg(msg)
  toastTimerRef.current = setTimeout(() => setToastMsg(null), 4000)
}, [])
```

Add cleanup effect:

```ts
useEffect(() => {
  return () => { if (toastTimerRef.current) clearTimeout(toastTimerRef.current) }
}, [])
```

### 6b: BFS path finder function

- [ ] **Step 2: Add findPath as a module-level function**

Add before the `GraphCanvas` component (after `pruneEdges`):

```ts
function findWikiPath(
  from: string,
  to: string,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  graph: any
): { path: string[]; edgeIds: string[] } | null {
  // Build undirected wiki adjacency (from current graphology graph, excluding overlays)
  const adj = new Map<string, Array<{ neighbor: string; edgeId: string }>>()
  ;(graph.nodes() as string[]).forEach((n: string) => adj.set(n, []))
  ;(graph.edges() as string[]).forEach((e: string) => {
    if (graph.getEdgeAttribute(e, 'kind') !== 'wiki') return
    if (graph.getEdgeAttribute(e, 'overlay')) return
    const src = graph.source(e) as string
    const tgt = graph.target(e) as string
    adj.get(src)?.push({ neighbor: tgt, edgeId: e })
    adj.get(tgt)?.push({ neighbor: src, edgeId: e })
  })

  // BFS
  const parent = new Map<string, { from: string; edgeId: string }>()
  const visited = new Set<string>([from])
  const queue = [from]
  let found = false

  while (queue.length > 0 && !found) {
    const curr = queue.shift()!
    for (const { neighbor, edgeId } of (adj.get(curr) ?? [])) {
      if (!visited.has(neighbor)) {
        visited.add(neighbor)
        parent.set(neighbor, { from: curr, edgeId })
        if (neighbor === to) { found = true; break }
        queue.push(neighbor)
      }
    }
  }

  if (!found) return null

  // Reconstruct path
  const path: string[] = []
  const edgeIds: string[] = []
  let curr = to
  while (curr !== from) {
    path.unshift(curr)
    const p = parent.get(curr)!
    edgeIds.unshift(p.edgeId)
    curr = p.from
  }
  path.unshift(from)
  return { path, edgeIds }
}
```

### 6c: Path highlighting in nodeReducer/edgeReducer

- [ ] **Step 3: Update nodeReducer to highlight path nodes**

In the `nodeReducer` function (inside the `init` async function), add at the **top** of the reducer (before the neighborhood check), so path highlight takes precedence:

```ts
const nodeReducer = (node: string, data: any) => {
  const pn = pathNodesRef.current
  if (pn.size > 0 && pn.has(node)) {
    const showLabel = labelsOnHoverOnlyRef.current ? node === hoveredNodeRef.current : true
    return { ...data, color: '#FFD700', zIndex: 10, label: showLabel ? data.label : '' }
  }
  if (pathSourceRef.current === node) {
    return { ...data, color: '#FFD700', zIndex: 5 }
  }
  // ... existing nh check continues ...
```

- [ ] **Step 4: Update edgeReducer to highlight path edges**

In the `edgeReducer` function, add at the **top**:

```ts
const edgeReducer = (edge: string, data: any) => {
  const pe = pathEdgesRef.current
  if (pe.size > 0 && pe.has(edge)) {
    return { ...data, color: '#FFD700', size: 3, hidden: false }
  }
  // ... existing nh check continues ...
```

### 6d: Update clickStage to clear path

- [ ] **Step 5: Extend clickStage handler**

Find the existing `sigma.on('clickStage', () => {` handler and add path clearing before the existing `sigma.refresh()` call:

```ts
sigma.on('clickStage', () => {
  onBackgroundClick()
  setNodeContextMenu(null)
  pathSourceRef.current = null
  pathNodesRef.current = new Set()
  pathEdgesRef.current = new Set()
  highlightedNodesRef.current = new Set()
  highlightedEdgesRef.current = new Set()
  sigma.refresh()
})
```

Also clear path refs in the init cleanup:

```ts
return () => {
  cancelled = true
  // ... existing cleanup ...
  pathSourceRef.current = null
  pathNodesRef.current = new Set()
  pathEdgesRef.current = new Set()
}
```

### 6e: Context menu path items

- [ ] **Step 6: Add path finder items to context menu**

In the return JSX, find the context menu `<div>` and add after the "View History" item.

**Important:** Capture `pathSourceRef.current` to a `const` at the top of the context menu render block (before any conditional JSX) so all conditions in the same render cycle see the same snapshot:

```tsx
{nodeContextMenu && (() => {
  // Capture ref value once per render — prevents stale comparisons in JSX conditionals
  const pathSource = pathSourceRef.current
  return (
    <div ...> {/* the existing context menu outer div */}
      {/* ... existing items ... */}

      {/* Path finder */}
      <div style={{ borderTop: '1px solid rgba(255,255,255,0.08)', margin: '2px 0' }} />
      {pathSource && pathSource !== nodeContextMenu.stem && (
        <div
          style={{ padding: '6px 12px', cursor: 'pointer', color: '#FFD700' }}
          onMouseEnter={e => (e.currentTarget.style.background = '#1a2040')}
          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
          onClick={() => {
            const result = findWikiPath(pathSourceRef.current!, nodeContextMenu.stem, graphRef.current)
            setNodeContextMenu(null)
            if (result) {
              pathNodesRef.current = new Set(result.path)
              pathEdgesRef.current = new Set(result.edgeIds)
              const d = dataRef.current
              const titleMap = new Map(d?.nodes.map(n => [n.id, n.title]) ?? [])
              const breadcrumb = result.path.map(id => titleMap.get(id) ?? id).join(' → ')
              showToast(breadcrumb)
            } else {
              pathNodesRef.current = new Set()
              pathEdgesRef.current = new Set()
              showToast('No wiki-link path found')
            }
            pathSourceRef.current = null
            sigmaRef.current?.refresh()
          }}
        >
          ⚡ Find Path Here
        </div>
      )}
      {pathSource === nodeContextMenu.stem ? (
        <div
          style={{ padding: '6px 12px', cursor: 'pointer', color: '#6B7A99' }}
          onMouseEnter={e => (e.currentTarget.style.background = '#1a2040')}
          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
          onClick={() => {
            pathSourceRef.current = null
            pathNodesRef.current = new Set()
            pathEdgesRef.current = new Set()
            setNodeContextMenu(null)
            sigmaRef.current?.refresh()
          }}
        >
          ✕ Clear Path Origin
        </div>
      ) : (
        <div
          style={{ padding: '6px 12px', cursor: 'pointer', color: pathSource ? '#f59e0b' : '#6B7A99' }}
          onMouseEnter={e => (e.currentTarget.style.background = '#1a2040')}
          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
          onClick={() => {
            pathSourceRef.current = nodeContextMenu.stem
            pathNodesRef.current = new Set()
            pathEdgesRef.current = new Set()
            setNodeContextMenu(null)
            sigmaRef.current?.refresh()
          }}
        >
          {pathSource
            ? `Origin: ${pathSource.slice(0, 18)}…`
            : '◎ Set Path Origin'}
        </div>
      )}
    </div>
  )
})()}
```

Note: `pathSourceRef.current` is still used directly inside `onClick` handlers (where it's read at call time, not render time). Only the JSX conditions use the captured `pathSource` snapshot.

### 6f: Toast rendering

- [ ] **Step 7: Add toast to the return JSX**

In the return JSX (after the context menu `{nodeContextMenu && (...)}` block), add:

```tsx
{toastMsg && (
  <div style={{
    position: 'absolute', bottom: 24, left: '50%', transform: 'translateX(-50%)',
    background: 'rgba(6, 8, 18, 0.95)',
    border: '1px solid rgba(255, 215, 0, 0.4)',
    borderRadius: 6, padding: '8px 16px',
    color: '#FFD700', fontSize: 11,
    fontFamily: "'JetBrains Mono', monospace",
    maxWidth: '80%', textAlign: 'center',
    boxShadow: '0 4px 20px rgba(0,0,0,0.7)',
    zIndex: 500, pointerEvents: 'none',
    animation: 'fadeSlideIn 0.3s ease-out both',
  }}>
    {toastMsg}
  </div>
)}
```

- [ ] **Step 8: Verify and test**

```bash
cd visualizer && bun run lint
```

Manual test:
1. Switch to full-vault graph view
2. Right-click any node → click "Set Path Origin" — node turns yellow
3. Right-click a different node → click "Find Path Here" — path lights up in yellow with edge trail; toast shows breadcrumb chain
4. Click background — path and origin cleared
5. Try two unconnected nodes (e.g., in different isolated components) — toast shows "No wiki-link path found"

- [ ] **Step 9: Commit**

```bash
git add visualizer/components/GraphCanvas.tsx
git commit -m "feat(visualizer): add shortest path finder with yellow highlight and toast"
```

---

## Task 7: Final integration check

- [ ] **Step 1: Full lint pass**

```bash
cd visualizer && bun run lint
```

Expected: zero errors

- [ ] **Step 2: Build check**

```bash
cd visualizer && bun run build
```

Expected: build succeeds with no TypeScript errors

- [ ] **Step 3: End-to-end smoke test**

Start server: `cd visualizer && bun run dev`

Verify all 5 features work together:
1. Switch Edge Color → Gradient: semantic edges turn blue-to-red
2. Switch Node Size → Centrality: brief "Computing…" then hub nodes enlarge
3. Open Graph Analysis in HUD: stats appear, clicking a hub chip flies to it
4. Toggle Edge Density (in full-vault, semantic mode): edges thin out
5. Shortest path: set origin, find path, see yellow trail and breadcrumb toast

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat(visualizer): implement 5 graph features (gradient coloring, centrality sizing, stats panel, path finder, edge density)"
```
