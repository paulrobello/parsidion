// components/ConflictDialog.tsx
'use client'

import { useState, useMemo } from 'react'
import { createPatch } from 'diff'
import { DiffViewer } from './DiffViewer'
import type { DiffMode } from './DiffViewer'
import { parseDiff } from '@/lib/parseDiff'

interface Props {
  stem: string
  myContent: string
  serverContent: string
  /** Called with the resolved content the user wants to save (force-save, no lastModified). */
  onResolve: (resolved: string) => void
  onCancel: () => void
}

export function ConflictDialog({ stem, myContent, serverContent, onResolve, onCancel }: Props) {
  const [view, setView] = useState<'diff' | 'merge'>('diff')
  const [diffMode, setDiffMode] = useState<DiffMode>('split')
  const [mergeText, setMergeText] = useState(myContent)

  // Compute a unified diff: server content → my content
  const unifiedDiff = createPatch(
    `${stem}.md`,
    serverContent,
    myContent,
    'Server version',
    'Your version',
  )

  // Parse into DiffHunk[] for DiffViewer
  const hunks = useMemo(() => parseDiff(unifiedDiff), [unifiedDiff])

  const overlayStyle: React.CSSProperties = {
    position: 'fixed', inset: 0,
    background: 'rgba(0,0,0,0.7)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    zIndex: 2000,
    fontFamily: "'JetBrains Mono', monospace",
  }

  const dialogStyle: React.CSSProperties = {
    background: '#0a0e1a',
    border: '1px solid #ef4444',
    borderRadius: 8,
    width: '90vw', maxWidth: 900,
    maxHeight: '80vh',
    display: 'flex', flexDirection: 'column',
    boxShadow: '0 8px 32px rgba(0,0,0,0.8)',
    overflow: 'hidden',
  }

  return (
    <div style={overlayStyle} onClick={onCancel}>
      <div style={dialogStyle} onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div style={{
          padding: '12px 16px',
          borderBottom: '1px solid #1e293b',
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <span style={{ color: '#ef4444', fontSize: 14 }}>⚠</span>
          <span style={{ color: '#e8e8f0', fontSize: 12, fontWeight: 600 }}>
            Edit conflict — {stem}.md was modified externally
          </span>
          <span style={{ flex: 1 }} />
          {/* Diff mode toggle (only visible when view='diff') */}
          {view === 'diff' && (
            <>
              <button
                onClick={() => setDiffMode('split')}
                style={{
                  background: diffMode === 'split' ? 'rgba(99,102,241,0.15)' : 'none',
                  border: '1px solid #334155', borderRadius: 4,
                  color: diffMode === 'split' ? '#818cf8' : '#6b7a99',
                  cursor: 'pointer', padding: '2px 8px', fontSize: 10,
                }}
              >
                Split
              </button>
              <button
                onClick={() => setDiffMode('unified')}
                style={{
                  background: diffMode === 'unified' ? 'rgba(99,102,241,0.15)' : 'none',
                  border: '1px solid #334155', borderRadius: 4,
                  color: diffMode === 'unified' ? '#818cf8' : '#6b7a99',
                  cursor: 'pointer', padding: '2px 8px', fontSize: 10,
                }}
              >
                Unified
              </button>
            </>
          )}
          {/* View toggle */}
          <button
            onClick={() => setView('diff')}
            style={{
              background: view === 'diff' ? 'rgba(239,68,68,0.15)' : 'none',
              border: '1px solid #334155', borderRadius: 4,
              color: view === 'diff' ? '#ef4444' : '#6b7a99',
              cursor: 'pointer', padding: '2px 8px', fontSize: 10,
            }}
          >
            Diff
          </button>
          <button
            onClick={() => { setView('merge'); setMergeText(myContent) }}
            style={{
              background: view === 'merge' ? 'rgba(239,68,68,0.15)' : 'none',
              border: '1px solid #334155', borderRadius: 4,
              color: view === 'merge' ? '#ef4444' : '#6b7a99',
              cursor: 'pointer', padding: '2px 8px', fontSize: 10,
            }}
          >
            Merge
          </button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflow: 'auto' }}>
          {view === 'diff' ? (
            <DiffViewer hunks={hunks} mode={diffMode} filename={`${stem}.md`} />
          ) : (
            <div style={{ padding: 12, display: 'flex', flexDirection: 'column', height: '100%' }}>
              <div style={{ color: '#9ca3af', fontSize: 10, marginBottom: 6 }}>
                Edit the merged result below. Your content is pre-loaded; incorporate changes from the diff view.
              </div>
              <textarea
                value={mergeText}
                onChange={e => setMergeText(e.target.value)}
                spellCheck={false}
                style={{
                  flex: 1, resize: 'none',
                  background: '#0a0f1e', border: '1px solid #334155', borderRadius: 4,
                  color: '#e8e8f0', fontFamily: "'JetBrains Mono', monospace",
                  fontSize: 12, lineHeight: 1.7, padding: 12, outline: 'none',
                  minHeight: 300,
                }}
              />
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{
          padding: '10px 16px',
          borderTop: '1px solid #1e293b',
          display: 'flex', gap: 8, justifyContent: 'flex-end',
        }}>
          <button
            onClick={onCancel}
            style={{
              background: 'none', border: '1px solid #334155', borderRadius: 5,
              color: '#6b7a99', cursor: 'pointer', padding: '4px 14px', fontSize: 11,
            }}
          >
            Cancel
          </button>
          <button
            onClick={() => onResolve(serverContent)}
            style={{
              background: 'rgba(99,102,241,0.15)', border: '1px solid rgba(99,102,241,0.3)',
              borderRadius: 5, color: '#818cf8', cursor: 'pointer',
              padding: '4px 14px', fontSize: 11,
            }}
          >
            Take theirs
          </button>
          <button
            onClick={() => onResolve(myContent)}
            style={{
              background: 'rgba(245,158,11,0.15)', border: '1px solid rgba(245,158,11,0.3)',
              borderRadius: 5, color: '#f59e0b', cursor: 'pointer',
              padding: '4px 14px', fontSize: 11,
            }}
          >
            Keep mine
          </button>
          {view === 'merge' && (
            <button
              onClick={() => onResolve(mergeText)}
              style={{
                background: 'rgba(0,255,200,0.15)', border: '1px solid rgba(0,255,200,0.3)',
                borderRadius: 5, color: '#00FFC8', cursor: 'pointer',
                padding: '4px 14px', fontSize: 11, fontWeight: 600,
              }}
            >
              Confirm Merge
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
