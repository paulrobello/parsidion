// ---------------------------------------------------------------------------
// Sigma/graphology instance lifecycle extracted from GraphCanvas.tsx (QA-013b)
//
// Owns the sigma instance, graphology graph, and container refs, plus the
// bootstrap effect (dynamic-imports sigma + graphology, builds the MultiGraph,
// wires reducers, event handlers, and the physics loop) and the unmount
// teardown. The [data] effect applies an incremental delta when an instance
// already exists (preserving camera + converged layout) and falls back to a
// full bootstrap on first load or large turnover.
// ---------------------------------------------------------------------------
import { useRef, useCallback, useEffect } from 'react'
import type { GraphData, GraphEdge, GraphSource } from '@/lib/graph'
import { filterEdges } from '@/lib/graph'
import {
  getNodeColor, getNodeSize, getSemanticEdgeColor, recencyHeatColor, LABEL_COLOR,
} from '@/lib/sigma-colors'
import type { EdgeColorMode, NodeSizeMode, NodeColorMode } from '@/lib/sigma-colors'
import type Sigma from 'sigma'
import type { MouseCoords } from 'sigma/types'
import type { AbstractGraph } from 'graphology-types'
import { drawNodeLabel, drawNodeHover } from '@/lib/sigma-renderers'
import { makeNodeReducer, makeEdgeReducer } from '@/lib/useGraphReducers'
import type { NeighborhoodInfo } from '@/lib/useGraphReducers'
import {
  buildLayoutLoop, buildRecencySizeMap, buildRecencyColorMap, pruneEdges,
  RECENCY_SIZE_MIN,
} from '@/lib/useForceLayout'
import {
  computeVisibleNodes, computeNodeDelta, shouldFullRebuild,
} from '@/lib/graphDelta'
import type { NodeDelta } from '@/lib/graphDelta'

// The shape of the `latest` ref that GraphCanvas owns and passes in. Every
// render-option value that a sigma callback or effect (whose deps deliberately
// omit it) reads as "latest" lives on this single ref.
export interface RenderOptions {
  threshold: number
  graphSource: GraphSource
  data: GraphData
  edgeColorMode: EdgeColorMode
  edgePruning: boolean
  edgePruningK: number
  nodeSizeMode: NodeSizeMode
  nodeColorMode: NodeColorMode
  nodeSizeMap: Map<string, number> | null
  edgeWeightInfluence: number
  showOverlayEdges: boolean
}

export interface SigmaInstanceRefs {
  sigmaRef: React.RefObject<Sigma | null>
  graphRef: React.RefObject<AbstractGraph | null>
  containerRef: React.RefObject<HTMLDivElement | null>
}

export interface UseSigmaInstanceOptions {
  // Layout hook refs (owned by useForceLayout, passed through)
  layoutGraphRef: React.RefObject<AbstractGraph | null>
  sigmaRefreshRef: React.RefObject<(() => void) | null>
  simVelocitiesRef: React.RefObject<Map<string, { vx: number; vy: number }>>
  layoutLoopRef: React.RefObject<(() => void) | null>
  rafRef: React.RefObject<number | null>
  temperatureRef: React.RefObject<number>
  isRunningRef: React.RefObject<boolean>
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
  reheat: () => void

  // GraphCanvas-owned refs (read/written by bootstrap + teardown)
  labelsOnHoverOnlyRef: React.RefObject<boolean>
  hoveredNodeRef: React.RefObject<string | null>
  highlightedNodesRef: React.RefObject<Set<string>>
  highlightedEdgesRef: React.RefObject<Set<string>>
  dragHasMovedRef: React.RefObject<boolean>
  pathSourceRef: React.RefObject<string | null>
  pathNodesRef: React.RefObject<Set<string>>
  pathEdgesRef: React.RefObject<Set<string>>
  latest: React.RefObject<RenderOptions>

  // GraphCanvas-owned state setters / callbacks.
  // flyToNode and applyNodeDelta are passed as refs (not direct callbacks)
  // because they are defined AFTER the hook call in GraphCanvas — they read
  // sigmaRef/graphRef which the hook OWNS and returns. A direct callback option
  // would create a circular dependency (hook needs callback, callback needs
  // hook's ref). The callbackRef pattern breaks the cycle: GraphCanvas assigns
  // the real callbacks to these refs right after defining them; by the time the
  // hook's effects run (post-commit), the refs are populated.
  setNodeContextMenu: React.Dispatch<React.SetStateAction<{ stem: string; x: number; y: number } | null>>
  setNodeDeltaVersion: React.Dispatch<React.SetStateAction<number>>
  applyNodeDeltaRef: React.RefObject<((graph: AbstractGraph, d: GraphData, delta: NodeDelta) => void) | undefined>
  flyToNodeRef: React.RefObject<((stem: string) => void) | undefined>

