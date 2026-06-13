// ---------------------------------------------------------------------------
// Sigma node/edge reducer factories extracted from GraphCanvas.tsx (QA-004)
//
// Sigma's reducer callbacks are typed as:
//   nodeReducer: (node: string, data: N) => Partial<NodeDisplayData>
//   edgeReducer: (edge: string, data: E) => Partial<EdgeDisplayData>
//
// where N and E extend graphology's Attributes = { [name: string]: any }.
// We define concrete interfaces for the attributes we store on the graph so
// the reducers can reference named fields without `any` suppressions.
// ---------------------------------------------------------------------------
import type { NodeDisplayData, EdgeDisplayData } from 'sigma/types'
import type { Attributes, AbstractGraph } from 'graphology-types'
import { HIGHLIGHT_COLOR, CANVAS_BACKGROUND } from '@/lib/sigma-colors'

// ---------------------------------------------------------------------------
// Graph attribute shapes
// ---------------------------------------------------------------------------

/** Attributes stored on each graphology node. */
export interface NodeAttrs {
  label: string
  color: string
  size: number
  x: number
  y: number
  nodeType: string
  originalColor: string
  /** Optionally set by external code (e.g. betweenness sizing). */
  [key: string]: unknown
}

/** Attributes stored on each graphology edge. */
export interface EdgeAttrs {
  weight: number
  baseWeight: number
  color: string
  size: number
  kind: 'wiki' | 'semantic'
  overlay: boolean
  originalColor: string
  [key: string]: unknown
}

// ---------------------------------------------------------------------------
// Reducer context — all refs the reducers close over
// ---------------------------------------------------------------------------

export interface ReducerContext {
  graph: AbstractGraph
  pathNodesRef: React.RefObject<Set<string>>
  pathEdgesRef: React.RefObject<Set<string>>
  pathSourceRef: React.RefObject<string | null>
  labelsOnHoverOnlyRef: React.RefObject<boolean>
  hoveredNodeRef: React.RefObject<string | null>
  neighborhoodRef: React.RefObject<NeighborhoodInfo | null>
  filteredNodesRef: React.RefObject<Set<string>>
  hideIsolatedRef: React.RefObject<boolean>
  highlightedNodesRef: React.RefObject<Set<string>>
  highlightedEdgesRef: React.RefObject<Set<string>>
}

export interface NeighborhoodInfo {
  nodes: Set<string>
  distances: Map<string, number>
  maxHop: number
}

// ---------------------------------------------------------------------------
// Reducer factories
// ---------------------------------------------------------------------------

/**
 * Returns a sigma nodeReducer that applies highlight, filter, and visibility
 * logic. The returned function is stable for a given context object —
 * recreate it only when the graph instance changes (i.e. inside the main
 * init() effect).
 *
 * The parameter type uses `Attributes` (= `{ [name: string]: any }`) because
 * that is what sigma passes for an untyped MultiGraph. We access named fields
 * via type-asserted casts internally, which is sound given that we set those
 * exact attributes in the graph construction code.
 */
