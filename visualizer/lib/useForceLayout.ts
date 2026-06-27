// ---------------------------------------------------------------------------
// Custom force-directed physics loop extracted from GraphCanvas.tsx (QA-004)
//
// This is a simple Newtonian physics simulation (gravity + Coulomb repulsion
// + Hooke attraction on edges + velocity damping). It does NOT use FA2.
// Temperature drives both the energy bar and the auto-stop threshold.
// ---------------------------------------------------------------------------
import { useRef, useCallback, useEffect } from 'react'
import type { AbstractGraph } from 'graphology-types'
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
    layoutLoopRef,
  } = deps

  const DAMPING = PHYSICS_DAMPING
  const DT = PHYSICS_DT
  const MIN_DIST = PHYSICS_MIN_DIST

  const layoutLoop = () => {
    if (!isRunningRef.current || !graphRef.current || !sigmaRefreshRef.current) {
      rafRef.current = null
      return
    }

    const g = graphRef.current
    const p = layoutParamsRef.current
    const velocities = simVelocitiesRef.current

    // Build set of VISIBLE nodes — same logic as nodeReducer.
    // Hidden nodes must not participate in physics at all.
    const fn = filteredNodesRef.current
    const allNodes = g.nodes() as string[]
    const visibleSet = new Set<string>()
    for (const n of allNodes) {
      if (fn.size > 0 && !fn.has(n)) continue
      if (neighborhoodRef.current && !neighborhoodRef.current.nodes.has(n)) continue
      visibleSet.add(n)
    }
    // Hide isolated: remove nodes with no visible non-overlay edges
    if (hideIsolatedRef.current) {
      for (const n of [...visibleSet]) {
        let hasVisibleEdge = false
        for (const e of g.edges(n) as string[]) {
          if (g.getEdgeAttribute(e, 'overlay')) continue
          const other = g.source(e) === n ? g.target(e) : g.source(e)
          if (visibleSet.has(other as string)) { hasVisibleEdge = true; break }
        }
        if (!hasVisibleEdge) visibleSet.delete(n)
      }
    }
    const nodes = [...visibleSet]

    // --- Drag mode ---
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

    // Accumulate forces (only for visible nodes)
    const forces = new Map<string, { fx: number; fy: number }>()
    for (const n of nodes) {
      forces.set(n, { fx: 0, fy: 0 })
    }

    // 1) Gravity — pull toward center.
    // Scale with SR² to stay balanced against repulsion (also SR²/dist²).
    // Factor 0.01 keeps forces moderate at default settings.
    const gravityStrength = p.gravity * p.scalingRatio * p.scalingRatio * 0.01
    for (const n of nodes) {
      const x = g.getNodeAttribute(n, 'x') as number
      const y = g.getNodeAttribute(n, 'y') as number
      const f = forces.get(n)!
      f.fx -= x * gravityStrength
      f.fy -= y * gravityStrength
    }

    // 2) Repulsion — all visible pairs (O(n²), acceptable for <1000 nodes)
    for (let i = 0; i < nodes.length; i++) {
      const n1 = nodes[i]
      const x1 = g.getNodeAttribute(n1, 'x') as number
      const y1 = g.getNodeAttribute(n1, 'y') as number
      const f1 = forces.get(n1)!
      for (let j = i + 1; j < nodes.length; j++) {
        const n2 = nodes[j]
        const x2 = g.getNodeAttribute(n2, 'x') as number
        const y2 = g.getNodeAttribute(n2, 'y') as number
        const dx = x1 - x2
        const dy = y1 - y2
        const dist = Math.max(MIN_DIST, Math.sqrt(dx * dx + dy * dy))
        // Coulomb repulsion: SR² / dist². Squaring slider value compensates
        // for cube-root equilibrium: d ∝ SR^(2/3). Slider 10→100 = 4.6x change.
        const rep = (p.scalingRatio * p.scalingRatio) / (dist * dist)
        const fx = (dx / dist) * rep
        const fy = (dy / dist) * rep
        f1.fx += fx
        f1.fy += fy
        const f2 = forces.get(n2)!
        f2.fx -= fx
        f2.fy -= fy
      }
    }

    // 3) Edge attraction — only non-overlay edges between visible nodes
    ;(g.edges() as string[]).forEach((e: string) => {
      if (g.getEdgeAttribute(e, 'overlay')) return
      const src = g.source(e) as string
      const tgt = g.target(e) as string
      if (!visibleSet.has(src) || !visibleSet.has(tgt)) return
      const w = (g.getEdgeAttribute(e, 'weight') as number) || 0
      if (w === 0) return
      const x1 = g.getNodeAttribute(src, 'x') as number
      const y1 = g.getNodeAttribute(src, 'y') as number
      const x2 = g.getNodeAttribute(tgt, 'x') as number
      const y2 = g.getNodeAttribute(tgt, 'y') as number
      const dx = x2 - x1
      const dy = y2 - y1
      const fx = dx * w
      const fy = dy * w
      forces.get(src)!.fx += fx
      forces.get(src)!.fy += fy
      forces.get(tgt)!.fx -= fx
      forces.get(tgt)!.fy -= fy
    })

    // 4) Apply forces → velocity → position (with velocity cap)
    const MAX_VEL = 20
    const dragNode = isDraggingRef.current ? draggedNodeRef.current : null
    for (const n of nodes) {
      if (n === dragNode) continue
      const f = forces.get(n)!
      const v = velocities.get(n) || { vx: 0, vy: 0 }
      v.vx = (v.vx + f.fx * DT) * DAMPING
      v.vy = (v.vy + f.fy * DT) * DAMPING
      // Cap velocity to prevent explosions
      const speed = Math.sqrt(v.vx * v.vx + v.vy * v.vy)
      if (speed > MAX_VEL) {
        v.vx = (v.vx / speed) * MAX_VEL
        v.vy = (v.vy / speed) * MAX_VEL
      }
      velocities.set(n, v)
      const x = (g.getNodeAttribute(n, 'x') as number) + v.vx
      const y = (g.getNodeAttribute(n, 'y') as number) + v.vy
      g.setNodeAttribute(n, 'x', x)
      g.setNodeAttribute(n, 'y', y)
    }

    // Decay temperature (energy bar + auto-stop)
    const temp = Math.max(0.0001, temperatureRef.current * (1 - coolingRateRef.current))
    temperatureRef.current = temp
    const thr = stopThresholdRef.current
    if (thr > 0 && temp < thr) {
      isRunningRef.current = false
      rafRef.current = null
      sigmaRefreshRef.current()
      return
    }

    sigmaRefreshRef.current()
    rafRef.current = requestAnimationFrame(layoutLoop)
  }

  layoutLoopRef.current = layoutLoop
}
