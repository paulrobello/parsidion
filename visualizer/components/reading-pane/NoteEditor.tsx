'use client'

import { ConflictDialog } from '../ConflictDialog'
import { FrontmatterEditor } from '../FrontmatterEditor'
import { NoteMarkdown } from './NoteMarkdown'
import { serializeFrontmatter } from '@/lib/frontmatter'
import type { FrontmatterFields } from '@/lib/frontmatter'
import type { NoteNode } from '@/lib/graph'

interface Props {
  node: NoteNode
  nodes: NoteNode[]
  editFields: FrontmatterFields
  editBody: string
  previewMode: boolean
  isSaving: boolean
  saveError: string | null
  externallyModified: boolean
  conflictData: { serverContent: string; mtimeMs: number } | null
  setEditFields: (fields: FrontmatterFields) => void
  setEditBody: (body: string) => void
  setPreviewMode: (mode: boolean) => void
  setConflictData: (value: { serverContent: string; mtimeMs: number } | null) => void
  handleSave: () => void
  handleCancelEdit: () => void
  handleConflictResolve: (resolved: string) => void
  onWikilink: (stem: string, e: React.MouseEvent) => void
}

export function NoteEditor({
  node, nodes,
  editFields, editBody, previewMode, isSaving, saveError, externallyModified, conflictData,
  setEditFields, setEditBody, setPreviewMode, setConflictData,
  handleSave, handleCancelEdit, handleConflictResolve, onWikilink,
}: Props) {
  const editPreviewContent = editBody
    .replace(/\[\[([^\]]+)\]\]/g, (_, s) => `[${s}](wikilink:${encodeURIComponent(s)})`)

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', padding: '16px 24px', gap: 12, overflow: 'hidden' }}>
      {/* Edit toolbar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
        <span style={{
          fontFamily: "'Oxanium', sans-serif", fontSize: 11, color: '#9ca3af',
          flex: 1, minWidth: 0,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          Editing: <span style={{ color: '#e8e8f0' }}>{node.title}</span>
        </span>
        {externallyModified && (
          <span style={{ color: '#f59e0b', fontFamily: "'JetBrains Mono', monospace", fontSize: 10, flexShrink: 0 }}>
            ⚠ modified externally — save to see conflict
          </span>
        )}
        {saveError && (
          <span style={{ color: '#ef4444', fontFamily: "'JetBrains Mono', monospace", fontSize: 11 }}>
            {saveError}
          </span>
        )}
        {/* Edit / Preview toggle */}
        <div style={{
          display: 'flex', borderRadius: 5, overflow: 'hidden',
          border: '1px solid #334155',
        }}>
          <button
            onClick={() => setPreviewMode(false)}
            style={{
              background: !previewMode ? 'rgba(123,97,255,0.2)' : 'transparent',
              border: 'none', borderRight: '1px solid #334155',
              color: !previewMode ? '#7b61ff' : '#6b7a99',
              cursor: 'pointer', padding: '3px 10px',
              fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
            }}
          >
            Edit
          </button>
          <button
            onClick={() => setPreviewMode(true)}
            style={{
              background: previewMode ? 'rgba(123,97,255,0.2)' : 'transparent',
              border: 'none',
              color: previewMode ? '#7b61ff' : '#6b7a99',
              cursor: 'pointer', padding: '3px 10px',
              fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
            }}
          >
            Preview
          </button>
        </div>
        <button
          onClick={handleCancelEdit}
          disabled={isSaving}
          style={{
            background: 'rgba(30,41,59,0.8)', border: '1px solid #334155',
            color: '#9ca3af', cursor: 'pointer', borderRadius: 5,
            padding: '4px 12px', fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
          }}
        >
          Cancel
        </button>
        <button
          onClick={handleSave}
          disabled={isSaving}
          style={{
            background: isSaving ? 'rgba(0,255,200,0.1)' : 'rgba(0,255,200,0.15)',
            border: '1px solid rgba(0,255,200,0.3)',
            color: '#00FFC8', cursor: isSaving ? 'default' : 'pointer', borderRadius: 5,
            padding: '4px 12px', fontFamily: "'Oxanium', sans-serif", fontSize: 11, fontWeight: 600,
          }}
        >
          {isSaving ? 'Saving…' : 'Save'}
        </button>
      </div>

      {/* Frontmatter editor */}
      <FrontmatterEditor
        fields={editFields}
        onChange={setEditFields}
        nodes={nodes}
      />

      {/* Body editor or preview */}
      {previewMode ? (
        <div style={{
          flex: 1, overflow: 'auto',
          background: '#0a0f1e',
          border: '1px solid #1e293b',
          borderRadius: 6,
          padding: '16px 24px',
          fontFamily: "'Syne', sans-serif",
        }}>
          <NoteMarkdown content={editPreviewContent} onWikilink={onWikilink} />
        </div>
      ) : (
        <textarea
          value={editBody}
          onChange={e => setEditBody(e.target.value)}
          onKeyDown={e => {
            if ((e.metaKey || e.ctrlKey) && e.key === 's') {
              e.preventDefault()
              handleSave()
            }
            if (e.key === 'Escape') handleCancelEdit()
          }}
          spellCheck={false}
          autoFocus
          style={{
            flex: 1, resize: 'none',
            background: '#0a0f1e',
            border: '1px solid #1e293b',
            borderRadius: 6,
            color: '#e8e8f0',
            fontFamily: "'JetBrains Mono', monospace",
            fontSize: 12,
            lineHeight: 1.7,
            padding: '16px',
            outline: 'none',
          }}
        />
      )}
      {conflictData && node && (
        <ConflictDialog
          stem={node.id}
          myContent={serializeFrontmatter(editFields, editBody)}
          serverContent={conflictData.serverContent}
          onResolve={handleConflictResolve}
          onCancel={() => setConflictData(null)}
        />
      )}
    </div>
  )
}
