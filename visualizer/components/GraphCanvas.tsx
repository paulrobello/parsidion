'use client'

import { useEffect, useRef, useCallback, useMemo, useState, forwardRef, useImperativeHandle } from 'react'
import type { GraphData, GraphEdge, GraphSource } from '@/lib/graph'
import { filterEdges } from '@/lib/graph'
import {
  getNodeColor, getNodeSize, getSemanticEdgeColor, recencyHeatColor,
  HIGHLIGHT_COLOR, LABEL_COLOR, MUTED_NODE_COLOR,
  MENU_BACKGROUND, MENU_BORDER, ACCENT_TEAL,
} from '@/lib/sigma-colors'
import type { EdgeColorMode, NodeSizeMode, NodeColorMode } from '@/lib/sigma-colors'
import type Sigma from 'sigma'
import type { MouseCoords } from 'sigma/types'
import type { AbstractGraph } from 'graphology-types'
import { drawNodeLabel, drawNodeHover } from '@/lib/sigma-renderers'
import { makeNodeReducer, makeEdgeReducer } from '@/lib/useGraphReducers'
import type { NeighborhoodInfo } from '@/lib/useGraphReducers'
import {
  useForceLayout, buildLayoutLoop,
  buildRecencySizeMap, buildRecencyColorMap, pruneEdges,
  RECENCY_SIZE_MIN,
} from '@/lib/useForceLayout'
import {
  computeVisibleNodes, computeNodeDelta, shouldFullRebuild,
  buildAdjacency, seedPlacementFrom, graphBounds, positionNewNode,
} from '@/lib/graphDelta'
import type { NodeDelta } from '@/lib/graphDelta'

export interface GraphCanvasHandle {
  flyToNode: (stem: string) => void
  selectNode: (stem: string) => void
  getEnergy: () => number   // returns current temperature (1.0 = hot, 0 = frozen)
}

interface Props {
  data: GraphData
  threshold: number
  graphSource: GraphSource
  activeTypes: Set<string>
  showDaily: boolean
  hideIsolated: boolean
  labelsOnHoverOnly: boolean
  showOverlayEdges: boolean
  filterNodesBySimilarity: boolean
  edgeColorMode: EdgeColorMode
  edgePruning: boolean
  edgePruningK: number
  nodeSizeMode: NodeSizeMode
  nodeColorMode: NodeColorMode
  nodeSizeMap: Map<string, number> | null
  onNodeClick: (stem: string, open: boolean, newTab: boolean) => void
  onBackgroundClick: () => void
  onOpenHistory?: (stem: string) => void
  scalingRatio: number
  gravity: number
  // slowDown is now the cooling rate (how fast temperature decays per frame).
  // It is NOT passed to FA2 — FA2 runs at a fixed slowDown internally.
  slowDown: number
  edgeWeightInfluence: number
  startTemperature: number
  stopThreshold: number
  isLayoutRunning: boolean
  onLayoutStop?: () => void
  onLayoutRestart?: () => void
  neighborhoodCenter?: string | null
  neighborhoodHops?: number
}

