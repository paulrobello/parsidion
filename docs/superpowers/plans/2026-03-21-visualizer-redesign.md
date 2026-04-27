# Vault Visualizer Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the vault visualizer from a graph-first app into an Obsidian-style layout with file explorer sidebar, tabbed reading pane, unified search, and Read/Graph mode toggle.

**Architecture:** The existing `page.tsx` is replaced with a new layout composing FileExplorer (left sidebar), Toolbar (tabs + search + mode toggle), and a content area that switches between ReadingPane and GraphCanvas. GraphCanvas gains a neighborhood mode. State is managed via a custom hook `useVisualizerState` to keep page.tsx lean.

**Tech Stack:** Next.js 16, React 19, TypeScript 5, Sigma.js 3, Graphology, Tailwind CSS 4, react-markdown, remark-gfm

**Spec:** `docs/superpowers/specs/2026-03-21-visualizer-redesign.md`

---

### Task 1: Add `path` field to graph.json schema

**Files:**
- Modify: `scripts/build_graph.py:282-295` (nodes list builder)
- Modify: `visualizer/lib/graph.ts:1-9` (NoteNode interface)

- [ ] **Step 1: Update build_graph.py to load and emit `path` field**

In `load_note_metadata`, add `path` to the SELECT and to the dict. In the nodes builder, convert the absolute path to vault-relative:

```python
# In load_note_metadata, update the SELECT:
cursor.execute(
    """
    SELECT stem, title, note_type, folder, tags, incoming_links, related, mtime, path
    FROM note_index
    """
)
# Update row unpacking:
for row in rows:
    stem, title, note_type, folder, tags, incoming_links, related, mtime, path = row
    # ... existing filter ...
    notes.append(
        {
            "stem": stem,
            "title": title or "",
            "type": note_type or "",
            "folder": folder or "",
            "tags": tags or "",
            "incoming_links": incoming_links or 0,
            "related": related or "",
            "mtime": mtime or 0,
            "path": path or "",
        }
    )
```

In the `main` function's nodes builder, convert absolute path to vault-relative:

```python
# In main(), where nodes list is built:
vault_root_str = str(vault_root) + "/"
for note in filtered_notes:
    rel_path = note["path"]
    if rel_path.startswith(vault_root_str):
        rel_path = rel_path[len(vault_root_str):]
    nodes.append(
        {
            "id": note["stem"],
            "title": note["title"],
            "type": note["type"],
            "folder": note["folder"],
            "path": rel_path,
            "tags": parse_tags(note["tags"]),
            "incoming_links": note["incoming_links"],
            "mtime": note["mtime"],
        }
    )
```

- [ ] **Step 2: Update NoteNode interface in graph.ts**

```typescript
export interface NoteNode {
  id: string
  title: string
  type: string
  folder: string
  path: string  // vault-relative path, e.g. "Patterns/architecture-patterns.md"
  tags: string[]
  incoming_links: number
  mtime: number
}
```

- [ ] **Step 3: Rebuild graph.json and verify**

Run: `cd /Users/probello/Repos/parsidion && uv run --no-project scripts/build_graph.py --include-daily`

Verify: `python3 -c "import json; d=json.load(open('visualizer/public/graph.json')); n=d['nodes'][0]; print(n.get('path','MISSING'))"`

Expected: A vault-relative path like `Research/some-note.md` (not absolute, not `MISSING`).

- [ ] **Step 4: Commit**

```bash
git add scripts/build_graph.py visualizer/lib/graph.ts visualizer/public/graph.json
git commit -m "feat(visualizer): add path field to graph.json schema"
```

---

### Task 2: Create `useVisualizerState` hook

**Files:**
- Create: `visualizer/lib/useVisualizerState.ts`

This hook centralizes all state that the new layout needs: open tabs, active tab, view mode, sidebar state, note content cache, and wikilink resolution. It also re-exports all existing graph control state from the current `page.tsx`.

- [ ] **Step 1: Create the hook file**

```typescript
// visualizer/lib/useVisualizerState.ts
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
      // Also map the filename without extension for subfolder notes
      const filename = node.path.split('/').pop()?.replace(/\.md$/, '')
      if (filename && filename !== node.id) {
        // Only set if not already taken (exact match wins)
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
      if (newTab || prev.length === 0) {
        if (prev.includes(resolvedStem)) {
          // Already open — just switch to it
          setActiveTabStem(resolvedStem)
          return prev
        }
        let next = [...prev, resolvedStem]
        // Enforce max tabs
        if (next.length > MAX_TABS) {
          // Close oldest inactive tab
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
      // Fallback: append
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
    // Sort notes within each group
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
    fetchNoteContent, resolveWikilink, nodeMap, fileTree,
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
```

- [ ] **Step 2: Verify it compiles**

Run: `cd /Users/probello/Repos/parsidion/visualizer && bunx tsc --noEmit`

Expected: No errors related to `useVisualizerState.ts`.

- [ ] **Step 3: Commit**

```bash
git add visualizer/lib/useVisualizerState.ts
git commit -m "feat(visualizer): add useVisualizerState hook for new layout"
```

---

### Task 3: Create FileExplorer component

**Files:**
- Create: `visualizer/components/FileExplorer.tsx`

Browse-only sidebar with folder tree, note counts, expand/collapse, resize handle, and active note highlighting.

- [ ] **Step 1: Create FileExplorer.tsx**

