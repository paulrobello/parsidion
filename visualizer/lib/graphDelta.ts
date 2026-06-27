// ---------------------------------------------------------------------------
// Pure helpers for incremental graph updates (no React, no sigma/graphology).
//
// When `graph.json` is rebuilt, GraphCanvas diffs the new GraphData against the
// live graphology instance instead of tearing it down. This module owns the
// deterministic pieces of that diff:
//   - which nodes are added / removed / kept
//   - when the delta is too large to diff (vault switch) → full rebuild
//   - where to place a newly-added node (near existing neighbors, else perimeter)
//
// Positioning mirrors the first-load placement in GraphCanvas.tsx (centroid of
// placed neighbors + uniform-circle jitter), but seeds the `placed` map with the
// live graph's positions so new nodes land next to existing neighbors.
// ---------------------------------------------------------------------------
import type { NoteNode, GraphEdge } from '@/lib/graph'

/** Fraction of nodes changed above which we give up diffing and rebuild fully. */
export const DELTA_REBUILD_THRESHOLD = 0.4

/** Placement jitter — mirrors GraphCanvas.tsx first-load constant. */
export const JITTER = 1.8

export interface Point {
  x: number
  y: number
}

export interface NodeDelta {
  removed: string[]
  added: NoteNode[]
  kept: NoteNode[]
}

export interface Bounds {
  cx: number
  cy: number
  maxR: number
}

/**
 * Apply the same visibility filter as first-load construction (GraphCanvas.tsx
 * ~:449-454). Extracted so the bootstrap and the delta path can't drift apart.
 */
export function computeVisibleNodes(
  nodes: NoteNode[],
  activeTypes: Set<string>,
  showDaily: boolean
): NoteNode[] {
  return nodes.filter(n => {
    if (!showDaily && n.folder === 'Daily') return false
    if (!activeTypes.has(n.type)) return false
    return true
  })
}

/** Partition new-visible nodes against the current graph's node ids. */
export function computeNodeDelta(
  currentIds: Set<string>,
  newVisible: NoteNode[]
): NodeDelta {
  const removed: string[] = []
  for (const id of currentIds) {
    if (!newVisible.some(n => n.id === id)) removed.push(id)
  }
  const added: NoteNode[] = []
  const kept: NoteNode[] = []
  for (const n of newVisible) {
    if (currentIds.has(n.id)) kept.push(n)
    else added.push(n)
  }
  return { removed, added, kept }
}

/**
 * Decide whether the delta is small enough to apply incrementally. A vault
 * switch produces near-100% turnover → full rebuild (positions are meaningless
 * across vaults). With no current nodes we can't diff → rebuild.
 */
export function shouldFullRebuild(delta: NodeDelta, currentTotal: number): boolean {
  if (currentTotal === 0) return true
  const total = currentTotal + delta.added.length
  const changed = delta.added.length + delta.removed.length
  return changed / total > DELTA_REBUILD_THRESHOLD
}

/**
 * Symmetric adjacency among visible nodes (mirrors GraphCanvas.tsx ~:456-463).
 * Edges touching a node not in `visibleIds` are skipped.
 */
export function buildAdjacency(
  edges: GraphEdge[],
  visibleIds: Set<string>
): Map<string, Set<string>> {
  const adj = new Map<string, Set<string>>()
  for (const e of edges) {
    if (!visibleIds.has(e.s) || !visibleIds.has(e.t)) continue
    if (!adj.has(e.s)) adj.set(e.s, new Set())
    if (!adj.has(e.t)) adj.set(e.t, new Set())
    adj.get(e.s)!.add(e.t)
    adj.get(e.t)!.add(e.s)
  }
  return adj
}

/** Seed a placement map from a position reader (e.g. graph.getNodeAttributes). */
export function seedPlacementFrom(
  readPos: (id: string) => Point,
  ids: Iterable<string>
): Map<string, Point> {
  const placed = new Map<string, Point>()
  for (const id of ids) placed.set(id, readPos(id))
  return placed
}

/** Centroid of placed positions + max distance from that centroid (the perimeter). */
export function graphBounds(placed: Map<string, Point>): Bounds {
  if (placed.size === 0) return { cx: 0, cy: 0, maxR: 0 }
  let sx = 0
  let sy = 0
  for (const p of placed.values()) {
    sx += p.x
    sy += p.y
  }
  const cx = sx / placed.size
  const cy = sy / placed.size
  let maxR = 0
  for (const p of placed.values()) {
    const d = Math.hypot(p.x - cx, p.y - cy)
    if (d > maxR) maxR = d
  }
  return { cx, cy, maxR }
}

/**
 * Position a newly-added node.
 *
 * - If it has any ALREADY-PLACED neighbors, place at their centroid plus
 *   uniform-circle jitter (mirrors first-load placement). This drops the node
 *   next to where it will be pulled.
 * - Otherwise place on the perimeter: a random angle at radius maxR + margin,
 *   where margin scales with the graph so isolated new nodes stay on the
 *   outside rather than landing in the middle of the mass.
 *
 * `placed` is mutated by the caller (after positioning) so that earlier-placed
 * new nodes become neighbors for later ones in the same delta — keeping new
 * connected clusters together.
 */
export function positionNewNode(
  nodeId: string,
  placed: Map<string, Point>,
  adjacency: Map<string, Set<string>>,
  bounds: Bounds,
  jitter: number = JITTER
): Point {
  const neighbors = adjacency.get(nodeId)
  const placedNeighbors: Point[] = []
  if (neighbors) {
    for (const n of neighbors) {
      const p = placed.get(n)
      if (p) placedNeighbors.push(p)
    }
  }

  const angle = Math.random() * Math.PI * 2

  if (placedNeighbors.length > 0) {
    let sx = 0
    let sy = 0
    for (const p of placedNeighbors) {
      sx += p.x
      sy += p.y
    }
    const cx = sx / placedNeighbors.length
    const cy = sy / placedNeighbors.length
    const radius = Math.sqrt(Math.random()) * jitter // uniform within circle
    return { x: cx + Math.cos(angle) * radius, y: cy + Math.sin(angle) * radius }
  }

  // Perimeter: fixed radius, random angle.
  const margin = Math.max(jitter * 3, bounds.maxR * 0.15)
  const r = bounds.maxR + margin
  return { x: bounds.cx + Math.cos(angle) * r, y: bounds.cy + Math.sin(angle) * r }
}
