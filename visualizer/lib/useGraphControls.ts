// ARC-037: graph-controls slice extracted from useVisualizerState.ts.
//
// Owns every piece of state that drives how the graph is rendered and which
// nodes/edges are visible:
//   - threshold + graphSource (which edge set to draw)
//   - type/daily/isolated/label filters
//   - simulation physics knobs (ForceAtlas2-ish — see useForceLayout)
//   - edge colour + pruning modes
//   - node size + colour modes, including the deferred betweenness-centrality
//     computation (Brandes algorithm itself lives in lib/betweenness.ts)
//   - HUD summary stats (visible-node count, edge count, avg score) and the
//     richer GraphStats (degree distribution, density, components, top hubs)
//
// The visibility computation (`computeVisibleNodeSet`) is extracted as a pure
// helper because `stats` and `graphStats` used to duplicate the same logic
// inline — a DDR violation that was easy to let drift.
'use client'

import { useState, useCallback, useMemo, useEffect } from 'react'
import { useLocalStorage } from '@/lib/useLocalStorage'
import type { GraphData, GraphSource, NoteNode } from '@/lib/graph'
import { filterEdges } from '@/lib/graph'
import { TYPE_COLORS, EdgeColorMode, NodeSizeMode, NodeColorMode } from '@/lib/sigma-colors'
import { computeBetweenness } from '@/lib/betweenness'

export const SIM_DEFAULTS = {
  scalingRatio: 10,
  gravity: 1,
  slowDown: 0.5,
  edgeWeightInfluence: 2,
  startTemperature: 0.8,
  stopThreshold: 0.01,
}

export interface GraphStats {
  avgDegree: number
  maxDegree: number
  topHubs: Array<{ id: string; title: string; degree: number }>
  density: number
  componentCount: number
}

export interface VisibleNodesOpts {
  showDaily: boolean
  activeTypes: Set<string>
  filterNodesBySimilarity: boolean
  graphSource: GraphSource
  threshold: number
}

/**
 * Compute the set of visible node ids under the current filter combination.
 *
 * Pure (no React) so the two stats memos below can share the exact same logic
 * without re-implementing it inline. The same helper is exported for unit
 * testing — see useGraphControls.test.ts.
 */
export function computeVisibleNodeSet(
  graphData: GraphData,
  opts: VisibleNodesOpts,
): Set<string> {
  const qualifying =
    opts.filterNodesBySimilarity && opts.graphSource === 'wiki'
      ? new Set(
          graphData.edges
            .filter(e => e.kind === 'semantic' && e.w >= opts.threshold)
            .flatMap(e => [e.s, e.t]),
        )
      : null
  const out = new Set<string>()
  for (const n of graphData.nodes) {
    if (!opts.showDaily && n.folder === 'Daily') continue
    if (!opts.activeTypes.has(n.type)) continue
    if (qualifying && !qualifying.has(n.id)) continue
    out.add(n.id)
  }
  return out
}

export interface GraphControlsOpts {
  graphData: GraphData | null
}

