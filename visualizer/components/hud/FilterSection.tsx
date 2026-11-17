'use client'

import { TYPE_COLORS } from '@/lib/sigma-colors'
import { Tip } from './Tip'

interface Props {
  activeTypes: Set<string>
  onToggleType: (type: string) => void
  showDaily: boolean
  onToggleDaily: () => void
  hideIsolated: boolean
  onToggleHideIsolated: () => void
  labelsOnHoverOnly: boolean
  onToggleLabelsOnHoverOnly: () => void
}

export function FilterSection({activeTypes, onToggleType, showDaily, onToggleDaily, hideIsolated, onToggleHideIsolated, labelsOnHoverOnly, onToggleLabelsOnHoverOnly}: Props) {
  const typeList = Object.keys(TYPE_COLORS)

  return (
    <>
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
    </>
  )
}
