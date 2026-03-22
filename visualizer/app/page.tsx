'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import dynamic from 'next/dynamic'
import { loadGraphData } from '@/lib/graph'
import type { GraphData } from '@/lib/graph'
import type { GraphCanvasHandle } from '@/components/GraphCanvas'
import { useVisualizerState } from '@/lib/useVisualizerState'
import { FileExplorer } from '@/components/FileExplorer'
import { Toolbar } from '@/components/Toolbar'
import { ReadingPane } from '@/components/ReadingPane'
import { HUDPanel } from '@/components/HUDPanel'

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

export default function Home() {
  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const graphCanvasRef = useRef<GraphCanvasHandle>(null)

  useEffect(() => {
    localStorage.removeItem('vv:isLayoutRunning')
    if (!localStorage.getItem('vv:threshold_v2')) {
      localStorage.removeItem('vv:threshold')
      localStorage.setItem('vv:threshold_v2', '1')
    }
    loadGraphData()
      .then(setGraphData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const state = useVisualizerState(graphData)

  // Auto-collapse sidebar on narrow viewports
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 768px)')
    const handler = (e: MediaQueryListEvent) => {
      if (e.matches) state.setSidebarCollapsed(true)
    }
    if (mq.matches) state.setSidebarCollapsed(true)
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSearchSelect = useCallback((stem: string, newTab: boolean) => {
    state.openNote(stem, newTab)
    if (state.viewMode === 'graph') {
      graphCanvasRef.current?.flyToNode(stem)
      graphCanvasRef.current?.selectNode(stem)
    }
  }, [state])

  const handleGraphNodeClick = useCallback((stem: string, newTab: boolean) => {
    state.openNote(stem, newTab)
    state.setSelectedNode(stem)
  }, [state])

  const handleNavigate = useCallback((stem: string, newTab: boolean) => {
    const resolved = state.resolveWikilink(stem) ?? stem
    state.openNote(resolved, newTab)
    if (state.viewMode === 'graph') {
      graphCanvasRef.current?.flyToNode(resolved)
    }
  }, [state])

  // Determine neighborhood center for graph mode
  const neighborhoodCenter = state.graphScope === 'local' ? state.activeTab : null

  return (
    <main style={{
      position: 'fixed', inset: 0,
      background: 'radial-gradient(ellipse at 50% 50%, #0C0F1E 0%, #060608 70%)',
      display: 'flex', flexDirection: 'column',
      overflow: 'hidden',
      // CSS variable for toolbar height
      ['--toolbar-height' as string]: '42px',
    }}>
      {/* Star field */}
      <div style={{
        position: 'absolute', inset: 0,
        backgroundImage: 'radial-gradient(1px 1px at 10% 15%, rgba(255,255,255,0.15) 0%, transparent 100%), radial-gradient(1px 1px at 35% 60%, rgba(255,255,255,0.1) 0%, transparent 100%), radial-gradient(1px 1px at 75% 25%, rgba(255,255,255,0.12) 0%, transparent 100%), radial-gradient(1px 1px at 90% 80%, rgba(255,255,255,0.08) 0%, transparent 100%)',
        pointerEvents: 'none', zIndex: 0,
      }} />

      {loading && (
        <div style={{
          position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: 'Oxanium, sans-serif', color: '#00FFC8', fontSize: 14, letterSpacing: '0.12em',
          flexDirection: 'column', gap: 12, zIndex: 10,
        }}>
          <div style={{ fontSize: 24 }}>◈</div>
          <div>LOADING GRAPH DATA...</div>
        </div>
      )}

      {error && !loading && (
        <div style={{
          position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: 'Oxanium, sans-serif', color: '#ef4444', fontSize: 13, flexDirection: 'column', gap: 8, zIndex: 10,
        }}>
          <div>⚠ Failed to load graph.json</div>
          <div style={{ color: '#6B7A99', fontSize: 11 }}>{error}</div>
          <div style={{ color: '#6B7A99', fontSize: 11 }}>Run: make graph</div>
        </div>
      )}

      {!loading && !error && graphData && (
        <>
          {/* Toolbar */}
          <Toolbar
            onToggleSidebar={() => state.setSidebarCollapsed(c => !c)}
            tabs={state.openTabs}
            activeTab={state.activeTab}
            nodeMap={state.nodeMap}
            onSwitchTab={state.switchTab}
            onCloseTab={state.closeTab}
            nodes={graphData.nodes}
            onSearchSelect={handleSearchSelect}
            viewMode={state.viewMode}
            onViewModeChange={state.setViewMode}
          />

          {/* Body: sidebar + content */}
          <div style={{ flex: 1, display: 'flex', overflow: 'hidden', position: 'relative' }}>
            {/* File Explorer */}
            <FileExplorer
              fileTree={state.fileTree}
              activeTab={state.activeTab}
              onSelectNote={(stem, newTab) => state.openNote(stem, newTab)}
              width={state.sidebarWidth}
              onWidthChange={state.setSidebarWidth}
              collapsed={state.sidebarCollapsed}
              totalNotes={graphData.nodes.length}
            />

            {/* Content area */}
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, position: 'relative' }}>
              {state.viewMode === 'read' ? (
                <ReadingPane
                  node={state.activeNode}
                  fetchContent={state.fetchNoteContent}
                  onNavigate={handleNavigate}
                />
              ) : (
                /* Graph mode */
                <div style={{ flex: 1, position: 'relative' }}>
                  {/* Scope indicator — top-right to avoid HUD overlap */}
                  <div style={{
                    position: 'absolute', top: 12, right: 12,
                    display: 'flex', gap: 6, zIndex: 10,
                    fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
                  }}>
                    {state.activeTab && state.graphScope === 'local' && (
                      <div style={{
                        background: 'rgba(15,23,42,0.92)',
                        border: '1px solid #1e293b', borderRadius: 5,
                        padding: '4px 10px',
                        display: 'flex', gap: 8, alignItems: 'center',
                      }}>
                        <span style={{ color: '#f97316' }}>●</span>
                        <span style={{ color: '#e8e8f0' }}>{state.activeTab}</span>
                        <span style={{ color: '#6b7a99' }}>· 2 hops</span>
                      </div>
                    )}
                    <button
                      onClick={() => state.setGraphScope(state.graphScope === 'local' ? 'full' : 'local')}
                      style={{
                        background: 'rgba(15,23,42,0.92)',
                        border: '1px solid #1e293b', borderRadius: 5,
                        padding: '4px 10px',
                        color: '#7b61ff', cursor: 'pointer',
                        fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
                      }}
                    >
                      {state.graphScope === 'local' ? 'Show Full Vault ⤢' : 'Show Neighborhood ⤡'}
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
                    selectedNode={state.selectedNode}
                    onNodeClick={handleGraphNodeClick}
                    onBackgroundClick={() => state.setSelectedNode(null)}
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

                  {/* HUD Panel — graph mode only */}
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
                  />
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </main>
  )
}
