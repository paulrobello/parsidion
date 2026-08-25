'use client'

// ARC-008: Graph view extracted out of `app/page.tsx::Home` as a cohesive
// presentational container. `Home` still owns `graphCanvasRef` and the
// search/sidebar node-click handlers that drive `flyToNode`, so this panel
// is purely the GraphCanvas + HUDPanel + scope-indicator JSX plus the
// lazy `dynamic()` import of GraphCanvas (moved here from page.tsx).
// Behaviour is identical: same props wired to the same children, the panel
// is always mounted alongside the reading pane (only visibility-toggled) so
// Sigma's container keeps real dimensions and the force layout is preserved.

import type { RefObject } from 'react'
import dynamic from 'next/dynamic'
import type { GraphData } from '@/lib/graph'
import type { GraphCanvasHandle } from '@/components/GraphCanvas'
import { HUDPanel } from '@/components/HUDPanel'
import type { useVisualizerState } from '@/lib/useVisualizerState'

type VisualizerState = ReturnType<typeof useVisualizerState>

const GraphCanvas = dynamic(() => import('@/components/GraphCanvas').then(m => m.GraphCanvas), {
  ssr: false,
  loading: () => (
    <div style={{
      position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
      fontFamily: 'Oxanium, sans-serif', color: '#00FFC8', fontSize: 14, letterSpacing: '0.1em',
    }}>
      <div>
        <div style={{ textAlign: 'center', marginBottom: 16 }}>◈</div>
        <div>INITIALIZING GRAPH...</div>
      </div>
    </div>
  ),
})

export interface GraphPanelProps {
  state: VisualizerState
  graphData: GraphData
  graphCanvasRef: RefObject<GraphCanvasHandle | null>
  onNodeClick: (stem: string, open: boolean, newTab: boolean) => void
}

export function GraphPanel({ state, graphData, graphCanvasRef, onNodeClick }: GraphPanelProps) {
  const neighborhoodCenter = state.neighborhoodCenter

  return (
    <div style={state.viewMode === 'graph'
      ? { flex: 1, position: 'relative' }
      : { position: 'absolute', inset: 0, visibility: 'hidden', pointerEvents: 'none' }
    }>
      {/* Scope indicator — top-right to avoid HUD overlap */}
      <div style={{
        position: 'absolute', top: 12, right: 12,
        display: 'flex', gap: 6, zIndex: 10,
        fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
      }}>
        {neighborhoodCenter && (
          <div style={{
            background: 'rgba(15,23,42,0.92)',
            border: '1px solid #1e293b', borderRadius: 5,
            padding: '4px 10px',
            display: 'flex', gap: 8, alignItems: 'center',
          }}>
            <span style={{ color: '#f97316' }}>●</span>
            <span style={{ color: '#e8e8f0' }}>{neighborhoodCenter}</span>
            <span style={{ color: '#6b7a99' }}>· 2 hops</span>
          </div>
        )}
        <button
          onClick={() => state.setNeighborhoodCenter(neighborhoodCenter ? null : state.activeTab)}
          style={{
            background: 'rgba(15,23,42,0.92)',
            border: '1px solid #1e293b', borderRadius: 5,
            padding: '4px 10px',
            color: '#7b61ff', cursor: 'pointer',
            fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
          }}
        >
          {neighborhoodCenter ? 'Show Full Vault ⤢' : 'Show Neighborhood ⤡'}
        </button>
      </div>

      <GraphCanvas
        ref={graphCanvasRef}
        data={graphData}
        threshold={state.threshold}
        graphSource={state.graphSource}
        activeTypes={state.activeTypes}
        showDaily={state.showDaily}
        hideIsolated={state.hideIsolated}
        labelsOnHoverOnly={state.labelsOnHoverOnly}
        showOverlayEdges={state.showOverlayEdges}
        filterNodesBySimilarity={state.filterNodesBySimilarity}
        edgeColorMode={state.edgeColorMode}
        edgePruning={state.edgePruning}
        edgePruningK={state.edgePruningK}
        nodeSizeMode={state.nodeSizeMode}
        nodeColorMode={state.nodeColorMode}
        nodeSizeMap={state.nodeSizeMap}
        onNodeClick={onNodeClick}
        onBackgroundClick={() => state.setNeighborhoodCenter(null)}
        onOpenHistory={state.openHistory}
        scalingRatio={state.scalingRatio}
        gravity={state.gravity}
        slowDown={state.slowDown}
        edgeWeightInfluence={state.edgeWeightInfluence}
        startTemperature={state.startTemperature}
        stopThreshold={state.stopThreshold}
        isLayoutRunning={state.isLayoutRunning}
        onLayoutStop={() => state.setIsLayoutRunning(false)}
        onLayoutRestart={() => state.setIsLayoutRunning(true)}
        neighborhoodCenter={neighborhoodCenter}
        neighborhoodHops={2}
      />

      {/* HUD Panel */}
      <HUDPanel
        threshold={state.threshold}
        onThresholdChange={state.setThreshold}
        graphSource={state.graphSource}
        onGraphSourceChange={state.setGraphSource}
        showOverlayEdges={state.showOverlayEdges}
        onToggleOverlayEdges={state.toggleOverlayEdges}
        filterNodesBySimilarity={state.filterNodesBySimilarity}
        onToggleFilterNodesBySimilarity={state.toggleFilterNodesBySimilarity}
        activeTypes={state.activeTypes}
        onToggleType={state.handleToggleType}
        showDaily={state.showDaily}
        onToggleDaily={state.toggleShowDaily}
        hideIsolated={state.hideIsolated}
        onToggleHideIsolated={state.toggleHideIsolated}
        labelsOnHoverOnly={state.labelsOnHoverOnly}
        onToggleLabelsOnHoverOnly={state.toggleLabelsOnHoverOnly}
        nodeCount={state.stats.nodeCount}
        edgeCount={state.stats.edgeCount}
        avgScore={state.stats.avgScore}
        scalingRatio={state.scalingRatio}
        onScalingRatioChange={state.setScalingRatio}
        gravity={state.gravity}
        onGravityChange={state.setGravity}
        slowDown={state.slowDown}
        onSlowDownChange={state.setSlowDown}
        edgeWeightInfluence={state.edgeWeightInfluence}
        onEdgeWeightInfluenceChange={state.setEdgeWeightInfluence}
        startTemperature={state.startTemperature}
        onStartTemperatureChange={state.setStartTemperature}
        stopThreshold={state.stopThreshold}
        onStopThresholdChange={state.setStopThreshold}
        isLayoutRunning={state.isLayoutRunning}
        onToggleLayout={() => state.setIsLayoutRunning(r => !r)}
        onResetSimSettings={state.resetSimSettings}
        canvasRef={graphCanvasRef}
        edgeColorMode={state.edgeColorMode}
        onEdgeColorModeChange={state.setEdgeColorMode}
        edgePruning={state.edgePruning}
        onToggleEdgePruning={state.toggleEdgePruning}
        edgePruningK={state.edgePruningK}
        onEdgePruningKChange={state.setEdgePruningK}
        totalEdgeCount={graphData?.meta.edge_count ?? 0}
        nodeSizeMode={state.nodeSizeMode}
        onNodeSizeModeChange={state.setNodeSizeMode}
        nodeColorMode={state.nodeColorMode}
        onNodeColorModeChange={state.setNodeColorMode}
        nodeSizeComputing={state.nodeSizeComputing}
        graphStats={state.graphStats}
        parsightBodyLinks={graphData.meta.parsight_body_links ?? null}
      />
    </div>
  )
}
