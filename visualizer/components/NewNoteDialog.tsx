'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { TYPE_COLORS } from '@/lib/sigma-colors'
import { FrontmatterEditor } from './FrontmatterEditor'
import { defaultFields, serializeFrontmatter } from '@/lib/frontmatter'
import type { FrontmatterFields } from '@/lib/frontmatter'
import type { NoteNode } from '@/lib/graph'

const TYPE_TO_FOLDER: Record<string, string> = {
  pattern:   'Patterns',
  debugging: 'Debugging',
  research:  'Research',
  project:   'Projects',
  tool:      'Tools',
  language:  'Languages',
  framework: 'Frameworks',
}

function toStem(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, '')
    .trim()
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
}

interface Props {
  onConfirm: (notePath: string, content: string, stem: string) => Promise<void>
  onCancel: () => void
  nodes: NoteNode[]
}

export function NewNoteDialog({ onConfirm, onCancel, nodes }: Props) {
  const [title, setTitle] = useState('')
  const [fields, setFields] = useState<FrontmatterFields>(defaultFields)
  const [isCreating, setIsCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const titleRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    titleRef.current?.focus()
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onCancel])

  const stem = toStem(title)
  const folder = TYPE_TO_FOLDER[fields.type] ?? 'Patterns'
  const notePath = stem ? `${folder}/${stem}.md` : ''
  const typeColor = TYPE_COLORS[fields.type] ?? '#6b7280'

  const handleSubmit = useCallback(async () => {
    if (!stem) return
    setIsCreating(true)
    setError(null)
    try {
      const body = `# ${title.trim()}\n`
      const content = serializeFrontmatter(fields, body)
      await onConfirm(notePath, content, stem)
    } catch (e) {
      setError((e as Error).message)
      setIsCreating(false)
    }
  }, [stem, title, fields, notePath, onConfirm])

  return (
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
      <div
        role="dialog"
        aria-modal="true"
        aria-label="New note"
        onClick={e => e.stopPropagation()}
        style={{
          background: 'linear-gradient(180deg, #0d1224 0%, #0a0f1e 100%)',
          border: '1px solid #1e293b',
          borderRadius: 10,
          boxShadow: '0 24px 64px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.04)',
          padding: '28px 32px',
          width: 560,
          maxWidth: '95vw',
          maxHeight: '90vh',
          overflow: 'auto',
        }}
      >
        <h2 style={{
          fontFamily: "'Oxanium', sans-serif",
          fontSize: 16, fontWeight: 700,
          color: '#e8e8f0', margin: '0 0 16px',
        }}>
          New Note
        </h2>

        {/* Title */}
        <div style={{ marginBottom: 14 }}>
          <div style={labelStyle}>Title</div>
          <input
            ref={titleRef}
            value={title}
            onChange={e => setTitle(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && stem) handleSubmit() }}
            placeholder="My Note Title"
            style={inputStyle}
          />
        </div>

        {/* Frontmatter fields */}
        <FrontmatterEditor
          fields={fields}
          onChange={setFields}
          nodes={nodes}
        />

        {/* Path preview */}
        <div style={{
          background: 'rgba(15,23,42,0.8)',
          border: '1px solid #1e293b',
          borderRadius: 5, padding: '8px 12px',
          margin: '14px 0',
          fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
        }}>
          <span style={{ color: '#4b5563' }}>Path: </span>
          {notePath ? (
            <>
              <span style={{ color: `${typeColor}99` }}>{folder}/</span>
              <span style={{ color: '#e8e8f0' }}>{stem}.md</span>
            </>
          ) : (
            <span style={{ color: '#4b5563' }}>enter a title…</span>
          )}
        </div>

        {error && (
          <div style={{
            color: '#ef4444', fontFamily: "'JetBrains Mono', monospace",
            fontSize: 11, marginBottom: 12,
          }}>
            {error}
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button onClick={onCancel} style={cancelBtnStyle}>
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={!stem || isCreating}
            style={{
              background: !stem || isCreating ? 'rgba(0,255,200,0.05)' : 'rgba(0,255,200,0.15)',
              border: '1px solid rgba(0,255,200,0.3)',
              color: !stem || isCreating ? 'rgba(0,255,200,0.4)' : '#00FFC8',
              cursor: !stem || isCreating ? 'default' : 'pointer',
              borderRadius: 6, padding: '7px 18px',
              fontFamily: "'Oxanium', sans-serif", fontSize: 12, fontWeight: 600,
            }}
          >
            {isCreating ? 'Creating…' : 'Create Note'}
          </button>
        </div>
      </div>
    </div>
  )
}

const labelStyle: React.CSSProperties = {
  fontFamily: "'JetBrains Mono', monospace",
  fontSize: 9, color: '#4b5563',
  textTransform: 'uppercase', letterSpacing: '0.06em',
  marginBottom: 4,
}

const inputStyle: React.CSSProperties = {
  display: 'block', width: '100%',
  background: 'rgba(15,23,42,0.8)',
  border: '1px solid #1e293b',
  borderRadius: 5, color: '#e8e8f0',
  fontFamily: "'Syne', sans-serif", fontSize: 13,
  padding: '8px 10px',
  outline: 'none', boxSizing: 'border-box',
}

const cancelBtnStyle: React.CSSProperties = {
  background: 'rgba(30,41,59,0.8)', border: '1px solid #334155',
  color: '#9ca3af', cursor: 'pointer', borderRadius: 6,
  padding: '7px 18px',
  fontFamily: "'JetBrains Mono', monospace", fontSize: 12,
}
