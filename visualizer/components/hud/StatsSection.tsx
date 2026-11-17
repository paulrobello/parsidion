'use client'

import { useState } from 'react'
import type { GraphStats } from '@/lib/useVisualizerState'
import type { GraphCanvasHandle } from '../GraphCanvas'

interface Props {
  nodeCount: number
  edgeCount: number
  avgScore: number
  graphStats: GraphStats | null
  /** meta.parsight_body_links from graph.json; null when absent. */
  parsightBodyLinks: number | null
  canvasRef: React.RefObject<GraphCanvasHandle | null>
}

export function StatsSection({nodeCount, edgeCount, avgScore, graphStats, parsightBodyLinks, canvasRef}: Props) {
  const [statsExpanded, setStatsExpanded] = useState(false)

  return (
    <div>
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

      {/* Graph Analysis collapsible */}
      {graphStats && (
        <div style={{ marginTop: 6 }}>
          <button
            onClick={() => setStatsExpanded(s => !s)}
            style={{
              width: '100%', background: 'none', border: 'none', cursor: 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '2px 0', color: '#6B7A99', fontSize: 9,
              textTransform: 'uppercase', letterSpacing: '0.06em', fontFamily: 'Oxanium, sans-serif',
            }}
          >
            <span>Graph Analysis</span>
            <span>{statsExpanded ? '▲' : '▼'}</span>
          </button>
          {statsExpanded && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginTop: 5 }}>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {[
                  { label: 'avg deg', value: graphStats.avgDegree.toFixed(1) },
                  { label: 'max deg', value: graphStats.maxDegree },
                  { label: 'density', value: (graphStats.density * 100).toFixed(2) + '%' },
                  { label: 'components', value: graphStats.componentCount },
                  ...(parsightBodyLinks != null ? [{ label: 'body links', value: parsightBodyLinks }] : []),
                ].map(s => (
                  <div key={s.label} style={{ background: 'rgba(123,97,255,0.06)', border: '1px solid rgba(123,97,255,0.15)', borderRadius: 4, padding: '3px 7px', flex: '1 0 auto', textAlign: 'center' }}>
                    <div style={{ color: '#7B61FF', fontWeight: 600, fontSize: 12 }}>{s.value}</div>
                    <div style={{ color: '#6B7A99', fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{s.label}</div>
                  </div>
                ))}
              </div>
              <div>
                <div style={{ color: '#6B7A99', fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>Top Hubs</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                  {graphStats.topHubs.map(hub => (
                    <button
                      key={hub.id}
                      onClick={() => canvasRef.current?.flyToNode(hub.id)}
                      style={{
                        background: 'rgba(0,255,200,0.04)', border: '1px solid rgba(0,255,200,0.1)',
                        borderRadius: 3, padding: '3px 7px', cursor: 'pointer',
                        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                        fontFamily: 'Oxanium, sans-serif',
                      }}
                    >
                      <span style={{ color: '#A0B8C8', fontSize: 10, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 160 }}>
                        {hub.title}
                      </span>
                      <span style={{ color: '#00FFC8', fontSize: 10, marginLeft: 6, flexShrink: 0 }}>{hub.degree}</span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
