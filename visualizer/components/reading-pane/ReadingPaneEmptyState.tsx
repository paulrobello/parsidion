'use client'

export function ReadingPaneEmptyState() {
  return (
    <div style={{
      flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
      color: '#6b7a99', fontFamily: "'JetBrains Mono', monospace", fontSize: 12,
      flexDirection: 'column', gap: 8,
    }}>
      <div style={{ fontSize: 24, opacity: 0.3 }}>◈</div>
      <div>Open a note from the sidebar or press ⌘K to search.</div>
    </div>
  )
}
