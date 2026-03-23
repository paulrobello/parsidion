'use client'

import { useEffect, useRef } from 'react'

interface Props {
  title: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmDialog({
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  danger = false,
  onConfirm,
  onCancel,
}: Props) {
  const cancelRef = useRef<HTMLButtonElement>(null)

  // Focus cancel button by default (safer) and handle Escape
  useEffect(() => {
    cancelRef.current?.focus()
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onCancel])

  const confirmColor = danger ? '#ef4444' : '#00FFC8'
  const confirmBg = danger ? 'rgba(239,68,68,0.15)' : 'rgba(0,255,200,0.15)'
  const confirmBorder = danger ? 'rgba(239,68,68,0.4)' : 'rgba(0,255,200,0.3)'

  return (
    /* Backdrop */
    <div
      onClick={onCancel}
      style={{
        position: 'fixed', inset: 0,
        background: 'rgba(0,0,0,0.6)',
        backdropFilter: 'blur(2px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 9999,
      }}
    >
      {/* Dialog */}
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-message"
        onClick={e => e.stopPropagation()}
        style={{
          background: 'linear-gradient(180deg, #0d1224 0%, #0a0f1e 100%)',
          border: '1px solid #1e293b',
          borderRadius: 10,
          boxShadow: '0 24px 64px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.04)',
          padding: '28px 32px',
          minWidth: 340,
          maxWidth: 440,
        }}
      >
        <h2
          id="confirm-title"
          style={{
            fontFamily: "'Oxanium', sans-serif",
            fontSize: 16, fontWeight: 700,
            color: '#e8e8f0',
            margin: '0 0 10px',
          }}
        >
          {title}
        </h2>
        <p
          id="confirm-message"
          style={{
            fontFamily: "'Syne', sans-serif",
            fontSize: 13, color: '#9ca3af',
            margin: '0 0 24px', lineHeight: 1.6,
          }}
        >
          {message}
        </p>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button
            ref={cancelRef}
            onClick={onCancel}
            style={{
              background: 'rgba(30,41,59,0.8)', border: '1px solid #334155',
              color: '#9ca3af', cursor: 'pointer', borderRadius: 6,
              padding: '7px 18px',
              fontFamily: "'JetBrains Mono', monospace", fontSize: 12,
            }}
          >
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            style={{
              background: confirmBg,
              border: `1px solid ${confirmBorder}`,
              color: confirmColor,
              cursor: 'pointer', borderRadius: 6,
              padding: '7px 18px',
              fontFamily: "'Oxanium', sans-serif", fontSize: 12, fontWeight: 600,
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
