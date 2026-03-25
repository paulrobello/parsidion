'use client'

import { useState, useRef, useCallback, useEffect } from 'react'
import type { GraphSource } from '@/lib/graph'
import { TYPE_COLORS } from '@/lib/sigma-colors'
import type { EdgeColorMode } from '@/lib/sigma-colors'
import { TemperatureBar } from './TemperatureBar'
import type { GraphCanvasHandle } from './GraphCanvas'

function Tip({ text }: { text: string }) {
  const [visible, setVisible] = useState(false)
  return (
    <span
      style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', marginLeft: 4, cursor: 'default' }}
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => setVisible(false)}
    >
      <span style={{ color: '#3A4560', fontSize: 9, lineHeight: 1, userSelect: 'none' }}>ⓘ</span>
      {visible && (
        <span style={{
          position: 'absolute', left: 14, top: '50%', transform: 'translateY(-50%)',
          background: 'rgba(6, 8, 18, 0.97)',
          border: '1px solid rgba(0,255,200,0.2)',
          borderRadius: 5, padding: '5px 8px',
          color: '#A0B0C8', fontSize: 10, fontFamily: 'Syne, sans-serif',
          whiteSpace: 'nowrap', zIndex: 200, pointerEvents: 'none',
          boxShadow: '0 4px 16px rgba(0,0,0,0.6)',
          fontWeight: 400, letterSpacing: 0, textTransform: 'none',
        }}>
          {text}
        </span>
      )}
    </span>
  )
}

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
}