  // Props read by the bootstrap closure
  data: GraphData
  activeTypes: Set<string>
  showDaily: boolean
  graphSource: GraphSource
  threshold: number
  onNodeClick: (stem: string, open: boolean, newTab: boolean) => void
  onBackgroundClick: () => void
}

export function useSigmaInstance(opts: UseSigmaInstanceOptions): SigmaInstanceRefs {
  const {
    // Layout hook refs
    layoutGraphRef, sigmaRefreshRef, simVelocitiesRef, layoutLoopRef, rafRef,
    temperatureRef, isRunningRef, layoutParamsRef, coolingRateRef, stopThresholdRef,
    filteredNodesRef, neighborhoodRef, hideIsolatedRef,
    isDraggingRef, draggedNodeRef, dragPositionRef,
    onLayoutStopRef, reheat,
    // GraphCanvas-owned refs
    labelsOnHoverOnlyRef, hoveredNodeRef, highlightedNodesRef, highlightedEdgesRef,
    dragHasMovedRef, pathSourceRef, pathNodesRef, pathEdgesRef, latest,
    // State setters / callbacks (flyToNode/applyNodeDelta via refs — see above)
    setNodeContextMenu, setNodeDeltaVersion, applyNodeDeltaRef, flyToNodeRef,
    // Props
    data, activeTypes, showDaily, graphSource, threshold, onNodeClick, onBackgroundClick,
  } = opts

  const containerRef = useRef<HTMLDivElement>(null)
  const sigmaRef = useRef<Sigma | null>(null)
  const graphRef = useRef<AbstractGraph | null>(null)

  // Wire layout hook refs to the live instances.
  // (useForceLayout creates these refs; we alias them here for clarity.)
  // Keep the layout's graphRef in sync — it IS the same ref (shared via the
  // hook), but we also keep sigmaRef local for the rest of GraphCanvas.
  // Wire sigmaRefreshRef → sigma.refresh()
  useEffect(() => {
    layoutGraphRef.current = graphRef.current
  })
  useEffect(() => {
    sigmaRefreshRef.current = () => sigmaRef.current?.refresh()
  })

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
        applyNodeDeltaRef.current!(graph, data, delta)
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

      const initRecencyMap = latest.current.nodeSizeMode === 'recency'
        ? buildRecencySizeMap(visibleNodeList.map(n => ({ id: n.id, mtime: n.mtime })))
        : null

      const initColorMap = latest.current.nodeColorMode === 'recency'
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
        const nsMode = latest.current.nodeSizeMode
        const nsMap = latest.current.nodeSizeMap
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

      const ewi = latest.current.edgeWeightInfluence
      let edges: GraphEdge[] = filterEdges(data.edges, graphSource, threshold)
      if (latest.current.edgePruning) edges = pruneEdges(edges, latest.current.edgePruningK)
      for (const edge of edges) {
        if (!visibleNodes.has(edge.s) || !visibleNodes.has(edge.t)) continue
        const col = getSemanticEdgeColor(edge.w, edge.kind, latest.current.edgeColorMode, latest.current.threshold)
        try {
          graph.addEdge(edge.s, edge.t, {
            weight: edge.w * ewi, baseWeight: edge.w, color: col,
            size: edge.kind === 'wiki' ? 1.5 : 1,
            kind: edge.kind, overlay: false, originalColor: col,
          })
        } catch { /* duplicate */ }
      }
      // Overlay edges (other source, visual-only — weight=0.001 so FA2 ignores them)
      if (latest.current.showOverlayEdges) {
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
        // Floor temperature so a settled sim doesn't immediately re-stop on the
        // loop's first frame — otherwise only the dragged node moves and
        // neighbors never react.
        temperatureRef.current = Math.max(temperatureRef.current, 0.4)
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
        isRunningRef.current = true
        if (!rafRef.current && layoutLoopRef.current) {
          rafRef.current = requestAnimationFrame(layoutLoopRef.current)
        }
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
        flyToNodeRef.current!(node)
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
        onLayoutStopRef,
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
  // they only matter when `data` changes; live toggle updates are handled by the
  // dedicated effect below via the same delta path.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data])

  // Unmount-only teardown. The [data] effect's cleanup no longer kills sigma
  // (so incremental updates preserve the instance); this effect owns the kill.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => () => teardownInstance(), [])

  return { sigmaRef, graphRef, containerRef }
}
