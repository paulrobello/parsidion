export interface NoteNode {
  id: string
  title: string
  type: string
  folder: string
  path: string
  tags: string[]
  incoming_links: number
  mtime: number
}

export interface GraphEdge {
  s: string
  t: string
  w: number
  kind: 'semantic' | 'wiki'
}

export interface GraphData {
  meta: {
    generated: string
    note_count: number
    edge_count: number
    min_semantic_threshold: number
    /** On-disk graph.json shape version (GRAPH_SCHEMA_VERSION in build_graph.py). Required since ENH-002 (schema_version 2). */
    schema_version?: number
    /** Whether Daily-folder notes are included in the node set (ENH-002). Required since schema_version 2. */
    include_daily?: boolean
    /** Maximum semantic edges kept per note (top-K nearest neighbours); 0 disables the cap. Absent on graphs built before ENH-001. */
    max_neighbors?: number
    /** True when this graph was produced by an incremental rebuild (ENH-002). Absent on full-rebuild graphs. */
    incremental?: boolean
    /** Wiki edges contributed by parsight body-link enrichment; absent when the enrichment was skipped or added nothing. */
    parsight_body_links?: number
    /** Outcome of parsight body-link enrichment when attempted (absent when --no-parsight was passed): 'fresh' = ran; 'skipped:index-stale' / '-absent' / '-invalid' = non-fresh index, skipped; 'unavailable' / 'error' = backend failure. */
    parsight_body_status?: string
  }
  nodes: NoteNode[]
  edges: GraphEdge[]
}

export type GraphSource = 'semantic' | 'wiki'

export function filterEdges(
  edges: GraphEdge[],
  source: GraphSource,
  threshold: number
): GraphEdge[] {
  return edges.filter(e => {
    if (source === 'semantic' && e.kind !== 'semantic') return false
    if (source === 'wiki' && e.kind !== 'wiki') return false
    if (e.kind === 'semantic' && e.w < threshold) return false
    return true
  })
}

export async function loadGraphData(vault?: string | null): Promise<GraphData> {
  const url = vault ? `/api/graph?vault=${encodeURIComponent(vault)}` : '/api/graph'
  const res = await fetch(url)
  if (!res.ok) throw new Error('Failed to load graph.json')
  return res.json()
}

/**
 * Shape of a delta response from GET /api/graph/delta. The server returns
 * `{full: true, ...}` when the prior snapshot is unknown or the delta is too
 * large; the caller must then fall back to {@link loadGraphData}. Otherwise
 * `addedNodes/removedNodes/addedEdges/removedEdges` describe the patch from
 * `since` to the current on-disk graph.json.
 */
export interface GraphDeltaResponse {
  full: boolean
  reason?: string
  generated?: string
  addedNodes?: NoteNode[]
  removedNodes?: string[]
  addedEdges?: GraphEdge[]
  removedEdges?: Array<{ s: string; t: string; kind: GraphEdge['kind'] }>
}

/**
 * Apply a delta response to a base GraphData. Returns a new GraphData (does
 * not mutate the input) or null if `response.full` is true (caller must do a
 * full refetch instead).
 *
 * Edge removal matches by composite (s, t, kind); this mirrors the server's
 * edge-key definition (a weight change alone does NOT count as a removal).
 */
export function applyGraphDelta(
  base: GraphData,
  response: GraphDeltaResponse,
): GraphData | null {
  if (response.full) return null
  const removedNodes = new Set(response.removedNodes ?? [])
  const removedEdgeKeys = new Set(
    (response.removedEdges ?? []).map(e => `${e.s}|${e.t}|${e.kind}`),
  )
  const keptNodes = base.nodes.filter(n => !removedNodes.has(n.id))
  const nodes = [...keptNodes, ...(response.addedNodes ?? [])]
  // After applying the delta, every retained edge must reference a node that
  // still exists. The server's removedEdges list is the authoritative source
  // of edge removals, but a removed node also orphans any edge still in
  // `base.edges` that the server happened not to enumerate (defense-in-depth
  // — the server DOES enumerate them, this just guarantees the invariant
  // for any future caller). Same logic for added edges whose endpoints
  // aren't in the final node set.
  const nodeIdSet = new Set(nodes.map(n => n.id))
  const edges: GraphEdge[] = []
  for (const e of base.edges) {
    if (removedEdgeKeys.has(`${e.s}|${e.t}|${e.kind}`)) continue
    if (!nodeIdSet.has(e.s) || !nodeIdSet.has(e.t)) continue
    edges.push(e)
  }
  for (const e of response.addedEdges ?? []) {
    if (nodeIdSet.has(e.s) && nodeIdSet.has(e.t)) edges.push(e)
  }
  return {
    meta: {
      ...base.meta,
      generated: response.generated ?? base.meta.generated,
      note_count: nodes.length,
      edge_count: edges.length,
    },
    nodes,
    edges,
  }
}

/**
 * ARC-015 step 4: fetch a delta from the server. Falls back to a full
 * loadGraphData when the server signals `full: true` (no cached baseline,
 * baseline evicted, or delta exceeds the rebuild threshold).
 *
 * `since` is the `meta.generated` timestamp of the current graphData the
 * caller holds; the server uses it as the diff baseline.
 */
export async function loadGraphDelta(
  since: string,
  vault?: string | null,
): Promise<{ delta: GraphDeltaResponse } | { full: GraphData }> {
  const params = new URLSearchParams({ since })
  if (vault) params.set('vault', vault)
  const res = await fetch(`/api/graph/delta?${params.toString()}`)
  if (!res.ok) {
    // Fall back to full fetch on any error — delta is an optimization.
    return { full: await loadGraphData(vault) }
  }
  const delta = (await res.json()) as GraphDeltaResponse
  if (delta.full) {
    return { full: await loadGraphData(vault) }
  }
  return { delta }
}
