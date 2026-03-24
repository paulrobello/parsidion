'use client'

import { useState, useEffect, useCallback, useTransition } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { getNodeColor } from '@/lib/sigma-colors'
import type { NoteNode } from '@/lib/graph'
import { ConfirmDialog } from './ConfirmDialog'
import { FrontmatterEditor } from './FrontmatterEditor'
import { parseFrontmatter, serializeFrontmatter } from '@/lib/frontmatter'
import type { FrontmatterFields } from '@/lib/frontmatter'

interface Props {
  node: NoteNode | null
  fetchContent: (stem: string) => Promise<string>
  onNavigate: (stem: string, newTab: boolean) => void
  onSave: (stem: string, content: string) => Promise<void>
  onDelete: (stem: string) => Promise<void>
  onOpenHistory: (stem: string) => void
  nodes: NoteNode[]
}

export function ReadingPane({ node, fetchContent, onNavigate, onSave, onDelete, onOpenHistory, nodes }: Props) {
  const [content, setContent] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [isPending, startTransition] = useTransition()
  const [isEditing, setIsEditing] = useState(false)
  const [editFields, setEditFields] = useState<FrontmatterFields | null>(null)
  const [editBody, setEditBody] = useState<string>('')
  const [isSaving, setIsSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [previewMode, setPreviewMode] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  useEffect(() => {
    if (!node) return
    setIsEditing(false)
    let cancelled = false
    startTransition(async () => {
      try {
        const c = await fetchContent(node.id)
        if (!cancelled) { setContent(c); setError(null) }
      } catch (e) {
        if (!cancelled) setError((e as Error).message)
      }
    })
    return () => { cancelled = true }
  }, [node, fetchContent])

  const handleStartEdit = useCallback(() => {
    if (!content) return
    const { fields, body } = parseFrontmatter(content)
    setEditFields(fields)
    setEditBody(body)
    setSaveError(null)
    setIsEditing(true)
  }, [content])

  // ⌘E to enter edit mode
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'e' && !isEditing) {
        e.preventDefault()
        handleStartEdit()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [isEditing, handleStartEdit])

  const handleCancelEdit = useCallback(() => {
    setIsEditing(false)
    setPreviewMode(false)
    setSaveError(null)
  }, [])

  const handleSave = useCallback(async () => {
    if (!node || !editFields) return
    setIsSaving(true)
    setSaveError(null)
    try {
      const fullContent = serializeFrontmatter(editFields, editBody)
      await onSave(node.id, fullContent)
      setContent(fullContent)
      setIsEditing(false)
      setPreviewMode(false)
    } catch (e) {
      setSaveError((e as Error).message)
    } finally {
      setIsSaving(false)
    }
  }, [node, editFields, editBody, onSave])

  const handleConfirmDelete = useCallback(async () => {
    if (!node) return
    setIsDeleting(true)
    setDeleteError(null)
    try {
      await onDelete(node.id)
      setShowDeleteConfirm(false)
    } catch (e) {
      setDeleteError((e as Error).message)
      setIsDeleting(false)
    }
  }, [node, onDelete])

  const handleWikilink = useCallback((stem: string, e: React.MouseEvent) => {
    onNavigate(stem, e.metaKey || e.ctrlKey)
  }, [onNavigate])

  if (!node) {
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

  const fm = content.match(/^---\n([\s\S]*?)\n---/)
  const fmBody = fm?.[1] ?? ''
  const noteDate = fmBody.match(/^date:\s*(.+)$/m)?.[1]?.trim() ?? null
  const confidence = fmBody.match(/^confidence:\s*(.+)$/m)?.[1]?.trim() ?? null
  const relatedStems: string[] = (() => {
    const relatedLine = fmBody.match(/^related:\s*(.+)$/m)
    if (!relatedLine) return []
    const stems: string[] = []
    const re = /\[\[([^\]]+)\]\]/g
    let m: RegExpExecArray | null
    while ((m = re.exec(relatedLine[1])) !== null) stems.push(m[1])
    return [...new Set(stems)]
  })()

  const displayContent = content
    .replace(/^---[\s\S]*?---\n/, '')
    .replace(/\[\[([^\]]+)\]\]/g, (_, stem) => `[${stem}](wikilink:${encodeURIComponent(stem)})`)

  if (isEditing && editFields) {
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
            <div className="note-markdown">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  a: ({ href, children }: any) => {
                    if (href?.startsWith('wikilink:')) {
                      const s = decodeURIComponent(href.slice(9))
                      return (
                        <span className="wikilink" onClick={(e: React.MouseEvent) => handleWikilink(s, e)}>
                          {children}
                        </span>
                      )
                    }
                    return <a href={href} target="_blank" rel="noreferrer">{children}</a>
                  },
                }}
              >
                {editPreviewContent}
              </ReactMarkdown>
            </div>
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
      </div>
    )
  }

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: '32px 48px', fontFamily: "'Syne', sans-serif" }}>
      <div style={{ maxWidth: 720, margin: '0 auto' }}>
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
                onClick={handleStartEdit}
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
                onClick={() => { setDeleteError(null); setShowDeleteConfirm(true) }}
                title="Delete note"
                style={{
                  background: 'none', border: '1px solid #1e293b',
                  color: '#6b7a99', cursor: 'pointer', borderRadius: 5,
                  padding: '2px 8px', fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
                }}
              >
                Delete
              </button>
              {!isEditing && node && (
                <button
                  onClick={() => onOpenHistory(node.id)}
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

        {isPending && (
          <div style={{ color: '#6b7a99', fontFamily: "'JetBrains Mono', monospace", fontSize: 12, paddingTop: 20 }}>
            Loading...
          </div>
        )}
        {error && (
          <div style={{ color: '#ef4444', fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }}>
            Could not load note: {node.id}
          </div>
        )}

        {!isPending && !error && relatedStems.length > 0 && (
          <div style={{
            marginBottom: 16, padding: '8px 12px',
            background: 'rgba(123,97,255,0.06)',
            border: '1px solid rgba(123,97,255,0.15)',
            borderRadius: 6,
          }}>
            <div style={{
              fontSize: 9, fontFamily: "'Oxanium', sans-serif", color: '#6b7a99',
              textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6,
            }}>Related</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {relatedStems.map(stem => (
                <span
                  key={stem}
                  className="wikilink"
                  onClick={(e) => handleWikilink(stem, e)}
                  style={{ fontSize: 12 }}
                >
                  {stem}
                </span>
              ))}
            </div>
          </div>
        )}

        {!isPending && !error && displayContent && (
          <div className="note-markdown">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                a: ({ href, children }: any) => {
                  if (href?.startsWith('wikilink:')) {
                    const stem = decodeURIComponent(href.slice(9))
                    return (
                      <span
                        className="wikilink"
                        onClick={(e: React.MouseEvent) => handleWikilink(stem, e)}
                      >
                        {children}
                      </span>
                    )
                  }
                  return <a href={href} target="_blank" rel="noreferrer">{children}</a>
                },
              }}
            >
              {displayContent}
            </ReactMarkdown>
          </div>
        )}

        {deleteError && (
          <div style={{ color: '#ef4444', fontFamily: "'JetBrains Mono', monospace", fontSize: 11, marginTop: 12 }}>
            Delete failed: {deleteError}
          </div>
        )}
      </div>

      {showDeleteConfirm && (
        <ConfirmDialog
          title="Delete note"
          message={`"${node.title}" will be permanently deleted from the vault. This cannot be undone.`}
          confirmLabel={isDeleting ? 'Deleting…' : 'Delete'}
          cancelLabel="Cancel"
          danger
          onConfirm={handleConfirmDelete}
          onCancel={() => setShowDeleteConfirm(false)}
        />
      )}
    </div>
  )
}