export interface GraphControlsSlice {
  threshold: number
  setThreshold: (v: number) => void
  graphSource: GraphSource
  setGraphSource: (v: GraphSource) => void
  showOverlayEdges: boolean
  toggleOverlayEdges: () => void
  filterNodesBySimilarity: boolean
  toggleFilterNodesBySimilarity: () => void
  activeTypes: Set<string>
  handleToggleType: (type: string) => void
  showDaily: boolean
  toggleShowDaily: () => void
  hideIsolated: boolean
  toggleHideIsolated: () => void
  labelsOnHoverOnly: boolean
  toggleLabelsOnHoverOnly: () => void
  scalingRatio: number
  setScalingRatio: (v: number) => void
  gravity: number
  setGravity: (v: number) => void
  slowDown: number
  setSlowDown: (v: number) => void
  edgeWeightInfluence: number
  setEdgeWeightInfluence: (v: number) => void
  startTemperature: number
  setStartTemperature: (v: number) => void
  stopThreshold: number
  setStopThreshold: (v: number) => void
  isLayoutRunning: boolean
  setIsLayoutRunning: (v: boolean | ((prev: boolean) => boolean)) => void
  edgeColorMode: EdgeColorMode
  setEdgeColorMode: (v: EdgeColorMode) => void
  edgePruning: boolean
  toggleEdgePruning: () => void
  edgePruningK: number
  setEdgePruningK: (v: number) => void
  nodeSizeMode: NodeSizeMode
  setNodeSizeMode: (v: NodeSizeMode) => void
  nodeColorMode: NodeColorMode
  setNodeColorMode: (v: NodeColorMode) => void
  nodeSizeMap: Map<string, number> | null
  nodeSizeComputing: boolean
  resetSimSettings: () => void
  stats: { nodeCount: number; edgeCount: number; avgScore: number }
  graphStats: GraphStats | null
}

