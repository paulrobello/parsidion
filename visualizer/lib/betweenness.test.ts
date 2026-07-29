// lib/betweenness.test.ts
// ARC-037: Brandes betweenness centrality, extracted from useVisualizerState.
import { describe, it, expect } from 'bun:test'
import { computeBetweenness, BETWEENNESS_MIN, BETWEENNESS_MAX } from './betweenness'

describe('computeBetweenness', () => {
  it('returns a size in [BETWEENNESS_MIN, BETWEENNESS_MAX] for every node', () => {
    const nodes = ['a', 'b', 'c']
    const adj = new Map([
      ['a', ['b']],
      ['b', ['a', 'c']],
      ['c', ['b']],
    ])
    const out = computeBetweenness(nodes, adj)
    for (const v of out.values()) {
      expect(v).toBeGreaterThanOrEqual(BETWEENNESS_MIN)
      expect(v).toBeLessThanOrEqual(BETWEENNESS_MAX)
    }
  })

  it('on the path graph a-b-c the middle node has the highest score', () => {
    // Every shortest path between a and c goes through b, so b's
    // betweenness is maximal and a, c are tied at zero.
    const nodes = ['a', 'b', 'c']
    const adj = new Map([
      ['a', ['b']],
      ['b', ['a', 'c']],
      ['c', ['b']],
    ])
    const out = computeBetweenness(nodes, adj)
    expect(out.get('b')).toBe(BETWEENNESS_MAX) // normalized to max
    // a and c have zero betweenness → clamp to BETWEENNESS_MIN
    expect(out.get('a')).toBe(BETWEENNESS_MIN)
    expect(out.get('c')).toBe(BETWEENNESS_MIN)
  })

  it('isolated nodes (no edges) all map to BETWEENNESS_MIN', () => {
    const nodes = ['x', 'y']
    const adj = new Map([['x', []], ['y', []]])
    const out = computeBetweenness(nodes, adj)
    expect(out.get('x')).toBe(BETWEENNESS_MIN)
    expect(out.get('y')).toBe(BETWEENNESS_MIN)
  })

  it('a complete graph K4 has uniform betweenness (all nodes equal)', () => {
    // In K4 every pair has a direct edge, so no node sits on a shortest
    // path between any other pair → uniform (zero) betweenness.
    const nodes = ['a', 'b', 'c', 'd']
    const adj = new Map<string, string[]>()
    for (const x of nodes) {
      adj.set(x, nodes.filter(y => y !== x))
    }
    const out = computeBetweenness(nodes, adj)
    const values = [...out.values()]
    const first = values[0]
    expect(values.every(v => v === first)).toBe(true)
  })

  it('handles a node present in nodes[] but missing from the adjacency map', () => {
    // Defensive: the hook-side caller builds adj from edges, so a node with
    // no edges is in `nodes` but may have no adj entry. computeBetweenness
    // treats missing entries as empty arrays.
    const nodes = ['lone', 'pair-a', 'pair-b']
    const adj = new Map([
      ['pair-a', ['pair-b']],
      ['pair-b', ['pair-a']],
      // 'lone' intentionally omitted
    ])
    const out = computeBetweenness(nodes, adj)
    expect(out.get('lone')).toBe(BETWEENNESS_MIN)
    // pair-a/pair-b have zero betweenness too (only one pair, direct edge)
    expect(out.get('pair-a')).toBe(BETWEENNESS_MIN)
    expect(out.get('pair-b')).toBe(BETWEENNESS_MIN)
  })

  it('produces no NaN or Infinity for a moderately sized cycle', () => {
    // Cycle C10 — every node symmetric, so all scores are equal.
    const N = 10
    const nodes = Array.from({ length: N }, (_, i) => `n${i}`)
    const adj = new Map<string, string[]>()
    for (let i = 0; i < N; i++) {
      adj.set(`n${i}`, [`n${(i + 1) % N}`, `n${(i - 1 + N) % N}`])
    }
    const out = computeBetweenness(nodes, adj)
    for (const v of out.values()) {
      expect(Number.isFinite(v)).toBe(true)
    }
    // Symmetric → all equal.
    const vals = [...out.values()]
    expect(vals.every(v => v === vals[0])).toBe(true)
  })
})
