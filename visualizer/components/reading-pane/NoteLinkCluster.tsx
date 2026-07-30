'use client'

interface Props {
  title: string
  stems: string[]
  onWikilink: (stem: string, e: React.MouseEvent) => void
  variant: 'related' | 'linked'
}

export function NoteLinkCluster({ title, stems, onWikilink, variant }: Props) {
  const isRelated = variant === 'related'
  return (
    <div style={{
      marginBottom: 16, padding: '8px 12px',
      background: isRelated ? 'rgba(123,97,255,0.06)' : 'rgba(0,255,200,0.05)',
      border: isRelated ? '1px solid rgba(123,97,255,0.15)' : '1px solid rgba(0,255,200,0.12)',
      borderRadius: 6,
    }}>
      <div style={{
        fontSize: 9, fontFamily: "'Oxanium', sans-serif", color: '#6b7a99',
        textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6,
      }}>{title}</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {stems.map(stem => (
          <span
            key={stem}
            className="wikilink"
            onClick={(e) => onWikilink(stem, e)}
            style={{ fontSize: 12 }}
          >
            {stem}
          </span>
        ))}
      </div>
    </div>
  )
}
