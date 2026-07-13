import { describe, test, expect } from 'bun:test'
import { computeLinkedStems } from './linkedNotes'
import type { GraphEdge } from './graph'

const edges: GraphEdge[] = [
  { s: 'alpha', t: 'beta', w: 1.0, kind: 'wiki' },
  { s: 'gamma', t: 'alpha', w: 1.0, kind: 'wiki' },
  { s: 'alpha', t: 'delta', w: 0.9, kind: 'semantic' },
  { s: 'beta', t: 'gamma', w: 1.0, kind: 'wiki' },
  { s: 'alpha', t: 'alpha', w: 1.0, kind: 'wiki' },
]

describe('computeLinkedStems', () => {
  test('collects wiki neighbors from both edge directions', () => {
    expect(computeLinkedStems(edges, 'alpha')).toEqual(['beta', 'gamma'])
  })

  test('ignores semantic edges', () => {
    expect(computeLinkedStems(edges, 'delta')).toEqual([])
  })

  test('excludes self-loops', () => {
    expect(computeLinkedStems(edges, 'alpha')).not.toContain('alpha')
  })

  test('returns sorted, deduplicated stems', () => {
    const dup: GraphEdge[] = [
      { s: 'a', t: 'z', w: 1.0, kind: 'wiki' },
      { s: 'z', t: 'a', w: 1.0, kind: 'wiki' },
      { s: 'a', t: 'b', w: 1.0, kind: 'wiki' },
    ]
    expect(computeLinkedStems(dup, 'a')).toEqual(['b', 'z'])
  })

  test('empty edges → empty result', () => {
    expect(computeLinkedStems([], 'alpha')).toEqual([])
  })
})