```typescript
// visualizer/components/FileExplorer.tsx
'use client'

import { useState, useRef, useCallback, useEffect } from 'react'
import { useLocalStorage } from '@/lib/useLocalStorage'
import type { NoteNode } from '@/lib/graph'
import { getNodeColor } from '@/lib/sigma-colors'

interface Props {
  fileTree: Map<string, Map<string, NoteNode[]>>
  activeTab: string | null
  onSelectNote: (stem: string, newTab: boolean) => void
  width: number
  onWidthChange: (w: number) => void
  collapsed: boolean
  totalNotes: number
}

export function FileExplorer({ fileTree, activeTab, onSelectNote, width, onWidthChange, collapsed, totalNotes }: Props) {
  const [expandedFolders, setExpandedFolders] = useLocalStorage<string[]>('vv:expandedFolders', [])
  const expandedSet = new Set(expandedFolders)
  const isDragging = useRef(false)
  const startX = useRef(0)
  const startWidth = useRef(width)

  const toggleFolder = useCallback((folder: string) => {
    setExpandedFolders(prev => {
      const set = new Set(prev)
      if (set.has(folder)) set.delete(folder)
      else set.add(folder)
      return [...set]
    })
  }, [setExpandedFolders])

  // Resize handle
  const onMouseDown = useCallback((e: React.MouseEvent) => {
    isDragging.current = true
    startX.current = e.clientX
    startWidth.current = width
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    e.preventDefault()
  }, [width])

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!isDragging.current) return
      const newWidth = Math.min(400, Math.max(180, startWidth.current + (e.clientX - startX.current)))
      onWidthChange(newWidth)
    }
    const onUp = () => {
      if (!isDragging.current) return
      isDragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [onWidthChange])

  if (collapsed) return null

  const sortedFolders = [...fileTree.keys()].sort()

  return (
    <div
      style={{
        width, minWidth: 180, maxWidth: 400,
        background: '#0c1021',
        borderRight: '1px solid #1e293b',
        display: 'flex', flexDirection: 'column',
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 11, color: '#e8e8f0',
        flexShrink: 0, position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div style={{
        padding: '8px 10px',
        borderBottom: '1px solid #1e293b',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      }}>
        <span style={{ color: '#6b7a99', fontSize: 9, textTransform: 'uppercase', letterSpacing: '1px' }}>Vault</span>
        <span style={{ color: '#6b7a99', fontSize: 10 }}>{totalNotes} notes</span>
      </div>

      {/* Tree */}
      <div style={{ flex: 1, padding: '6px 0', overflowY: 'auto', overflowX: 'hidden' }}>
        {sortedFolders.map(folder => {
          const subMap = fileTree.get(folder)!
          const isExpanded = expandedSet.has(folder)
          // Count total notes in this folder (all subfolders)
          let count = 0
          for (const [, notes] of subMap) count += notes.length
          const sortedSubs = [...subMap.keys()].sort()

          return (
            <div key={folder}>
              {/* Folder row */}
              <div
                onClick={() => toggleFolder(folder)}
                style={{
                  padding: '4px 6px 4px 8px',
                  display: 'flex', alignItems: 'center', gap: 4,
                  cursor: 'pointer',
                }}
              >
                <span style={{ color: isExpanded ? '#f59e0b' : '#6b7a99', fontSize: 10, width: 12, textAlign: 'center' }}>
                  {isExpanded ? '▾' : '▸'}
                </span>
                <span style={{ flex: 1 }}>{folder}/</span>
                <span style={{ color: '#4b5563', fontSize: 9 }}>{count}</span>
              </div>

              {/* Folder contents */}
              {isExpanded && sortedSubs.map(sub => {
                const notes = subMap.get(sub)!
                if (sub) {
                  // Subfolder
                  const subKey = `${folder}/${sub}`
                  const subExpanded = expandedSet.has(subKey)
                  return (
                    <div key={subKey}>
                      <div
                        onClick={() => toggleFolder(subKey)}
                        style={{
                          padding: '3px 6px 3px 22px',
                          display: 'flex', alignItems: 'center', gap: 4,
                          cursor: 'pointer', color: '#9ca3af', fontSize: 10,
                        }}
                      >
                        <span style={{ color: subExpanded ? '#f59e0b' : '#6b7a99', fontSize: 9, width: 10, textAlign: 'center' }}>
                          {subExpanded ? '▾' : '▸'}
                        </span>
                        <span>{sub}/</span>
                        <span style={{ color: '#4b5563', fontSize: 9, marginLeft: 'auto' }}>{notes.length}</span>
                      </div>
                      {subExpanded && notes.map(note => (
                        <NoteItem
                          key={note.id}
                          note={note}
                          isActive={note.id === activeTab}
                          indent={36}
                          onSelect={onSelectNote}
                        />
                      ))}
                    </div>
                  )
                }
                // Direct children (no subfolder)
                return notes.map(note => (
                  <NoteItem
                    key={note.id}
                    note={note}
                    isActive={note.id === activeTab}
                    indent={22}
                    onSelect={onSelectNote}
                  />
                ))
              })}
            </div>
          )
        })}
      </div>

      {/* Resize handle */}
      <div
        onMouseDown={onMouseDown}
        style={{
          position: 'absolute', right: 0, top: 0, bottom: 0, width: 4,
          cursor: 'col-resize', zIndex: 10,
        }}
        onMouseEnter={e => (e.currentTarget.style.background = 'rgba(99,102,241,0.5)')}
        onMouseLeave={e => { if (!isDragging.current) e.currentTarget.style.background = 'transparent' }}
      />
    </div>
  )
}

function NoteItem({ note, isActive, indent, onSelect }: {
  note: NoteNode
  isActive: boolean
  indent: number
  onSelect: (stem: string, newTab: boolean) => void
}) {
  return (
    <div
      onClick={(e) => onSelect(note.id, e.metaKey || e.ctrlKey)}
      style={{
        padding: `3px 6px 3px ${indent}px`,
        fontSize: 10, cursor: 'pointer',
        color: isActive ? '#e8e8f0' : '#9ca3af',
        background: isActive ? 'rgba(99,102,241,0.15)' : 'transparent',
        borderLeft: isActive ? '2px solid #6366f1' : '2px solid transparent',
        borderRadius: isActive ? '0 3px 3px 0' : 0,
        display: 'flex', alignItems: 'center', gap: 4,
        overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis',
      }}
    >
      <span style={{ color: getNodeColor(note.type), fontSize: 6 }}>●</span>
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {note.id}.md
      </span>
    </div>
  )
}
```

- [ ] **Step 2: Verify it compiles**

Run: `cd /Users/probello/Repos/parsidion/visualizer && bunx tsc --noEmit`

- [ ] **Step 3: Commit**

```bash
git add visualizer/components/FileExplorer.tsx
git commit -m "feat(visualizer): add FileExplorer sidebar component"
```

---

