import { describe, it, expect } from 'bun:test'
import { MultiGraph } from 'graphology'
import type { AbstractGraph } from 'graphology-types'
import { buildRecencyColorMap, buildLayoutLoop, type LayoutLoopDeps } from './useForceLayout'
import { isEffectivelyIsolated } from './useGraphReducers'

// Bun's test environment has no requestAnimationFrame; the loop below is
// driven manually frame-by-frame, but buildLayoutLoop still schedules the
// next frame via rAF when it keeps running, so stub it to a no-op.
if (typeof globalThis.requestAnimationFrame !== 'function') {
  (globalThis as unknown as { requestAnimationFrame: (cb: FrameRequestCallback) => number })
    .requestAnimationFrame = () => 0
}

const HEX = /^#[0-9a-f]{6}$/i
function redChannel(hex: string) {
  const m = /^#([0-9a-f]{2})[0-9a-f]{4}$/i.exec(hex)
  if (!m) throw new Error(`bad hex: ${hex}`)
  return parseInt(m[1], 16)
}

describe('buildRecencyColorMap', () => {
  it('returns an empty map for empty input', () => {
    expect(buildRecencyColorMap([]).size).toBe(0)
  })

  it('colors the newest node red-dominant and the oldest blue-dominant', () => {
    const now = Date.now() / 1000
    const map = buildRecencyColorMap([
      { id: 'old', mtime: now - 60 * 86400 },     // 60 days ago
      { id: 'new', mtime: now - 60 },             // 1 minute ago
    ])
    expect(map.size).toBe(2)
    expect([...map.values()].every(c => HEX.test(c))).toBe(true)
    expect(redChannel(map.get('new')!)).toBeGreaterThan(redChannel(map.get('old')!))
  })

  it('handles a single node without dividing by zero', () => {
    const now = Date.now() / 1000
    const map = buildRecencyColorMap([{ id: 'solo', mtime: now }])
    expect(map.size).toBe(1)
    expect(HEX.test(map.get('solo')!)).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// isEffectivelyIsolated — shared visibility predicate
// ---------------------------------------------------------------------------

describe('isEffectivelyIsolated', () => {
  it('treats a node whose only edge is an overlay edge as isolated', () => {
    const graph = new MultiGraph()
    graph.addNode('x')
    graph.addNode('y')
    graph.addEdge('x', 'y', { overlay: true })
    expect(isEffectivelyIsolated(graph, 'x', () => true)).toBe(true)
  })

  it('treats a node whose only neighbor is out-of-neighborhood as isolated', () => {
    const graph = new MultiGraph()
    graph.addNode('x')
    graph.addNode('y')
    graph.addEdge('x', 'y', { overlay: false })
    // 'y' is excluded by the caller's visibility predicate (e.g. outside the
    // active neighborhood / filtered set).
    expect(isEffectivelyIsolated(graph, 'x', other => other !== 'y')).toBe(true)
  })

  it('is not isolated when it has a visible non-overlay edge', () => {
    const graph = new MultiGraph()
    graph.addNode('x')
    graph.addNode('y')
    graph.addNode('z')
    graph.addEdge('x', 'y', { overlay: true })
    graph.addEdge('x', 'z', { overlay: false })
    expect(isEffectivelyIsolated(graph, 'x', () => true)).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// buildLayoutLoop — flat-array physics loop
// ---------------------------------------------------------------------------

function makeDeps(graph: AbstractGraph, overrides: Partial<LayoutLoopDeps> = {}): LayoutLoopDeps {
  const base: LayoutLoopDeps = {
    graphRef: { current: graph },
    sigmaRefreshRef: { current: () => {} },
    rafRef: { current: null },
    isRunningRef: { current: true },
    temperatureRef: { current: 1 },
    simVelocitiesRef: { current: new Map(graph.nodes().map((id: string) => [id, { vx: 0, vy: 0 }])) },
    layoutParamsRef: { current: { scalingRatio: 10, gravity: 1 } },
    coolingRateRef: { current: 0.05 },
    stopThresholdRef: { current: 0.01 },
    filteredNodesRef: { current: new Set() },
    neighborhoodRef: { current: null },
    hideIsolatedRef: { current: false },
    isDraggingRef: { current: false },
    draggedNodeRef: { current: null },
    dragPositionRef: { current: null },
    onLayoutStopRef: { current: undefined },
    layoutLoopRef: { current: null },
  }
  return { ...base, ...overrides }
}

// Drives the loop manually (no rAF chain) until it auto-stops or a safety
// cap is hit, so the test never hangs if the stop threshold is unreachable.
function runUntilStopped(deps: LayoutLoopDeps, maxFrames = 2000): number {
  let frames = 0
  while (deps.isRunningRef.current && frames < maxFrames) {
    deps.layoutLoopRef.current!()
    frames++
  }
  return frames
}

describe('buildLayoutLoop (flat-array path)', () => {
  it('converges to a stable, finite layout for a small graph', () => {
    const graph = new MultiGraph()
    graph.addNode('a', { x: 0, y: 0 })
    graph.addNode('b', { x: 120, y: 0 })
    graph.addNode('c', { x: 0, y: 120 })
    graph.addEdge('a', 'b', { weight: 1, overlay: false })
    graph.addEdge('b', 'c', { weight: 1, overlay: false })

    const deps = makeDeps(graph)
    buildLayoutLoop(deps)

    const frames = runUntilStopped(deps)
    expect(frames).toBeGreaterThan(0)
    expect(deps.isRunningRef.current).toBe(false) // auto-stopped via temperature threshold

    const snapshot = new Map(
      graph.nodes().map((id: string) => [id, {
        x: graph.getNodeAttribute(id, 'x') as number,
        y: graph.getNodeAttribute(id, 'y') as number,
      }])
    )
    for (const [, pos] of snapshot) {
      expect(Number.isFinite(pos.x)).toBe(true)
      expect(Number.isFinite(pos.y)).toBe(true)
    }

    // Re-run a few more frames (auto-stop disabled) and confirm positions
    // barely move — i.e. the layout has actually settled, not just timed out.
    deps.isRunningRef.current = true
    deps.stopThresholdRef.current = 0
    for (let i = 0; i < 5; i++) deps.layoutLoopRef.current!()

    for (const [id, before] of snapshot) {
      const afterX = graph.getNodeAttribute(id, 'x') as number
      const afterY = graph.getNodeAttribute(id, 'y') as number
      const displacement = Math.hypot(afterX - before.x, afterY - before.y)
      expect(displacement).toBeLessThan(2)
    }
  })

  it('excludes a hideIsolated node connected only via an overlay edge from physics', () => {
    const graph = new MultiGraph()
    graph.addNode('anchor', { x: 0, y: 0 })
    graph.addNode('mover', { x: 50, y: 0 })
    graph.addNode('isolated', { x: 500, y: 500 })
    graph.addEdge('anchor', 'mover', { weight: 1, overlay: false })
    // 'isolated' only has an overlay edge — hideIsolated should drop it from
    // the physics loop entirely (same isEffectivelyIsolated rule the node
    // reducer uses to hide it visually).
    graph.addEdge('anchor', 'isolated', { weight: 1, overlay: true })

    const deps = makeDeps(graph, { hideIsolatedRef: { current: true } })
    buildLayoutLoop(deps)

    for (let i = 0; i < 10; i++) deps.layoutLoopRef.current!()

    expect(graph.getNodeAttribute('isolated', 'x')).toBe(500)
    expect(graph.getNodeAttribute('isolated', 'y')).toBe(500)
    // The connected pair should have actually moved under gravity/attraction.
    const moverMoved = graph.getNodeAttribute('mover', 'x') !== 50
      || graph.getNodeAttribute('mover', 'y') !== 0
    expect(moverMoved).toBe(true)
  })
})
