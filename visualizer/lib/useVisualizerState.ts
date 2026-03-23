'use client'

import { useState, useCallback, useMemo, useRef } from 'react'
import { useLocalStorage } from '@/lib/useLocalStorage'
import type { GraphData, GraphSource, NoteNode } from '@/lib/graph'
import { filterEdges } from '@/lib/graph'
import { TYPE_COLORS } from '@/lib/sigma-colors'

const SIM_DEFAULTS = {
  scalingRatio: 10,
  gravity: 1,
  slowDown: 0.5,
  edgeWeightInfluence: 2,
  startTemperature: 0.8,
  stopThreshold: 0.01,
}

const MAX_TABS = 20

export interface TabInfo {
  stem: string
  node: NoteNode
}

export function useVisualizerState(graphData: GraphData | null) {
  // --- Tab state ---
  const [openTabStems, setOpenTabStems] = useLocalStorage<string[]>('vv:openTabs', [])
  const [activeTabStem, setActiveTabStem] = useLocalStorage<string | null>('vv:activeTab', null)
  const [viewMode, setViewMode] = useLocalStorage<'read' | 'graph'>('vv:viewMode', 'read')
  const [graphScope, setGraphScope] = useLocalStorage<'local' | 'full'>('vv:graphScope', 'local')

  // --- Sidebar state ---
  const [sidebarWidth, setSidebarWidth] = useLocalStorage('vv:sidebarWidth', 240)
  const [sidebarCollapsed, setSidebarCollapsed] = useLocalStorage('vv:sidebarCollapsed', false)

  // --- Note content cache ---
  const contentCache = useRef<Map<string, string>>(new Map())

  // --- Wikilink resolution map ---
  const stemLookup = useMemo(() => {
    if (!graphData) return new Map<string, string>()
    const map = new Map<string, string>()
    for (const node of graphData.nodes) {
      map.set(node.id, node.id)
      const filename = node.path.split('/').pop()?.replace(/\.md$/, '')
      if (filename && filename !== node.id) {
        if (!map.has(filename)) map.set(filename, node.id)
      }
    }
    return map
  }, [graphData])

  // --- Node lookup ---
  const nodeMap = useMemo(() => {
    if (!graphData) return new Map<string, NoteNode>()
    const map = new Map<string, NoteNode>()
    for (const node of graphData.nodes) map.set(node.id, node)
    return map
  }, [graphData])

  // Validate persisted tabs against current graph data
  const validTabs = useMemo(() => {
    if (!graphData) return []
    return openTabStems.filter(stem => nodeMap.has(stem))
  }, [openTabStems, nodeMap, graphData])

  const validActiveTab = useMemo(() => {
    if (activeTabStem && validTabs.includes(activeTabStem)) return activeTabStem
    return validTabs.length > 0 ? validTabs[0] : null
  }, [activeTabStem, validTabs])

  const activeNode = useMemo(() => {
    if (!validActiveTab) return null
    return nodeMap.get(validActiveTab) ?? null
  }, [validActiveTab, nodeMap])

  // --- Tab operations ---
  const openNote = useCallback((stem: string, newTab: boolean) => {
    const resolvedStem = stemLookup.get(stem) ?? stem
    if (!nodeMap.has(resolvedStem)) return

    setOpenTabStems(prev => {
      // Already open — just switch to it
      if (prev.includes(resolvedStem)) {
        setActiveTabStem(resolvedStem)
        return prev
      }
      if (newTab || prev.length === 0) {
        let next = [...prev, resolvedStem]
        if (next.length > MAX_TABS) {
          const oldest = next.find(s => s !== resolvedStem)
          if (oldest) next = next.filter(s => s !== oldest)
        }
        setActiveTabStem(resolvedStem)
        return next
      }
      // Replace current tab
      const idx = prev.indexOf(validActiveTab ?? '')
      if (idx >= 0) {
        const next = [...prev]
        next[idx] = resolvedStem
        setActiveTabStem(resolvedStem)
        return next
      }
      setActiveTabStem(resolvedStem)
      return [...prev, resolvedStem]
    })
  }, [stemLookup, nodeMap, setOpenTabStems, setActiveTabStem, validActiveTab])

  const closeTab = useCallback((stem: string) => {
    setOpenTabStems(prev => {
      const next = prev.filter(s => s !== stem)
      if (stem === validActiveTab) {
        const idx = prev.indexOf(stem)
        const newActive = next[Math.min(idx, next.length - 1)] ?? null
        setActiveTabStem(newActive)
      }
      return next
    })
    contentCache.current.delete(stem)
  }, [setOpenTabStems, setActiveTabStem, validActiveTab])

  const switchTab = useCallback((stem: string) => {
    setActiveTabStem(stem)
  }, [setActiveTabStem])

  // --- Fetch note content (with cache) ---
  const fetchNoteContent = useCallback(async (stem: string): Promise<string> => {
    const cached = contentCache.current.get(stem)
    if (cached !== undefined) return cached

    const res = await fetch(`/api/note?stem=${encodeURIComponent(stem)}`)
    const data = await res.json()
    if (data.error) throw new Error(data.error as string)
    const content = data.content as string
    contentCache.current.set(stem, content)
    return content
  }, [])

  // --- Save note content ---
  const saveNote = useCallback(async (stem: string, content: string): Promise<void> => {
    const res = await fetch('/api/note', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stem, content }),
    })
    const data = await res.json()
    if (data.error) throw new Error(data.error as string)
    contentCache.current.set(stem, content)
  }, [])

  // --- Delete note ---
  const deleteNote = useCallback(async (stem: string): Promise<void> => {
    const res = await fetch(`/api/note?stem=${encodeURIComponent(stem)}`, { method: 'DELETE' })
    const data = await res.json()
    if (data.error) throw new Error(data.error as string)
  }, [])

  // --- Create note ---
  const createNote = useCallback(async (notePath: string, content: string): Promise<void> => {
    const res = await fetch('/api/note', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: notePath, content }),
    })
    const data = await res.json()
    if (data.error) throw new Error(data.error as string)
  }, [])

  // --- Resolve wikilink stem ---
  const resolveWikilink = useCallback((rawStem: string): string | null => {
    return stemLookup.get(rawStem) ?? null
  }, [stemLookup])

  // --- Graph control state (migrated from page.tsx) ---
  const [threshold, setThreshold] = useLocalStorage('vv:threshold', 0.8)
  const [graphSource, setGraphSource] = useLocalStorage<GraphSource>('vv:graphSource', 'semantic')
  const [showOverlayEdges, setShowOverlayEdges] = useLocalStorage('vv:showOverlayEdges', false)
  const [filterNodesBySimilarity, setFilterNodesBySimilarity] = useLocalStorage('vv:filterNodesBySimilarity', false)
  const [activeTypesArr, setActiveTypesArr] = useLocalStorage<string[]>(
    'vv:activeTypes',
    Object.keys(TYPE_COLORS).filter(t => t !== 'daily')
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
  const [selectedNode, setSelectedNode] = useState<string | null>(null)

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

  // Stats for HUD
  const stats = useMemo(() => {
    if (!graphData) return { nodeCount: 0, edgeCount: 0, avgScore: 0 }
    const qualifying = (filterNodesBySimilarity && graphSource === 'wiki')
      ? new Set(graphData.edges.filter(e => e.kind === 'semantic' && e.w >= threshold).flatMap(e => [e.s, e.t]))
      : null
    const visibleNodes = new Set(
      graphData.nodes
        .filter(n => (showDaily || n.folder !== 'Daily') && activeTypes.has(n.type) && (!qualifying || qualifying.has(n.id)))
        .map(n => n.id)
    )
    const edges = filterEdges(graphData.edges, graphSource, threshold)
      .filter(e => visibleNodes.has(e.s) && visibleNodes.has(e.t))
    const semEdges = edges.filter(e => e.kind === 'semantic')
    const avg = semEdges.length > 0
      ? semEdges.reduce((sum, e) => sum + e.w, 0) / semEdges.length
      : 0
    return { nodeCount: visibleNodes.size, edgeCount: edges.length, avgScore: avg }
  }, [graphData, threshold, graphSource, activeTypes, showDaily, filterNodesBySimilarity])

  // --- File tree ---
  const fileTree = useMemo(() => {
    if (!graphData) return new Map<string, Map<string, NoteNode[]>>()
    const tree = new Map<string, Map<string, NoteNode[]>>()
    for (const node of graphData.nodes) {
      const parts = node.path.replace(/\.md$/, '').split('/')
      const folder = parts[0] || 'Root'
      const subfolder = parts.length > 2 ? parts[1] : ''
      if (!tree.has(folder)) tree.set(folder, new Map())
      const folderMap = tree.get(folder)!
      if (!folderMap.has(subfolder)) folderMap.set(subfolder, [])
      folderMap.get(subfolder)!.push(node)
    }
    for (const [, subMap] of tree) {
      for (const [, notes] of subMap) {
        notes.sort((a, b) => a.title.localeCompare(b.title))
      }
    }
    return tree
  }, [graphData])

  return {
    // Tab state
    openTabs: validTabs, activeTab: validActiveTab, activeNode,
    openNote, closeTab, switchTab,
    // View state
    viewMode, setViewMode, graphScope, setGraphScope,
    // Sidebar state
    sidebarWidth, setSidebarWidth, sidebarCollapsed, setSidebarCollapsed,
    // Content
    fetchNoteContent, saveNote, deleteNote, createNote, resolveWikilink, nodeMap, fileTree,
    // Graph controls
    threshold, setThreshold,
    graphSource, setGraphSource,
    showOverlayEdges, toggleOverlayEdges,
    filterNodesBySimilarity, toggleFilterNodesBySimilarity,
    activeTypes, handleToggleType,
    showDaily, toggleShowDaily,
    hideIsolated, toggleHideIsolated,
    labelsOnHoverOnly, toggleLabelsOnHoverOnly,
    scalingRatio, setScalingRatio,
    gravity, setGravity,
    slowDown, setSlowDown,
    edgeWeightInfluence, setEdgeWeightInfluence,
    startTemperature, setStartTemperature,
    stopThreshold, setStopThreshold,
    isLayoutRunning, setIsLayoutRunning,
    selectedNode, setSelectedNode,
    resetSimSettings,
    stats,
    SIM_DEFAULTS,
  }
}
