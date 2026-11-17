'use client'

import { useState } from 'react'

export function Tip({ text }: { text: string }) {
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
