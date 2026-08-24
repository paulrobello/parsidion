'use client'

import { useEffect, useRef, useCallback, useMemo, useState, forwardRef, useImperativeHandle } from 'react'
import type { GraphData, GraphEdge, GraphSource } from '@/lib/graph'
import { filterEdges } from '@/lib/graph'
import { addOverlayEdges, addPrimaryEdges } from '@/lib/graphEdges'
import {
  getNodeColor, getNodeSize, getSemanticEdgeColor, recencyHeatColor,
  HIGHLIGHT_COLOR, MUTED_NODE_COLOR,
  MENU_BACKGROUND, MENU_BORDER, ACCENT_TEAL,
} from '@/lib/sigma-colors'
import type { EdgeColorMode, NodeSizeMode, NodeColorMode } from '@/lib/sigma-colors'
import type { AbstractGraph } from 'graphology-types'
import type { NeighborhoodInfo } from '@/lib/useGraphReducers'
import {
  useForceLayout,
  buildRecencySizeMap, buildRecencyColorMap, pruneEdges,
  RECENCY_SIZE_MIN,
} from '@/lib/useForceLayout'
import {
  computeVisibleNodes, computeNodeDelta,
  buildAdjacency, seedPlacementFrom, graphBounds, positionNewNode,
} from '@/lib/graphDelta'
import type { NodeDelta } from '@/lib/graphDelta'
import { useSigmaInstance } from '@/lib/useSigmaInstance'
import type { RenderOptions } from '@/lib/useSigmaInstance'
import { useGraphCanvasInteractions, type NodeContextMenuState } from '@/lib/useGraphCanvasInteractions'

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

