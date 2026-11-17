'use client'

import { getNodeColor } from '@/lib/sigma-colors'
import type { NoteNode } from '@/lib/graph'

interface Props {
  node: NoteNode
  noteDate: string | null
  confidence: string | null
  isPending: boolean
  error: string | null
  isEditing: boolean
  onStartEdit: () => void
  onRequestDelete: () => void
  onOpenHistory: (stem: string, notePath?: string) => void
}

/**
 * Badge/date/confidence row, the Edit/Delete/HISTORY buttons, the title,
 * and the tag chips — the reading pane's header seam. Extracted from
 * ReadingPane so the toolbar's conditional renders live here instead of
 * in the parent component function.
 */
export function NoteHeader({ node, noteDate, confidence, isPending, error, isEditing, onStartEdit, onRequestDelete, onOpenHistory }: Props) {
  return (
    <>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
        <span style={{
          background: `${getNodeColor(node.type)}22`,
          border: `1px solid ${getNodeColor(node.type)}55`,
          color: getNodeColor(node.type),
          padding: '2px 8px', borderRadius: 3,
          fontSize: 10, fontFamily: "'Oxanium', sans-serif",
          fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em',
        }}>
          {node.type}
        </span>
        {noteDate && <span style={{ color: '#6b7a99', fontSize: 11 }}>{noteDate}</span>}
        {confidence && (
          <>
            <span style={{ color: '#6b7a99', fontSize: 11 }}>·</span>
            <span style={{
              color: confidence === 'high' ? '#10b981' : confidence === 'medium' ? '#f59e0b' : '#6b7a99',
              fontSize: 11,
            }}>
              {confidence} confidence
            </span>
          </>
        )}
        <span style={{ flex: 1 }} />
        {!isPending && !error && (
          <>
            <button
              onClick={onStartEdit}
              title="Edit note (⌘E)"
              style={{
                background: 'none', border: '1px solid #1e293b',
                color: '#6b7a99', cursor: 'pointer', borderRadius: 5,
                padding: '2px 8px', fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
              }}
            >
              Edit
            </button>
            <button
              onClick={onRequestDelete}
              title="Delete note"
              style={{
                background: 'none', border: '1px solid #1e293b',
                color: '#6b7a99', cursor: 'pointer', borderRadius: 5,
                padding: '2px 8px', fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
              }}
            >
              Delete
            </button>
            {!isEditing && (
              <button
                onClick={() => onOpenHistory(node.id, node.path)}
                title="Version History"
                style={{
                  background: 'none', border: '1px solid #1e293b', borderRadius: 5,
                  color: '#888', cursor: 'pointer', padding: '2px 8px', fontSize: 10,
                  fontFamily: "'JetBrains Mono', monospace",
                }}
              >
                HISTORY
              </button>
            )}
          </>
        )}
      </div>

      <h1 style={{
        fontFamily: "'Oxanium', sans-serif",
        fontSize: 24, fontWeight: 700,
        color: '#e8e8f0', lineHeight: 1.3,
        margin: '0 0 12px',
      }}>
        {node.title}
      </h1>

      <div style={{ display: 'flex', gap: 6, marginBottom: 20, flexWrap: 'wrap' }}>
        {node.tags.map(tag => (
          <span key={tag} style={{
            background: '#1e293b', color: '#9ca3af',
            padding: '2px 8px', borderRadius: 12, fontSize: 10,
          }}>
            #{tag}
          </span>
        ))}
      </div>
    </>
  )
}
