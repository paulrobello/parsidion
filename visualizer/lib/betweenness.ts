// lib/betweenness.ts
// ARC-037: Brandes betweenness centrality extracted from useVisualizerState.ts.
//
// Betweenness measures how often a node lies on shortest paths between
// other pairs of nodes. Brandes' algorithm is O(n*(n+m)) — fast enough on
// the wiki-edges subgraph of a vault (~thousands of nodes / ~tens of
// thousands of edges) to compute on the main thread behind a deferred
// setTimeout, but not fast enough to run on every render. The hook gates
// it behind a node-count limit and a status flag, both preserved in the
// hook after extraction.
//
// Pure (no React) so it can be unit-tested without rendering a component.
//
// Reference: U. Brandes, "A Faster Algorithm for Betweenness Centrality",
// Journal of Mathematical Sociology 25(2):163-177, 2001.

export const BETWEENNESS_MIN = 2
export const BETWEENNESS_MAX = 14

/**
 * Compute betweenness centrality for every node id in `nodes`, using the
 * undirected adjacency in `wikiAdj`. Returns a Map from node id to a
 * betweenness-derived size in [BETWEENNESS_MIN, BETWEENNESS_MAX], normalized
 * so the largest betweenness score maps to BETWEENNESS_MAX.
 *
 * `nodes` and `wikiAdj` are typically built from `graphData.nodes` and the
 * `kind === 'wiki'` edges of `graphData.edges` — see useVisualizerState.ts.
 */
export function computeBetweenness(
  nodes: string[],
  wikiAdj: Map<string, string[]>,
): Map<string, number> {
  const bc = new Map<string, number>()
  for (const n of nodes) bc.set(n, 0)

  for (const s of nodes) {
    const stack: string[] = []
    const pred = new Map<string, string[]>()
    for (const n of nodes) pred.set(n, [])
    const sigma = new Map<string, number>()
    for (const n of nodes) sigma.set(n, 0)
    sigma.set(s, 1)
    const dist = new Map<string, number>()
    for (const n of nodes) dist.set(n, -1)
    dist.set(s, 0)
    const queue: string[] = [s]

    while (queue.length > 0) {
      const v = queue.shift()!
      stack.push(v)
      for (const w of (wikiAdj.get(v) ?? [])) {
        if (dist.get(w) === -1) {
          queue.push(w)
          dist.set(w, dist.get(v)! + 1)
        }
        if (dist.get(w) === dist.get(v)! + 1) {
          sigma.set(w, sigma.get(w)! + sigma.get(v)!)
          pred.get(w)!.push(v)
        }
      }
    }

    const delta = new Map<string, number>()
    for (const n of nodes) delta.set(n, 0)
    while (stack.length > 0) {
      const w = stack.pop()!
      for (const v of (pred.get(w) ?? [])) {
        const ratio = (sigma.get(v)! / sigma.get(w)!) * (1 + delta.get(w)!)
        delta.set(v, delta.get(v)! + ratio)
      }
      if (w !== s) bc.set(w, bc.get(w)! + delta.get(w)!)
    }
  }

  // Normalize to [BETWEENNESS_MIN, BETWEENNESS_MAX]
  let maxVal = 0
  for (const v of bc.values()) if (v > maxVal) maxVal = v
  if (maxVal === 0) maxVal = 1
  const result = new Map<string, number>()
  for (const [id, val] of bc) {
    result.set(id, BETWEENNESS_MIN + (val / maxVal) * (BETWEENNESS_MAX - BETWEENNESS_MIN))
  }
  return result
}
