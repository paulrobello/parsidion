'use client'

import { useState, useCallback, useMemo, useRef } from 'react'
import { useLocalStorage } from '@/lib/useLocalStorage'
import type { GraphData, GraphSource, NoteNode } from '@/lib/graph'
import { filterEdges } from '@/lib/graph'
import { TYPE_COLORS, EdgeColorMode } from '@/lib/sigma-colors'

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
  // --- Vault selection ---
  const [selectedVault, setSelectedVaultInternal] = useLocalStorage<string | null>('vv:selectedVault', null)

  // --- Tab state ---
  const [openTabStems, setOpenTabStems] = useLocalStorage<string[]>('vv:openTabs', [])
  const [activeTabStem, setActiveTabStem] = useLocalStorage<string | null>('vv:activeTab', null)
  const [viewMode, setViewMode] = useLocalStorage<'read' | 'graph'>('vv:viewMode', 'read')
  const [graphScope, setGraphScope] = useLocalStorage<'local' | 'full'>('vv:graphScope', 'local')

  // --- History mode state ---
  const [historyMode, setHistoryMode] = useState(false)
  const [historyNote, setHistoryNote] = useState<string | null>(null)
  const [historyPath, setHistoryPath] = useState<string | null>(null)
  // Internal: ref avoids stale-closure risk; restored by closeHistory
  const prevViewModeRef = useRef<'read' | 'graph'>('read')

  // --- Sidebar state ---
  const [sidebarWidth, setSidebarWidth] = useLocalStorage('vv:sidebarWidth', 240)
  const [sidebarCollapsed, setSidebarCollapsed] = useLocalStorage('vv:sidebarCollapsed', false)

  // --- Note content cache ---
  const contentCache = useRef<Map<string, string>>(new Map())

  // Wrapper that clears cache/tabs when vault changes
  const setSelectedVault = useCallback((vault: string | null) => {
    if (vault !== selectedVault) {
      // Clear content cache
      contentCache.current.clear()
      // Clear tabs
      setOpenTabStems([])
      setActiveTabStem(null)
    }
    setSelectedVaultInternal(vault)
  }, [selectedVault, setSelectedVaultInternal, setOpenTabStems, setActiveTabStem])

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

  // Keep all persisted tabs — vault-only notes (not in graph.json) are still valid
  const validTabs = useMemo(() => {
    if (!graphData) return []
    return openTabStems
  }, [openTabStems, graphData])

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
    // resolvedStem: use wikilink resolution for graph notes; fall back to raw stem for vault-only notes
    const resolvedStem = stemLookup.get(stem) ?? stem

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
  }, [stemLookup, setOpenTabStems, setActiveTabStem, validActiveTab])

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

  const openHistory = useCallback((stem: string, notePath?: string) => {
    if (historyMode) {
      // Already in history mode — just swap the note, don't re-save prevViewMode
      setHistoryNote(stem)
      setHistoryPath(notePath ?? null)
      return
    }
    prevViewModeRef.current = viewMode
    setHistoryNote(stem)
    setHistoryPath(notePath ?? null)
    setHistoryMode(true)
  }, [historyMode, viewMode])

  const closeHistory = useCallback(() => {
    setHistoryMode(false)
    setHistoryNote(null)
    setHistoryPath(null)
    setViewMode(prevViewModeRef.current)
  }, [setViewMode])

  // --- Fetch note content (with cache) ---
  // notePath: vault-relative path (e.g. "Daily/MANIFEST.md"). When provided, used for both
  // the API call and the cache key so same-stem notes in different folders don't collide.
  const fetchNoteContent = useCallback(async (stem: string, notePath?: string): Promise<string> => {
    const cacheKey = notePath ?? stem
    const cached = contentCache.current.get(cacheKey)
    if (cached !== undefined) return cached

    const query = notePath
      ? `path=${encodeURIComponent(notePath)}`
      : `stem=${encodeURIComponent(stem)}`
    const res = await fetch(`/api/note?${query}`)
    const data = await res.json()
    if (data.error) throw new Error(data.error as string)
    const content = data.content as string
    contentCache.current.set(cacheKey, content)
    return content
  }, [])

  // --- Save note content ---
  const saveNote = useCallback(async (
    stem: string,
    content: string,
    lastModified?: number,
    notePath?: string,
  ): Promise<{ conflict: true; serverContent: string } | { ok: true }> => {
    const res = await fetch('/api/note', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stem, content, lastModified }),
    })
    const data = await res.json() as { error?: string; conflict?: boolean; serverContent?: string; ok?: boolean }
    if (data.error) throw new Error(data.error)
    if (data.conflict && data.serverContent) {
      return { conflict: true, serverContent: data.serverContent }
    }
    // Cache under both stem and path so fetches always hit
    contentCache.current.set(stem, content)
    if (notePath) contentCache.current.set(notePath, content)
    return { ok: true }
  }, [])

  // --- Invalidate cached note (called when vault watcher detects external edit) ---
  const invalidateNote = useCallback((stem: string, notePath?: string): void => {
    contentCache.current.delete(stem)
    if (notePath) contentCache.current.delete(notePath)
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
  const [edgeColorMode, setEdgeColorMode] = useLocalStorage<EdgeColorMode>('vv:edgeColorMode', 'binary')

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

  return {
    // Vault state
    selectedVault, setSelectedVault,
    // Tab state
    openTabs: validTabs, activeTab: validActiveTab, activeNode,
    openNote, closeTab, switchTab,
    // View state
    viewMode, setViewMode, graphScope, setGraphScope,
    historyMode, historyNote, historyPath, openHistory, closeHistory,
    // Sidebar state
    sidebarWidth, setSidebarWidth, sidebarCollapsed, setSidebarCollapsed,
    // Content
    fetchNoteContent, saveNote, deleteNote, createNote, resolveWikilink, nodeMap, invalidateNote,
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
    edgeColorMode, setEdgeColorMode,
    resetSimSettings,
    stats,
    SIM_DEFAULTS,
  }
}
