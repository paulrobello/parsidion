import { describe, it, expect } from 'bun:test'
import {
  DELTA_REBUILD_THRESHOLD,
  JITTER,
  computeVisibleNodes,
  computeNodeDelta,
  shouldFullRebuild,
  buildAdjacency,
  seedPlacementFrom,
  graphBounds,
  positionNewNode,
} from './graphDelta'
import type { NoteNode, GraphEdge } from './graph'

function node(id: string, type = 'pattern', folder = 'Patterns'): NoteNode {
  return { id, title: id, type, folder, path: `${folder}/${id}.md`, tags: [], incoming_links: 0, mtime: 0 }
}

function edge(s: string, t: string, w = 0.5, kind: 'wiki' | 'semantic' = 'wiki'): GraphEdge {
  return { s, t, w, kind }
}

const dist = (a: { x: number; y: number }, b: { x: number; y: number }) =>
  Math.hypot(a.x - b.x, a.y - b.y)

describe('computeVisibleNodes', () => {
  it('drops Daily notes when showDaily is false', () => {
    const nodes = [node('a', 'pattern', 'Patterns'), node('d', 'daily', 'Daily')]
    const out = computeVisibleNodes(nodes, new Set(['pattern', 'daily']), false)
    expect(out.map(n => n.id)).toEqual(['a'])
  })

  it('keeps Daily notes when showDaily is true', () => {
    const nodes = [node('a', 'pattern', 'Patterns'), node('d', 'daily', 'Daily')]
    const out = computeVisibleNodes(nodes, new Set(['pattern', 'daily']), true)
    expect(out.map(n => n.id).sort()).toEqual(['a', 'd'])
  })

  it('filters by activeTypes', () => {
    const nodes = [node('a', 'pattern'), node('b', 'debugging')]
    const out = computeVisibleNodes(nodes, new Set(['pattern']), false)
    expect(out.map(n => n.id)).toEqual(['a'])
  })
})

describe('computeNodeDelta', () => {
  it('partitions added / removed / kept', () => {
    const current = new Set(['a', 'b', 'c'])
    const newVisible = [node('b'), node('c'), node('d'), node('e')]
    const delta = computeNodeDelta(current, newVisible)
    expect(delta.removed.sort()).toEqual(['a'])
    expect(delta.added.map(n => n.id).sort()).toEqual(['d', 'e'])
    expect(delta.kept.map(n => n.id).sort()).toEqual(['b', 'c'])
  })

  it('returns empty arrays on identical sets', () => {
    const current = new Set(['a', 'b'])
    const delta = computeNodeDelta(current, [node('a'), node('b')])
    expect(delta.removed).toEqual([])
    expect(delta.added).toEqual([])
    expect(delta.kept.map(n => n.id).sort()).toEqual(['a', 'b'])
  })
})

describe('shouldFullRebuild', () => {
  it('is false for a single-node addition', () => {
    const delta = { removed: [], added: [node('x')], kept: [] }
    expect(shouldFullRebuild(delta, 100)).toBe(false)
  })

  it('is true for a vault-switch-like turnover', () => {
    const added = Array.from({ length: 100 }, (_, i) => node(`n${i}`))
    const delta = { removed: Array.from({ length: 100 }, (_, i) => `o${i}`), added, kept: [] }
    expect(shouldFullRebuild(delta, 100)).toBe(true)
  })

  it('respects the threshold boundary', () => {
    // 70 added / 100 current -> 70/170 ≈ 0.411 > 0.4 -> rebuild
    const added70 = Array.from({ length: 70 }, (_, i) => node(`n${i}`))
    expect(shouldFullRebuild({ removed: [], added: added70, kept: [] }, 100)).toBe(true)
    // 60 added / 100 current -> 60/160 = 0.375 < 0.4 -> incremental
    const added60 = Array.from({ length: 60 }, (_, i) => node(`n${i}`))
    expect(shouldFullRebuild({ removed: [], added: added60, kept: [] }, 100)).toBe(false)
  })

  it('forces a rebuild when there are no current nodes', () => {
    expect(shouldFullRebuild({ removed: [], added: [node('x')], kept: [] }, 0)).toBe(true)
  })

  it('exports the configured threshold', () => {
    expect(DELTA_REBUILD_THRESHOLD).toBe(0.4)
  })
})

