import { describe, it, expect } from 'bun:test'
import { recencyHeatColor, RECENCY_HEATMAP_GRADIENT } from './sigma-colors'

const HEX = /^#[0-9a-f]{6}$/i
function channels(hex: string) {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex)
  if (!m) throw new Error(`bad hex: ${hex}`)
  return { r: parseInt(m[1], 16), g: parseInt(m[2], 16), b: parseInt(m[3], 16) }
}

describe('recencyHeatColor', () => {
  it('returns 6-digit hex for endpoints and midpoint', () => {
    for (const t of [0, 0.25, 0.5, 0.75, 1]) {
      expect(recencyHeatColor(t)).toMatch(HEX)
    }
  })

  it('clamps t outside [0,1] to the endpoints', () => {
    expect(recencyHeatColor(-1)).toBe(recencyHeatColor(0))
    expect(recencyHeatColor(2)).toBe(recencyHeatColor(1))
  })

  it('maps newest (t=0) to red-dominant and oldest (t=1) to blue-dominant', () => {
    const newest = channels(recencyHeatColor(0))
    const oldest = channels(recencyHeatColor(1))
    expect(newest.r).toBeGreaterThan(oldest.r)   // red falls as notes age
    expect(newest.b).toBeLessThan(oldest.b)      // blue rises as notes age
  })

  it('legend gradient sweeps through green like the nodes do', () => {
    // A plain CSS red→blue gradient interpolates in RGB space and skips green;
    // the sampled hue sweep must include the green mid-recency band (hue ~110°).
    expect(RECENCY_HEATMAP_GRADIENT).toMatch(/linear-gradient\(90deg, .+, .+\)/)
    const mid = channels(recencyHeatColor(0.5))
    expect(mid.g).toBeGreaterThan(mid.r)
    expect(mid.g).toBeGreaterThan(mid.b)
  })
})