### Task 4: Create TabBar component

**Files:**
- Create: `visualizer/components/TabBar.tsx`

- [ ] **Step 1: Create TabBar.tsx**

```typescript
// visualizer/components/TabBar.tsx
'use client'

import { useRef, useEffect } from 'react'
import type { NoteNode } from '@/lib/graph'
import { getNodeColor } from '@/lib/sigma-colors'

interface Props {
  tabs: string[]
  activeTab: string | null
  nodeMap: Map<string, NoteNode>
  onSwitch: (stem: string) => void
  onClose: (stem: string) => void
}

export function TabBar({ tabs, activeTab, nodeMap, onSwitch, onClose }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to active tab
  useEffect(() => {
    if (!scrollRef.current || !activeTab) return
    const el = scrollRef.current.querySelector(`[data-tab="${activeTab}"]`) as HTMLElement
    el?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' })
  }, [activeTab])

  if (tabs.length === 0) return null

  return (
    <div
      ref={scrollRef}
      style={{
        display: 'flex', gap: 1, overflowX: 'auto', overflowY: 'hidden',
        flex: 1, minWidth: 0, scrollbarWidth: 'none',
      }}
    >
      {tabs.map(stem => {
        const node = nodeMap.get(stem)
        const isActive = stem === activeTab
        return (
          <div
            key={stem}
            data-tab={stem}
            onClick={() => onSwitch(stem)}
            style={{
              background: isActive ? '#111827' : 'transparent',
              padding: '5px 12px',
              borderRadius: '6px 6px 0 0',
              color: isActive ? '#e8e8f0' : '#6b7a99',
              fontSize: 11,
              fontFamily: "'JetBrains Mono', monospace",
              cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 6,
              border: isActive ? '1px solid #1e293b' : '1px solid transparent',
              borderBottom: isActive ? '1px solid #111827' : '1px solid transparent',
              whiteSpace: 'nowrap',
              flexShrink: 0,
              maxWidth: 200,
              overflow: 'hidden',
            }}
          >
            <span style={{ color: getNodeColor(node?.type ?? ''), fontSize: 7 }}>●</span>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {node?.title ?? stem}
            </span>
            <span
              onClick={(e) => { e.stopPropagation(); onClose(stem) }}
              style={{
                color: '#4b5563', fontSize: 9, cursor: 'pointer',
                padding: '0 2px', marginLeft: 2,
                borderRadius: 2,
              }}
              onMouseEnter={e => (e.currentTarget.style.color = '#e8e8f0')}
              onMouseLeave={e => (e.currentTarget.style.color = '#4b5563')}
            >
              ✕
            </span>
          </div>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add visualizer/components/TabBar.tsx
git commit -m "feat(visualizer): add TabBar component"
```

---

### Task 5: Create UnifiedSearch component

**Files:**
- Create: `visualizer/components/UnifiedSearch.tsx`

- [ ] **Step 1: Create UnifiedSearch.tsx**

```typescript
// visualizer/components/UnifiedSearch.tsx
'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import type { NoteNode } from '@/lib/graph'
import { getNodeColor } from '@/lib/sigma-colors'

interface Props {
  nodes: NoteNode[]
  onSelect: (stem: string, newTab: boolean) => void
}

export function UnifiedSearch({ nodes, onSelect }: Props) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const [results, setResults] = useState<NoteNode[]>([])
  const [selectedIdx, setSelectedIdx] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  // Global ⌘K handler
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        inputRef.current?.focus()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  // Search logic
  useEffect(() => {
    const q = query.trim()
    if (!q) { setResults([]); setOpen(false); return }

    let filtered: NoteNode[]

    if (q.startsWith('#')) {
      // Tag search
      const tagQ = q.slice(1).toLowerCase()
      filtered = nodes.filter(n => n.tags.some(t => t.toLowerCase().includes(tagQ)))
    } else if (q.startsWith('/')) {
      // Folder/path search
      const pathQ = q.slice(1).toLowerCase()
      filtered = nodes.filter(n => n.path.toLowerCase().includes(pathQ))
    } else {
      // Fuzzy title search
      const lq = q.toLowerCase()
      filtered = nodes.filter(n => n.title.toLowerCase().includes(lq) || n.id.toLowerCase().includes(lq))
    }

    setResults(filtered.slice(0, 8))
    setSelectedIdx(0)
    setOpen(filtered.length > 0)
  }, [query, nodes])

  const handleSelect = useCallback((stem: string, newTab: boolean) => {
    setQuery('')
    setOpen(false)
    onSelect(stem, newTab)
  }, [onSelect])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIdx(i => Math.min(i + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIdx(i => Math.max(i - 1, 0))
    } else if (e.key === 'Enter' && results.length > 0) {
      e.preventDefault()
      handleSelect(results[selectedIdx].id, e.metaKey || e.ctrlKey)
    } else if (e.key === 'Escape') {
      setQuery('')
      setOpen(false)
      inputRef.current?.blur()
    }
  }, [results, selectedIdx, handleSelect])

  // Highlight match in title
  const highlight = (title: string) => {
    const q = query.startsWith('#') || query.startsWith('/') ? '' : query.trim().toLowerCase()
    if (!q) return <>{title}</>
    const idx = title.toLowerCase().indexOf(q)
    if (idx < 0) return <>{title}</>
    return (
      <>
        {title.slice(0, idx)}
        <span style={{ color: '#f97316' }}>{title.slice(idx, idx + q.length)}</span>
        {title.slice(idx + q.length)}
      </>
    )
  }

  return (
    <div style={{ position: 'relative' }}>
      {/* Backdrop dim when dropdown is open */}
      {open && (
        <div
          style={{
            position: 'fixed', inset: 0, zIndex: 150,
            background: 'rgba(0,0,0,0.3)',
          }}
          onClick={() => { setOpen(false); setQuery(''); inputRef.current?.blur() }}
        />
      )}
      <input
        ref={inputRef}
        value={query}
        onChange={e => setQuery(e.target.value)}
        onFocus={() => results.length > 0 && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 200)}
        onKeyDown={handleKeyDown}
        placeholder="⌘K  Search titles, #tags, /folders..."
        style={{
          width: 240, padding: '4px 10px',
          background: '#111827',
          border: open ? '1px solid #6366f1' : '1px solid #1e293b',
          borderRadius: 5, color: '#e8e8f0',
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 10, outline: 'none',
          boxShadow: open ? '0 0 12px rgba(99,102,241,0.2)' : 'none',
          transition: 'border-color 0.15s, box-shadow 0.15s',
        }}
      />

      {open && results.length > 0 && (
        <div style={{
          position: 'absolute', top: '100%', right: 0,
          width: 360, marginTop: 4,
          background: '#111827',
          border: '1px solid #1e293b',
          borderRadius: 8,
          boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
          zIndex: 200, overflow: 'hidden',
          fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
        }}>
          <div style={{
            padding: '6px 12px', borderBottom: '1px solid #1e293b',
            color: '#6b7a99', fontSize: 9, textTransform: 'uppercase', letterSpacing: '1px',
          }}>
            {results.length} results · <span style={{ color: '#4b5563' }}>Cmd+click for new tab</span>
          </div>
          <div style={{ padding: 4 }}>
            {results.map((node, i) => (
              <div
                key={node.id}
                onMouseDown={(e) => handleSelect(node.id, e.metaKey || e.ctrlKey)}
                onMouseEnter={() => setSelectedIdx(i)}
                style={{
                  padding: '8px 10px',
                  background: i === selectedIdx ? 'rgba(99,102,241,0.1)' : 'transparent',
                  borderRadius: 4, cursor: 'pointer',
                  display: 'flex', alignItems: 'center', gap: 8,
                  marginBottom: i < results.length - 1 ? 2 : 0,
                }}
              >
                <span style={{ color: getNodeColor(node.type), fontSize: 9 }}>●</span>
                <div style={{ minWidth: 0, overflow: 'hidden' }}>
                  <div style={{ color: '#e8e8f0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {highlight(node.title)}
                  </div>
                  <div style={{ color: '#4b5563', fontSize: 9, marginTop: 1 }}>
                    {node.folder}/ · {node.tags.slice(0, 3).map(t => `#${t}`).join(' ')}
                  </div>
                </div>
              </div>
            ))}
          </div>
          <div style={{
            padding: '6px 12px', borderTop: '1px solid #1e293b',
            color: '#4b5563', fontSize: 9,
            display: 'flex', gap: 12,
          }}>
            <span>↑↓ navigate</span>
            <span>⏎ open</span>
            <span>⌘⏎ new tab</span>
            <span>esc close</span>
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add visualizer/components/UnifiedSearch.tsx
git commit -m "feat(visualizer): add UnifiedSearch component with prefix modes"
```

---

### Task 6: Create ViewToggle and Toolbar components

**Files:**
- Create: `visualizer/components/ViewToggle.tsx`
- Create: `visualizer/components/Toolbar.tsx`

- [ ] **Step 1: Create ViewToggle.tsx**

```typescript
// visualizer/components/ViewToggle.tsx
'use client'

