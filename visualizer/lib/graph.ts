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
