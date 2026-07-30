// ARC-037: note-tabs + view-UI slice extracted from useVisualizerState.ts.
//
// Owns every piece of state tied to "what note is the user looking at and
// how is it displayed":
//   - open tabs + active tab + tab operations (open/close/switch)
//   - in-memory note content cache (with mtime conflict tokens)
//   - note CRUD operations (fetch/save/delete/create) + cache invalidation
//   - viewMode (read/graph) + sidebar geometry + neighborhood focus + history mode
//   - stem/node lookup maps derived from graphData
//
// The slice exposes `_resetForVaultChange` so the orchestrator can clear
// tabs + cache + neighborhood focus synchronously when the user switches
// vaults — preserving the original behaviour where the cleanup runs before
// the new vault is committed, so consumers never observe a half-cleared UI.
'use client'

import { useState, useCallback, useMemo, useRef } from 'react'
import { useLocalStorage } from '@/lib/useLocalStorage'
import type { GraphData, NoteNode } from '@/lib/graph'

const MAX_TABS = 20

export interface TabInfo {
  stem: string
  node: NoteNode
}

export interface NoteTabsOpts {
  graphData: GraphData | null
  selectedVault: string | null
}

export interface NoteTabsSlice {
  // Tab state
  openTabs: string[]
  activeTab: string | null
  activeNode: NoteNode | null
  openNote: (stem: string, newTab: boolean) => void
  closeTab: (stem: string, path?: string) => void
  switchTab: (stem: string) => void
  // View state
  viewMode: 'read' | 'graph'
  setViewMode: (v: 'read' | 'graph') => void
  neighborhoodCenter: string | null
  setNeighborhoodCenter: (v: string | null) => void
  // History mode
  historyMode: boolean
  historyNote: string | null
  historyPath: string | null
  openHistory: (stem: string, notePath?: string) => void
  closeHistory: () => void
  // Sidebar
  sidebarWidth: number
  setSidebarWidth: (v: number | ((prev: number) => number)) => void
  sidebarCollapsed: boolean
  setSidebarCollapsed: (v: boolean | ((prev: boolean) => boolean)) => void
  // Content
  fetchNoteContent: (
    stem: string,
    notePath?: string,
  ) => Promise<{ content: string; mtimeMs?: number; fromCache: boolean }>
  saveNote: (
    stem: string,
    content: string,
    baseMtimeMs?: number,
    notePath?: string,
  ) => Promise<{ conflict: true; serverContent: string; mtimeMs: number } | { ok: true; mtimeMs: number }>
  deleteNote: (stem: string, notePath?: string) => Promise<void>
  createNote: (notePath: string, content: string) => Promise<void>
  resolveWikilink: (rawStem: string) => string | null
  nodeMap: Map<string, NoteNode>
  invalidateNote: (stem: string, notePath?: string) => void
  /** Internal: orchestrator calls this when the vault actually changes. */
  _resetForVaultChange: () => void
}