interface Props {
  mode: 'read' | 'graph'
  onToggle: (mode: 'read' | 'graph') => void
}

export function ViewToggle({ mode, onToggle }: Props) {
  return (
    <div style={{
      display: 'flex', background: '#1e293b', borderRadius: 5, padding: 2,
    }}>
      {(['read', 'graph'] as const).map(m => (
        <button
          key={m}
          onClick={() => onToggle(m)}
          style={{
            padding: '3px 10px', borderRadius: 4, border: 'none',
            background: mode === m ? '#6366f1' : 'transparent',
            color: mode === m ? '#e8e8f0' : '#6b7a99',
            fontSize: 10, fontFamily: "'JetBrains Mono', monospace",
            cursor: 'pointer', textTransform: 'capitalize',
            transition: 'all 0.15s',
          }}
        >
          {m === 'read' ? 'Read' : 'Graph'}
        </button>
      ))}
    </div>
  )
}
```

- [ ] **Step 2: Create Toolbar.tsx**

```typescript
// visualizer/components/Toolbar.tsx
'use client'

import { useEffect } from 'react'
import type { NoteNode } from '@/lib/graph'
import { TabBar } from './TabBar'
import { UnifiedSearch } from './UnifiedSearch'
import { ViewToggle } from './ViewToggle'

interface Props {
  // Sidebar
  onToggleSidebar: () => void
  // Tabs
  tabs: string[]
  activeTab: string | null
  nodeMap: Map<string, NoteNode>
  onSwitchTab: (stem: string) => void
  onCloseTab: (stem: string) => void
  // Search
  nodes: NoteNode[]
  onSearchSelect: (stem: string, newTab: boolean) => void
  // View mode
  viewMode: 'read' | 'graph'
  onViewModeChange: (mode: 'read' | 'graph') => void
}

