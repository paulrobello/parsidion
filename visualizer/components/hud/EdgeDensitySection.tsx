'use client'

import { Tip } from './Tip'

interface Props {
  totalEdgeCount: number
  edgePruning: boolean
  onToggleEdgePruning: () => void
  edgePruningK: number
  onEdgePruningKChange: (k: number) => void
}

export function EdgeDensitySection({totalEdgeCount, edgePruning, onToggleEdgePruning, edgePruningK, onEdgePruningKChange}: Props) {

  return (
    <>
      {/* Edge Density — only shown for dense graphs */}
      {totalEdgeCount > 2000 && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 5 }}>
            <span style={{ color: '#6B7A99', textTransform: 'uppercase', letterSpacing: '0.06em', fontSize: 10 }}>Edge Density</span>
            <Tip text="Keeps the K strongest connections per node — reduces visual clutter on dense graphs." />
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <input
              type="checkbox" checked={edgePruning} onChange={onToggleEdgePruning}
              style={{ accentColor: '#00FFC8', width: 14, height: 14 }}
            />
            <span style={{ color: '#6B7A99', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
              Reduce Edge Density
            </span>
          </label>
          {edgePruning && (
            <div style={{ marginTop: 6 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                <span style={{ color: '#6B7A99', fontSize: 10 }}>Max edges/node</span>
                <span style={{ color: '#00FFC8', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 }}>{edgePruningK}</span>
              </div>
              <input
                type="range" min={3} max={20} step={1} value={edgePruningK}
                onChange={e => onEdgePruningKChange(parseInt(e.target.value))}
                style={{ width: '100%', accentColor: '#00FFC8', cursor: 'pointer' }}
              />
            </div>
          )}
        </div>
      )}
    </>
  )
}
