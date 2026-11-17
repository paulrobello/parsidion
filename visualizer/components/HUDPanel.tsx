'use client'

import { useState, useRef, useCallback, useEffect } from 'react'
import type { GraphSource } from '@/lib/graph'
import type { GraphStats } from '@/lib/useVisualizerState'
import type { EdgeColorMode, NodeSizeMode, NodeColorMode } from '@/lib/sigma-colors'
import { TemperatureBar } from './TemperatureBar'
import type { GraphCanvasHandle } from './GraphCanvas'
import { StatsSection } from './hud/StatsSection'
import { AppearanceSection } from './hud/AppearanceSection'
import { SourceSection } from './hud/SourceSection'
import { FilterSection } from './hud/FilterSection'
import { EdgeDensitySection } from './hud/EdgeDensitySection'
import { LayoutSection } from './hud/LayoutSection'

interface Props {
  threshold: number
  onThresholdChange: (v: number) => void
  graphSource: GraphSource
  onGraphSourceChange: (v: GraphSource) => void
  showOverlayEdges: boolean
  onToggleOverlayEdges: () => void
  filterNodesBySimilarity: boolean
  onToggleFilterNodesBySimilarity: () => void
  activeTypes: Set<string>
  onToggleType: (type: string) => void
  showDaily: boolean
  onToggleDaily: () => void
  hideIsolated: boolean
  onToggleHideIsolated: () => void
  labelsOnHoverOnly: boolean
  onToggleLabelsOnHoverOnly: () => void
  nodeCount: number
  edgeCount: number
  avgScore: number
  scalingRatio: number
  onScalingRatioChange: (v: number) => void
  gravity: number
  onGravityChange: (v: number) => void
  slowDown: number
  onSlowDownChange: (v: number) => void
  edgeWeightInfluence: number
  onEdgeWeightInfluenceChange: (v: number) => void
  startTemperature: number
  onStartTemperatureChange: (v: number) => void
  stopThreshold: number
  onStopThresholdChange: (v: number) => void
  isLayoutRunning: boolean
  onToggleLayout: () => void
  onResetSimSettings: () => void
  canvasRef: React.RefObject<GraphCanvasHandle | null>
  edgeColorMode: EdgeColorMode
  onEdgeColorModeChange: (mode: EdgeColorMode) => void
  edgePruning: boolean
  onToggleEdgePruning: () => void
  edgePruningK: number
  onEdgePruningKChange: (k: number) => void
  totalEdgeCount: number
  nodeSizeMode: NodeSizeMode
  onNodeSizeModeChange: (mode: NodeSizeMode) => void
  nodeColorMode: NodeColorMode
  onNodeColorModeChange: (mode: NodeColorMode) => void
  nodeSizeComputing: boolean
  graphStats: GraphStats | null
  /** meta.parsight_body_links from graph.json; null when absent. */
  parsightBodyLinks: number | null
}

