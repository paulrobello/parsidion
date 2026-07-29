// app/api/graph/delta/route.ts
// ARC-015 step 3: server-side counterpart to lib/graphDelta.ts.
//
// lib/graphDelta.ts diffs two in-memory GraphData snapshots for the client
// (which nodes/edges were added or removed, and where to place the new
// ones). What it cannot do is avoid re-fetching the full 47.5 MB graph.json
// after a rebuild: that's this endpoint's job.
//
// Strategy:
//   - `since` query param = the `generated` timestamp the client currently
//     holds (from GraphData.meta.generated).
//   - Load the current graph.json, partition its nodes/edges into
//     added/removed relative to the snapshot at `since`.
//   - If the snapshot at `since` is unavailable (client disconnected for a
//     long time, server cache evicted it, the timestamp doesn't match) OR
//     the delta would be too large (vault switch, mass rebuild), return a
//     full-document sentinel so the client falls back to GET /api/graph.
//
// The cache lives in module scope and is keyed by vault path. It holds the
// last N snapshots so a brief rotation (the common case — last 1-2 rebuilds)
// doesn't force a full refetch. A miss is correct, just suboptimal.
import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { resolveVault, VaultConfigError } from '@/lib/vaultResolver'
import { withApi } from '@/lib/apiAuth'
import type { GraphData, GraphEdge, NoteNode } from '@/lib/graph'

/** Maximum historical snapshots to retain per vault. */
const MAX_SNAPSHOTS_PER_VAULT = 8

interface CachedSnapshot {
  generated: string
  nodes: NoteNode[]
  /** Compressed representation of edges for the diff (src,tgt,kind,w only). */
  edgeKeys: Set<string>
  /** Original edges so an "added" response can return the full objects. */
  edges: GraphEdge[]
  /** Set of node ids, stored separately for O(1) added/removed partition. */
  nodeIds: Set<string>
}

interface VaultCache {
  /** Keyed by `generated` timestamp string. Insertion-ordered (Map), so the
   *  oldest snapshot is the first entry — that's the one we evict. */
  byGenerated: Map<string, CachedSnapshot>
}

const cache = new Map<string, VaultCache>()

/** Rebuild the cache entry for the current on-disk graph.json. Returns null
 *  if the file is missing or unparseable. */
function loadSnapshot(graphPath: string): CachedSnapshot | null {
  let raw: string
  try {
    raw = fs.readFileSync(graphPath, 'utf-8')
  } catch {
    return null
  }
  let data: GraphData
  try {
    data = JSON.parse(raw) as GraphData
  } catch {
    return null
  }
  const nodeIds = new Set(data.nodes.map(n => n.id))
  // Edge key: "s|t|kind". `w` is excluded — a weight change between rebuilds
  // is not a "new edge", it's a tweak, and treating it as a removal+add would
  // double-count. The full edge object is retained for the added-edges
  // payload below.
  const edgeKeys = new Set<string>()
  for (const e of data.edges) {
    edgeKeys.add(`${e.s}|${e.t}|${e.kind}`)
  }
  return {
    generated: data.meta?.generated ?? '',
    nodes: data.nodes,
    edgeKeys,
    edges: data.edges,
    nodeIds,
  }
}

function rememberSnapshot(vaultPath: string, snap: CachedSnapshot): void {
  let entry = cache.get(vaultPath)
  if (!entry) {
    entry = { byGenerated: new Map() }
    cache.set(vaultPath, entry)
  }
  // Replace any existing entry for the same generated token (defensive —
  // build_graph.py emits a fresh ISO timestamp per run).
  entry.byGenerated.set(snap.generated, snap)
  while (entry.byGenerated.size > MAX_SNAPSHOTS_PER_VAULT) {
    // Map iteration is insertion-order; the first item is the oldest.
    const oldest = entry.byGenerated.keys().next().value
    if (oldest === undefined) break
    entry.byGenerated.delete(oldest)
  }
}

/** Ratio above which we surrender and tell the client to do a full refetch. */
const DELTA_FULL_REBUILD_FRACTION = 0.4

export const GET = withApi(async (req: NextRequest) => {
  const vault = req.nextUrl.searchParams.get('vault')
  const since = req.nextUrl.searchParams.get('since')
  let vaultPath: string
  try {
    vaultPath = resolveVault(vault)
  } catch (err) {
    if (err instanceof VaultConfigError) {
      return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
    }
    return NextResponse.json({ error: 'Failed to resolve vault' }, { status: 500 })
  }
  const graphPath = path.join(vaultPath, 'graph.json')
  if (!fs.existsSync(graphPath)) {
    return NextResponse.json(
      { error: `graph.json not found in vault: ${vaultPath}` },
      { status: 404 },
    )
  }
  if (!since) {
    // No baseline → tell the caller to do a full fetch.
    return NextResponse.json({ full: true, reason: 'missing since' })
  }

  const current = loadSnapshot(graphPath)
  if (!current) {
    return NextResponse.json({ error: 'Failed to read graph.json' }, { status: 500 })
  }
  // Cache the freshly-loaded snapshot so future delta calls can diff against it.
  rememberSnapshot(vaultPath, current)

  const prior = cache.get(vaultPath)?.byGenerated.get(since)
  if (!prior) {
    // Unknown baseline → full refetch. This is the expected outcome the first
    // time a client asks for a delta after a server restart, and any time the
    // baseline rotated out of the small in-memory cache.
    return NextResponse.json({ full: true, reason: 'unknown since' })
  }

  // Partition nodes.
  const addedNodes: NoteNode[] = []
  for (const n of current.nodes) {
    if (!prior.nodeIds.has(n.id)) addedNodes.push(n)
  }
  const removedNodes: string[] = []
  for (const id of prior.nodeIds) {
    if (!current.nodeIds.has(id)) removedNodes.push(id)
  }

  // Partition edges by composite key.
  const addedEdges: GraphEdge[] = []
  for (const e of current.edges) {
    const key = `${e.s}|${e.t}|${e.kind}`
    if (!prior.edgeKeys.has(key)) addedEdges.push(e)
  }
  const removedEdgeKeys: Array<{ s: string; t: string; kind: GraphEdge['kind'] }> = []
  for (const e of prior.edges) {
    const key = `${e.s}|${e.t}|${e.kind}`
    if (!current.edgeKeys.has(key)) {
      removedEdgeKeys.push({ s: e.s, t: e.t, kind: e.kind })
    }
  }

  // Delta-too-large gate. Mirrors DELTA_REBUILD_THRESHOLD in lib/graphDelta.ts
  // so server and client agree on when incremental stops being worth it.
  const totalOld = prior.nodes.length
  const changed = addedNodes.length + removedNodes.length
  if (totalOld > 0 && changed / totalOld > DELTA_FULL_REBUILD_FRACTION) {
    return NextResponse.json({
      full: true,
      reason: 'delta too large',
      generated: current.generated,
    })
  }

  return NextResponse.json({
    full: false,
    generated: current.generated,
    addedNodes,
    removedNodes,
    addedEdges,
    removedEdges: removedEdgeKeys,
  })
})