export function useGraphControls({ graphData }: GraphControlsOpts): GraphControlsSlice {
  const [threshold, setThreshold] = useLocalStorage('vv:threshold', 0.8)
  const [graphSource, setGraphSource] = useLocalStorage<GraphSource>('vv:graphSource', 'semantic')
  const [showOverlayEdges, setShowOverlayEdges] = useLocalStorage('vv:showOverlayEdges', false)
  const [filterNodesBySimilarity, setFilterNodesBySimilarity] = useLocalStorage('vv:filterNodesBySimilarity', false)
  const [activeTypesArr, setActiveTypesArr] = useLocalStorage<string[]>(
    'vv:activeTypes',
    Object.keys(TYPE_COLORS).filter(t => t !== 'daily'),
  )
  const activeTypes = useMemo(() => new Set(activeTypesArr), [activeTypesArr])
  const setActiveTypes = useCallback((updater: Set<string> | ((prev: Set<string>) => Set<string>)) => {
    setActiveTypesArr(prev => {
      const prevSet = new Set(prev)
      const next = typeof updater === 'function' ? updater(prevSet) : updater
      return [...next]
    })
  }, [setActiveTypesArr])
  const [showDaily, setShowDaily] = useLocalStorage('vv:showDaily', false)
  const [hideIsolated, setHideIsolated] = useLocalStorage('vv:hideIsolated', false)
  const [labelsOnHoverOnly, setLabelsOnHoverOnly] = useLocalStorage('vv:labelsOnHoverOnly', false)
  const [scalingRatio, setScalingRatio] = useLocalStorage('vv:scalingRatio', SIM_DEFAULTS.scalingRatio)
  const [gravityRaw, setGravity] = useLocalStorage('vv:gravity', SIM_DEFAULTS.gravity)
  const gravity = Math.min(gravityRaw, 5)
  const [slowDown, setSlowDown] = useLocalStorage('vv:slowDown', SIM_DEFAULTS.slowDown)
  const [edgeWeightInfluence, setEdgeWeightInfluence] = useLocalStorage('vv:edgeWeightInfluence', SIM_DEFAULTS.edgeWeightInfluence)
  const [startTemperature, setStartTemperature] = useLocalStorage('vv:startTemperature', SIM_DEFAULTS.startTemperature)
  const [stopThreshold, setStopThreshold] = useLocalStorage('vv:stopThreshold', SIM_DEFAULTS.stopThreshold)
  const [isLayoutRunning, setIsLayoutRunning] = useState(true)
  const [edgeColorMode, setEdgeColorMode] = useLocalStorage<EdgeColorMode>('vv:edgeColorMode', 'binary')
  const [edgePruning, setEdgePruning] = useLocalStorage('vv:edgePruning', false)
  const [edgePruningK, setEdgePruningK] = useLocalStorage('vv:edgePruningK', 8)
  const toggleEdgePruning = useCallback(() => setEdgePruning(s => !s), [setEdgePruning])
  const [nodeSizeMode, setNodeSizeMode] = useLocalStorage<NodeSizeMode>('vv:nodeSizeMode', 'incoming_links')
  const [nodeColorMode, setNodeColorMode] = useLocalStorage<NodeColorMode>('vv:nodeColorMode', 'type')
  // null = not computed yet or non-betweenness mode; a Map = computed result
  const [nodeSizeMap, setNodeSizeMap] = useState<Map<string, number> | null>(null)
  // 'idle' | 'queued' (timer set, not started) | 'done'
  const [nodeSizeStatus, setNodeSizeStatus] = useState<'idle' | 'queued' | 'done'>('idle')
  const nodeSizeComputing = nodeSizeStatus === 'queued'

  useEffect(() => {
    if (nodeSizeMode !== 'betweenness' || !graphData) {
      // Use functional updater to avoid synchronous setState-in-effect lint warning
      // by deferring via the scheduler
      const id = setTimeout(() => {
        setNodeSizeMap(null)
        setNodeSizeStatus('idle')
      }, 0)
      return () => clearTimeout(id)
    }
    // QA-011: Gate betweenness centrality behind a node-count limit to prevent
    // O(n*(n+m)) computation from blocking the main UI thread on large graphs.
    const MAX_BETWEENNESS_NODES = 500
    if (graphData.nodes.length > MAX_BETWEENNESS_NODES) {
      const id = setTimeout(() => {
        // Fall back to null (GraphCanvas uses incoming_links as fallback)
        setNodeSizeMap(null)
        setNodeSizeStatus('done')
      }, 0)
      return () => clearTimeout(id)
    }
    // Mark as queued immediately (shows "Computing...")
    const idStatus = setTimeout(() => setNodeSizeStatus('queued'), 0)
    // Defer heavy computation to next tick so "Computing..." renders first
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
      setNodeSizeStatus('done')
    }, 50)
    return () => { clearTimeout(idStatus); clearTimeout(id) }
  }, [nodeSizeMode, graphData])

  const handleToggleType = useCallback((type: string) => {
    setActiveTypes(prev => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  }, [setActiveTypes])

  // Memoized toggle callbacks to prevent unnecessary re-renders
  const toggleOverlayEdges = useCallback(() => setShowOverlayEdges(s => !s), [setShowOverlayEdges])
  const toggleFilterNodesBySimilarity = useCallback(() => setFilterNodesBySimilarity(s => !s), [setFilterNodesBySimilarity])
  const toggleShowDaily = useCallback(() => setShowDaily(s => !s), [setShowDaily])
  const toggleHideIsolated = useCallback(() => setHideIsolated(s => !s), [setHideIsolated])
  const toggleLabelsOnHoverOnly = useCallback(() => setLabelsOnHoverOnly(s => !s), [setLabelsOnHoverOnly])

  const resetSimSettings = useCallback(() => {
    setScalingRatio(SIM_DEFAULTS.scalingRatio)
    setGravity(SIM_DEFAULTS.gravity)
    setSlowDown(SIM_DEFAULTS.slowDown)
    setEdgeWeightInfluence(SIM_DEFAULTS.edgeWeightInfluence)
    setStartTemperature(SIM_DEFAULTS.startTemperature)
    setStopThreshold(SIM_DEFAULTS.stopThreshold)
  }, [setScalingRatio, setGravity, setSlowDown, setEdgeWeightInfluence, setStartTemperature, setStopThreshold])

  // Shared visibility computation — extracted so `stats` and `graphStats` can't drift apart.
  const visibleNodes = useMemo(() => {
    if (!graphData) return new Set<string>()
    return computeVisibleNodeSet(graphData, {
      showDaily,
      activeTypes,
      filterNodesBySimilarity,
      graphSource,
      threshold,
    })
  }, [graphData, showDaily, activeTypes, filterNodesBySimilarity, graphSource, threshold])

  // Stats for HUD
  const stats = useMemo(() => {
    if (!graphData) return { nodeCount: 0, edgeCount: 0, avgScore: 0 }
    const edges = filterEdges(graphData.edges, graphSource, threshold)
      .filter(e => visibleNodes.has(e.s) && visibleNodes.has(e.t))
    const semEdges = edges.filter(e => e.kind === 'semantic')
    const avg = semEdges.length > 0
      ? semEdges.reduce((sum, e) => sum + e.w, 0) / semEdges.length
      : 0
    return { nodeCount: visibleNodes.size, edgeCount: edges.length, avgScore: avg }
  }, [graphData, threshold, graphSource, visibleNodes])

  const graphStats = useMemo<GraphStats | null>(() => {
    if (!graphData) return null

    // Degree from wiki edges (undirected), both endpoints visible
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
    const maxDegree = n > 0 ? degrees.reduce((m, d) => (d > m ? d : m), 0) : 0
    const density = n > 1 ? wikiEdgeCount / (n * (n - 1) / 2) : 0

    // Top 5 hubs
    const nodeIdToTitle = new Map(graphData.nodes.map((nd: NoteNode) => [nd.id, nd.title]))
    const topHubs = [...degree.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5)
      .map(([id, deg]) => ({ id, title: nodeIdToTitle.get(id) ?? id, degree: deg }))

    // Connected components via BFS on wiki adjacency within visibleNodes
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
  }, [graphData, visibleNodes])

  return useMemo<GraphControlsSlice>(
    () => ({
      threshold,
      setThreshold,
      graphSource,
      setGraphSource,
      showOverlayEdges,
      toggleOverlayEdges,
      filterNodesBySimilarity,
      toggleFilterNodesBySimilarity,
      activeTypes,
      handleToggleType,
      showDaily,
      toggleShowDaily,
      hideIsolated,
      toggleHideIsolated,
      labelsOnHoverOnly,
      toggleLabelsOnHoverOnly,
      scalingRatio,
      setScalingRatio,
      gravity,
      setGravity,
      slowDown,
      setSlowDown,
      edgeWeightInfluence,
      setEdgeWeightInfluence,
      startTemperature,
      setStartTemperature,
      stopThreshold,
      setStopThreshold,
      isLayoutRunning,
      setIsLayoutRunning,
      edgeColorMode,
      setEdgeColorMode,
      edgePruning,
      toggleEdgePruning,
      edgePruningK,
      setEdgePruningK,
      nodeSizeMode,
      setNodeSizeMode,
      nodeColorMode,
      setNodeColorMode,
      nodeSizeMap,
      nodeSizeComputing,
      resetSimSettings,
      stats,
      graphStats,
    }),
    [
      threshold,
      setThreshold,
      graphSource,
      setGraphSource,
      showOverlayEdges,
      toggleOverlayEdges,
      filterNodesBySimilarity,
      toggleFilterNodesBySimilarity,
      activeTypes,
      handleToggleType,
      showDaily,
      toggleShowDaily,
      hideIsolated,
      toggleHideIsolated,
      labelsOnHoverOnly,
      toggleLabelsOnHoverOnly,
      scalingRatio,
      setScalingRatio,
      gravity,
      setGravity,
      slowDown,
      setSlowDown,
      edgeWeightInfluence,
      setEdgeWeightInfluence,
      startTemperature,
      setStartTemperature,
      stopThreshold,
      setStopThreshold,
      isLayoutRunning,
      setIsLayoutRunning,
      edgeColorMode,
      setEdgeColorMode,
      edgePruning,
      toggleEdgePruning,
      edgePruningK,
      setEdgePruningK,
      nodeSizeMode,
      setNodeSizeMode,
      nodeColorMode,
      setNodeColorMode,
      nodeSizeMap,
      nodeSizeComputing,
      resetSimSettings,
      stats,
      graphStats,
    ],
  )
}