export const GraphCanvas = forwardRef<GraphCanvasHandle, Props>(function GraphCanvas(
  {
    data, threshold, graphSource, activeTypes, showDaily, hideIsolated, labelsOnHoverOnly, showOverlayEdges, filterNodesBySimilarity, edgeColorMode, edgePruning, edgePruningK, nodeSizeMode, nodeColorMode, nodeSizeMap,
    onNodeClick, onBackgroundClick, onOpenHistory,
    scalingRatio, gravity, slowDown, edgeWeightInfluence, startTemperature, stopThreshold, isLayoutRunning, onLayoutStop, onLayoutRestart,
    neighborhoodCenter, neighborhoodHops,
  },
  ref
) {
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

  // QA-013: every render-option value that a sigma callback or an effect
  // (whose deps deliberately omit it) reads as "latest" lives on ONE ref.
  // This replaces ~a dozen per-prop mirror effects that were easy to forget
  // (a forgotten mirror = silently-stale value inside a sigma callback).
  // useRef — not a custom helper — so the react-hooks linter treats `latest`
  // as the stable ref it is; the single no-deps effect below keeps it current.
  const renderOptions = {
    threshold, graphSource, data,
    edgeColorMode, edgePruning, edgePruningK,
    nodeSizeMode, nodeColorMode, nodeSizeMap,
    edgeWeightInfluence, showOverlayEdges,
  }
  const latest = useRef<RenderOptions>(renderOptions)
  useEffect(() => { latest.current = renderOptions })
  // Read by the node/edge reducers, which need a RefObject<boolean>.
  const labelsOnHoverOnlyRef = useRef(labelsOnHoverOnly)
  const hoveredNodeRef = useRef<string | null>(null)
  const highlightedNodesRef = useRef<Set<string>>(new Set())
  const highlightedEdgesRef = useRef<Set<string>>(new Set())
  const dragHasMovedRef = useRef(false)

  const [nodeContextMenu, setNodeContextMenu] = useState<NodeContextMenuState | null>(null)
  // Bumped after each incremental node delta so the size + color effects re-run
  // and size/color newly-added and changed nodes (their deps are otherwise
  // mode-only, so they'd skip a pure data change). See applyNodeDelta.
  const [nodeDeltaVersion, setNodeDeltaVersion] = useState(0)

  const pathSourceRef = useRef<string | null>(null)
  const pathNodesRef = useRef<Set<string>>(new Set())
  const pathEdgesRef = useRef<Set<string>>(new Set())

  // QA-013b: sigma/graphology instance lifecycle extracted into useSigmaInstance.
  // flyToNode/applyNodeDelta are passed via refs because they read sigmaRef/graphRef
  // (owned by the hook) — a direct callback option would create a circular dep.
  const flyToNodeRef = useRef<((stem: string) => void) | undefined>(undefined)
  const applyNodeDeltaRef = useRef<((graph: AbstractGraph, d: GraphData, delta: NodeDelta) => void) | undefined>(undefined)

  const { sigmaRef, graphRef, containerRef } = useSigmaInstance({
    // Layout hook refs
    layoutGraphRef, sigmaRefreshRef, simVelocitiesRef, layoutLoopRef, rafRef,
    temperatureRef, isRunningRef, layoutParamsRef, coolingRateRef, stopThresholdRef,
    filteredNodesRef, neighborhoodRef, hideIsolatedRef,
    isDraggingRef, draggedNodeRef, dragPositionRef,
    onLayoutStopRef: layout.onLayoutStopRef, reheat,
    // GraphCanvas-owned refs
    labelsOnHoverOnlyRef, hoveredNodeRef, highlightedNodesRef, highlightedEdgesRef,
    dragHasMovedRef, pathSourceRef, pathNodesRef, pathEdgesRef, latest,
    // State setters / callbacks (via refs)
    setNodeContextMenu, applyNodeDeltaRef, flyToNodeRef,
    // Props read by the bootstrap closure
    data, activeTypes, showDaily, graphSource, threshold, onNodeClick, onBackgroundClick,
  })

  // QA-008: context-menu actions, path-finding, and toast extracted into a hook.
  const interactions = useGraphCanvasInteractions({
    graphRef, sigmaRef, latest,
    setNodeContextMenu, pathSourceRef, pathNodesRef, pathEdgesRef,
    onNodeClick, onOpenHistory,
  })

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
      // QA-006: pull the adjacency lists into locals so TS can narrow them
      // to string[] without a `!` assertion. The prior `wikiAdj.get(...)!`
      // was provably safe (the key was set on the line above) but fragile.
      let sAdj = wikiAdj.get(edge.s)
      let tAdj = wikiAdj.get(edge.t)
      if (!sAdj) { sAdj = []; wikiAdj.set(edge.s, sAdj) }
      if (!tAdj) { tAdj = []; wikiAdj.set(edge.t, tAdj) }
      sAdj.push(edge.t)
      tAdj.push(edge.s)
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

  useEffect(() => {
    sigmaRef.current?.refresh()
  }, [neighborhoodCenter, neighborhoodHops, sigmaRef])

  useEffect(() => {
    hideIsolatedRef.current = hideIsolated
    sigmaRef.current?.refresh()
  }, [hideIsolated, hideIsolatedRef, sigmaRef])
  useEffect(() => {
    labelsOnHoverOnlyRef.current = labelsOnHoverOnly
    sigmaRef.current?.refresh()
  }, [labelsOnHoverOnly, sigmaRef])
  useEffect(() => {
    const graph = graphRef.current
    const sigma = sigmaRef.current
    const d = latest.current.data
    if (!graph || !sigma || !d) return
    // Remove existing overlay edges
    const toRemove = (graph.edges() as string[]).filter(
      (e: string) => graph.getEdgeAttribute(e, 'overlay') === true
    )
    toRemove.forEach((e: string) => graph.dropEdge(e))
    // Add new overlay edges if enabled — no reheat
    if (showOverlayEdges) {
      const visibleNodes = new Set(graph.nodes() as string[])
      addOverlayEdges(graph, d.edges, latest.current.graphSource, latest.current.threshold, visibleNodes)
    }
    sigma.refresh()
  }, [showOverlayEdges, graphRef, sigmaRef])

  // Recompute similarity-filtered node set; reheat so newly visible/hidden nodes settle
  useEffect(() => {
    const d = latest.current.data
    if (!filterNodesBySimilarity || latest.current.graphSource !== 'wiki' || !d) {
      filteredNodesRef.current = new Set()
    } else {
      const qualifying = new Set<string>()
      for (const edge of d.edges) {
        if (edge.kind === 'semantic' && edge.w >= latest.current.threshold) {
          qualifying.add(edge.s)
          qualifying.add(edge.t)
        }
      }
      filteredNodesRef.current = qualifying
    }
    sigmaRef.current?.refresh()
    reheat()
  }, [filterNodesBySimilarity, threshold, graphSource, data, reheat, filteredNodesRef, sigmaRef])

  // Edge weight influence acts as a direct weight multiplier on graph edges.
  useEffect(() => {
    const graph = graphRef.current
    if (!graph) return
    ;(graph.edges() as string[]).forEach((e: string) => {
      if (graph.getEdgeAttribute(e, 'overlay')) return
      const base = graph.getEdgeAttribute(e, 'baseWeight') as number
      if (base != null) graph.setEdgeAttribute(e, 'weight', base * edgeWeightInfluence)
    })
    reheat()
  }, [edgeWeightInfluence, reheat, graphRef])

  useEffect(() => {
    const graph = graphRef.current
    const sigma = sigmaRef.current
    const d = latest.current.data
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
  }, [nodeSizeMode, nodeSizeMap, nodeDeltaVersion, graphRef, sigmaRef])

  // Recolor nodes when the color mode toggles. Color has no physics effect, so
  // this is a refresh-only update — do NOT call reheat().
  useEffect(() => {
    const graph = graphRef.current
    const sigma = sigmaRef.current
    const d = latest.current.data
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
  }, [nodeColorMode, nodeDeltaVersion, graphRef, sigmaRef])

  useEffect(() => {
    const graph = graphRef.current
    const sigma = sigmaRef.current
    if (!graph || !sigma) return
    ;(graph.edges() as string[]).forEach((e: string) => {
      if (graph.getEdgeAttribute(e, 'overlay')) return
      const kind = graph.getEdgeAttribute(e, 'kind') as 'wiki' | 'semantic'
      if (kind === 'wiki') return
      const baseWeight = graph.getEdgeAttribute(e, 'baseWeight') as number
      const col = getSemanticEdgeColor(baseWeight, kind, edgeColorMode, latest.current.threshold)
      graph.setEdgeAttribute(e, 'color', col)
      graph.setEdgeAttribute(e, 'originalColor', col)
    })
    sigma.refresh()
  }, [edgeColorMode, threshold, graphRef, sigmaRef])

  const flyToNode = useCallback((stem: string) => {
    if (!sigmaRef.current || !graphRef.current) return
    if (!graphRef.current.hasNode(stem)) return
    const nodePos = sigmaRef.current.getNodeDisplayData(stem)
    if (!nodePos) return
    sigmaRef.current.getCamera().animate(
      { x: nodePos.x, y: nodePos.y, ratio: 0.3 },
      { duration: 600, easing: 'cubicInOut' }
    )
  }, [graphRef, sigmaRef])
  useEffect(() => { flyToNodeRef.current = flyToNode }, [flyToNode])

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
  }, [graphRef, sigmaRef])

  // temperature IS the energy metric exposed to the temperature bar
  const getEnergy = useCallback(() => temperatureRef.current, [temperatureRef])
  useImperativeHandle(ref, () => ({ flyToNode, selectNode, getEnergy }), [flyToNode, selectNode, getEnergy])

  // Apply an incremental node delta to the live graphology instance:
  //   - drop removed nodes (graphology cascades incident edges → removed links
  //     vanish instantly)
  //   - add new nodes positioned near their existing neighbors, or on the
  //     perimeter if they have none
  //   - refresh labels for kept (possibly renamed) nodes
  //   - reheat so the layout settles from current positions
  // Size/color for new + changed nodes are corrected by the size/color effects
  // via nodeDeltaVersion — bumped here so every caller gets the re-render.
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
    setNodeDeltaVersion(v => v + 1)
  }, [reheat, setNodeDeltaVersion, dragPositionRef, draggedNodeRef, sigmaRef, simVelocitiesRef])
  useEffect(() => { applyNodeDeltaRef.current = applyNodeDelta }, [applyNodeDelta])

  // Apply activeTypes/showDaily toggle changes immediately via the same delta
  // path used for graph.json rebuilds — otherwise a chip toggle only takes
  // effect on the next data reload.
  useEffect(() => {
    const graph = graphRef.current
    const d = latest.current.data
    if (!graph || !d) return
    const currentIds = new Set(graph.nodes() as string[])
    const newVisible = computeVisibleNodes(d.nodes, activeTypes, showDaily)
    const delta = computeNodeDelta(currentIds, newVisible)
    if (delta.added.length === 0 && delta.removed.length === 0) return
    applyNodeDelta(graph, d, delta)
  }, [activeTypes, showDaily, applyNodeDelta, graphRef])

  useEffect(() => {
    if (!sigmaRef.current || !graphRef.current || !data) return
    const graph = graphRef.current
    graph.clearEdges()
    const visibleNodes = new Set(graph.nodes() as string[])
    const ewi = latest.current.edgeWeightInfluence
    let edges: GraphEdge[] = filterEdges(data.edges, graphSource, threshold)
    if (latest.current.edgePruning) edges = pruneEdges(edges, latest.current.edgePruningK)
    addPrimaryEdges(graph, edges, {
      visibleNodes,
      edgeWeightInfluence: ewi,
      edgeColorMode: latest.current.edgeColorMode,
      threshold: latest.current.threshold,
    })
    if (latest.current.showOverlayEdges) {
      addOverlayEdges(graph, data.edges, graphSource, threshold, visibleNodes)
    }
    highlightedNodesRef.current = new Set()
    highlightedEdgesRef.current = new Set()
    pathSourceRef.current = null
    pathNodesRef.current = new Set()
    pathEdgesRef.current = new Set()
    sigmaRef.current.refresh()
    reheat()
  // Note: edgePruning/edgePruningK are in the dep array intentionally — unlike edgeWeightInfluence
  // (which updates weights on existing edges and therefore only needs a ref), pruning requires a
  // full edge rebuild via graph.clearEdges(). The effect must re-run when pruning toggles or K
  // changes, so these must be real deps rather than ref-only values.
  }, [threshold, graphSource, data, reheat, edgePruning, edgePruningK, graphRef, sigmaRef])

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative' }}>
      <div
        ref={containerRef}
        style={{ width: '100%', height: '100%', background: 'transparent' }}
      />
      {nodeContextMenu && (() => {
        // Captured at menu-open time (state), so no ref access during render.
        const { pathSource } = nodeContextMenu
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
              onClick={() => interactions.openInReadingPane(nodeContextMenu.stem)}
            >
              Open in Reading Pane
            </div>
            {onOpenHistory && (
              <div
                style={{ padding: '6px 12px', cursor: 'pointer', color: ACCENT_TEAL }}
                onMouseEnter={e => (e.currentTarget.style.background = MENU_BORDER)}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                onClick={() => interactions.viewHistory(nodeContextMenu.stem)}
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
                onClick={() => interactions.findPathTo(nodeContextMenu.stem)}
              >
                ⚡ Find Path Here
              </div>
            )}
            {pathSource === nodeContextMenu.stem ? (
              <div
                style={{ padding: '6px 12px', cursor: 'pointer', color: MUTED_NODE_COLOR }}
                onMouseEnter={e => (e.currentTarget.style.background = MENU_BORDER)}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                onClick={() => interactions.clearPathOrigin()}
              >
                ✕ Clear Path Origin
              </div>
            ) : (
              <div
                style={{ padding: '6px 12px', cursor: 'pointer', color: pathSource ? '#f59e0b' : MUTED_NODE_COLOR }}
                onMouseEnter={e => (e.currentTarget.style.background = MENU_BORDER)}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                onClick={() => interactions.setPathOrigin(nodeContextMenu.stem)}
              >
                {pathSource
                  ? `Origin: ${pathSource.slice(0, 18)}…`
                  : '◎ Set Path Origin'}
              </div>
            )}
          </div>
        )
      })()}
      {interactions.toastMsg && (
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
          {interactions.toastMsg}
        </div>
      )}
    </div>
  )
})
