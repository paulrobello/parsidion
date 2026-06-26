import { describe, it, expect } from 'bun:test'
import { buildRecencyColorMap } from './useForceLayout'

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
