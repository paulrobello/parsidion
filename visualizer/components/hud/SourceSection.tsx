'use client'

import { useState, useRef, useCallback, useEffect } from 'react'
import type { GraphSource } from '@/lib/graph'
import { Tip } from './Tip'

interface Props {
  threshold: number
  onThresholdChange: (v: number) => void
  graphSource: GraphSource
  onGraphSourceChange: (v: GraphSource) => void
  showOverlayEdges: boolean
  onToggleOverlayEdges: () => void
  filterNodesBySimilarity: boolean
  onToggleFilterNodesBySimilarity: () => void
}

export function SourceSection({threshold: thresholdProp, onThresholdChange, graphSource, onGraphSourceChange, showOverlayEdges, onToggleOverlayEdges, filterNodesBySimilarity, onToggleFilterNodesBySimilarity}: Props) {
  // ARC-037: local fast state for the similarity-threshold slider. The
  // parent's onThresholdChange triggers a graph.clearEdges() + re-add
  // across 376k edges plus a layout reheat per tick — running that on every
  // drag tick (60+ Hz during a drag) freezes the UI for ~150 ms each. The
  // slider updates locally per onChange (responsive), and a 120 ms
  // debounce coalesces rapid-fire changes into a single parent commit.
  // The local value syncs from the prop when the parent's value changes
  // for any other reason (e.g. reset).
  const [thresholdLocal, setThresholdLocal] = useState(thresholdProp)
  useEffect(() => { setThresholdLocal(thresholdProp) }, [thresholdProp])
  const thresholdTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (thresholdTimerRef.current !== null) clearTimeout(thresholdTimerRef.current)
  }, [])
  const onThresholdSliderChange = useCallback((v: number) => {
    setThresholdLocal(v)
    if (thresholdTimerRef.current !== null) clearTimeout(thresholdTimerRef.current)
    thresholdTimerRef.current = setTimeout(() => {
      thresholdTimerRef.current = null
      onThresholdChange(v)
    }, 120)
  }, [onThresholdChange])
  // The display value uses the local state so the slider thumb tracks the
  // mouse smoothly even before the parent commits.
  const threshold = thresholdLocal

  const sources: GraphSource[] = ['semantic', 'wiki']

  return (
    <>
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
            onChange={e => onThresholdSliderChange(parseFloat(e.target.value))}
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
    </>
  )
}
