'use client'

import { RECENCY_HEATMAP_GRADIENT } from '@/lib/sigma-colors'
import type { EdgeColorMode, NodeSizeMode, NodeColorMode } from '@/lib/sigma-colors'
import { Tip } from './Tip'

interface Props {
  edgeColorMode: EdgeColorMode
  onEdgeColorModeChange: (mode: EdgeColorMode) => void
  nodeColorMode: NodeColorMode
  onNodeColorModeChange: (mode: NodeColorMode) => void
  nodeSizeMode: NodeSizeMode
  onNodeSizeModeChange: (mode: NodeSizeMode) => void
  nodeSizeComputing: boolean
}

export function AppearanceSection({edgeColorMode, onEdgeColorModeChange, nodeColorMode, onNodeColorModeChange, nodeSizeMode, onNodeSizeModeChange, nodeSizeComputing}: Props) {

  return (
    <>
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

      {/* Node Color */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 5 }}>
          <span style={{ color: '#6B7A99', textTransform: 'uppercase', letterSpacing: '0.06em', fontSize: 10 }}>Node Color</span>
          <Tip text="Type: color by note type. Heatmap: color by how recently each note was changed (red = recent, blue = old)." />
        </div>
        <div style={{ display: 'flex', gap: 4 }}>
          {(['type', 'recency'] as const).map(m => (
            <button
              key={m}
              onClick={() => onNodeColorModeChange(m)}
              style={{
                flex: 1, padding: '4px 0', borderRadius: 4, border: '1px solid',
                borderColor: nodeColorMode === m ? '#00FFC8' : 'rgba(255,255,255,0.08)',
                background: nodeColorMode === m ? 'rgba(0,255,200,0.12)' : 'transparent',
                color: nodeColorMode === m ? '#00FFC8' : '#6B7A99',
                cursor: 'pointer', fontSize: 10, fontFamily: 'Oxanium, sans-serif',
                transition: 'all 0.15s',
              }}
            >
              {m === 'type' ? 'Type' : 'Heatmap'}
            </button>
          ))}
        </div>
        {nodeColorMode === 'recency' && (
          <div style={{ marginTop: 6 }}>
            <div style={{
              height: 8, borderRadius: 4,
              background: RECENCY_HEATMAP_GRADIENT,
            }} />
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 3 }}>
              <span style={{ color: '#6B7A99', fontSize: 9, fontFamily: 'Oxanium, sans-serif' }}>recent</span>
              <span style={{ color: '#6B7A99', fontSize: 9, fontFamily: 'Oxanium, sans-serif' }}>old</span>
            </div>
          </div>
        )}
      </div>

      {/* Node Size */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 5 }}>
          <span style={{ color: '#6B7A99', textTransform: 'uppercase', letterSpacing: '0.06em', fontSize: 10 }}>Node Size</span>
          <Tip text="Uniform: equal size. Links: by incoming wikilinks. Betweenness: by graph centrality. Recency: newer notes larger." />
          {nodeSizeComputing && (
            <span style={{ marginLeft: 6, color: '#f59e0b', fontSize: 9, fontFamily: 'JetBrains Mono, monospace' }}>Computing…</span>
          )}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {([
            ['uniform',        'Uniform'],
            ['incoming_links', 'Links'],
            ['betweenness',    'Centrality'],
            ['recency',        'Recency'],
          ] as const).map(([mode, label]) => (
            <button
              key={mode}
              onClick={() => onNodeSizeModeChange(mode)}
              style={{
                flex: '1 0 auto', padding: '4px 0', borderRadius: 4, border: '1px solid',
                borderColor: nodeSizeMode === mode ? '#00FFC8' : 'rgba(255,255,255,0.08)',
                background: nodeSizeMode === mode ? 'rgba(0,255,200,0.12)' : 'transparent',
                color: nodeSizeMode === mode ? '#00FFC8' : '#6B7A99',
                cursor: 'pointer', fontSize: 10, fontFamily: 'Oxanium, sans-serif',
                transition: 'all 0.15s',
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
    </>
  )
}
