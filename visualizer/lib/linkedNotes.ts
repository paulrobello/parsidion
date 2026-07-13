// lib/linkedNotes.ts
import type { GraphEdge } from './graph'

/**
 * Stems connected to `stem` by wiki edges. graph.json wiki edges are
 * lexicographically normalized (s < t) by the builder, so direction is not
 * recoverable — this is "linked with", not directed backlinks.
 */
export function computeLinkedStems(edges: GraphEdge[], stem: string): string[] {
  const out = new Set<string>()
  for (const e of edges) {
    if (e.kind !== 'wiki') continue
    if (e.s === stem && e.t !== stem) out.add(e.t)
    else if (e.t === stem && e.s !== stem) out.add(e.s)
  }
  return [...out].sort()
}