export function useNoteTabs({ graphData, selectedVault }: NoteTabsOpts): NoteTabsSlice {
  // --- Tab state ---
  const [openTabStems, setOpenTabStems] = useLocalStorage<string[]>('vv:openTabs', [])
  const [activeTabStem, setActiveTabStem] = useLocalStorage<string | null>('vv:activeTab', null)
  const [viewMode, setViewMode] = useLocalStorage<'read' | 'graph'>('vv:viewMode', 'read')
  const [neighborhoodCenter, setNeighborhoodCenter] = useState<string | null>(null)

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
  // Server mtime (fs.stat().mtimeMs) for each cached note — the conflict-detection
  // token echoed back on save. Keyed identically to contentCache (stem and/or path).
  const mtimeCache = useRef<Map<string, number>>(new Map())

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

  const closeTab = useCallback((stem: string, path?: string) => {
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
    mtimeCache.current.delete(stem)
    const p = path ?? nodeMap.get(stem)?.path
    if (p) {
      contentCache.current.delete(p)
      mtimeCache.current.delete(p)
    }
  }, [setOpenTabStems, setActiveTabStem, validActiveTab, nodeMap])

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
  // mtimeMs is the server's fs.stat().mtimeMs for the note — the conflict-detection token
  // callers must echo back via saveNote's baseMtimeMs. It is returned on cache hits too
  // (from mtimeCache) so the token survives tab switches without a clock-based fallback.
  const fetchNoteContent = useCallback(async (stem: string, notePath?: string): Promise<{ content: string; mtimeMs?: number; fromCache: boolean }> => {
    const cacheKey = notePath ?? stem
    const cached = contentCache.current.get(cacheKey)
    if (cached !== undefined) return { content: cached, mtimeMs: mtimeCache.current.get(cacheKey), fromCache: true }

    const params = new URLSearchParams()
    if (notePath) params.set('path', notePath)
    else params.set('stem', stem)
    if (selectedVault) params.set('vault', selectedVault)
    const res = await fetch(`/api/note?${params.toString()}`)
    // ARC-040: surface non-2xx responses. A 5xx with a malformed body would
    // otherwise throw inside res.json() with a confusing parse error.
    const data = res.ok ? await res.json().catch(() => ({ error: 'Invalid server response' })) : { error: `Failed to fetch note (${res.status})` }
    if (data.error) throw new Error(data.error as string)
    const content = data.content as string
    const mtimeMs = data.mtimeMs as number
    contentCache.current.set(cacheKey, content)
    mtimeCache.current.set(cacheKey, mtimeMs)
    return { content, mtimeMs, fromCache: false }
  }, [selectedVault])

  // --- Save note content ---
  // baseMtimeMs: the server mtime the caller last observed for this note (from
  // fetchNoteContent or a prior saveNote response) — echoed back so the server can
  // detect an external edit by comparing server-side mtimes only (no client clock).
  const saveNote = useCallback(async (
    stem: string,
    content: string,
    baseMtimeMs?: number,
    notePath?: string,
  ): Promise<{ conflict: true; serverContent: string; mtimeMs: number } | { ok: true; mtimeMs: number }> => {
    const body: Record<string, unknown> = { stem, content, baseMtimeMs }
    if (notePath) body.path = notePath
    if (selectedVault) body.vault = selectedVault
    const res = await fetch('/api/note', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    // ARC-040: a 409 conflict response is expected and must be parsed so the
    // caller can react; other non-2xx statuses are surfaced as errors.
    const data = (res.status === 409 || res.ok)
      ? await res.json().catch(() => ({ error: 'Invalid server response' }))
      : { error: `Failed to save note (${res.status})` }
    const payload = data as { error?: string; conflict?: boolean; serverContent?: string; ok?: boolean; mtimeMs?: number }
    if (payload.error && payload.conflict !== true) throw new Error(payload.error)
    if (payload.conflict === true) {
      return { conflict: true, serverContent: payload.serverContent ?? '', mtimeMs: payload.mtimeMs ?? 0 }
    }
    const newMtimeMs = payload.mtimeMs ?? 0
    // Cache under both stem and path so fetches always hit
    contentCache.current.set(stem, content)
    mtimeCache.current.set(stem, newMtimeMs)
    if (notePath) {
      contentCache.current.set(notePath, content)
      mtimeCache.current.set(notePath, newMtimeMs)
    }
    return { ok: true, mtimeMs: newMtimeMs }
  }, [selectedVault])

  // --- Invalidate cached note (called when vault watcher detects external edit) ---
  const invalidateNote = useCallback((stem: string, notePath?: string): void => {
    contentCache.current.delete(stem)
    mtimeCache.current.delete(stem)
    if (notePath) {
      contentCache.current.delete(notePath)
      mtimeCache.current.delete(notePath)
    }
  }, [])

  // --- Delete note ---
  const deleteNote = useCallback(async (stem: string, notePath?: string): Promise<void> => {
    const params = new URLSearchParams()
    if (notePath) params.set('path', notePath)
    else params.set('stem', stem)
    if (selectedVault) params.set('vault', selectedVault)
    const res = await fetch(`/api/note?${params.toString()}`, { method: 'DELETE' })
    const data = res.ok ? await res.json().catch(() => ({ error: 'Invalid server response' })) : { error: `Failed to delete note (${res.status})` }
    if (data.error) throw new Error(data.error as string)
  }, [selectedVault])

  // --- Create note ---
  const createNote = useCallback(async (notePath: string, content: string): Promise<void> => {
    const body: Record<string, unknown> = { path: notePath, content }
    if (selectedVault) body.vault = selectedVault
    const res = await fetch('/api/note', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = res.ok ? await res.json().catch(() => ({ error: 'Invalid server response' })) : { error: `Failed to create note (${res.status})` }
    if (data.error) throw new Error(data.error as string)
  }, [selectedVault])

  // --- Resolve wikilink stem ---
  const resolveWikilink = useCallback((rawStem: string): string | null => {
    return stemLookup.get(rawStem) ?? null
  }, [stemLookup])

  // Synchronous cleanup run by the orchestrator when the vault actually changes.
  // All setters here are stable (useLocalStorage / useState), so this closure
  // never needs to change identity — which keeps the orchestrator's
  // setSelectedVault wrapper stable as well.
  const _resetForVaultChange = useCallback(() => {
    setOpenTabStems([])
    setActiveTabStem(null)
    setNeighborhoodCenter(null)
    contentCache.current.clear()
    mtimeCache.current.clear()
  }, [setOpenTabStems, setActiveTabStem, setNeighborhoodCenter])

  return useMemo<NoteTabsSlice>(
    () => ({
      openTabs: validTabs,
      activeTab: validActiveTab,
      activeNode,
      openNote,
      closeTab,
      switchTab,
      viewMode,
      setViewMode,
      neighborhoodCenter,
      setNeighborhoodCenter,
      historyMode,
      historyNote,
      historyPath,
      openHistory,
      closeHistory,
      sidebarWidth,
      setSidebarWidth,
      sidebarCollapsed,
      setSidebarCollapsed,
      fetchNoteContent,
      saveNote,
      deleteNote,
      createNote,
      resolveWikilink,
      nodeMap,
      invalidateNote,
      _resetForVaultChange,
    }),
    [
      validTabs,
      validActiveTab,
      activeNode,
      openNote,
      closeTab,
      switchTab,
      viewMode,
      setViewMode,
      neighborhoodCenter,
      setNeighborhoodCenter,
      historyMode,
      historyNote,
      historyPath,
      openHistory,
      closeHistory,
      sidebarWidth,
      setSidebarWidth,
      sidebarCollapsed,
      setSidebarCollapsed,
      fetchNoteContent,
      saveNote,
      deleteNote,
      createNote,
      resolveWikilink,
      nodeMap,
      invalidateNote,
      _resetForVaultChange,
    ],
  )
}