export function makeNodeReducer(
  ctx: ReducerContext
): (node: string, data: Attributes) => Partial<NodeDisplayData> {
  const {
    graph,
    pathNodesRef,
    pathSourceRef,
    labelsOnHoverOnlyRef,
    hoveredNodeRef,
    neighborhoodRef,
    filteredNodesRef,
    hideIsolatedRef,
    highlightedNodesRef,
  } = ctx

  return (node: string, data: Attributes): Partial<NodeDisplayData> => {
    // Cast to our known shape — safe because we set these exact attributes
    // during graph construction in GraphCanvas.
    const d = data as NodeAttrs
    const pn = pathNodesRef.current
    if (pn.size > 0 && pn.has(node)) {
      const showLabel = labelsOnHoverOnlyRef.current ? node === hoveredNodeRef.current : true
      return {
        ...d,
        color: HIGHLIGHT_COLOR,
        zIndex: 10,
        label: showLabel ? d.label : '',
        forceLabel: showLabel,
        highlighted: showLabel,
      }
    }
    if (pathSourceRef.current === node) {
      return { ...d, color: HIGHLIGHT_COLOR, zIndex: 5 }
    }
    const nh = neighborhoodRef.current
    if (nh && !nh.nodes.has(node)) {
      return { ...d, hidden: true, label: '' }
    }
    const fn = filteredNodesRef.current
    if (fn.size > 0 && !fn.has(node)) {
      return { ...d, hidden: true, label: '' }
    }
    if (hideIsolatedRef.current) {
      // When a similarity filter is active, only count edges to other visible
      // (non-filtered-out) neighbors — edgeReducer hides cross-filter edges
      // but graph.degree() still counts them, causing isolated-looking nodes.
      const effectiveDegree = fn.size > 0
        ? (graph.neighbors(node) as string[]).filter((n: string) => fn.has(n)).length
        : graph.degree(node)
      if (effectiveDegree === 0) return { ...d, hidden: true, label: '' }
    }
    const hn = highlightedNodesRef.current
    const isHovered = node === hoveredNodeRef.current
    const isHighlighted = hn.size === 0 || hn.has(node)
    const showLabel = labelsOnHoverOnlyRef.current
      ? (isHovered || (hn.size > 0 && hn.has(node)))
      : (hn.size === 0 || isHovered || hn.has(node))
    const label = showLabel ? d.label : ''
    // forceLabel bypasses sigma's label-density grid so hovered/highlighted
    // nodes always render their label. highlighted tells sigma to include
    // the node in its hover-layer rendering (renderHighlightedNodes).
    const forceLabel = showLabel && (isHovered || (hn.size > 0 && hn.has(node)))
    if (!isHighlighted && !isHovered) {
      return { ...d, label, color: CANVAS_BACKGROUND, size: d.size * 0.6, zIndex: 0 }
    }
    if (nh) {
      const hopDist = nh.distances.get(node)
      if (hopDist === nh.maxHop) {
        const dimColor = (d.originalColor || d.color) + '66'
        return { ...d, label, color: dimColor, size: d.size * 0.8, forceLabel, highlighted: forceLabel }
      }
    }
    return { ...d, label, forceLabel, highlighted: forceLabel }
  }
}

/**
 * Returns a sigma edgeReducer that applies path, neighbourhood, filter, and
 * highlight visibility logic.
 *
 * Uses `Attributes` as the data parameter for the same reason as makeNodeReducer —
 * sigma passes the graph's raw edge attributes, typed as Attributes.
 */
export function makeEdgeReducer(
  ctx: ReducerContext
): (edge: string, data: Attributes) => Partial<EdgeDisplayData> {
  const {
    graph,
    pathEdgesRef,
    neighborhoodRef,
    filteredNodesRef,
    highlightedEdgesRef,
  } = ctx

  return (edge: string, data: Attributes): Partial<EdgeDisplayData> => {
    const d = data as EdgeAttrs
    const pe = pathEdgesRef.current
    if (pe.size > 0 && pe.has(edge)) {
      return { ...d, color: HIGHLIGHT_COLOR, size: 3, hidden: false }
    }
    const nh = neighborhoodRef.current
    if (nh) {
      const src = graph.source(edge)
      const tgt = graph.target(edge)
      if (!nh.nodes.has(src) || !nh.nodes.has(tgt)) return { ...d, hidden: true }
    }
    const fn = filteredNodesRef.current
    if (fn.size > 0) {
      const src = graph.source(edge)
      const tgt = graph.target(edge)
      if (!fn.has(src) || !fn.has(tgt)) return { ...d, hidden: true }
    }
    const he = highlightedEdgesRef.current
    if (he.size === 0 || he.has(edge)) return d
    return { ...d, color: CANVAS_BACKGROUND, size: 0.3 }
  }
}
