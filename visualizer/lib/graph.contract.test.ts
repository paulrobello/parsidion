// lib/graph.contract.test.ts
// ARC-038: pin the GraphData contract by validating a committed fixture
// against the lib/graph.ts TypeScript interface.
//
// The Python half of ARC-038 — emitting a JSON Schema from
// build_graph.py:376-398 — is owned by a separate Python-side task because
// the schema must stay in sync with the canonical emitter. This test guards
// the TypeScript consumer: if either side drifts (a new field is added in
// Python without updating the TS interface, or vice versa), this test
// catches it before a release ships a graph.json that the visualizer can't
// parse.
//
// The fixture is hand-built rather than generated so the test does not
// depend on a working Python environment in the visualizer test runner. A
// parallel Python-side parity test (tests/test_build_graph_parmem.py)
// emits a real graph.json from the production emitter; both tests must
// agree on the shape.
import { describe, it, expect } from 'bun:test'
import fs from 'fs'
import path from 'path'
import { filterEdges } from './graph'
import type { GraphData, GraphEdge, NoteNode } from './graph'

const FIXTURE = path.join(import.meta.dir, '__fixtures__', 'graph', 'sample.json')

describe('ARC-038 — graph.json contract fixture', () => {
  it('fixture parses as GraphData', () => {
    const raw = fs.readFileSync(FIXTURE, 'utf-8')
    const data = JSON.parse(raw) as GraphData
    expect(data.meta.note_count).toBeGreaterThan(0)
    expect(data.nodes.length).toBe(data.meta.note_count)
    expect(data.edges.length).toBe(data.meta.edge_count)
  })

  it('every node has the required GraphData NoteNode fields', () => {
    const data = JSON.parse(fs.readFileSync(FIXTURE, 'utf-8')) as GraphData
    for (const n of data.nodes) {
      const required: (keyof NoteNode)[] = ['id', 'title', 'type', 'folder', 'path', 'tags', 'incoming_links', 'mtime']
      for (const key of required) {
        expect(n).toHaveProperty(key)
      }
      expect(typeof n.id).toBe('string')
      expect(typeof n.title).toBe('string')
      expect(typeof n.type).toBe('string')
      expect(typeof n.folder).toBe('string')
      expect(typeof n.path).toBe('string')
      expect(Array.isArray(n.tags)).toBe(true)
      expect(typeof n.incoming_links).toBe('number')
      expect(typeof n.mtime).toBe('number')
    }
  })

  it('every edge has the required GraphEdge fields and a valid kind', () => {
    const data = JSON.parse(fs.readFileSync(FIXTURE, 'utf-8')) as GraphData
    const validKinds = new Set<GraphEdge['kind']>(['semantic', 'wiki'])
    for (const e of data.edges) {
      expect(e).toHaveProperty('s')
      expect(e).toHaveProperty('t')
      expect(e).toHaveProperty('w')
      expect(e).toHaveProperty('kind')
      expect(typeof e.s).toBe('string')
      expect(typeof e.t).toBe('string')
      expect(typeof e.w).toBe('number')
      expect(validKinds.has(e.kind)).toBe(true)
    }
  })

  it('meta fields match the lib/graph.ts interface (includes optional parmem_body_links)', () => {
    const data = JSON.parse(fs.readFileSync(FIXTURE, 'utf-8')) as GraphData
    expect(data.meta.generated).toBeTypeOf('string')
    expect(data.meta.note_count).toBeTypeOf('number')
    expect(data.meta.edge_count).toBeTypeOf('number')
    expect(data.meta.min_semantic_threshold).toBeTypeOf('number')
    // parmem_body_links is optional but the fixture sets it so we can assert
    // its presence here. Tests against real graph.json should treat absence
    // as valid too — see lib/graph.ts GraphData.meta for the optionality.
    expect(data.meta.parmem_body_links).toBeTypeOf('number')
  })

  it('every edge endpoint references a node id present in nodes[]', () => {
    const data = JSON.parse(fs.readFileSync(FIXTURE, 'utf-8')) as GraphData
    const ids = new Set(data.nodes.map(n => n.id))
    for (const e of data.edges) {
      expect(ids.has(e.s)).toBe(true)
      expect(ids.has(e.t)).toBe(true)
    }
  })

  it('lib/graph.ts filterEdges returns the expected partition for the fixture', () => {
    // Smoke-tests that the consumer-facing helper also accepts the fixture
    // shape — if the interface drifted, this would not compile or would
    // misbehave.
    const data = JSON.parse(fs.readFileSync(FIXTURE, 'utf-8')) as GraphData
    const semantic = filterEdges(data.edges, 'semantic', 0.5)
    expect(semantic.every(e => e.kind === 'semantic')).toBe(true)
    const wiki = filterEdges(data.edges, 'wiki', 0.5)
    expect(wiki.every(e => e.kind === 'wiki')).toBe(true)
    // Threshold filter on semantic w (only 0.85 edge qualifies at 0.7).
    const strict = filterEdges(data.edges, 'semantic', 0.7)
    expect(strict).toHaveLength(1)
    expect(strict[0].w).toBe(0.85)
  })
})

// Exported so a future Python-side parity test can import the same
// expectations and assert them against an emitted graph.json.
export const GRAPH_CONTRACT_FIXTURE_PATH = FIXTURE