// QA-004: findWikiPath kept here — it is only called from GraphCanvas JSX
// (the context-menu "Find Path Here" handler).
function findWikiPath(
  from: string,
  to: string,
  graph: AbstractGraph
): { path: string[]; edgeIds: string[] } | null {
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

export const GraphCanvas = forwardRef<GraphCanvasHandle, Props>(function GraphCanvas(
  {
    data, threshold, graphSource, activeTypes, showDaily, hideIsolated, labelsOnHoverOnly, showOverlayEdges, filterNodesBySimilarity, edgeColorMode, edgePruning, edgePruningK, nodeSizeMode, nodeColorMode, nodeSizeMap,
    onNodeClick, onBackgroundClick, onOpenHistory,
    scalingRatio, gravity, slowDown, edgeWeightInfluence, startTemperature, stopThreshold, isLayoutRunning, onLayoutStop, onLayoutRestart,
    neighborhoodCenter, neighborhoodHops,
  },
  ref
) {
  const containerRef = useRef<HTMLDivElement>(null)
  const sigmaRef = useRef<Sigma | null>(null)
  const graphRef = useRef<AbstractGraph | null>(null)

  // -------------------------------------------------------------------------
  // Force layout hook — owns the physics loop and all layout-related refs
  // -------------------------------------------------------------------------
  const layout = useForceLayout({
    isLayoutRunning,
    startTemperature,
    slowDown,
    stopThreshold,
    scalingRatio,
    gravity,
    onLayoutStop,
    onLayoutRestart,
  })
  const { reheat } = layout

  // Expose graphRef and sigmaRefreshRef into the layout hook so the loop can
  // read graph positions and trigger sigma renders.
  // (useForceLayout creates these refs; we alias them here for clarity.)
  const {
    graphRef: layoutGraphRef,
    sigmaRefreshRef,
    simVelocitiesRef,
    layoutLoopRef,
    rafRef,
    temperatureRef,
    isRunningRef,
    layoutParamsRef,
    coolingRateRef,
    stopThresholdRef,
    filteredNodesRef,
    neighborhoodRef,
    hideIsolatedRef,
    isDraggingRef,
    draggedNodeRef,
    dragPositionRef,
  } = layout

  // Keep the layout's graphRef in sync — it IS the same ref (shared via the
  // hook), but we also keep sigmaRef local for the rest of GraphCanvas.
  // Wire sigmaRefreshRef → sigma.refresh()
  useEffect(() => {
    layoutGraphRef.current = graphRef.current
  })
  useEffect(() => {
    sigmaRefreshRef.current = () => sigmaRef.current?.refresh()
  })

  // Local refs not owned by useForceLayout
  const edgeWeightInfluenceRef = useRef(edgeWeightInfluence)
  const labelsOnHoverOnlyRef = useRef(labelsOnHoverOnly)
  const showOverlayEdgesRef = useRef(showOverlayEdges)
  const filterNodesBySimilarityRef = useRef(false)
  const thresholdRef = useRef(threshold)
  const edgeColorModeRef = useRef(edgeColorMode)
  const graphSourceRef = useRef(graphSource)
  const dataRef = useRef(data)
  const edgePruningRef = useRef(edgePruning)
  const edgePruningKRef = useRef(edgePruningK)
  const nodeSizeModeRef = useRef(nodeSizeMode)
  const nodeColorModeRef = useRef(nodeColorMode)
  const nodeSizeMapRef = useRef(nodeSizeMap)
  const hoveredNodeRef = useRef<string | null>(null)
  const highlightedNodesRef = useRef<Set<string>>(new Set())
  const highlightedEdgesRef = useRef<Set<string>>(new Set())
  const dragHasMovedRef = useRef(false)

  const [nodeContextMenu, setNodeContextMenu] = useState<{ stem: string; x: number; y: number } | null>(null)
  // Bumped after each incremental node delta so the size + color effects re-run
  // and size/color newly-added and changed nodes (their deps are otherwise
  // mode-only, so they'd skip a pure data change). See applyNodeDelta.
  const [nodeDeltaVersion, setNodeDeltaVersion] = useState(0)

  const pathSourceRef = useRef<string | null>(null)
  const pathNodesRef = useRef<Set<string>>(new Set())
  const pathEdgesRef = useRef<Set<string>>(new Set())
  const [toastMsg, setToastMsg] = useState<string | null>(null)
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Compute neighborhood BFS when in local mode.
  // Uses wiki edges only — semantic edges are too dense (19K+) and would
  // reach ~70% of the graph in 2 hops, defeating the purpose of local view.
  // All edge types are still rendered for nodes within the neighborhood.
  const neighborhoodInfo = useMemo<NeighborhoodInfo | null>(() => {
    if (!neighborhoodCenter || !data) return null
    const hops = neighborhoodHops ?? 2
    // Pre-build wiki adjacency list for O(1) neighbor lookup
    const wikiAdj = new Map<string, string[]>()
    for (const edge of data.edges) {
      if (edge.kind !== 'wiki') continue
      if (!wikiAdj.has(edge.s)) wikiAdj.set(edge.s, [])
      if (!wikiAdj.has(edge.t)) wikiAdj.set(edge.t, [])
      wikiAdj.get(edge.s)!.push(edge.t)
      wikiAdj.get(edge.t)!.push(edge.s)
    }
    const distances = new Map<string, number>()
    distances.set(neighborhoodCenter, 0)
    let frontier = [neighborhoodCenter]
    for (let h = 1; h <= hops; h++) {
      const nextFrontier: string[] = []
      for (const nodeId of frontier) {
        const neighbors = wikiAdj.get(nodeId) ?? []
        for (const other of neighbors) {
          if (!distances.has(other)) {
            distances.set(other, h)
            nextFrontier.push(other)
          }
        }
      }
      frontier = nextFrontier
    }
    return { nodes: new Set(distances.keys()), distances, maxHop: hops }
  }, [neighborhoodCenter, neighborhoodHops, data])

  useEffect(() => { neighborhoodRef.current = neighborhoodInfo }, [neighborhoodInfo, neighborhoodRef])

  const showToast = useCallback((msg: string) => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current)
    setToastMsg(msg)
    toastTimerRef.current = setTimeout(() => setToastMsg(null), 4000)
  }, [])

  useEffect(() => {
    return () => { if (toastTimerRef.current) clearTimeout(toastTimerRef.current) }
  }, [])

  useEffect(() => {
    sigmaRef.current?.refresh()
  }, [neighborhoodCenter, neighborhoodHops])

  useEffect(() => { thresholdRef.current = threshold }, [threshold])
  useEffect(() => { graphSourceRef.current = graphSource }, [graphSource])
  useEffect(() => { dataRef.current = data }, [data])
  useEffect(() => {
    hideIsolatedRef.current = hideIsolated
    sigmaRef.current?.refresh()
  }, [hideIsolated, hideIsolatedRef])
  useEffect(() => {
    labelsOnHoverOnlyRef.current = labelsOnHoverOnly
    sigmaRef.current?.refresh()
  }, [labelsOnHoverOnly])
  useEffect(() => {
    showOverlayEdgesRef.current = showOverlayEdges
    const graph = graphRef.current
    const sigma = sigmaRef.current
    const d = dataRef.current
    if (!graph || !sigma || !d) return
    // Remove existing overlay edges
    const toRemove = (graph.edges() as string[]).filter(
      (e: string) => graph.getEdgeAttribute(e, 'overlay') === true
    )
    toRemove.forEach((e: string) => graph.dropEdge(e))
    // Add new overlay edges if enabled — no reheat
    if (showOverlayEdges) {
      const gs = graphSourceRef.current
      const thr = thresholdRef.current
      const visibleNodes = new Set(graph.nodes() as string[])
      const overlayKind = gs === 'semantic' ? 'wiki' : 'semantic'
      const overlayEdges = d.edges.filter(e => e.kind === overlayKind &&
        (overlayKind === 'semantic' ? e.w >= thr : true))
      for (const edge of overlayEdges) {
        if (!visibleNodes.has(edge.s) || !visibleNodes.has(edge.t)) continue
        const col = overlayKind === 'wiki' ? 'rgba(123,97,255,0.18)' : 'rgba(150,150,160,0.18)'
        try {
          graph.addEdge(edge.s, edge.t, {
            weight: 0.001, color: col, size: 0.8,
            kind: overlayKind, overlay: true, originalColor: col,
          })
        } catch { /* skip */ }
      }
    }
    sigma.refresh()
  }, [showOverlayEdges])

  // Recompute similarity-filtered node set; reheat so newly visible/hidden nodes settle
  useEffect(() => {
    filterNodesBySimilarityRef.current = filterNodesBySimilarity
    const d = dataRef.current
    if (!filterNodesBySimilarity || graphSourceRef.current !== 'wiki' || !d) {
      filteredNodesRef.current = new Set()
    } else {
      const qualifying = new Set<string>()
      for (const edge of d.edges) {
        if (edge.kind === 'semantic' && edge.w >= thresholdRef.current) {
          qualifying.add(edge.s)
          qualifying.add(edge.t)
        }
      }
      filteredNodesRef.current = qualifying
    }
    sigmaRef.current?.refresh()
    reheat()
  }, [filterNodesBySimilarity, threshold, graphSource, data, reheat, filteredNodesRef])

  // Edge weight influence acts as a direct weight multiplier on graph edges.
  useEffect(() => {
    edgeWeightInfluenceRef.current = edgeWeightInfluence
    const graph = graphRef.current
    if (!graph) return
    ;(graph.edges() as string[]).forEach((e: string) => {
      if (graph.getEdgeAttribute(e, 'overlay')) return
      const base = graph.getEdgeAttribute(e, 'baseWeight') as number
      if (base != null) graph.setEdgeAttribute(e, 'weight', base * edgeWeightInfluence)
    })
    reheat()
  }, [edgeWeightInfluence, reheat])

  useEffect(() => { edgeColorModeRef.current = edgeColorMode }, [edgeColorMode])
  useEffect(() => { edgePruningRef.current = edgePruning }, [edgePruning])
  useEffect(() => { edgePruningKRef.current = edgePruningK }, [edgePruningK])
  useEffect(() => { nodeSizeModeRef.current = nodeSizeMode }, [nodeSizeMode])
  useEffect(() => { nodeColorModeRef.current = nodeColorMode }, [nodeColorMode])
  useEffect(() => { nodeSizeMapRef.current = nodeSizeMap }, [nodeSizeMap])

  useEffect(() => {
    const graph = graphRef.current
    const sigma = sigmaRef.current
    const d = dataRef.current
    if (!graph || !sigma || !d) return
    // Skip while betweenness is still computing — the computation effect will re-trigger this
    if (nodeSizeMode === 'betweenness' && nodeSizeMap === null) return
    const nodeDataMap = new Map(d.nodes.map(n => [n.id, n]))
    const graphNodeIds = graph.nodes() as string[]
    const recencyMap = nodeSizeMode === 'recency'
      ? buildRecencySizeMap(graphNodeIds.map(id => ({ id, mtime: nodeDataMap.get(id)?.mtime ?? 0 })))
      : null
    graphNodeIds.forEach((nodeId: string) => {
      const nd = nodeDataMap.get(nodeId)
      if (!nd) return
      let size: number
      if (nodeSizeMode === 'uniform') {
        size = 4
      } else if (nodeSizeMode === 'betweenness') {
        size = nodeSizeMap?.get(nodeId) ?? getNodeSize(nd.incoming_links)
      } else if (nodeSizeMode === 'recency') {
        size = recencyMap!.get(nodeId) ?? RECENCY_SIZE_MIN
      } else {
        size = getNodeSize(nd.incoming_links)
      }
      graph.setNodeAttribute(nodeId, 'size', size)
    })
    sigma.refresh()
  }, [nodeSizeMode, nodeSizeMap, nodeDeltaVersion])

  // Recolor nodes when the color mode toggles. Color has no physics effect, so
  // this is a refresh-only update — do NOT call reheat().
  useEffect(() => {
    const graph = graphRef.current
    const sigma = sigmaRef.current
    const d = dataRef.current
    if (!graph || !sigma || !d) return
    const nodeDataMap = new Map(d.nodes.map(n => [n.id, n]))
    const ids = graph.nodes() as string[]
    const colorMap = nodeColorMode === 'recency'
      ? buildRecencyColorMap(ids.map(id => ({ id, mtime: nodeDataMap.get(id)?.mtime ?? 0 })))
      : null
    ids.forEach((nodeId: string) => {
      const nd = nodeDataMap.get(nodeId)
      const col = colorMap
        ? (colorMap.get(nodeId) ?? recencyHeatColor(1))
        : getNodeColor(nd?.type ?? '')
      graph.setNodeAttribute(nodeId, 'color', col)
      graph.setNodeAttribute(nodeId, 'originalColor', col)
    })
    sigma.refresh()
  }, [nodeColorMode, nodeDeltaVersion])

  useEffect(() => {
    const graph = graphRef.current
    const sigma = sigmaRef.current
    if (!graph || !sigma) return
    ;(graph.edges() as string[]).forEach((e: string) => {
      if (graph.getEdgeAttribute(e, 'overlay')) return
      const kind = graph.getEdgeAttribute(e, 'kind') as 'wiki' | 'semantic'
      if (kind === 'wiki') return
      const baseWeight = graph.getEdgeAttribute(e, 'baseWeight') as number
      const col = getSemanticEdgeColor(baseWeight, kind, edgeColorMode, thresholdRef.current)
      graph.setEdgeAttribute(e, 'color', col)
      graph.setEdgeAttribute(e, 'originalColor', col)
    })
    sigma.refresh()
  }, [edgeColorMode, threshold])

  const flyToNode = useCallback((stem: string) => {
    if (!sigmaRef.current || !graphRef.current) return
    if (!graphRef.current.hasNode(stem)) return
    const nodePos = sigmaRef.current.getNodeDisplayData(stem)
    if (!nodePos) return
    sigmaRef.current.getCamera().animate(
      { x: nodePos.x, y: nodePos.y, ratio: 0.3 },
      { duration: 600, easing: 'cubicInOut' }
    )
  }, [])

  const selectNode = useCallback((stem: string) => {
    if (!sigmaRef.current || !graphRef.current) return
    if (!graphRef.current.hasNode(stem)) return
    const graph = graphRef.current
    const neighbors = new Set(graph.neighbors(stem) as string[])
    neighbors.add(stem)
    highlightedNodesRef.current = neighbors
    const neighborEdges = new Set<string>()
    ;(graph.edges(stem) as string[]).forEach((e: string) => neighborEdges.add(e))
    highlightedEdgesRef.current = neighborEdges
    sigmaRef.current.refresh()
  }, [])

  // temperature IS the energy metric exposed to the temperature bar
  const getEnergy = useCallback(() => temperatureRef.current, [temperatureRef])
  useImperativeHandle(ref, () => ({ flyToNode, selectNode, getEnergy }), [flyToNode, selectNode, getEnergy])

  // Kill the sigma/graphology instance and reset all refs. Used by the unmount
  // effect and by the [data] effect's safety-valve full-rebuild path. It is NOT
  // called on an ordinary data change — that path applies an incremental delta
  // so the camera and converged layout survive a graph.json rebuild.
  const teardownInstance = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
    layoutLoopRef.current = null
    simVelocitiesRef.current.clear()
    sigmaRef.current?.kill()
    sigmaRef.current = null
    graphRef.current = null
    layoutGraphRef.current = null
    sigmaRefreshRef.current = null
    highlightedNodesRef.current = new Set()
    highlightedEdgesRef.current = new Set()
    hoveredNodeRef.current = null
    isDraggingRef.current = false
    draggedNodeRef.current = null
    dragPositionRef.current = null
    pathSourceRef.current = null
    pathNodesRef.current = new Set()
    pathEdgesRef.current = new Set()
    // refs from useForceLayout are stable; exhaustive-deps can't see that
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Apply an incremental node delta to the live graphology instance:
  //   - drop removed nodes (graphology cascades incident edges → removed links
  //     vanish instantly)
  //   - add new nodes positioned near their existing neighbors, or on the
  //     perimeter if they have none
  //   - refresh labels for kept (possibly renamed) nodes
  //   - reheat so the layout settles from current positions
  // Size/color for new + changed nodes are corrected by the size/color effects
  // via nodeDeltaVersion (bumped by the caller) — not here.
  const applyNodeDelta = useCallback((graph: AbstractGraph, d: GraphData, delta: NodeDelta) => {
    // If the dragged node was removed, stop dragging it (so mousemovebody and
    // the layout loop don't write to a missing node). Keep isDraggingRef true so
    // the mouseup handler still runs its full cleanup (cursor reset + reheat).
    if (draggedNodeRef.current && delta.removed.includes(draggedNodeRef.current)) {
      draggedNodeRef.current = null
      dragPositionRef.current = null
    }

    for (const id of delta.removed) {
      if (graph.hasNode(id)) graph.dropNode(id)
      simVelocitiesRef.current.delete(id) // don't let stale entries grow the map
    }

    // Placement seed = surviving nodes' current positions, so new nodes land
    // near existing neighbors; isolated new nodes fall to the perimeter.
    const visibleIds = new Set<string>(graph.nodes() as string[])
    for (const n of delta.added) visibleIds.add(n.id)
    const adjacency = buildAdjacency(d.edges, visibleIds)
    const placed = seedPlacementFrom((id: string) => {
      const a = graph.getNodeAttributes(id) as { x: number; y: number }
      return { x: a.x, y: a.y }
    }, graph.nodes() as string[])
    const bounds = graphBounds(placed)

    // Place well-connected new nodes first so later siblings can cluster on them.
    const addedSorted = [...delta.added].sort(
      (a, b) => (adjacency.get(b.id)?.size ?? 0) - (adjacency.get(a.id)?.size ?? 0)
    )
    for (const n of addedSorted) {
      if (graph.hasNode(n.id)) continue
      const { x, y } = positionNewNode(n.id, placed, adjacency, bounds)
      const col = getNodeColor(n.type)
      // Fallback size/color; the size + color effects overwrite with the
      // mode-correct values on the next commit (nodeDeltaVersion dep).
      graph.addNode(n.id, {
        label: n.title, color: col, size: getNodeSize(n.incoming_links),
        x, y, nodeType: n.type, originalColor: col,
      })
      placed.set(n.id, { x, y })
      simVelocitiesRef.current.set(n.id, { vx: 0, vy: 0 }) // add only — never clear()
    }

    // Kept nodes: refresh label for renames (there is no dedicated label effect).
    for (const n of delta.kept) {
      const attrs = graph.getNodeAttributes(n.id) as { label?: string }
      if (attrs.label !== n.title) graph.setNodeAttribute(n.id, 'label', n.title)
    }

    sigmaRef.current?.refresh()
    reheat()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reheat])

  useEffect(() => {
    if (!containerRef.current || !data) return

    // INCREMENTAL DELTA — a sigma/graphology instance already exists. Instead of
    // tearing it down (which discards the camera and the converged layout), mutate
    // it: drop removed nodes, add new ones near their existing neighbors, reheat.
    // Falls back to the full bootstrap below when there is no instance yet or the
    // turnover is large (e.g. a vault switch).
    if (sigmaRef.current && graphRef.current) {
      const graph = graphRef.current
      const currentIds = new Set(graph.nodes() as string[])
      const newVisible = computeVisibleNodes(data.nodes, activeTypes, showDaily)
      const delta = computeNodeDelta(currentIds, newVisible)

      if (shouldFullRebuild(delta, currentIds.size)) {
        teardownInstance()   // large turnover → discard and re-bootstrap below
      } else {
        applyNodeDelta(graph, data, delta)
        setNodeDeltaVersion(v => v + 1)
        return () => { /* delta is synchronous; never kill sigma on a data change */ }
      }
    }

    let cancelled = false

    const init = async () => {
      const { default: SigmaClass } = await import('sigma')
      const { MultiGraph } = await import('graphology')

      if (cancelled) return

      const graph = new MultiGraph()

      const visibleNodes = new Set<string>()
      const visibleNodeList: typeof data.nodes = []
      for (const node of data.nodes) {
        if (!showDaily && node.folder === 'Daily') continue
        if (!activeTypes.has(node.type)) continue
        visibleNodes.add(node.id)
        visibleNodeList.push(node)
      }

      const adjacency = new Map<string, Set<string>>()
      for (const edge of data.edges) {
        if (!visibleNodes.has(edge.s) || !visibleNodes.has(edge.t)) continue
        if (!adjacency.has(edge.s)) adjacency.set(edge.s, new Set())
        if (!adjacency.has(edge.t)) adjacency.set(edge.t, new Set())
        adjacency.get(edge.s)!.add(edge.t)
        adjacency.get(edge.t)!.add(edge.s)
      }

      visibleNodeList.sort((a, b) => (adjacency.get(b.id)?.size ?? 0) - (adjacency.get(a.id)?.size ?? 0))

      const initRecencyMap = nodeSizeModeRef.current === 'recency'
        ? buildRecencySizeMap(visibleNodeList.map(n => ({ id: n.id, mtime: n.mtime })))
        : null

      const initColorMap = nodeColorModeRef.current === 'recency'
        ? buildRecencyColorMap(visibleNodeList.map(n => ({ id: n.id, mtime: n.mtime })))
        : null

      const JITTER = 1.8
      const placed = new Map<string, { x: number; y: number }>()

      for (const node of visibleNodeList) {
        const neighbors = adjacency.get(node.id)
        const placedNeighbors = neighbors
          ? [...neighbors].map(n => placed.get(n)).filter(Boolean) as { x: number; y: number }[]
          : []

        let x: number, y: number
        if (placedNeighbors.length > 0) {
          const cx = placedNeighbors.reduce((s, p) => s + p.x, 0) / placedNeighbors.length
          const cy = placedNeighbors.reduce((s, p) => s + p.y, 0) / placedNeighbors.length
          const angle = Math.random() * Math.PI * 2
          const radius = Math.sqrt(Math.random()) * JITTER
          x = cx + Math.cos(angle) * radius
          y = cy + Math.sin(angle) * radius
        } else {
          x = (Math.random() - 0.5) * 20
          y = (Math.random() - 0.5) * 20
        }

        placed.set(node.id, { x, y })
        const nsMode = nodeSizeModeRef.current
        const nsMap = nodeSizeMapRef.current
        let nodeSize: number
        if (nsMode === 'uniform') {
          nodeSize = 4
        } else if (nsMode === 'betweenness' && nsMap) {
          nodeSize = nsMap.get(node.id) ?? getNodeSize(node.incoming_links)
        } else if (nsMode === 'recency') {
          nodeSize = initRecencyMap?.get(node.id) ?? RECENCY_SIZE_MIN
        } else {
          nodeSize = getNodeSize(node.incoming_links)
        }
        const typeCol = getNodeColor(node.type)
        const nodeColor = initColorMap ? (initColorMap.get(node.id) ?? recencyHeatColor(1)) : typeCol
        graph.addNode(node.id, {
          label: node.title,
          color: nodeColor,
          size: nodeSize,
          x, y,
          nodeType: node.type,
          originalColor: nodeColor,
        })
      }

      const ewi = edgeWeightInfluenceRef.current
      let edges: GraphEdge[] = filterEdges(data.edges, graphSource, threshold)
      if (edgePruningRef.current) edges = pruneEdges(edges, edgePruningKRef.current)
      for (const edge of edges) {
        if (!visibleNodes.has(edge.s) || !visibleNodes.has(edge.t)) continue
        const col = getSemanticEdgeColor(edge.w, edge.kind, edgeColorModeRef.current, thresholdRef.current)
        try {
          graph.addEdge(edge.s, edge.t, {
            weight: edge.w * ewi, baseWeight: edge.w, color: col,
            size: edge.kind === 'wiki' ? 1.5 : 1,
            kind: edge.kind, overlay: false, originalColor: col,
          })
        } catch { /* duplicate */ }
      }
      // Overlay edges (other source, visual-only — weight=0.001 so FA2 ignores them)
      if (showOverlayEdgesRef.current) {
        const overlayKind = graphSource === 'semantic' ? 'wiki' : 'semantic'
        const overlayEdges = data.edges.filter(e => e.kind === overlayKind &&
          (overlayKind === 'semantic' ? e.w >= threshold : true))
        for (const edge of overlayEdges) {
          if (!visibleNodes.has(edge.s) || !visibleNodes.has(edge.t)) continue
          const col = overlayKind === 'wiki' ? 'rgba(123,97,255,0.18)' : 'rgba(150,150,160,0.18)'
          try {
            graph.addEdge(edge.s, edge.t, {
              weight: 0.001, color: col, size: 0.8,
              kind: overlayKind, overlay: true, originalColor: col,
            })
          } catch { /* duplicate */ }
        }
      }

      if (cancelled) return

      // Wire typed reducers — no `any` suppressions needed
      const reducerCtx = {
        graph,
        pathNodesRef,
        pathEdgesRef,
        pathSourceRef,
        labelsOnHoverOnlyRef,
        hoveredNodeRef,
        neighborhoodRef,
        filteredNodesRef,
        hideIsolatedRef,
        highlightedNodesRef,
        highlightedEdgesRef,
      }
      const nodeReducer = makeNodeReducer(reducerCtx)
      const edgeReducer = makeEdgeReducer(reducerCtx)

      const sigma = new SigmaClass(graph, containerRef.current!, {
        renderEdgeLabels: false,
        defaultEdgeColor: 'rgba(150,150,160,0.25)',
        defaultNodeColor: '#6b7280',
        labelFont: 'Oxanium, sans-serif',
        labelSize: 11,
        labelColor: { color: LABEL_COLOR },
        minCameraRatio: 0.05,
        maxCameraRatio: 10,
        // Scale nodes with zoom: shrink when zoomed out, grow when zoomed in.
        // ratio = current camera zoom; returns a multiplier applied to node sizes.
        zoomToSizeRatioFunction: (ratio: number) => ratio,
        nodeReducer,
        edgeReducer,
        defaultDrawNodeLabel: drawNodeLabel,
        defaultDrawNodeHover: drawNodeHover,
      })

      sigmaRef.current = sigma
      graphRef.current = graph
      // Wire layout hook refs to the live instances
      layoutGraphRef.current = graph
      sigmaRefreshRef.current = () => sigma.refresh()

      sigma.on('enterNode', ({ node }: { node: string }) => {
        if (isDraggingRef.current) return
        hoveredNodeRef.current = node
        if (containerRef.current) containerRef.current.style.cursor = 'grab'
        // No sigma.refresh() here — sigma's own scheduleHighlightedNodesRender()
        // handles the hover label via defaultDrawNodeHover. A full refresh of
        // 1500+ nodes on every mouse enter/leave freezes the browser.
      })
      sigma.on('leaveNode', () => {
        if (isDraggingRef.current) return
        hoveredNodeRef.current = null
        if (containerRef.current && !isDraggingRef.current) containerRef.current.style.cursor = ''
      })
      sigma.on('downNode', ({ node }: { node: string }) => {
        isDraggingRef.current = true
        draggedNodeRef.current = node
        dragHasMovedRef.current = false
        hoveredNodeRef.current = null
        if (containerRef.current) containerRef.current.style.cursor = 'grabbing'
        isRunningRef.current = true
        if (!rafRef.current && layoutLoopRef.current) {
          rafRef.current = requestAnimationFrame(layoutLoopRef.current)
        }
      })
      sigma.getMouseCaptor().on('mousemovebody', (e: MouseCoords) => {
        if (!isDraggingRef.current || !draggedNodeRef.current) return
        dragHasMovedRef.current = true
        const pos = sigma.viewportToGraph({ x: e.x, y: e.y })
        dragPositionRef.current = { x: pos.x, y: pos.y }
        graph.setNodeAttribute(draggedNodeRef.current, 'x', pos.x)
        graph.setNodeAttribute(draggedNodeRef.current, 'y', pos.y)
        // Floor temperature so neighbors keep reacting
        temperatureRef.current = Math.max(temperatureRef.current, 0.4)
        e.preventSigmaDefault()
        e.original.preventDefault()
        e.original.stopPropagation()
      })
      sigma.getMouseCaptor().on('mouseup', () => {
        if (!isDraggingRef.current) return
        isDraggingRef.current = false
        draggedNodeRef.current = null
        dragPositionRef.current = null
        hoveredNodeRef.current = null
        if (containerRef.current) containerRef.current.style.cursor = ''
        // Restart async FA2 worker and reheat so graph settles from new positions
        reheat()
      })
      sigma.on('clickNode', ({ node, event }: { node: string; event: { original: MouseEvent | TouchEvent } }) => {
        if (dragHasMovedRef.current) return  // drag, not click
        // shift/cmd/ctrl => open the note; cmd/ctrl => new tab. Read modifiers
        // defensively off event.original (sigma's wrapped native event),
        // falling back to event itself — an instanceof MouseEvent guard proved
        // unreliable here (sigma's wrapper is not always a native MouseEvent).
        const orig = (event.original ?? event) as { shiftKey?: boolean; metaKey?: boolean; ctrlKey?: boolean }
        const open = !!(orig.shiftKey || orig.metaKey || orig.ctrlKey)
        const newTab = !!(orig.metaKey || orig.ctrlKey)
        onNodeClick(node, open, newTab)
        const neighbors = new Set(graph.neighbors(node) as string[])
        neighbors.add(node)
        highlightedNodesRef.current = neighbors
        const neighborEdges = new Set<string>()
        ;(graph.edges(node) as string[]).forEach((e: string) => neighborEdges.add(e))
        highlightedEdgesRef.current = neighborEdges
        sigma.refresh()
        flyToNode(node)
      })
      sigma.on('rightClickNode', ({ node, event }: { node: string; event: { original: MouseEvent | TouchEvent } }) => {
        const orig = event.original
        if (orig instanceof MouseEvent) orig.preventDefault()
        const x = orig instanceof MouseEvent ? orig.clientX : 0
        const y = orig instanceof MouseEvent ? orig.clientY : 0
        setNodeContextMenu({ stem: node, x, y })
      })
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

      // Initialize velocity map for all nodes
      const velocities = simVelocitiesRef.current
      velocities.clear()
      graph.forEachNode((node: string) => {
        velocities.set(node, { vx: 0, vy: 0 })
      })

      // Build and start the physics loop via the extracted factory
      buildLayoutLoop({
        graphRef: layoutGraphRef,
        sigmaRefreshRef,
        rafRef,
        isRunningRef,
        temperatureRef,
        simVelocitiesRef,
        layoutParamsRef,
        coolingRateRef,
        stopThresholdRef,
        filteredNodesRef,
        neighborhoodRef,
        hideIsolatedRef,
        isDraggingRef,
        draggedNodeRef,
        dragPositionRef,
        onLayoutStopRef: layout.onLayoutStopRef,
        layoutLoopRef,
      })

      if (isRunningRef.current) {
        rafRef.current = requestAnimationFrame(layoutLoopRef.current!)
      }
    }

    init().catch(console.error)

    return () => {
      cancelled = true
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
      // NOTE: sigma is NOT killed here. On a data change we apply an incremental
      // delta (above) or re-bootstrap after teardownInstance(); killing here would
      // discard the camera + converged layout on every graph.json rebuild — the
      // exact regression incremental updates exist to prevent. Teardown happens
      // only on unmount (the effect below) or via teardownInstance().
    }
  // QA-017: Intentionally only depends on `data`. The effect bootstraps the
  // Sigma/graphology instance on first load (or after a large turnover), and
  // applies an incremental delta otherwise. Including all prop dependencies
  // (threshold, activeTypes, etc.) would re-run it on every slider change. The
  // delta branch also reads activeTypes/showDaily from this closure — safe because
  // they only matter when `data` changes (filter-toggle live updates are a
  // separate, currently-unimplemented concern).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data])

  // Unmount-only teardown. The [data] effect's cleanup no longer kills sigma
  // (so incremental updates preserve the instance); this effect owns the kill.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => () => teardownInstance(), [])

  useEffect(() => {
    if (!sigmaRef.current || !graphRef.current || !data) return
    const graph = graphRef.current
    graph.clearEdges()
    const visibleNodes = new Set(graph.nodes() as string[])
    const ewi = edgeWeightInfluenceRef.current
    let edges: GraphEdge[] = filterEdges(data.edges, graphSource, threshold)
    if (edgePruningRef.current) edges = pruneEdges(edges, edgePruningKRef.current)
    for (const edge of edges) {
      if (!visibleNodes.has(edge.s) || !visibleNodes.has(edge.t)) continue
      const col = getSemanticEdgeColor(edge.w, edge.kind, edgeColorModeRef.current)
      try {
        graph.addEdge(edge.s, edge.t, {
          weight: edge.w * ewi, baseWeight: edge.w, color: col,
          size: edge.kind === 'wiki' ? 1.5 : 1,
          kind: edge.kind, overlay: false, originalColor: col,
        })
      } catch { /* skip */ }
    }
    if (showOverlayEdgesRef.current) {
      const overlayKind = graphSource === 'semantic' ? 'wiki' : 'semantic'
      const overlayEdges = data.edges.filter(e => e.kind === overlayKind &&
        (overlayKind === 'semantic' ? e.w >= threshold : true))
      for (const edge of overlayEdges) {
        if (!visibleNodes.has(edge.s) || !visibleNodes.has(edge.t)) continue
        const col = overlayKind === 'wiki' ? 'rgba(123,97,255,0.18)' : 'rgba(150,150,160,0.18)'
        try {
          graph.addEdge(edge.s, edge.t, {
            weight: 0.001, color: col, size: 0.8,
            kind: overlayKind, overlay: true, originalColor: col,
          })
        } catch { /* skip */ }
      }
    }
    highlightedNodesRef.current = new Set()
    highlightedEdgesRef.current = new Set()
    sigmaRef.current.refresh()
    reheat()
  // Note: edgePruning/edgePruningK are in the dep array intentionally — unlike edgeWeightInfluence
  // (which updates weights on existing edges and therefore only needs a ref), pruning requires a
  // full edge rebuild via graph.clearEdges(). The effect must re-run when pruning toggles or K
  // changes, so these must be real deps rather than ref-only values.
  }, [threshold, graphSource, data, reheat, edgePruning, edgePruningK])

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative' }}>
      <div
        ref={containerRef}
        style={{ width: '100%', height: '100%', background: 'transparent' }}
      />
      {nodeContextMenu && (() => {
        // Capture ref value once per render — prevents stale comparisons in JSX conditionals
        const pathSource = pathSourceRef.current
        return (
          <div
            style={{
              position: 'fixed', left: nodeContextMenu.x, top: nodeContextMenu.y,
              background: MENU_BACKGROUND, border: `1px solid ${MENU_BORDER}`, borderRadius: 4,
              zIndex: 1000, minWidth: 160, boxShadow: '0 4px 16px rgba(0,0,0,0.6)',
              fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
            }}
            onClick={e => e.stopPropagation()}
          >
            <div
              style={{ padding: '6px 12px', cursor: 'pointer', color: '#ccc' }}
              onMouseEnter={e => (e.currentTarget.style.background = MENU_BORDER)}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
              onClick={() => { onNodeClick(nodeContextMenu.stem, true, false); setNodeContextMenu(null) }}
            >
              Open in Reading Pane
            </div>
            {onOpenHistory && (
              <div
                style={{ padding: '6px 12px', cursor: 'pointer', color: ACCENT_TEAL }}
                onMouseEnter={e => (e.currentTarget.style.background = MENU_BORDER)}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                onClick={() => { onOpenHistory!(nodeContextMenu.stem); setNodeContextMenu(null) }}
              >
                View History
              </div>
            )}
            {/* Path finder */}
            <div style={{ borderTop: '1px solid rgba(255,255,255,0.08)', margin: '2px 0' }} />
            {pathSource && pathSource !== nodeContextMenu.stem && (
              <div
                style={{ padding: '6px 12px', cursor: 'pointer', color: HIGHLIGHT_COLOR }}
                onMouseEnter={e => (e.currentTarget.style.background = MENU_BORDER)}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                onClick={() => {
                  if (!graphRef.current) return
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
                    sigmaRef.current?.refresh()
                    return // keep pathSourceRef set so user can pick a different destination
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
                style={{ padding: '6px 12px', cursor: 'pointer', color: MUTED_NODE_COLOR }}
                onMouseEnter={e => (e.currentTarget.style.background = MENU_BORDER)}
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
                style={{ padding: '6px 12px', cursor: 'pointer', color: pathSource ? '#f59e0b' : MUTED_NODE_COLOR }}
                onMouseEnter={e => (e.currentTarget.style.background = MENU_BORDER)}
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
      {toastMsg && (
        <div style={{
          position: 'absolute', bottom: 24, left: '50%', transform: 'translateX(-50%)',
          background: 'rgba(6, 8, 18, 0.95)',
          border: '1px solid rgba(255, 215, 0, 0.4)',
          borderRadius: 6, padding: '8px 16px',
          color: HIGHLIGHT_COLOR, fontSize: 11,
          fontFamily: "'JetBrains Mono', monospace",
          maxWidth: '80%', textAlign: 'center',
          boxShadow: '0 4px 20px rgba(0,0,0,0.7)',
          zIndex: 500, pointerEvents: 'none',
        }}>
          {toastMsg}
        </div>
      )}
    </div>
  )
})