describe('buildAdjacency', () => {
  it('builds symmetric adjacency and skips edges to invisible nodes', () => {
    const edges = [edge('a', 'b'), edge('b', 'c'), edge('c', 'x')] // x not visible
    const adj = buildAdjacency(edges, new Set(['a', 'b', 'c']))
    expect([...(adj.get('a') ?? [])]).toEqual(['b'])
    expect([...(adj.get('b') ?? [])].sort()).toEqual(['a', 'c'])
    expect([...(adj.get('c') ?? [])]).toEqual(['b']) // edge to x dropped
    expect(adj.has('x')).toBe(false)
  })
})

describe('seedPlacementFrom', () => {
  it('maps each id through the position reader', () => {
    const placed = seedPlacementFrom(id => ({ x: id.charCodeAt(0), y: 0 }), ['a', 'b'])
    expect(placed.get('a')).toEqual({ x: 97, y: 0 })
    expect(placed.get('b')).toEqual({ x: 98, y: 0 })
  })
})

describe('graphBounds', () => {
  it('computes centroid and max radius', () => {
    const placed = new Map([
      ['a', { x: 0, y: 0 }],
      ['b', { x: 10, y: 0 }],
      ['c', { x: 0, y: 10 }],
    ])
    const b = graphBounds(placed)
    // centroid = (10/3, 10/3)
    expect(b.cx).toBeCloseTo(10 / 3, 6)
    expect(b.cy).toBeCloseTo(10 / 3, 6)
    // farthest point from centroid is c (0,10): dist = sqrt((10/3)^2 + (20/3)^2)
    const expected = Math.hypot(10 / 3, 20 / 3)
    expect(b.maxR).toBeCloseTo(expected, 6)
  })

  it('returns zeros for an empty map', () => {
    expect(graphBounds(new Map())).toEqual({ cx: 0, cy: 0, maxR: 0 })
  })
})

describe('positionNewNode', () => {
  it('places within jitter of the centroid when placed neighbors exist', () => {
    const placed = new Map([
      ['a', { x: 0, y: 0 }],
      ['b', { x: 10, y: 0 }],
    ])
    const adj = buildAdjacency([edge('new', 'a'), edge('new', 'b')], new Set(['a', 'b', 'new']))
    const bounds = graphBounds(placed) // centroid (5,0)
    const p = positionNewNode('new', placed, adj, bounds)
    expect(dist(p, { x: 5, y: 0 })).toBeLessThanOrEqual(JITTER + 1e-9)
  })

  it('places on the perimeter when there are no placed neighbors', () => {
    const placed = new Map([['a', { x: 0, y: 0 }], ['b', { x: 20, y: 0 }]])
    const bounds = graphBounds(placed) // centroid (10,0), maxR=10
    const adj = new Map<string, Set<string>>() // no neighbors at all
    const p = positionNewNode('orphan', placed, adj, bounds)
    const margin = Math.max(JITTER * 3, bounds.maxR * 0.15)
    expect(dist(p, { x: bounds.cx, y: bounds.cy })).toBeCloseTo(bounds.maxR + margin, 6)
  })

  it('uses the larger margin when maxR is large', () => {
    const placed = new Map([['a', { x: 0, y: 0 }], ['b', { x: 200, y: 0 }]])
    const bounds = graphBounds(placed) // centroid (100,0), maxR=100
    const p = positionNewNode('orphan', placed, new Map(), bounds)
    const margin = Math.max(JITTER * 3, 100 * 0.15) // 15 > 5.4
    expect(dist(p, { x: 100, y: 0 })).toBeCloseTo(100 + margin, 6)
  })

  it('only counts placed neighbors (not unplaced new-cluster siblings) for centroid', () => {
    // 'new' is adjacent to 'a' (placed) and 'sibling' (NOT placed yet) -> centroid over 'a' only
    const placed = new Map([['a', { x: 4, y: 4 }]])
    const adj = buildAdjacency([edge('new', 'a'), edge('new', 'sibling')], new Set(['a', 'new', 'sibling']))
    const bounds = graphBounds(placed)
    const p = positionNewNode('new', placed, adj, bounds)
    expect(dist(p, { x: 4, y: 4 })).toBeLessThanOrEqual(JITTER + 1e-9)
  })
})
