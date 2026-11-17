'use client'

import { Tip } from './Tip'

interface Props {
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
}

export function LayoutSection({scalingRatio, onScalingRatioChange, gravity, onGravityChange, slowDown, onSlowDownChange, edgeWeightInfluence, onEdgeWeightInfluenceChange, startTemperature, onStartTemperatureChange, stopThreshold, onStopThresholdChange, isLayoutRunning, onToggleLayout, onResetSimSettings}: Props) {
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
    <div style={{ borderTop: '1px solid rgba(255,255,255,0.06)', paddingTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <span style={{ color: '#6B7A99', textTransform: 'uppercase', letterSpacing: '0.06em', fontSize: 10 }}>Force Layout</span>
          <Tip text="Newtonian physics simulation (gravity + Coulomb repulsion + Hooke attraction). Runs continuously until paused or converged." />
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
  )
}
