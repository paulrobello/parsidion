import { describe, it, expect } from 'bun:test'
import { computeVisibleNodeSet } from './useGraphControls'
import type { GraphData, NoteNode, GraphEdge } from './graph'

// Minimal factory helpers — these tests target only the visibility predicate
// extracted from the duplicated `stats` / `graphStats` memos; the React hook
// wrapper is covered by the type-checker (consumer prop signatures) and the
// existing visualizer gate.
function node(id: string, type: string, folder = 'Patterns'): NoteNode {
  return { id, title: id, type, folder, path: `${folder}/${id}.md`, tags: [], incoming_links: 0, mtime: 0 }
}

function semEdge(s: string, t: string, w: number): GraphEdge {
  return { s, t, w, kind: 'semantic' }
}

function graph(nodes: NoteNode[], edges: GraphEdge[]): GraphData {
  return {
    meta: {
      generated: 't',
      note_count: nodes.length,
      edge_count: edges.length,
      min_semantic_threshold: 0,
    },
    nodes,
    edges,
  }
}

describe('computeVisibleNodeSet', () => {
  it('returns an empty set for an empty graph', () => {
    const g = graph([], [])
    const visible = computeVisibleNodeSet(g, {
      showDaily: false,
      activeTypes: new Set(['pattern']),
      filterNodesBySimilarity: false,
      graphSource: 'semantic',
      threshold: 0,
    })
    expect(visible.size).toBe(0)
  })

  it('includes nodes whose type is in activeTypes', () => {
    const g = graph([node('a', 'pattern'), node('b', 'debugging')], [])
    const visible = computeVisibleNodeSet(g, {
      showDaily: false,
      activeTypes: new Set(['pattern']),
      filterNodesBySimilarity: false,
      graphSource: 'semantic',
      threshold: 0,
    })
    expect(visible.has('a')).toBe(true)
    expect(visible.has('b')).toBe(false)
  })

  it('excludes Daily notes unless showDaily is true', () => {
    const g = graph([node('a', 'pattern', 'Patterns'), node('d', 'daily', 'Daily')], [])
    const hidden = computeVisibleNodeSet(g, {
      showDaily: false,
      activeTypes: new Set(['pattern', 'daily']),
      filterNodesBySimilarity: false,
      graphSource: 'semantic',
      threshold: 0,
    })
    expect(hidden.has('a')).toBe(true)
    expect(hidden.has('d')).toBe(false)

    const shown = computeVisibleNodeSet(g, {
      showDaily: true,
      activeTypes: new Set(['pattern', 'daily']),
      filterNodesBySimilarity: false,
      graphSource: 'semantic',
      threshold: 0,
    })
    expect(shown.has('a')).toBe(true)
    expect(shown.has('d')).toBe(true)
  })

  it('in semantic mode, filterNodesBySimilarity has no effect', () => {
    // graphSource='semantic' must short-circuit the qualifying-set construction,
    // so even with filterNodesBySimilarity=true no nodes are filtered by edges.
    const g = graph(
      [node('a', 'pattern'), node('b', 'pattern'), node('c', 'pattern')],
      [],
    )
    const visible = computeVisibleNodeSet(g, {
      showDaily: false,
      activeTypes: new Set(['pattern']),
      filterNodesBySimilarity: true,
      graphSource: 'semantic',
      threshold: 0.5,
    })
    expect(visible.size).toBe(3)
  })

  it('in wiki mode with filterNodesBySimilarity, only nodes connected by a strong-enough semantic edge survive', () => {
    // a-b qualifies (w=0.9); b-c is too weak (w=0.2); d is isolated
    const g = graph(
      [node('a', 'pattern'), node('b', 'pattern'), node('c', 'pattern'), node('d', 'pattern')],
      [semEdge('a', 'b', 0.9), semEdge('b', 'c', 0.2)],
    )
    const visible = computeVisibleNodeSet(g, {
      showDaily: false,
      activeTypes: new Set(['pattern']),
      filterNodesBySimilarity: true,
      graphSource: 'wiki',
      threshold: 0.5,
    })
    expect(visible.has('a')).toBe(true)
    expect(visible.has('b')).toBe(true)
    expect(visible.has('c')).toBe(false)
    expect(visible.has('d')).toBe(false)
  })

  it('respects the threshold cutoff at exactly the boundary (>=)', () => {
    const g = graph(
      [node('a', 'pattern'), node('b', 'pattern')],
      [semEdge('a', 'b', 0.5)],
    )
    const at = computeVisibleNodeSet(g, {
      showDaily: false,
      activeTypes: new Set(['pattern']),
      filterNodesBySimilarity: true,
      graphSource: 'wiki',
      threshold: 0.5,
    })
    expect(at.has('a')).toBe(true)

    const above = computeVisibleNodeSet(g, {
      showDaily: false,
      activeTypes: new Set(['pattern']),
      filterNodesBySimilarity: true,
      graphSource: 'wiki',
      threshold: 0.51,
    })
    expect(above.size).toBe(0)
  })
})
