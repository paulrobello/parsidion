// lib/graphDelta.server.test.ts
// ARC-015: covers the client-side delta helpers in lib/graph.ts.
// (lib/graphDelta.test.ts covers the in-memory GraphCanvas diff helpers.)
import { describe, it, expect } from 'bun:test'
import { applyGraphDelta, type GraphDeltaResponse } from './graph'
import type { GraphData, GraphEdge, NoteNode } from './graph'

function node(id: string): NoteNode {
  return { id, title: id, type: 'pattern', folder: 'Patterns', path: `Patterns/${id}.md`, tags: [], incoming_links: 0, mtime: 0 }
}
function edge(s: string, t: string, w = 0.5, kind: 'wiki' | 'semantic' = 'wiki'): GraphEdge {
  return { s, t, w, kind }
}
function base(...nodes: NoteNode[]): GraphData {
  return {
    meta: { generated: 'old', note_count: nodes.length, edge_count: 0, min_semantic_threshold: 0.6 },
    nodes,
    edges: [],
  }
}

describe('applyGraphDelta', () => {
  it('returns null when response.full is true (caller must full-refetch)', () => {
    const out = applyGraphDelta(base(node('a')), { full: true, reason: 'unknown since' })
    expect(out).toBeNull()
  })

  it('adds new nodes and removes gone nodes', () => {
    const b = base(node('a'), node('b'))
    b.edges = [edge('a', 'b')]
    const resp: GraphDeltaResponse = {
      full: false,
      generated: 'new',
      addedNodes: [node('c')],
      removedNodes: ['b'],
    }
    const out = applyGraphDelta(b, resp)
    expect(out).not.toBeNull()
    expect(out!.nodes.map(n => n.id).sort()).toEqual(['a', 'c'])
    // Removed node's edges are dropped (no edges remain after b removed).
    expect(out!.edges).toEqual([])
    expect(out!.meta.generated).toBe('new')
    expect(out!.meta.note_count).toBe(2)
  })

  it('adds and removes edges by composite (s, t, kind)', () => {
    const b = base(node('a'), node('b'), node('c'))
    b.edges = [
      edge('a', 'b', 0.7, 'wiki'),
      edge('a', 'c', 0.9, 'semantic'),
    ]
    const resp: GraphDeltaResponse = {
      full: false,
      generated: 'new',
      addedEdges: [edge('b', 'c', 0.8, 'semantic')],
      removedEdges: [{ s: 'a', t: 'c', kind: 'semantic' }],
    }
    const out = applyGraphDelta(b, resp)!
    expect(out.edges).toHaveLength(2)
    // The removed (a,c,semantic) is gone; (a,b,wiki) and (b,c,semantic) remain.
    expect(out.edges.find(e => e.s === 'a' && e.t === 'c')).toBeUndefined()
    expect(out.edges.find(e => e.s === 'a' && e.t === 'b')).toBeDefined()
    expect(out.edges.find(e => e.s === 'b' && e.t === 'c')).toBeDefined()
    expect(out.meta.edge_count).toBe(2)
  })

  it('does not treat a weight change on an existing edge as a removal', () => {
    const b = base(node('a'), node('b'))
    b.edges = [edge('a', 'b', 0.5)]
    // Server returns no removal, no addition for (a,b,wiki) — weight tweak.
    const out = applyGraphDelta(b, { full: false, generated: 'new' })!
    expect(out.edges).toHaveLength(1)
    expect(out.edges[0].w).toBe(0.5)
  })

  it('empty delta returns the same nodes/edges with a new generated', () => {
    const b = base(node('a'))
    b.edges = [edge('a', 'a')]
    const out = applyGraphDelta(b, { full: false, generated: 'newer' })!
    expect(out.nodes.map(n => n.id)).toEqual(['a'])
    expect(out.edges).toHaveLength(1)
    expect(out.meta.generated).toBe('newer')
  })

  it('meta.parsight_body_links is preserved when present on base', () => {
    const b = base(node('a'))
    b.meta.parsight_body_links = 42
    const out = applyGraphDelta(b, { full: false, generated: 'new' })!
    expect(out.meta.parsight_body_links).toBe(42)
  })
})
