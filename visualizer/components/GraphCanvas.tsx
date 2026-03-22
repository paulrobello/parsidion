'use client'

import { useEffect, useRef, useCallback, useMemo, forwardRef, useImperativeHandle } from 'react'
import type { GraphData, GraphSource } from '@/lib/graph'
import { filterEdges } from '@/lib/graph'
import { getNodeColor, getNodeSize } from '@/lib/sigma-colors'

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
  selectedNode: string | null
  onNodeClick: (stem: string, newTab: boolean) => void
  onBackgroundClick: () => void
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

// Per-frame temperature decay: temp *= (1 - COOL_FACTOR * slowDown)
// At slowDown=1 → ~29s to reach 0.005 threshold at 60fps
// At slowDown=5 → ~6s
const COOL_FACTOR = 0.002

export const GraphCanvas = forwardRef<GraphCanvasHandle, Props>(function GraphCanvas(
  {
    data, threshold, graphSource, activeTypes, showDaily, hideIsolated, labelsOnHoverOnly, showOverlayEdges, filterNodesBySimilarity, selectedNode,
    onNodeClick, onBackgroundClick,
    scalingRatio, gravity, slowDown, edgeWeightInfluence, startTemperature, stopThreshold, isLayoutRunning, onLayoutStop, onLayoutRestart,
    neighborhoodCenter, neighborhoodHops,
  },
  ref
) {
  const containerRef = useRef<HTMLDivElement>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const sigmaRef = useRef<any>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const graphRef = useRef<any>(null)
  // Simple force simulation state — velocity per node, persists across frames
  const simVelocitiesRef = useRef<Map<string, { vx: number; vy: number }>>(new Map())

  const layoutParamsRef = useRef({ scalingRatio, gravity })
  const edgeWeightInfluenceRef = useRef(edgeWeightInfluence)
  const coolingRateRef = useRef(slowDown * COOL_FACTOR)
  const startTemperatureRef = useRef(startTemperature)
  const stopThresholdRef = useRef(stopThreshold)
  const isRunningRef = useRef(isLayoutRunning)
  // temperature: 1.0 = fully hot (no damping), 0 = frozen.
  // Each frame: temp *= (1 - coolingRate). Applied as movement scale to FA2 deltas.
  const temperatureRef = useRef(1.0)
  const onLayoutRestartRef = useRef(onLayoutRestart)
  const rafRef = useRef<number | null>(null)
  const layoutLoopRef = useRef<(() => void) | null>(null)

  const hideIsolatedRef = useRef(hideIsolated)
  const labelsOnHoverOnlyRef = useRef(labelsOnHoverOnly)
  const showOverlayEdgesRef = useRef(showOverlayEdges)
  const filteredNodesRef = useRef<Set<string>>(new Set())
  const filterNodesBySimilarityRef = useRef(false)
  const thresholdRef = useRef(threshold)
  const graphSourceRef = useRef(graphSource)
  const dataRef = useRef(data)
  const hoveredNodeRef = useRef<string | null>(null)
  const highlightedNodesRef = useRef<Set<string>>(new Set())
  const highlightedEdgesRef = useRef<Set<string>>(new Set())
  const isDraggingRef = useRef(false)
  const draggedNodeRef = useRef<string | null>(null)
  const dragHasMovedRef = useRef(false)
  const dragPositionRef = useRef<{ x: number; y: number } | null>(null)

  // Compute neighborhood BFS when in local mode
  const neighborhoodInfo = useMemo(() => {
    if (!neighborhoodCenter || !data) return null
    const hops = neighborhoodHops ?? 2
    const distances = new Map<string, number>()
    distances.set(neighborhoodCenter, 0)
    let frontier = [neighborhoodCenter]
    for (let h = 1; h <= hops; h++) {
      const nextFrontier: string[] = []
      for (const nodeId of frontier) {
        for (const edge of data.edges) {
          const other = edge.s === nodeId ? edge.t : edge.t === nodeId ? edge.s : null
          if (other && !distances.has(other)) {
            distances.set(other, h)
            nextFrontier.push(other)
          }
        }
      }
      frontier = nextFrontier
    }
    return { nodes: new Set(distances.keys()), distances, maxHop: hops }
  }, [neighborhoodCenter, neighborhoodHops, data])

  const neighborhoodRef = useRef(neighborhoodInfo)
  useEffect(() => { neighborhoodRef.current = neighborhoodInfo }, [neighborhoodInfo])

  useEffect(() => {
    sigmaRef.current?.refresh()
  }, [neighborhoodCenter, neighborhoodHops])

  useEffect(() => { onLayoutRestartRef.current = onLayoutRestart }, [onLayoutRestart])
  useEffect(() => { thresholdRef.current = threshold }, [threshold])
  useEffect(() => { graphSourceRef.current = graphSource }, [graphSource])
  useEffect(() => { dataRef.current = data }, [data])
  useEffect(() => {
    hideIsolatedRef.current = hideIsolated
    sigmaRef.current?.refresh()
  }, [hideIsolated])
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

  const reheat = useCallback(() => {
    temperatureRef.current = startTemperatureRef.current
    // Reset velocities so old momentum doesn't fight the new force balance
    simVelocitiesRef.current.forEach((v) => { v.vx = 0; v.vy = 0 })
    const wasRunning = isRunningRef.current
    isRunningRef.current = true
    if (!rafRef.current && layoutLoopRef.current) {
      rafRef.current = requestAnimationFrame(layoutLoopRef.current)
    }
    if (!wasRunning) onLayoutRestartRef.current?.()
  }, [])

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
  }, [filterNodesBySimilarity, threshold, graphSource, data, reheat])

  useEffect(() => {
    layoutParamsRef.current = { scalingRatio, gravity }
    reheat()
  }, [scalingRatio, gravity, reheat])

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

  useEffect(() => {
    coolingRateRef.current = slowDown * COOL_FACTOR
    reheat()
  }, [slowDown, reheat])

  useEffect(() => {
    startTemperatureRef.current = startTemperature
    reheat()
  }, [startTemperature, reheat])
  useEffect(() => { stopThresholdRef.current = stopThreshold }, [stopThreshold])

  useEffect(() => {
    isRunningRef.current = isLayoutRunning
    if (isLayoutRunning) {
      temperatureRef.current = startTemperatureRef.current
      if (!rafRef.current && layoutLoopRef.current) {
        rafRef.current = requestAnimationFrame(layoutLoopRef.current)
      }
    }
  }, [isLayoutRunning])

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
  const getEnergy = useCallback(() => temperatureRef.current, [])
  useImperativeHandle(ref, () => ({ flyToNode, selectNode, getEnergy }), [flyToNode, selectNode, getEnergy])

  useEffect(() => {
    if (!containerRef.current || !data) return

    let cancelled = false

    const init = async () => {
      const { default: Sigma } = await import('sigma')
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
        graph.addNode(node.id, {
          label: node.title,
          color: getNodeColor(node.type),
          size: getNodeSize(node.incoming_links),
          x, y,
          nodeType: node.type,
          originalColor: getNodeColor(node.type),
        })
      }

      const ewi = edgeWeightInfluenceRef.current
      const edges = filterEdges(data.edges, graphSource, threshold)
      for (const edge of edges) {
        if (!visibleNodes.has(edge.s) || !visibleNodes.has(edge.t)) continue
        const col = edge.kind === 'wiki' ? 'rgba(123,97,255,0.35)' : `rgba(150,150,160,${Math.min(0.45, edge.w * 0.5)})`
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

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const nodeReducer = (node: string, data: any) => {
        const nh = neighborhoodRef.current
        if (nh && !nh.nodes.has(node)) {
          return { ...data, hidden: true, label: '' }
        }
        const fn = filteredNodesRef.current
        if (fn.size > 0 && !fn.has(node)) {
          return { ...data, hidden: true, label: '' }
        }
        if (hideIsolatedRef.current) {
          // When a similarity filter is active, only count edges to other visible
          // (non-filtered-out) neighbors — edgeReducer hides cross-filter edges
          // but graph.degree() still counts them, causing isolated-looking nodes.
          const effectiveDegree = fn.size > 0
            ? (graph.neighbors(node) as string[]).filter((n: string) => fn.has(n)).length
            : graph.degree(node)
          if (effectiveDegree === 0) return { ...data, hidden: true, label: '' }
        }
        const hn = highlightedNodesRef.current
        const isHovered = node === hoveredNodeRef.current
        const isHighlighted = hn.size === 0 || hn.has(node)
        const showLabel = labelsOnHoverOnlyRef.current
          ? (isHovered || (hn.size > 0 && hn.has(node)))
          : (hn.size === 0 || isHovered || hn.has(node))
        const label = showLabel ? data.label : ''
        if (!isHighlighted && !isHovered) {
          return { ...data, label, color: '#0d1020', size: data.size * 0.6, zIndex: 0 }
        }
        if (nh) {
          const hopDist = nh.distances.get(node)
          if (hopDist === nh.maxHop) {
            const dimColor = (data.originalColor || data.color) + '66'
            return { ...data, label, color: dimColor, size: data.size * 0.8 }
          }
        }
        return { ...data, label }
      }

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const edgeReducer = (edge: string, data: any) => {
        const nh = neighborhoodRef.current
        if (nh) {
          const src = graph.source(edge)
          const tgt = graph.target(edge)
          if (!nh.nodes.has(src) || !nh.nodes.has(tgt)) return { ...data, hidden: true }
        }
        const fn = filteredNodesRef.current
        if (fn.size > 0) {
          const src = graph.source(edge)
          const tgt = graph.target(edge)
          if (!fn.has(src) || !fn.has(tgt)) return { ...data, hidden: true }
        }
        const he = highlightedEdgesRef.current
        if (he.size === 0 || he.has(edge)) return data
        return { ...data, color: '#0d1020', size: 0.3 }
      }

      const sigma = new Sigma(graph, containerRef.current!, {
        renderEdgeLabels: false,
        defaultEdgeColor: 'rgba(150,150,160,0.25)',
        defaultNodeColor: '#6b7280',
        labelFont: 'Oxanium, sans-serif',
        labelSize: 11,
        labelColor: { color: '#FFFFFF' },
        minCameraRatio: 0.05,
        maxCameraRatio: 10,
        // Scale nodes with zoom: shrink when zoomed out, grow when zoomed in.
        // ratio = current camera zoom; returns a multiplier applied to node sizes.
        zoomToSizeRatioFunction: (ratio: number) => ratio,
        nodeReducer,
        edgeReducer,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        defaultDrawNodeLabel: (context: CanvasRenderingContext2D, data: any, settings: any) => {
          if (!data.label) return
          const size: number = settings.labelSize ?? 11
          const font: string = settings.labelFont ?? 'sans-serif'
          context.font = `700 ${size}px ${font}`
          const x = data.x + data.size + 3
          const y = data.y + size / 4
          context.lineJoin = 'round'
          context.lineWidth = 3
          context.strokeStyle = 'rgba(3, 4, 10, 0.95)'
          context.strokeText(data.label, x, y)
          context.fillStyle = '#FFFFFF'
          context.fillText(data.label, x, y)
        },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        defaultDrawNodeHover: (context: CanvasRenderingContext2D, data: any, settings: any) => {
          if (!data.label) return
          const size: number = settings.labelSize ?? 11
          const font: string = settings.labelFont ?? 'sans-serif'
          context.font = `700 ${size}px ${font}`
          const x = data.x + data.size + 3
          const y = data.y + size / 4
          context.lineJoin = 'round'
          context.lineWidth = 3
          context.strokeStyle = 'rgba(3, 4, 10, 0.95)'
          context.strokeText(data.label, x, y)
          context.fillStyle = '#f97316'
          context.fillText(data.label, x, y)
        },
      })

      sigmaRef.current = sigma
      graphRef.current = graph

      sigma.on('enterNode', ({ node }: { node: string }) => {
        hoveredNodeRef.current = node
        if (containerRef.current) containerRef.current.style.cursor = 'grab'
        sigma.refresh()
      })
      sigma.on('leaveNode', () => {
        hoveredNodeRef.current = null
        if (containerRef.current && !isDraggingRef.current) containerRef.current.style.cursor = ''
        sigma.refresh()
      })
      sigma.on('downNode', ({ node }: { node: string }) => {
        isDraggingRef.current = true
        draggedNodeRef.current = node
        dragHasMovedRef.current = false
        if (containerRef.current) containerRef.current.style.cursor = 'grabbing'
        isRunningRef.current = true
        if (!rafRef.current && layoutLoopRef.current) {
          rafRef.current = requestAnimationFrame(layoutLoopRef.current)
        }
      })
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      sigma.getMouseCaptor().on('mousemovebody', (e: any) => {
        if (!isDraggingRef.current || !draggedNodeRef.current) return
        dragHasMovedRef.current = true
        const pos = sigma.viewportToGraph(e)
        dragPositionRef.current = { x: pos.x, y: pos.y }
        graph.setNodeAttribute(draggedNodeRef.current, 'x', pos.x)
        graph.setNodeAttribute(draggedNodeRef.current, 'y', pos.y)
        // Floor temperature so neighbors keep reacting
        temperatureRef.current = Math.max(temperatureRef.current, 0.4)
        e.preventSigmaDefault()
        e.original.preventDefault()
        e.original.stopPropagation()
      })
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      sigma.getMouseCaptor().on('mouseup', (_e: any) => {
        if (!isDraggingRef.current) return
        isDraggingRef.current = false
        draggedNodeRef.current = null
        dragPositionRef.current = null
        if (containerRef.current) {
          containerRef.current.style.cursor = hoveredNodeRef.current ? 'grab' : ''
        }
        // Restart async FA2 worker and reheat so graph settles from new positions
        reheat()
      })
      sigma.on('clickNode', ({ node, event }: { node: string; event: { original: MouseEvent | TouchEvent } }) => {
        if (dragHasMovedRef.current) return  // drag, not click
        const orig = event.original
        const newTab = orig instanceof MouseEvent ? (orig.metaKey || orig.ctrlKey) : false
        onNodeClick(node, newTab)
        const neighbors = new Set(graph.neighbors(node) as string[])
        neighbors.add(node)
        highlightedNodesRef.current = neighbors
        const neighborEdges = new Set<string>()
        ;(graph.edges(node) as string[]).forEach((e: string) => neighborEdges.add(e))
        highlightedEdgesRef.current = neighborEdges
        sigma.refresh()
        flyToNode(node)
      })
      sigma.on('clickStage', () => {
        onBackgroundClick()
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

      // Simple force-directed simulation — no FA2, no adaptive state,
      // no hidden convergence issues. Just Newtonian physics + damping.
      const DAMPING = 0.85       // velocity decay per frame
      const DT = 0.005          // time step
      const MIN_DIST = 0.5      // prevent division by zero / extreme forces

      const layoutLoop = () => {
        if (!isRunningRef.current || !graphRef.current || !sigmaRef.current) {
          rafRef.current = null
          return
        }

        const g = graphRef.current
        const p = layoutParamsRef.current

        // Build set of VISIBLE nodes — same logic as nodeReducer.
        // Hidden nodes must not participate in physics at all.
        const fn = filteredNodesRef.current
        const allNodes = g.nodes() as string[]
        const visibleSet = new Set<string>()
        for (const n of allNodes) {
          // Similarity filter: if active, only nodes in the filtered set are visible
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
          const dp = dragPositionRef.current
          g.setNodeAttribute(dn, 'x', dp.x)
          g.setNodeAttribute(dn, 'y', dp.y)
          velocities.set(dn, { vx: 0, vy: 0 })
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
          sigmaRef.current.refresh()
          return
        }

        sigmaRef.current.refresh()
        rafRef.current = requestAnimationFrame(layoutLoop)
      }

      layoutLoopRef.current = layoutLoop
      if (isRunningRef.current) {
        rafRef.current = requestAnimationFrame(layoutLoop)
      }
    }

    init().catch(console.error)

    return () => {
      cancelled = true
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
      layoutLoopRef.current = null
      simVelocitiesRef.current.clear()
      sigmaRef.current?.kill()
      sigmaRef.current = null
      graphRef.current = null
      highlightedNodesRef.current = new Set()
      highlightedEdgesRef.current = new Set()
      hoveredNodeRef.current = null
      isDraggingRef.current = false
      draggedNodeRef.current = null
      dragPositionRef.current = null
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data])

  useEffect(() => {
    if (!sigmaRef.current || !graphRef.current || !data) return
    const graph = graphRef.current
    graph.clearEdges()
    const visibleNodes = new Set(graph.nodes() as string[])
    const ewi = edgeWeightInfluenceRef.current
    const edges = filterEdges(data.edges, graphSource, threshold)
    for (const edge of edges) {
      if (!visibleNodes.has(edge.s) || !visibleNodes.has(edge.t)) continue
      const col = edge.kind === 'wiki' ? 'rgba(123,97,255,0.35)' : `rgba(150,150,160,${Math.min(0.45, edge.w * 0.5)})`
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
  }, [threshold, graphSource, data, reheat])

  return (
    <div
      ref={containerRef}
      style={{ width: '100%', height: '100%', background: 'transparent' }}
    />
  )
})
