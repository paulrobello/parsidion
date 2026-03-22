'use client'

interface Props {
  mode: 'read' | 'graph'
  onToggle: (mode: 'read' | 'graph') => void
}

export function ViewToggle({ mode, onToggle }: Props) {
  return (
    <div style={{
      display: 'flex', background: '#1e293b', borderRadius: 5, padding: 2,
    }}>
      {(['read', 'graph'] as const).map(m => (
        <button
          key={m}
          onClick={() => onToggle(m)}
          style={{
            padding: '3px 10px', borderRadius: 4, border: 'none',
            background: mode === m ? '#6366f1' : 'transparent',
            color: mode === m ? '#e8e8f0' : '#6b7a99',
            fontSize: 10, fontFamily: "'JetBrains Mono', monospace",
            cursor: 'pointer', textTransform: 'capitalize',
            transition: 'all 0.15s',
          }}
        >
          {m === 'read' ? 'Read' : 'Graph'}
        </button>
      ))}
    </div>
  )
}