export function HUDPanel({
  threshold: thresholdProp, onThresholdChange,
  graphSource, onGraphSourceChange,
  showOverlayEdges, onToggleOverlayEdges,
  filterNodesBySimilarity, onToggleFilterNodesBySimilarity,
  activeTypes, onToggleType,
  showDaily, onToggleDaily,
  hideIsolated, onToggleHideIsolated,
  labelsOnHoverOnly, onToggleLabelsOnHoverOnly,
  nodeCount, edgeCount, avgScore,
  scalingRatio, onScalingRatioChange,
  gravity, onGravityChange,
  slowDown, onSlowDownChange,
  edgeWeightInfluence, onEdgeWeightInfluenceChange,
  startTemperature, onStartTemperatureChange,
  stopThreshold, onStopThresholdChange,
  isLayoutRunning, onToggleLayout, onResetSimSettings,
  canvasRef,
  edgeColorMode, onEdgeColorModeChange,
  edgePruning, onToggleEdgePruning, edgePruningK, onEdgePruningKChange, totalEdgeCount,
  nodeSizeMode, onNodeSizeModeChange, nodeSizeComputing,
  nodeColorMode, onNodeColorModeChange,
  graphStats,
  parsightBodyLinks,
}: Props) {
  const [collapsed, setCollapsed] = useState(false)
  const [pos, setPos] = useState({ x: 16, y: 58 })
  const dragging = useRef(false)
  const dragOffset = useRef({ x: 0, y: 0 })
  const panelRef = useRef<HTMLDivElement>(null)

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    dragging.current = true
    dragOffset.current = { x: e.clientX - pos.x, y: e.clientY - pos.y }
  }, [pos])

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragging.current) return
      setPos({ x: e.clientX - dragOffset.current.x, y: e.clientY - dragOffset.current.y })
    }
    const onUp = () => { dragging.current = false }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [])

  return (
    <div
      ref={panelRef}
      style={{
        position: 'absolute',
        left: pos.x,
        top: pos.y,
        zIndex: 100,
        width: 260,
        background: 'rgba(8, 10, 18, 0.88)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
        border: '1px solid rgba(0, 255, 200, 0.2)',
        borderRadius: 10,
        boxShadow: '0 0 30px rgba(0,255,200,0.05), 0 8px 32px rgba(0,0,0,0.6)',
        fontFamily: 'Oxanium, sans-serif',
        fontSize: 12,
        color: '#E8E8F0',
        userSelect: 'none',
        animation: 'fadeSlideIn 0.4s ease-out both',
        animationDelay: '0.15s',
      }}
    >
      {/* Header */}
      <div
        onMouseDown={onMouseDown}
        style={{
          padding: '10px 12px',
          cursor: 'grab',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          borderBottom: collapsed ? 'none' : '1px solid rgba(0,255,200,0.1)',
        }}
      >
        <span style={{ color: '#00FFC8', fontWeight: 600, letterSpacing: '0.08em', fontSize: 11, textTransform: 'uppercase' }}>
          ◈ Vault Graph
        </span>
        <button
          onMouseDown={e => e.stopPropagation()}
          onClick={() => setCollapsed(c => !c)}
          style={{ background: 'none', border: 'none', color: '#6B7A99', cursor: 'pointer', fontSize: 14, lineHeight: 1 }}
        >
          {collapsed ? '⊞' : '⊟'}
        </button>
      </div>

      {!collapsed && (
        <div style={{ padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <StatsSection
            nodeCount={nodeCount}
            edgeCount={edgeCount}
            avgScore={avgScore}
            graphStats={graphStats}
            parsightBodyLinks={parsightBodyLinks}
            canvasRef={canvasRef}
          />

          <AppearanceSection
            edgeColorMode={edgeColorMode}
            onEdgeColorModeChange={onEdgeColorModeChange}
            nodeColorMode={nodeColorMode}
            onNodeColorModeChange={onNodeColorModeChange}
            nodeSizeMode={nodeSizeMode}
            onNodeSizeModeChange={onNodeSizeModeChange}
            nodeSizeComputing={nodeSizeComputing}
          />

          <SourceSection
            threshold={thresholdProp}
            onThresholdChange={onThresholdChange}
            graphSource={graphSource}
            onGraphSourceChange={onGraphSourceChange}
            showOverlayEdges={showOverlayEdges}
            onToggleOverlayEdges={onToggleOverlayEdges}
            filterNodesBySimilarity={filterNodesBySimilarity}
            onToggleFilterNodesBySimilarity={onToggleFilterNodesBySimilarity}
          />

          <FilterSection
            activeTypes={activeTypes}
            onToggleType={onToggleType}
            showDaily={showDaily}
            onToggleDaily={onToggleDaily}
            hideIsolated={hideIsolated}
            onToggleHideIsolated={onToggleHideIsolated}
            labelsOnHoverOnly={labelsOnHoverOnly}
            onToggleLabelsOnHoverOnly={onToggleLabelsOnHoverOnly}
          />

          <EdgeDensitySection
            totalEdgeCount={totalEdgeCount}
            edgePruning={edgePruning}
            onToggleEdgePruning={onToggleEdgePruning}
            edgePruningK={edgePruningK}
            onEdgePruningKChange={onEdgePruningKChange}
          />

          <LayoutSection
            scalingRatio={scalingRatio}
            onScalingRatioChange={onScalingRatioChange}
            gravity={gravity}
            onGravityChange={onGravityChange}
            slowDown={slowDown}
            onSlowDownChange={onSlowDownChange}
            edgeWeightInfluence={edgeWeightInfluence}
            onEdgeWeightInfluenceChange={onEdgeWeightInfluenceChange}
            startTemperature={startTemperature}
            onStartTemperatureChange={onStartTemperatureChange}
            stopThreshold={stopThreshold}
            onStopThresholdChange={onStopThresholdChange}
            isLayoutRunning={isLayoutRunning}
            onToggleLayout={onToggleLayout}
            onResetSimSettings={onResetSimSettings}
          />

        </div>
      )}

      {/* Temperature bar — always visible even when collapsed */}
      <TemperatureBar canvasRef={canvasRef} stopThreshold={stopThreshold} />
    </div>
  )
}
