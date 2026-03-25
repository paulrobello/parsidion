function hslToHex(h: number, s: number, l: number): string {
  s /= 100; l /= 100
  const k = (n: number) => (n + h / 30) % 12
  const a = s * Math.min(l, 1 - l)
  const f = (n: number) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)))
  const toHex = (x: number) => Math.round(x * 255).toString(16).padStart(2, '0')
  return `#${toHex(f(0))}${toHex(f(8))}${toHex(f(4))}`
}

export const TYPE_COLORS: Record<string, string> = {
  pattern:   '#6366f1',
  debugging: '#ef4444',
  research:  '#10b981',
  project:   '#0ea5e9',
  tool:      '#f59e0b',
  language:  '#a855f7',
  framework: '#f97316',
  daily:     '#4b5563',
}

export function getNodeColor(type: string): string {
  return TYPE_COLORS[type] ?? '#6b7280'
}

export function getNodeSize(incomingLinks: number): number {
  return Math.max(2, Math.log(incomingLinks + 1) * 2)
}

export type EdgeColorMode = 'binary' | 'gradient'
export type NodeSizeMode = 'uniform' | 'incoming_links' | 'betweenness' | 'recency'

/**
 * Returns the color for an edge.
 * Wiki edges always use binary coloring regardless of mode.
 * Semantic edges: binary = opacity-based gray, gradient = blue→red by weight.
 */
export function getSemanticEdgeColor(
  weight: number,
  kind: 'wiki' | 'semantic',
  mode: EdgeColorMode,
  threshold = 0.7
): string {
  if (kind === 'wiki') return 'rgba(123,97,255,0.35)'
  if (mode === 'binary') return `rgba(150,150,160,${Math.min(0.45, weight * 0.5)})`
  // gradient: blue (220°) → red (0°) mapped over [threshold, 1.0], as hex for WebGL
  const range = Math.max(0.01, 1 - threshold)
  const t = Math.max(0, Math.min(1, (weight - threshold) / range))
  const hue = 220 * (1 - t)
  return hslToHex(hue, 80, 55)
}
