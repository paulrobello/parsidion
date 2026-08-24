/**
 * QA-011: the shared edge-build loops for the sigma graph.
 *
 * Adding a batch of edges to a graphology graph — skip pairs whose
 * endpoints are not visible, compute the color, addEdge inside a
 * try/catch that swallows duplicate-edge errors — was copy-pasted three
 * times (GraphCanvas overlay effect, GraphCanvas full rebuild,
 * useSigmaInstance initial build). These two helpers are the single home
 * for the two shapes that existed: primary edges (weighted, semantic
 * colors) and overlay edges (the non-active source, visual-only).
 */
import type { AbstractGraph } from 'graphology-types'
import type { GraphEdge, GraphSource } from './graph'
import { getSemanticEdgeColor, type EdgeColorMode } from './sigma-colors'

export interface PrimaryEdgeOptions {
  /** Endpoints must be in this set for the edge to be added. */
  visibleNodes: Set<string>
  /** Sigma edgeWeightInfluence multiplier applied to each weight. */
  edgeWeightInfluence: number
  /** Passed through to getSemanticEdgeColor. */
  edgeColorMode: EdgeColorMode
  /** Passed through to getSemanticEdgeColor. */
  threshold: number
}

/** Add weighted, semantically-colored primary edges; duplicates swallowed. */
export function addPrimaryEdges(
  graph: AbstractGraph,
  edges: GraphEdge[],
  opts: PrimaryEdgeOptions,
): void {
  const { visibleNodes, edgeWeightInfluence, edgeColorMode, threshold } = opts
  for (const edge of edges) {
    if (!visibleNodes.has(edge.s) || !visibleNodes.has(edge.t)) continue
    const col = getSemanticEdgeColor(edge.w, edge.kind, edgeColorMode, threshold)
    try {
      graph.addEdge(edge.s, edge.t, {
        weight: edge.w * edgeWeightInfluence,
        baseWeight: edge.w,
        color: col,
        size: edge.kind === 'wiki' ? 1.5 : 1,
        kind: edge.kind,
        overlay: false,
        originalColor: col,
      })
    } catch { /* duplicate edge — skip */ }
  }
}

/** Add visual-only overlay edges for the non-active source; duplicates swallowed. */
export function addOverlayEdges(
  graph: AbstractGraph,
  allEdges: GraphEdge[],
  graphSource: GraphSource,
  threshold: number,
  visibleNodes: Set<string>,
): void {
  const overlayKind = graphSource === 'semantic' ? 'wiki' : 'semantic'
  const overlayEdges = allEdges.filter(
    e => e.kind === overlayKind && (overlayKind === 'semantic' ? e.w >= threshold : true),
  )
  for (const edge of overlayEdges) {
    if (!visibleNodes.has(edge.s) || !visibleNodes.has(edge.t)) continue
    const col = overlayKind === 'wiki' ? 'rgba(123,97,255,0.18)' : 'rgba(150,150,160,0.18)'
    try {
      graph.addEdge(edge.s, edge.t, {
        weight: 0.001,
        color: col,
        size: 0.8,
        kind: overlayKind,
        overlay: true,
        originalColor: col,
      })
    } catch { /* duplicate edge — skip */ }
  }
}
