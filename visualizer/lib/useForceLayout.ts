// ---------------------------------------------------------------------------
// Custom force-directed physics loop extracted from GraphCanvas.tsx (QA-004)
//
// This is a simple Newtonian physics simulation (gravity + Coulomb repulsion
// + Hooke attraction on edges + velocity damping). It does NOT use FA2.
// Temperature drives both the energy bar and the auto-stop threshold.
// ---------------------------------------------------------------------------
import { useRef, useCallback, useEffect } from 'react'
import type { AbstractGraph } from 'graphology-types'
import { isEffectivelyIsolated } from '@/lib/useGraphReducers'
import type { NeighborhoodInfo } from '@/lib/useGraphReducers'
import {
  PHYSICS_DAMPING,
  PHYSICS_DT,
  PHYSICS_MIN_DIST,
  recencyHeatColor,
} from '@/lib/sigma-colors'

// ---------------------------------------------------------------------------
// Helpers (pure, exported for potential testing)
// ---------------------------------------------------------------------------

export const RECENCY_SIZE_MIN = 2
export const RECENCY_SIZE_MAX = 12

/** Normalize node sizes by recency across a set of mtimes so the full range is always used. */
export function buildRecencySizeMap(mtimes: { id: string; mtime: number }[]): Map<string, number> {
  if (mtimes.length === 0) return new Map()
  const now = Date.now() / 1000
  const ages = mtimes.map(n => now - n.mtime)
  const minAge = Math.min(...ages)
  const maxAge = Math.max(...ages)
  const range = Math.max(0.001, maxAge - minAge)
  return new Map(mtimes.map((n, i) => {
    const t = (ages[i] - minAge) / range  // 0 = newest, 1 = oldest
    return [n.id, RECENCY_SIZE_MIN + (1 - t) * (RECENCY_SIZE_MAX - RECENCY_SIZE_MIN)]
  }))
}

/**
 * Map each node id to a recency heatmap hex color. Mirrors buildRecencySizeMap's
 * normalization (0 = newest, 1 = oldest) so the size and color ramps stay aligned.
 */
export function buildRecencyColorMap(mtimes: { id: string; mtime: number }[]): Map<string, string> {
  if (mtimes.length === 0) return new Map()
  const now = Date.now() / 1000
  const ages = mtimes.map(n => now - n.mtime)
  const minAge = Math.min(...ages)
  const maxAge = Math.max(...ages)
  const range = Math.max(0.001, maxAge - minAge)
  return new Map(mtimes.map((n, i) => {
    const t = (ages[i] - minAge) / range  // 0 = newest, 1 = oldest
    return [n.id, recencyHeatColor(t)]
  }))
}

import type { GraphEdge } from '@/lib/graph'