export function Toolbar({
  onToggleSidebar,
  tabs, activeTab, nodeMap, onSwitchTab, onCloseTab,
  nodes, onSearchSelect,
  viewMode, onViewModeChange,
}: Props) {
  // Global keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'b') {
        e.preventDefault()
        onToggleSidebar()
      }
      if ((e.metaKey || e.ctrlKey) && e.key === '\\') {
        e.preventDefault()
        onViewModeChange(viewMode === 'read' ? 'graph' : 'read')
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onToggleSidebar, viewMode, onViewModeChange])

  return (
    <div style={{
      background: '#0a0f1e',
      padding: '6px 12px',
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      borderBottom: '1px solid #1e293b',
      flexShrink: 0,
      height: 'var(--toolbar-height, 42px)',
    }}>
      {/* Left: hamburger + tabs */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 0 }}>
        <button
          onClick={onToggleSidebar}
          style={{
            background: 'none', border: 'none',
            color: '#6b7a99', cursor: 'pointer',
            fontSize: 14, padding: '2px 4px',
            flexShrink: 0,
          }}
          title="Toggle sidebar (⌘B)"
        >
          ☰
        </button>
        <TabBar
          tabs={tabs}
          activeTab={activeTab}
          nodeMap={nodeMap}
          onSwitch={onSwitchTab}
          onClose={onCloseTab}
        />
      </div>

      {/* Right: search + toggle */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
        <UnifiedSearch nodes={nodes} onSelect={onSearchSelect} />
        <ViewToggle mode={viewMode} onToggle={onViewModeChange} />
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Commit**

```bash
git add visualizer/components/ViewToggle.tsx visualizer/components/Toolbar.tsx
git commit -m "feat(visualizer): add ViewToggle and Toolbar components"
```

---

### Task 7: Create ReadingPane component

**Files:**
- Create: `visualizer/components/ReadingPane.tsx`

Reuses markdown rendering logic from NotePanel but full-width with centered column.

- [ ] **Step 1: Create ReadingPane.tsx**

```typescript
// visualizer/components/ReadingPane.tsx
'use client'

import { useState, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { getNodeColor } from '@/lib/sigma-colors'
import type { NoteNode } from '@/lib/graph'

interface Props {
  node: NoteNode | null
  fetchContent: (stem: string) => Promise<string>
  onNavigate: (stem: string, newTab: boolean) => void
}

export function ReadingPane({ node, fetchContent, onNavigate }: Props) {
  const [content, setContent] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!node) { setContent(''); setError(null); return }
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchContent(node.id)
      .then(c => { if (!cancelled) setContent(c) })
      .catch(e => { if (!cancelled) setError(e.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [node, fetchContent])

  // Handle wikilink click
  const handleWikilink = useCallback((stem: string, e: React.MouseEvent) => {
    onNavigate(stem, e.metaKey || e.ctrlKey)
  }, [onNavigate])

  if (!node) {
    return (
      <div style={{
        flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: '#6b7a99', fontFamily: "'JetBrains Mono', monospace", fontSize: 12,
        flexDirection: 'column', gap: 8,
      }}>
        <div style={{ fontSize: 24, opacity: 0.3 }}>◈</div>
        <div>Open a note from the sidebar or press ⌘K to search.</div>
      </div>
    )
  }

  // Parse frontmatter
  const fm = content.match(/^---\n([\s\S]*?)\n---/)
  const fmBody = fm?.[1] ?? ''
  const noteDate = fmBody.match(/^date:\s*(.+)$/m)?.[1]?.trim() ?? null
  const confidence = fmBody.match(/^confidence:\s*(.+)$/m)?.[1]?.trim() ?? null
  const relatedStems: string[] = (() => {
    const relatedLine = fmBody.match(/^related:\s*(.+)$/m)
    if (!relatedLine) return []
    const stems: string[] = []
    const re = /\[\[([^\]]+)\]\]/g
    let m: RegExpExecArray | null
    while ((m = re.exec(relatedLine[1])) !== null) stems.push(m[1])
    return stems
  })()

  const displayContent = content
    .replace(/^---[\s\S]*?---\n/, '')
    .replace(/\[\[([^\]]+)\]\]/g, (_, stem) => `[${stem}](wikilink:${encodeURIComponent(stem)})`)

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: '32px 48px', fontFamily: "'Syne', sans-serif" }}>
      <div style={{ maxWidth: 720, margin: '0 auto' }}>
        {/* Header */}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
          <span style={{
            background: `${getNodeColor(node.type)}22`,
            border: `1px solid ${getNodeColor(node.type)}55`,
            color: getNodeColor(node.type),
            padding: '2px 8px', borderRadius: 3,
            fontSize: 10, fontFamily: "'Oxanium', sans-serif",
            fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em',
          }}>
            {node.type}
          </span>
          {noteDate && <span style={{ color: '#6b7a99', fontSize: 11 }}>{noteDate}</span>}
          {confidence && (
            <>
              <span style={{ color: '#6b7a99', fontSize: 11 }}>·</span>
              <span style={{
                color: confidence === 'high' ? '#10b981' : confidence === 'medium' ? '#f59e0b' : '#6b7a99',
                fontSize: 11,
              }}>
                {confidence} confidence
              </span>
            </>
          )}
        </div>

        {/* Title */}
        <h1 style={{
          fontFamily: "'Oxanium', sans-serif",
          fontSize: 24, fontWeight: 700,
          color: '#e8e8f0', lineHeight: 1.3,
          margin: '0 0 12px',
        }}>
          {node.title}
        </h1>

        {/* Tags */}
        <div style={{ display: 'flex', gap: 6, marginBottom: 20, flexWrap: 'wrap' }}>
          {node.tags.map(tag => (
            <span key={tag} style={{
              background: '#1e293b', color: '#9ca3af',
              padding: '2px 8px', borderRadius: 12, fontSize: 10,
            }}>
              #{tag}
            </span>
          ))}
        </div>

        {/* Loading / Error */}
        {loading && (
          <div style={{ color: '#6b7a99', fontFamily: "'JetBrains Mono', monospace", fontSize: 12, paddingTop: 20 }}>
            Loading...
          </div>
        )}
        {error && (
          <div style={{ color: '#ef4444', fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }}>
            Could not load note: {node.id}
          </div>
        )}

        {/* Related */}
        {!loading && !error && relatedStems.length > 0 && (
          <div style={{
            marginBottom: 16, padding: '8px 12px',
            background: 'rgba(123,97,255,0.06)',
            border: '1px solid rgba(123,97,255,0.15)',
            borderRadius: 6,
          }}>
            <div style={{
              fontSize: 9, fontFamily: "'Oxanium', sans-serif", color: '#6b7a99',
              textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6,
            }}>Related</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {relatedStems.map(stem => (
                <span
                  key={stem}
                  className="wikilink"
                  onClick={(e) => handleWikilink(stem, e)}
                  style={{ fontSize: 12 }}
                >
                  {stem}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Body */}
        {!loading && !error && displayContent && (
          <div className="note-markdown">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                a: ({ href, children }: any) => {
                  if (href?.startsWith('wikilink:')) {
                    const stem = decodeURIComponent(href.slice(9))
                    return (
                      <span
                        className="wikilink"
                        onClick={(e: React.MouseEvent) => handleWikilink(stem, e)}
                      >
                        {children}
                      </span>
                    )
                  }
                  return <a href={href} target="_blank" rel="noreferrer">{children}</a>
                },
              }}
            >
              {displayContent}
            </ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add visualizer/components/ReadingPane.tsx
git commit -m "feat(visualizer): add ReadingPane component with full-width markdown"
```

---

### Task 8: Add neighborhood mode to GraphCanvas

**Files:**
- Modify: `visualizer/components/GraphCanvas.tsx:8-11` (Props interface)
- Modify: `visualizer/components/GraphCanvas.tsx:245-265` (node visibility logic)

Add `neighborhoodCenter` and `neighborhoodHops` props. When set, compute the N-hop BFS subgraph and filter nodes/edges accordingly. Outer-hop nodes render at 0.4 opacity.

- [ ] **Step 1: Add new props to GraphCanvas**

Add to the `Props` interface in `GraphCanvas.tsx`:

```typescript
// Change onNodeClick to include newTab flag:
onNodeClick: (stem: string, newTab: boolean) => void
// Add after onBackgroundClick:
neighborhoodCenter?: string | null
neighborhoodHops?: number
```

Also update the `clickNode` handler (around line 468) to pass modifier key state:

```typescript
sigma.on('clickNode', ({ node, event }: { node: string; event: { original: MouseEvent } }) => {
  if (dragHasMovedRef.current) return
  const newTab = event.original.metaKey || event.original.ctrlKey
  onNodeClick(node, newTab)
  // ... rest of highlight logic unchanged
})
```

- [ ] **Step 2: Add neighborhood computation**

Add a `useMemo` that computes the neighborhood set and hop distances. Place it after the refs block (around line 90):

```typescript
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
      // Find neighbors via ALL edges (unfiltered)
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
```

Store a ref for the layout loop to access:

```typescript
const neighborhoodRef = useRef(neighborhoodInfo)
useEffect(() => { neighborhoodRef.current = neighborhoodInfo }, [neighborhoodInfo])
```

- [ ] **Step 3: Integrate neighborhood into nodeReducer**

In the `nodeReducer` (around line 333), add neighborhood filtering at the top (before the `filteredNodesRef` check):

```typescript
// At the start of nodeReducer, before the filteredNodes check:
const nh = neighborhoodRef.current
if (nh && !nh.nodes.has(node)) {
  return { ...data, hidden: true, label: '' }
}
```

Then, at the end of nodeReducer, before the final return, add outer-hop opacity:

```typescript
// Just before the final return { ...data, label }:
if (nh) {
  const hopDist = nh.distances.get(node)
  if (hopDist === nh.maxHop) {
    // Sigma uses hex colors — append alpha hex for 0.4 opacity
    const dimColor = (data.originalColor || data.color) + '66'
    return { ...data, label, color: dimColor, size: data.size * 0.8 }
  }
}
```

Also integrate into the layout loop's visible set computation (around line 510):

```typescript
// In layoutLoop, after building visibleSet from filters:
const nh = neighborhoodRef.current
if (nh) {
  for (const n of [...visibleSet]) {
    if (!nh.nodes.has(n)) visibleSet.delete(n)
  }
}
```

- [ ] **Step 4: Integrate neighborhood into edgeReducer**

In the `edgeReducer` (around line 361), add neighborhood filtering at the top:

```typescript
// At the start of edgeReducer, before the filteredNodes check:
const nh = neighborhoodRef.current
if (nh) {
  const src = graph.source(edge)
  const tgt = graph.target(edge)
  if (!nh.nodes.has(src) || !nh.nodes.has(tgt)) return { ...data, hidden: true }
}
```

- [ ] **Step 5: Trigger sigma refresh when neighborhoodCenter changes**

```typescript
useEffect(() => {
  sigmaRef.current?.refresh()
  if (neighborhoodCenter) reheat()
}, [neighborhoodCenter, neighborhoodHops, reheat])
```

- [ ] **Step 6: Verify compilation**

Run: `cd /Users/probello/Repos/parsidion/visualizer && bunx tsc --noEmit`

- [ ] **Step 7: Commit**

```bash
git add visualizer/components/GraphCanvas.tsx
git commit -m "feat(visualizer): add neighborhood mode to GraphCanvas"
```

---

### Task 9: Rewrite page.tsx with new layout

**Files:**
- Modify: `visualizer/app/page.tsx` (full rewrite)

This replaces the entire page with the new Obsidian-style layout using all new components.

- [ ] **Step 1: Rewrite page.tsx**

```typescript
// visualizer/app/page.tsx
'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import dynamic from 'next/dynamic'
import { loadGraphData } from '@/lib/graph'
import type { GraphData } from '@/lib/graph'
import type { GraphCanvasHandle } from '@/components/GraphCanvas'
import { useVisualizerState } from '@/lib/useVisualizerState'
import { FileExplorer } from '@/components/FileExplorer'
import { Toolbar } from '@/components/Toolbar'
import { ReadingPane } from '@/components/ReadingPane'
import { HUDPanel } from '@/components/HUDPanel'

const GraphCanvas = dynamic(() => import('@/components/GraphCanvas').then(m => m.GraphCanvas), {
  ssr: false,
  loading: () => (
    <div style={{
      position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: 'Oxanium, sans-serif', color: '#00FFC8', fontSize: 14, letterSpacing: '0.1em',
    }}>
      <div>
        <div style={{ textAlign: 'center', marginBottom: 16 }}>◈</div>
        <div>INITIALIZING GRAPH...</div>
      </div>
    </div>
  ),
})

export default function Home() {
  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const graphCanvasRef = useRef<GraphCanvasHandle>(null)

  useEffect(() => {
    localStorage.removeItem('vv:isLayoutRunning')
    if (!localStorage.getItem('vv:threshold_v2')) {
      localStorage.removeItem('vv:threshold')
      localStorage.setItem('vv:threshold_v2', '1')
    }
    loadGraphData()
      .then(setGraphData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const state = useVisualizerState(graphData)

  // Auto-collapse sidebar on narrow viewports
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 768px)')
    const handler = (e: MediaQueryListEvent) => {
      if (e.matches) state.setSidebarCollapsed(true)
    }
    if (mq.matches) state.setSidebarCollapsed(true)
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSearchSelect = useCallback((stem: string, newTab: boolean) => {
    state.openNote(stem, newTab)
    if (state.viewMode === 'graph') {
      graphCanvasRef.current?.flyToNode(stem)
      graphCanvasRef.current?.selectNode(stem)
    }
  }, [state])

  const handleGraphNodeClick = useCallback((stem: string, newTab: boolean) => {
    state.openNote(stem, newTab)
    state.setSelectedNode(stem)
  }, [state])

  const handleNavigate = useCallback((stem: string, newTab: boolean) => {
    const resolved = state.resolveWikilink(stem) ?? stem
    state.openNote(resolved, newTab)
    if (state.viewMode === 'graph') {
      graphCanvasRef.current?.flyToNode(resolved)
    }
  }, [state])

  // Determine neighborhood center for graph mode
  const neighborhoodCenter = state.graphScope === 'local' ? state.activeTab : null

  return (
    <main style={{
      position: 'fixed', inset: 0,
      background: 'radial-gradient(ellipse at 50% 50%, #0C0F1E 0%, #060608 70%)',
      display: 'flex', flexDirection: 'column',
      overflow: 'hidden',
      // CSS variable for toolbar height
      ['--toolbar-height' as string]: '42px',
    }}>
      {/* Star field */}
      <div style={{
        position: 'absolute', inset: 0,
        backgroundImage: 'radial-gradient(1px 1px at 10% 15%, rgba(255,255,255,0.15) 0%, transparent 100%), radial-gradient(1px 1px at 35% 60%, rgba(255,255,255,0.1) 0%, transparent 100%), radial-gradient(1px 1px at 75% 25%, rgba(255,255,255,0.12) 0%, transparent 100%), radial-gradient(1px 1px at 90% 80%, rgba(255,255,255,0.08) 0%, transparent 100%)',
        pointerEvents: 'none', zIndex: 0,
      }} />

      {loading && (
        <div style={{
          position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: 'Oxanium, sans-serif', color: '#00FFC8', fontSize: 14, letterSpacing: '0.12em',
          flexDirection: 'column', gap: 12, zIndex: 10,
        }}>
          <div style={{ fontSize: 24 }}>◈</div>
          <div>LOADING GRAPH DATA...</div>
        </div>
      )}

      {error && !loading && (
        <div style={{
          position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: 'Oxanium, sans-serif', color: '#ef4444', fontSize: 13, flexDirection: 'column', gap: 8, zIndex: 10,
        }}>
          <div>⚠ Failed to load graph.json</div>
          <div style={{ color: '#6B7A99', fontSize: 11 }}>{error}</div>
          <div style={{ color: '#6B7A99', fontSize: 11 }}>Run: make graph</div>
        </div>
      )}

      {!loading && !error && graphData && (
        <>
          {/* Toolbar */}
          <Toolbar
            onToggleSidebar={() => state.setSidebarCollapsed(c => !c)}
            tabs={state.openTabs}
            activeTab={state.activeTab}
            nodeMap={state.nodeMap}
            onSwitchTab={state.switchTab}
            onCloseTab={state.closeTab}
            nodes={graphData.nodes}
            onSearchSelect={handleSearchSelect}
            viewMode={state.viewMode}
            onViewModeChange={state.setViewMode}
          />

          {/* Body: sidebar + content */}
          <div style={{ flex: 1, display: 'flex', overflow: 'hidden', position: 'relative' }}>
            {/* File Explorer */}
            <FileExplorer
              fileTree={state.fileTree}
              activeTab={state.activeTab}
              onSelectNote={(stem, newTab) => state.openNote(stem, newTab)}
              width={state.sidebarWidth}
              onWidthChange={state.setSidebarWidth}
              collapsed={state.sidebarCollapsed}
              totalNotes={graphData.nodes.length}
            />

            {/* Content area */}
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, position: 'relative' }}>
              {state.viewMode === 'read' ? (
                <ReadingPane
                  node={state.activeNode}
                  fetchContent={state.fetchNoteContent}
                  onNavigate={handleNavigate}
                />
              ) : (
                /* Graph mode */
                <div style={{ flex: 1, position: 'relative' }}>
                  {/* Scope indicator */}
                  <div style={{
                    position: 'absolute', top: 12, left: 12,
                    display: 'flex', gap: 6, zIndex: 10,
                    fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
                  }}>
                    {state.activeTab && state.graphScope === 'local' && (
                      <div style={{
                        background: 'rgba(15,23,42,0.92)',
                        border: '1px solid #1e293b', borderRadius: 5,
                        padding: '4px 10px',
                        display: 'flex', gap: 8, alignItems: 'center',
                      }}>
                        <span style={{ color: '#f97316' }}>●</span>
                        <span style={{ color: '#e8e8f0' }}>{state.activeTab}</span>
                        <span style={{ color: '#6b7a99' }}>· 2 hops</span>
                      </div>
                    )}
                    <button
                      onClick={() => state.setGraphScope(state.graphScope === 'local' ? 'full' : 'local')}
                      style={{
                        background: 'rgba(15,23,42,0.92)',
                        border: '1px solid #1e293b', borderRadius: 5,
                        padding: '4px 10px',
                        color: '#7b61ff', cursor: 'pointer',
                        fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
                      }}
                    >
                      {state.graphScope === 'local' ? 'Show Full Vault ⤢' : 'Show Neighborhood ⤡'}
                    </button>
                  </div>

                  <GraphCanvas
                    ref={graphCanvasRef}
                    data={graphData}
                    threshold={state.threshold}
                    graphSource={state.graphSource}
                    activeTypes={state.activeTypes}
                    showDaily={state.showDaily}
                    hideIsolated={state.hideIsolated}
                    labelsOnHoverOnly={state.labelsOnHoverOnly}
                    showOverlayEdges={state.showOverlayEdges}
                    filterNodesBySimilarity={state.filterNodesBySimilarity}
                    selectedNode={state.selectedNode}
                    onNodeClick={handleGraphNodeClick}
                    onBackgroundClick={() => state.setSelectedNode(null)}
                    scalingRatio={state.scalingRatio}
                    gravity={state.gravity}
                    slowDown={state.slowDown}
                    edgeWeightInfluence={state.edgeWeightInfluence}
                    startTemperature={state.startTemperature}
                    stopThreshold={state.stopThreshold}
                    isLayoutRunning={state.isLayoutRunning}
                    onLayoutStop={() => state.setIsLayoutRunning(false)}
                    onLayoutRestart={() => state.setIsLayoutRunning(true)}
                    neighborhoodCenter={neighborhoodCenter}
                    neighborhoodHops={2}
                  />

                  {/* HUD Panel — graph mode only */}
                  <HUDPanel
                    threshold={state.threshold}
                    onThresholdChange={state.setThreshold}
                    graphSource={state.graphSource}
                    onGraphSourceChange={state.setGraphSource}
                    showOverlayEdges={state.showOverlayEdges}
                    onToggleOverlayEdges={state.toggleOverlayEdges}
                    filterNodesBySimilarity={state.filterNodesBySimilarity}
                    onToggleFilterNodesBySimilarity={state.toggleFilterNodesBySimilarity}
                    activeTypes={state.activeTypes}
                    onToggleType={state.handleToggleType}
                    showDaily={state.showDaily}
                    onToggleDaily={state.toggleShowDaily}
                    hideIsolated={state.hideIsolated}
                    onToggleHideIsolated={state.toggleHideIsolated}
                    labelsOnHoverOnly={state.labelsOnHoverOnly}
                    onToggleLabelsOnHoverOnly={state.toggleLabelsOnHoverOnly}
                    nodeCount={state.stats.nodeCount}
                    edgeCount={state.stats.edgeCount}
                    avgScore={state.stats.avgScore}
                    scalingRatio={state.scalingRatio}
                    onScalingRatioChange={state.setScalingRatio}
                    gravity={state.gravity}
                    onGravityChange={state.setGravity}
                    slowDown={state.slowDown}
                    onSlowDownChange={state.setSlowDown}
                    edgeWeightInfluence={state.edgeWeightInfluence}
                    onEdgeWeightInfluenceChange={state.setEdgeWeightInfluence}
                    startTemperature={state.startTemperature}
                    onStartTemperatureChange={state.setStartTemperature}
                    stopThreshold={state.stopThreshold}
                    onStopThresholdChange={state.setStopThreshold}
                    isLayoutRunning={state.isLayoutRunning}
                    onToggleLayout={() => state.setIsLayoutRunning(r => !r)}
                    onResetSimSettings={state.resetSimSettings}
                    canvasRef={graphCanvasRef}
                  />
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </main>
  )
}
```

- [ ] **Step 2: Verify compilation**

Run: `cd /Users/probello/Repos/parsidion/visualizer && bunx tsc --noEmit`

Fix any type errors.

- [ ] **Step 3: Commit**

```bash
git add visualizer/app/page.tsx
git commit -m "feat(visualizer): rewrite page.tsx with Obsidian-style layout"
```

---

### Task 10: Update HUDPanel default position

**Files:**
- Modify: `visualizer/components/HUDPanel.tsx:94` (initial position)

- [ ] **Step 1: Change default position to offset below toolbar**

In `HUDPanel.tsx`, update the initial `pos` state:

```typescript
// Change from:
const [pos, setPos] = useState({ x: 16, y: 16 })
// To:
const [pos, setPos] = useState({ x: 16, y: 58 })  // 42px toolbar + 16px gap
```

- [ ] **Step 2: Remove `position: 'fixed'` and use `position: 'absolute'`**

Since HUDPanel is now rendered inside the graph container (which has `position: relative`), change:

```typescript
// In the outer div style, change:
position: 'absolute',  // was 'fixed'
```

- [ ] **Step 3: Commit**

```bash
git add visualizer/components/HUDPanel.tsx
git commit -m "fix(visualizer): offset HUDPanel below toolbar in new layout"
```

---

### Task 11: Delete removed components and verify full build

**Files:**
- Delete: `visualizer/components/NotePanel.tsx`
- Delete: `visualizer/components/SearchBox.tsx`

- [ ] **Step 1: Delete NotePanel.tsx and SearchBox.tsx**

```bash
rm visualizer/components/NotePanel.tsx visualizer/components/SearchBox.tsx
```

- [ ] **Step 2: Remove any remaining imports**

Search for lingering imports of NotePanel or SearchBox:

Run: `cd /Users/probello/Repos/parsidion && grep -rn "NotePanel\|SearchBox" visualizer/ --include="*.tsx" --include="*.ts"`

Fix any remaining references.

- [ ] **Step 3: Run full build**

Run: `cd /Users/probello/Repos/parsidion/visualizer && bun run build`

Expected: Build succeeds with no errors.

- [ ] **Step 4: Run dev server and manual test**

Run: `cd /Users/probello/Repos/parsidion && make visualizer`

Open http://localhost:3999 and verify:
- Sidebar shows file tree with folders
- Clicking a note opens it in the reading pane
- ⌘K search works with title, #tag, /folder modes
- Tabs appear, close, and switch correctly
- Read/Graph toggle switches views
- Graph mode shows neighborhood for active note
- "Show Full Vault" button works
- HUD panel is positioned correctly in graph mode
- Sidebar resize drag works
- ⌘B collapses sidebar

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(visualizer): complete Obsidian-style layout redesign

- File explorer sidebar with folder tree, resize, collapse
- Tabbed reading pane with full-width markdown rendering
- Unified search (⌘K) with title, tag, and folder prefix modes
- Read/Graph mode toggle with local neighborhood default
- Remove NotePanel and SearchBox (replaced by ReadingPane + UnifiedSearch)"
```

---

### Task 12: Fix any remaining type/lint issues

**Files:**
- Modify: various (as needed)

- [ ] **Step 1: Run typecheck**

Run: `cd /Users/probello/Repos/parsidion/visualizer && bunx tsc --noEmit`

Fix any errors.

- [ ] **Step 2: Run lint**

Run: `cd /Users/probello/Repos/parsidion/visualizer && bun run lint`

Fix any lint issues.

- [ ] **Step 3: Commit if any fixes needed**

```bash
git add -A
git commit -m "fix(visualizer): resolve type and lint issues"
```