export function HUDPanel({
  threshold, onThresholdChange,
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

  const typeList = Object.keys(TYPE_COLORS)
  const sources: GraphSource[] = ['semantic', 'wiki']

  const sliders = [
    {
      label: 'Repulsion', value: scalingRatio, min: 1, max: 100, step: 1,
      fmt: (v: number) => v.toFixed(0), onChange: onScalingRatioChange, accent: '#7B61FF',
      tip: 'Node repulsion strength. Higher = more spread out.',
    },
    {
      label: 'Gravity', value: gravity, min: 0.1, max: 5, step: 0.1,
      fmt: (v: number) => v.toFixed(1), onChange: onGravityChange, accent: '#7B61FF',
      tip: 'Pulls nodes toward center. Higher = more compact.',
    },
    {
      label: 'Cooling', value: slowDown, min: 0.1, max: 5, step: 0.05,
      fmt: (v: number) => v.toFixed(2), onChange: onSlowDownChange, accent: '#7B61FF',
      tip: 'How fast the simulation cools. Higher = faster settling.',
    },
    {
      label: 'Edge Strength', value: edgeWeightInfluence, min: 0, max: 10, step: 0.5,
      fmt: (v: number) => v.toFixed(1), onChange: onEdgeWeightInfluenceChange, accent: '#f59e0b',
      tip: 'Multiplier on edge attraction. 0 = no pull, 1 = normal, higher = tighter clusters.',
    },
    {
      label: 'Start Temp', value: startTemperature, min: 0.05, max: 1.0, step: 0.05,
      fmt: (v: number) => v.toFixed(2), onChange: onStartTemperatureChange, accent: '#f97316',
      tip: 'Temperature at simulation start/reheat. Lower = gentler, less disruptive restarts.',
    },
    {
      label: 'Auto-Stop', value: stopThreshold, min: 0, max: 0.05, step: 0.001,
      fmt: (v: number) => v === 0 ? 'off' : v.toFixed(3), onChange: onStopThresholdChange, accent: '#e879f9',
      tip: 'Stop simulation when avg movement falls below this threshold. 0 = never auto-stop.',
    },
  ]

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
          {/* Stats */}
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {[
              { label: 'nodes', value: nodeCount },
              { label: 'edges', value: edgeCount },
              { label: 'avg sim', value: avgScore.toFixed(2) },
            ].map(s => (
              <div key={s.label} style={{ background: 'rgba(0,255,200,0.05)', border: '1px solid rgba(0,255,200,0.1)', borderRadius: 4, padding: '3px 7px', flex: '1 0 auto', textAlign: 'center' }}>
                <div style={{ color: '#00FFC8', fontWeight: 600, fontSize: 13 }}>{s.value}</div>
                <div style={{ color: '#6B7A99', fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{s.label}</div>
              </div>
            ))}
          </div>

          {/* Edge Color */}
          <div>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 5 }}>
              <span style={{ color: '#6B7A99', textTransform: 'uppercase', letterSpacing: '0.06em', fontSize: 10 }}>Edge Color</span>
              <Tip text="Binary: semantic edges use opacity. Gradient: blue (weak) → red (strong) by similarity score." />
            </div>
            <div style={{ display: 'flex', gap: 4 }}>
              {(['binary', 'gradient'] as const).map(m => (
                <button
                  key={m}
                  onClick={() => onEdgeColorModeChange(m)}
                  style={{
                    flex: 1, padding: '4px 0', borderRadius: 4, border: '1px solid',
                    borderColor: edgeColorMode === m ? '#00FFC8' : 'rgba(255,255,255,0.08)',
                    background: edgeColorMode === m ? 'rgba(0,255,200,0.12)' : 'transparent',
                    color: edgeColorMode === m ? '#00FFC8' : '#6B7A99',
                    cursor: 'pointer', fontSize: 10, fontFamily: 'Oxanium, sans-serif',
                    textTransform: 'capitalize', transition: 'all 0.15s',
                  }}
                >
                  {m}
                </button>
              ))}
            </div>
          </div>

          {/* Threshold */}
          {(graphSource !== 'wiki' || filterNodesBySimilarity) && (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 5 }}>
                <span style={{ display: 'flex', alignItems: 'center' }}>
                  <span style={{ color: '#6B7A99', textTransform: 'uppercase', letterSpacing: '0.06em', fontSize: 10 }}>Similarity</span>
                  <Tip text="Minimum cosine similarity for semantic edges. Higher = fewer but stronger connections." />
                </span>
                <span style={{ color: '#00FFC8', fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }}>{threshold.toFixed(2)}</span>
              </div>
              <input
                type="range" min={0.60} max={0.99} step={0.01}
                value={threshold}
                onChange={e => onThresholdChange(parseFloat(e.target.value))}
                style={{ width: '100%', accentColor: '#00FFC8', cursor: 'pointer' }}
              />
            </div>
          )}

          {/* Graph Source */}
          <div>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 5 }}>
              <span style={{ color: '#6B7A99', textTransform: 'uppercase', letterSpacing: '0.06em', fontSize: 10 }}>Edge Source</span>
              <Tip text="Which edge types to show: semantic (similarity-based), wiki (wikilinks), or both." />
            </div>
            <div style={{ display: 'flex', gap: 4 }}>
              {sources.map(s => (
                <button
                  key={s}
                  onClick={() => onGraphSourceChange(s)}
                  style={{
                    flex: 1, padding: '4px 0', borderRadius: 4, border: '1px solid',
                    borderColor: graphSource === s ? '#00FFC8' : 'rgba(255,255,255,0.08)',
                    background: graphSource === s ? 'rgba(0,255,200,0.12)' : 'transparent',
                    color: graphSource === s ? '#00FFC8' : '#6B7A99',
                    cursor: 'pointer', fontSize: 10, fontFamily: 'Oxanium, sans-serif',
                    textTransform: 'capitalize', transition: 'all 0.15s',
                  }}
                >
                  {s}
                </button>
              ))}
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginTop: 6 }}>
              <input
                type="checkbox" checked={showOverlayEdges} onChange={onToggleOverlayEdges}
                style={{ accentColor: '#00FFC8', width: 14, height: 14 }}
              />
              <span style={{ color: '#6B7A99', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                Show {graphSource === 'semantic' ? 'Wiki' : 'Semantic'} Links
              </span>
              <Tip text={`Overlay ${graphSource === 'semantic' ? 'wikilink' : 'semantic similarity'} edges without affecting the layout.`} />
            </label>
            {graphSource === 'wiki' && (
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', marginTop: 6 }}>
                <input
                  type="checkbox" checked={filterNodesBySimilarity} onChange={onToggleFilterNodesBySimilarity}
                  style={{ accentColor: '#00FFC8', width: 14, height: 14 }}
                />
                <span style={{ color: '#6B7A99', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  Filter Nodes by Similarity
                </span>
                <Tip text="Hide nodes with no semantic connections above the similarity threshold. Also hides their wiki links." />
              </label>
            )}
          </div>

          {/* Type filters */}
          <div>
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 5 }}>
              <span style={{ color: '#6B7A99', textTransform: 'uppercase', letterSpacing: '0.06em', fontSize: 10 }}>Note Types</span>
              <Tip text="Toggle which note types are visible in the graph." />
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {typeList.filter(t => t !== 'daily').map(t => (
                <button
                  key={t}
                  onClick={() => onToggleType(t)}
                  style={{
                    padding: '3px 7px', borderRadius: 3, border: '1px solid',
                    borderColor: activeTypes.has(t) ? TYPE_COLORS[t] : 'rgba(255,255,255,0.06)',
                    background: activeTypes.has(t) ? `${TYPE_COLORS[t]}22` : 'transparent',
                    color: activeTypes.has(t) ? TYPE_COLORS[t] : '#4A5570',
                    cursor: 'pointer', fontSize: 10, fontFamily: 'Oxanium, sans-serif',
                    transition: 'all 0.15s', letterSpacing: '0.04em',
                  }}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>

          {/* Daily / isolated toggles */}
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <input
              type="checkbox" checked={showDaily} onChange={onToggleDaily}
              style={{ accentColor: '#00FFC8', width: 14, height: 14 }}
            />
            <span style={{ color: '#6B7A99', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              Show Daily Notes
            </span>
            <Tip text="Include daily journal notes in the graph." />
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <input
              type="checkbox" checked={hideIsolated} onChange={onToggleHideIsolated}
              style={{ accentColor: '#00FFC8', width: 14, height: 14 }}
            />
            <span style={{ color: '#6B7A99', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              Hide Unlinked Nodes
            </span>
            <Tip text="Hide nodes with no edges under current filters." />
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <input
              type="checkbox" checked={labelsOnHoverOnly} onChange={onToggleLabelsOnHoverOnly}
              style={{ accentColor: '#00FFC8', width: 14, height: 14 }}
            />
            <span style={{ color: '#6B7A99', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              Labels on Hover Only
            </span>
            <Tip text="Only show node labels when hovering or selecting." />
          </label>

          {/* Layout params */}
          <div style={{ borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ display: 'flex', alignItems: 'center' }}>
                <span style={{ color: '#6B7A99', textTransform: 'uppercase', letterSpacing: '0.06em', fontSize: 10 }}>Force Layout</span>
                <Tip text="ForceAtlas2 physics simulation. Runs continuously until paused or converged." />
              </div>
              <div style={{ display: 'flex', gap: 4 }}>
                <button
                  onClick={onResetSimSettings}
                  title="Reset simulation settings to defaults"
                  style={{
                    padding: '2px 8px', borderRadius: 3,
                    border: '1px solid rgba(255,255,255,0.12)',
                    background: 'rgba(255,255,255,0.04)',
                    color: '#6B7A99',
                    cursor: 'pointer', fontFamily: 'Oxanium, sans-serif', fontSize: 10,
                    letterSpacing: '0.06em', transition: 'all 0.15s',
                  }}
                >
                  Reset
                </button>
                <button
                  onClick={onToggleLayout}
                  style={{
                    padding: '2px 10px', borderRadius: 3,
                    border: '1px solid',
                    borderColor: isLayoutRunning ? 'rgba(0,255,200,0.35)' : 'rgba(255,255,255,0.12)',
                    background: isLayoutRunning ? 'rgba(0,255,200,0.1)' : 'rgba(255,255,255,0.04)',
                    color: isLayoutRunning ? '#00FFC8' : '#6B7A99',
                    cursor: 'pointer', fontFamily: 'Oxanium, sans-serif', fontSize: 10,
                    letterSpacing: '0.06em', transition: 'all 0.15s',
                  }}
                >
                  {isLayoutRunning ? '⏸ Pause' : '▶ Run'}
                </button>
              </div>
            </div>
            {sliders.map(({ label, value, min, max, step, fmt, onChange, accent, tip }) => (
              <div key={label}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                  <span style={{ display: 'flex', alignItems: 'center' }}>
                    <span style={{ color: '#6B7A99', fontSize: 10 }}>{label}</span>
                    <Tip text={tip} />
                  </span>
                  <span style={{ color: accent, fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>{fmt(value)}</span>
                </div>
                <input
                  type="range" min={min} max={max} step={step} value={value}
                  onChange={e => onChange(parseFloat(e.target.value))}
                  style={{ width: '100%', accentColor: accent, cursor: 'pointer' }}
                />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Temperature bar — always visible even when collapsed */}
      <TemperatureBar canvasRef={canvasRef} stopThreshold={stopThreshold} />
    </div>
  )
}