export function pruneEdges(edges: GraphEdge[], k: number): GraphEdge[] {
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

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export interface ForceLayoutRefs {
  graphRef: React.RefObject<AbstractGraph | null>
  simVelocitiesRef: React.RefObject<Map<string, { vx: number; vy: number }>>
  layoutLoopRef: React.RefObject<(() => void) | null>
  rafRef: React.RefObject<number | null>
  temperatureRef: React.RefObject<number>
  isRunningRef: React.RefObject<boolean>
  // Called when layout auto-stops (temperature drops below threshold)
  onLayoutStopRef: React.RefObject<(() => void) | undefined>
  onLayoutRestartRef: React.RefObject<(() => void) | undefined>
  sigmaRefreshRef: React.RefObject<(() => void) | null>
  // Physics params (updated by parent via separate effects)
  layoutParamsRef: React.RefObject<{ scalingRatio: number; gravity: number }>
  coolingRateRef: React.RefObject<number>
  startTemperatureRef: React.RefObject<number>
  stopThresholdRef: React.RefObject<number>
  // Visibility filter refs (read by layout loop to skip hidden nodes)
  filteredNodesRef: React.RefObject<Set<string>>
  neighborhoodRef: React.RefObject<NeighborhoodInfo | null>
  hideIsolatedRef: React.RefObject<boolean>
  // Drag state
  isDraggingRef: React.RefObject<boolean>
  draggedNodeRef: React.RefObject<string | null>
  dragPositionRef: React.RefObject<{ x: number; y: number } | null>
}

interface UseForceLayoutOptions {
  isLayoutRunning: boolean
  startTemperature: number
  slowDown: number
  stopThreshold: number
  scalingRatio: number
  gravity: number
  onLayoutStop?: () => void
  onLayoutRestart?: () => void
}

// Per-frame temperature decay multiplier.
// At slowDown=1 → ~29 s to reach 0.005 threshold at 60 fps.
// At slowDown=5 → ~6 s.
export const COOL_FACTOR = 0.002

/**
 * Manages the force-layout animation loop and exposes refs the parent
 * component wires into graph construction and event handlers.
 *
 * The hook owns:
 * - The rAF loop lifecycle (start / stop / reheat)
 * - Temperature tracking
 * - Velocity state
 *
 * The parent must:
 * - Populate `graphRef` and `sigmaRefreshRef` after graph construction
 * - Set `simVelocitiesRef` entries when adding nodes
 * - Wire drag refs from sigma event handlers
 */
export function useForceLayout(opts: UseForceLayoutOptions): ForceLayoutRefs & {
  reheat: () => void
} {
  const graphRef = useRef<AbstractGraph | null>(null)
  const simVelocitiesRef = useRef<Map<string, { vx: number; vy: number }>>(new Map())
  const layoutLoopRef = useRef<(() => void) | null>(null)
  const rafRef = useRef<number | null>(null)
  const temperatureRef = useRef(1.0)
  const isRunningRef = useRef(opts.isLayoutRunning)
  const sigmaRefreshRef = useRef<(() => void) | null>(null)

  const onLayoutStopRef = useRef(opts.onLayoutStop)
  const onLayoutRestartRef = useRef(opts.onLayoutRestart)
  const layoutParamsRef = useRef({ scalingRatio: opts.scalingRatio, gravity: opts.gravity })
  const coolingRateRef = useRef(opts.slowDown * COOL_FACTOR)
  const startTemperatureRef = useRef(opts.startTemperature)
  const stopThresholdRef = useRef(opts.stopThreshold)

  // Visibility refs — parent writes, layout loop reads
  const filteredNodesRef = useRef<Set<string>>(new Set())
  const neighborhoodRef = useRef<NeighborhoodInfo | null>(null)
  const hideIsolatedRef = useRef(false)

  // Drag refs — parent writes from sigma events
  const isDraggingRef = useRef(false)
  const draggedNodeRef = useRef<string | null>(null)
  const dragPositionRef = useRef<{ x: number; y: number } | null>(null)

  // Keep option refs in sync
  useEffect(() => { onLayoutRestartRef.current = opts.onLayoutRestart }, [opts.onLayoutRestart])
  useEffect(() => { onLayoutStopRef.current = opts.onLayoutStop }, [opts.onLayoutStop])
  useEffect(() => { stopThresholdRef.current = opts.stopThreshold }, [opts.stopThreshold])

  useEffect(() => {
    layoutParamsRef.current = { scalingRatio: opts.scalingRatio, gravity: opts.gravity }
  }, [opts.scalingRatio, opts.gravity])

  useEffect(() => {
    coolingRateRef.current = opts.slowDown * COOL_FACTOR
  }, [opts.slowDown])

  useEffect(() => {
    startTemperatureRef.current = opts.startTemperature
  }, [opts.startTemperature])

  const reheat = useCallback(() => {
    temperatureRef.current = startTemperatureRef.current
    simVelocitiesRef.current.forEach(v => { v.vx = 0; v.vy = 0 })
    const wasRunning = isRunningRef.current
    isRunningRef.current = true
    if (!rafRef.current && layoutLoopRef.current) {
      rafRef.current = requestAnimationFrame(layoutLoopRef.current)
    }
    if (!wasRunning) onLayoutRestartRef.current?.()
  }, [])

  useEffect(() => {
    isRunningRef.current = opts.isLayoutRunning
    if (opts.isLayoutRunning) {
      temperatureRef.current = startTemperatureRef.current
      if (!rafRef.current && layoutLoopRef.current) {
        rafRef.current = requestAnimationFrame(layoutLoopRef.current)
      }
    }
  }, [opts.isLayoutRunning])

  // The layout loop itself is installed by the parent's init() effect after
  // the graph is constructed, via layoutLoopRef. We expose a factory so the
  // parent can call buildLayoutLoop() once and assign the result.
  // This avoids having the hook depend on graphRef content at construction time.

  return {
    graphRef,
    simVelocitiesRef,
    layoutLoopRef,
    rafRef,
    temperatureRef,
    isRunningRef,
    onLayoutStopRef,
    onLayoutRestartRef,
    sigmaRefreshRef,
    layoutParamsRef,
    coolingRateRef,
    startTemperatureRef,
    stopThresholdRef,
    filteredNodesRef,
    neighborhoodRef,
    hideIsolatedRef,
    isDraggingRef,
    draggedNodeRef,
    dragPositionRef,
    reheat,
  }
}

// ---------------------------------------------------------------------------
// Layout loop factory — called once inside the init() async effect
// ---------------------------------------------------------------------------

export interface LayoutLoopDeps {
  graphRef: React.RefObject<AbstractGraph | null>
  sigmaRefreshRef: React.RefObject<(() => void) | null>
  rafRef: React.RefObject<number | null>
  isRunningRef: React.RefObject<boolean>
  temperatureRef: React.RefObject<number>
  simVelocitiesRef: React.RefObject<Map<string, { vx: number; vy: number }>>
  layoutParamsRef: React.RefObject<{ scalingRatio: number; gravity: number }>
  coolingRateRef: React.RefObject<number>
  stopThresholdRef: React.RefObject<number>
  filteredNodesRef: React.RefObject<Set<string>>
  neighborhoodRef: React.RefObject<NeighborhoodInfo | null>
  hideIsolatedRef: React.RefObject<boolean>
  isDraggingRef: React.RefObject<boolean>
  draggedNodeRef: React.RefObject<string | null>
  dragPositionRef: React.RefObject<{ x: number; y: number } | null>
  onLayoutStopRef: React.RefObject<(() => void) | undefined>
  layoutLoopRef: React.RefObject<(() => void) | null>
}

// Above this many visible nodes, exact O(n²) all-pairs repulsion is swapped
// for an approximate O(n) uniform-grid scheme (see applyGridRepulsion) so the
// frame rate stays usable on large vaults. Below the threshold the exact pass
// runs (still over the flat-array snapshot), so small/medium graphs get
// identical physics to before this change.
export const BARNES_HUT_THRESHOLD = 1000

/**
 * Approximate repulsion for visible-node counts above BARNES_HUT_THRESHOLD.
 * Bins nodes into a uniform grid sized so each cell holds ~1 node on average,
 * then sums repulsion only between nodes in the same cell or one of the 4
 * "forward" neighbor cells (right, down, down-right, down-left) — the
 * classic linked-cell half-shell trick, which covers every unordered pair in
 * the full 3x3 neighborhood exactly once with no double-counting. Repulsion
 * beyond ~1 cell width is dropped; since it decays with 1/dist² this trades a
 * small amount of accuracy for O(n) instead of O(n²) pair evaluations. A grid
 * is used instead of a quadtree because it is simpler and lower-risk while
 * still giving a visually stable layout — it does not need to reproduce the
 * exact all-pairs result.
 */
function applyGridRepulsion(
  xs: Float64Array,
  ys: Float64Array,
  count: number,
  apply: (i: number, j: number) => void
): void {
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
  for (let i = 0; i < count; i++) {
    if (xs[i] < minX) minX = xs[i]
    if (xs[i] > maxX) maxX = xs[i]
    if (ys[i] < minY) minY = ys[i]
    if (ys[i] > maxY) maxY = ys[i]
  }
  const width = Math.max(1, maxX - minX)
  const height = Math.max(1, maxY - minY)
  // Target ~1 node per cell on average.
  const cellSize = Math.max(1, Math.sqrt((width * height) / count))
  const cols = Math.max(1, Math.ceil(width / cellSize) + 1)

  const buckets = new Map<number, number[]>()
  for (let i = 0; i < count; i++) {
    const cx = Math.floor((xs[i] - minX) / cellSize)
    const cy = Math.floor((ys[i] - minY) / cellSize)
    const key = cy * cols + cx
    let bucket = buckets.get(key)
    if (!bucket) { bucket = []; buckets.set(key, bucket) }
    bucket.push(i)
  }

  const FORWARD_OFFSETS: ReadonlyArray<readonly [number, number]> = [[1, 0], [0, 1], [1, 1], [-1, 1]]
  for (const [key, bucket] of buckets) {
    for (let a = 0; a < bucket.length; a++) {
      for (let b = a + 1; b < bucket.length; b++) {
        apply(bucket[a], bucket[b])
      }
    }
    const cx = key % cols
    const cy = Math.floor(key / cols)
    for (const [dx, dy] of FORWARD_OFFSETS) {
      const neighbor = buckets.get((cy + dy) * cols + (cx + dx))
      if (!neighbor) continue
      for (const a of bucket) {
        for (const b of neighbor) {
          apply(a, b)
        }
      }
    }
  }
}

/**
 * Builds the per-frame physics loop closure and assigns it to layoutLoopRef.
 * Called once after graph construction so all graphRef reads are safe.
 */
export function buildLayoutLoop(deps: LayoutLoopDeps): void {
  const {
    graphRef, sigmaRefreshRef, rafRef, isRunningRef,
    temperatureRef, simVelocitiesRef, layoutParamsRef,
    coolingRateRef, stopThresholdRef,
    filteredNodesRef, neighborhoodRef, hideIsolatedRef,
    isDraggingRef, draggedNodeRef, dragPositionRef,
    onLayoutStopRef, layoutLoopRef,
  } = deps

  const DAMPING = PHYSICS_DAMPING
  const DT = PHYSICS_DT
  const MIN_DIST = PHYSICS_MIN_DIST
  const MAX_VEL = 20

  const layoutLoop = () => {
    if (!isRunningRef.current || !graphRef.current || !sigmaRefreshRef.current) {
      rafRef.current = null
      return
    }

    const g = graphRef.current
    const p = layoutParamsRef.current
    const velocities = simVelocitiesRef.current

    // Build set of VISIBLE nodes — same logic as nodeReducer, via the shared
    // isEffectivelyIsolated predicate. Hidden nodes must not participate in
    // physics at all.
    const fn = filteredNodesRef.current
    const nh = neighborhoodRef.current
    const allNodes = g.nodes() as string[]
    const visibleSet = new Set<string>()
    for (const id of allNodes) {
      if (fn.size > 0 && !fn.has(id)) continue
      if (nh && !nh.nodes.has(id)) continue
      visibleSet.add(id)
    }
    // Hide isolated: remove nodes with no visible non-overlay edges
    if (hideIsolatedRef.current) {
      for (const id of [...visibleSet]) {
        if (isEffectivelyIsolated(g, id, other => visibleSet.has(other))) {
          visibleSet.delete(id)
        }
      }
    }
    const nodes = [...visibleSet]
    const count = nodes.length

    // --- Drag mode — writes straight to graphology. The flat-array snapshot
    // below reads positions back out of graphology, so it picks up the
    // dragged node's current position automatically. ---
    if (isDraggingRef.current && draggedNodeRef.current && dragPositionRef.current) {
      const dn = draggedNodeRef.current
      // Defense-in-depth: if the dragged node was dropped (e.g. by an incremental
      // graph update mid-drag), clear drag state rather than writing to a missing
      // node (graphology would throw and kill the rAF loop). GraphCanvas also
      // clears these refs in applyNodeDelta; this guards any future caller.
      if (!g.hasNode(dn)) {
        // Dragged node was dropped (incremental update). Stop dragging it, but
        // keep isDraggingRef true so the mouseup handler still runs its full
        // cleanup (cursor reset + reheat) — clearing it here makes mouseup bail.
        draggedNodeRef.current = null
        dragPositionRef.current = null
      } else {
        const dp = dragPositionRef.current
        g.setNodeAttribute(dn, 'x', dp.x)
        g.setNodeAttribute(dn, 'y', dp.y)
        velocities.set(dn, { vx: 0, vy: 0 })
      }
    }

    // --- Flat-array snapshot ---
    // Snapshot visible nodes' x/y/vx/vy into flat typed arrays once per frame
    // so the O(n²) repulsion + attraction passes below do plain array
    // indexing instead of millions of string-keyed graphology attribute
    // lookups. Results are written back to graphology + simVelocitiesRef
    // once, at the end of the frame.
    const xs = new Float64Array(count)
    const ys = new Float64Array(count)
    const vxs = new Float64Array(count)
    const vys = new Float64Array(count)
    const fxs = new Float64Array(count)
    const fys = new Float64Array(count)
    const nodeIndex = new Map<string, number>()
    for (let i = 0; i < count; i++) {
      const id = nodes[i]
      nodeIndex.set(id, i)
      xs[i] = g.getNodeAttribute(id, 'x') as number
      ys[i] = g.getNodeAttribute(id, 'y') as number
      const v = velocities.get(id)
      vxs[i] = v ? v.vx : 0
      vys[i] = v ? v.vy : 0
    }

    // 1) Gravity — pull toward center.
    // Scale with SR² to stay balanced against repulsion (also SR²/dist²).
    // Factor 0.01 keeps forces moderate at default settings.
    const gravityStrength = p.gravity * p.scalingRatio * p.scalingRatio * 0.01
    for (let i = 0; i < count; i++) {
      fxs[i] -= xs[i] * gravityStrength
      fys[i] -= ys[i] * gravityStrength
    }

    // 2) Repulsion — exact all-pairs below BARNES_HUT_THRESHOLD, approximate
    // uniform-grid above it.
    const applyRepulsion = (i: number, j: number) => {
      const dx = xs[i] - xs[j]
      const dy = ys[i] - ys[j]
      const dist = Math.max(MIN_DIST, Math.sqrt(dx * dx + dy * dy))
      // Coulomb repulsion: SR² / dist². Squaring slider value compensates
      // for cube-root equilibrium: d ∝ SR^(2/3). Slider 10→100 = 4.6x change.
      const rep = (p.scalingRatio * p.scalingRatio) / (dist * dist)
      const fx = (dx / dist) * rep
      const fy = (dy / dist) * rep
      fxs[i] += fx
      fys[i] += fy
      fxs[j] -= fx
      fys[j] -= fy
    }
    if (count > BARNES_HUT_THRESHOLD) {
      applyGridRepulsion(xs, ys, count, applyRepulsion)
    } else {
      for (let i = 0; i < count; i++) {
        for (let j = i + 1; j < count; j++) {
          applyRepulsion(i, j)
        }
      }
    }

    // 3) Edge attraction — only non-overlay edges between visible nodes.
    //
    // ARC-037: snapshot edge endpoints, weights, and overlay flags into
    // parallel typed arrays ONCE per frame and iterate them with plain
    // numeric indexing. The previous form called graphology's
    // `g.edges()` + `getEdgeAttribute`/`source`/`target` per edge, which
    // for a 376k-edge vault was ~1.5M method calls per frame — the single
    // largest per-frame cost in the layout loop after the repulsion pass
    // had already been typed-array'd. Plain-array indexing also lets the
    // JIT keep the hot loop in registers.
    //
    // We walk via g.edges() → g.source/target/getEdgeAttribute to read,
    // but only once per frame, then never touch graphology again for the
    // rest of the attraction pass. The `overlay` filter is inlined as a
    // 0/1 marker on `edgeOverlay` so the per-edge branch stays branch-
    // predictable.
    const allEdges = g.edges() as string[]
    const edgeCount = allEdges.length
    const edgeS = new Int32Array(edgeCount)
    const edgeT = new Int32Array(edgeCount)
    const edgeW = new Float64Array(edgeCount)
    const edgeOverlay = new Uint8Array(edgeCount)
    for (let i = 0; i < edgeCount; i++) {
      const e = allEdges[i]
      const src = g.source(e) as string
      const tgt = g.target(e) as string
      const si = nodeIndex.get(src)
      const ti = nodeIndex.get(tgt)
      // Store -1 for edges touching a hidden node — the attraction loop
      // below skips them via a single `>= 0` check rather than a branch
      // on undefined.
      edgeS[i] = si === undefined ? -1 : si
      edgeT[i] = ti === undefined ? -1 : ti
      edgeW[i] = (g.getEdgeAttribute(e, 'weight') as number) || 0
      edgeOverlay[i] = g.getEdgeAttribute(e, 'overlay') ? 1 : 0
    }
    for (let i = 0; i < edgeCount; i++) {
      if (edgeOverlay[i] !== 0) continue
      const si = edgeS[i]
      const ti = edgeT[i]
      if (si < 0 || ti < 0) continue
      const w = edgeW[i]
      if (w === 0) continue
      const dx = xs[ti] - xs[si]
      const dy = ys[ti] - ys[si]
      const fx = dx * w
      const fy = dy * w
      fxs[si] += fx
      fys[si] += fy
      fxs[ti] -= fx
      fys[ti] -= fy
    }

    // 4) Apply forces → velocity → position (with velocity cap)
    const dragNode = isDraggingRef.current ? draggedNodeRef.current : null
    const dragIdx = dragNode !== null ? nodeIndex.get(dragNode) : undefined
    for (let i = 0; i < count; i++) {
      if (i === dragIdx) continue
      let vx = (vxs[i] + fxs[i] * DT) * DAMPING
      let vy = (vys[i] + fys[i] * DT) * DAMPING
      // Cap velocity to prevent explosions
      const speed = Math.sqrt(vx * vx + vy * vy)
      if (speed > MAX_VEL) {
        vx = (vx / speed) * MAX_VEL
        vy = (vy / speed) * MAX_VEL
      }
      vxs[i] = vx
      vys[i] = vy
      xs[i] += vx
      ys[i] += vy
    }

    // Write results back to graphology + the velocity map once per frame.
    for (let i = 0; i < count; i++) {
      const id = nodes[i]
      g.setNodeAttribute(id, 'x', xs[i])
      g.setNodeAttribute(id, 'y', ys[i])
      velocities.set(id, { vx: vxs[i], vy: vys[i] })
    }

    // Decay temperature (energy bar + auto-stop)
    const temp = Math.max(0.0001, temperatureRef.current * (1 - coolingRateRef.current))
    temperatureRef.current = temp
    const thr = stopThresholdRef.current
    if (thr > 0 && temp < thr) {
      isRunningRef.current = false
      rafRef.current = null
      sigmaRefreshRef.current()
      onLayoutStopRef.current?.()
      return
    }

    sigmaRefreshRef.current()
    rafRef.current = requestAnimationFrame(layoutLoop)
  }

  layoutLoopRef.current = layoutLoop
}
