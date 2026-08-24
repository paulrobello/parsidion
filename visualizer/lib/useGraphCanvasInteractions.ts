'use client'

// QA-008: GraphCanvas right-click context-menu actions, path-finding, and toast
// logic extracted out of components/GraphCanvas.tsx into this hook. Owns the
// toast banner (state + timer + showToast + unmount cleanup) and the menu action
// callbacks, and houses findWikiPath (relocated from GraphCanvas). GraphCanvas's
// JSX now calls these actions instead of inlining ~50 lines of handler bodies.
//
// The actions are plain functions, not useCallback: they are invoked only from
// onClick handlers and have no memoized consumers, so memoizing them is
// pointless — and useCallback bodies that write refs are exactly what the React
// Compiler's preserve-manual-memoization rule rejects. Plain functions are both
// simpler and correct here.
//
// What stays in GraphCanvas: the nodeContextMenu state and the pathSourceRef /
// pathNodesRef / pathEdgesRef refs. Those are consumed by useSigmaInstance at
// construction (the right-click handler calls setNodeContextMenu; the reducers
// read the path refs), and useSigmaInstance owns sigmaRef/graphRef — so there is
// a construction-order cycle the codebase already resolves with the callbackRef
// pattern (see useSigmaInstance.ts flyToNodeRef/applyNodeDeltaRef). GraphCanvas
// passes those refs/setter into this hook; the hook owns the logic that acts on
// them. A full lift of the state would require useSigmaInstance to accept
// externally-owned sigmaRef/graphRef — a larger change left as a follow-up.
//
// The audit also named "edge-pruning" for this hook; that was judged not worth
// extracting — pruning is a single inline line inside the edge-rebuild effect
// (this hook owns *interactions*; pruning is a pipeline step in an effect, not a
// discrete interaction).

import { useState, useRef, useEffect } from 'react'
import type { RefObject, Dispatch, SetStateAction } from 'react'
import type Sigma from 'sigma'
import type { AbstractGraph } from 'graphology-types'
import type { RenderOptions } from '@/lib/useSigmaInstance'

/** Menu position + target stem, or null when the menu is closed. */
export interface NodeContextMenuState {
  stem: string
  x: number
  y: number
  /** Path-origin stem captured from the ref at menu-open time. */
  pathSource: string | null
}

// QA-004: findWikiPath lives here — it is only called from the Find Path action.
// BFS over non-overlay wiki edges; returns the stem path + the graphology edge
// ids traversed, or null when no wiki-link path exists.
function findWikiPath(
  from: string,
  to: string,
  graph: AbstractGraph,
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
    const curr = queue.shift()
    if (curr === undefined) break // QA-006: replaces queue.shift()! — length>0 makes this unreachable
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
    const p = parent.get(curr)
    if (!p) break // QA-006: replaces parent.get(curr)! — BFS reached `to`, so every step back has a parent entry
    edgeIds.unshift(p.edgeId)
    curr = p.from
  }
  path.unshift(from)
  return { path, edgeIds }
}

export interface GraphCanvasInteractions {
  toastMsg: string | null
  showToast: (msg: string) => void
  /** Open the stem in the reading pane and close the menu. */
  openInReadingPane: (stem: string) => void
  /** Open the stem's history view and close the menu. */
  viewHistory: (stem: string) => void
  /** Find a wiki-link path from the stored origin to `to`; toast the breadcrumb. */
  findPathTo: (to: string) => void
  /** Remember `stem` as the path origin and close the menu. */
  setPathOrigin: (stem: string) => void
  /** Forget the path origin + any highlighted path, and close the menu. */
  clearPathOrigin: () => void
}

interface Options {
  graphRef: RefObject<AbstractGraph | null>
  sigmaRef: RefObject<Sigma | null>
  latest: RefObject<RenderOptions>
  setNodeContextMenu: Dispatch<SetStateAction<NodeContextMenuState | null>>
  pathSourceRef: RefObject<string | null>
  pathNodesRef: RefObject<Set<string>>
  pathEdgesRef: RefObject<Set<string>>
  onNodeClick: (stem: string, open: boolean, newTab: boolean) => void
  onOpenHistory?: (stem: string) => void
}

export function useGraphCanvasInteractions({
  graphRef,
  sigmaRef,
  latest,
  setNodeContextMenu,
  pathSourceRef,
  pathNodesRef,
  pathEdgesRef,
  onNodeClick,
  onOpenHistory,
}: Options): GraphCanvasInteractions {
  const [toastMsg, setToastMsg] = useState<string | null>(null)
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const showToast = (msg: string) => {
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current)
    setToastMsg(msg)
    toastTimerRef.current = setTimeout(() => setToastMsg(null), 4000)
  }

  // Clear any pending toast timer on unmount.
  useEffect(() => {
    return () => { if (toastTimerRef.current) clearTimeout(toastTimerRef.current) }
  }, [])

  const openInReadingPane = (stem: string) => {
    onNodeClick(stem, true, false)
    setNodeContextMenu(null)
  }

  const viewHistory = (stem: string) => {
    onOpenHistory?.(stem)
    setNodeContextMenu(null)
  }

  const findPathTo = (to: string) => {
    const graph = graphRef.current
    if (!graph) return
    const result = findWikiPath(pathSourceRef.current!, to, graph)
    setNodeContextMenu(null)
    if (result) {
      pathNodesRef.current = new Set(result.path)
      pathEdgesRef.current = new Set(result.edgeIds)
      const d = latest.current.data
      const titleMap = new Map(d?.nodes.map(n => [n.id, n.title]) ?? [])
      const breadcrumb = result.path.map(id => titleMap.get(id) ?? id).join(' → ')
      showToast(breadcrumb)
      pathSourceRef.current = null
    } else {
      pathNodesRef.current = new Set()
      pathEdgesRef.current = new Set()
      showToast('No wiki-link path found')
      // keep pathSourceRef set so the user can pick a different destination
    }
    sigmaRef.current?.refresh()
  }

  const setPathOrigin = (stem: string) => {
    pathSourceRef.current = stem
    pathNodesRef.current = new Set()
    pathEdgesRef.current = new Set()
    setNodeContextMenu(null)
    sigmaRef.current?.refresh()
  }

  const clearPathOrigin = () => {
    pathSourceRef.current = null
    pathNodesRef.current = new Set()
    pathEdgesRef.current = new Set()
    setNodeContextMenu(null)
    sigmaRef.current?.refresh()
  }

  return {
    toastMsg, showToast,
    openInReadingPane, viewHistory, findPathTo, setPathOrigin, clearPathOrigin,
  }
}
