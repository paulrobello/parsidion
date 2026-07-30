'use client'

import { useState, useEffect, useCallback, useTransition, useRef } from 'react'
import { getNodeColor } from '@/lib/sigma-colors'
import type { NoteNode } from '@/lib/graph'
import { ConfirmDialog } from './ConfirmDialog'
import { parseFrontmatter, serializeFrontmatter } from '@/lib/frontmatter'
import type { FrontmatterFields } from '@/lib/frontmatter'
import { NoteEditor } from './reading-pane/NoteEditor'
import { NoteMarkdown } from './reading-pane/NoteMarkdown'
import { NoteLinkCluster } from './reading-pane/NoteLinkCluster'
import { ReadingPaneEmptyState } from './reading-pane/ReadingPaneEmptyState'

interface Props {
  node: NoteNode | null
  fetchContent: (stem: string, path?: string) => Promise<{ content: string; mtimeMs?: number; fromCache: boolean }>
  onNavigate: (stem: string, newTab: boolean) => void
  onSave: (stem: string, content: string, baseMtimeMs?: number, notePath?: string) => Promise<{ conflict: true; serverContent: string; mtimeMs: number } | { ok: true; mtimeMs: number }>
  onDelete: (stem: string, notePath?: string) => Promise<void>
  onOpenHistory: (stem: string, notePath?: string) => void
  nodes: NoteNode[]
  refreshTrigger?: number
  /** Whether this pane is currently visible (not hidden behind graph view) — gates the ⌘E shortcut. */
  visible?: boolean
  /** Stems wiki-linked to this note (undirected; from graph.json edges). */
  linkedStems?: string[]
}

export function ReadingPane({ node, fetchContent, onNavigate, onSave, onDelete, onOpenHistory, nodes, refreshTrigger = 0, visible = true, linkedStems = [] }: Props) {
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
  // Conflict-detection token: the server's mtimeMs for the note as currently loaded.
  const [baseMtime, setBaseMtime] = useState<number | undefined>(undefined)
  const [conflictData, setConflictData] = useState<{ serverContent: string; mtimeMs: number } | null>(null)
  const [externallyModified, setExternallyModified] = useState(false)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const savedScrollRef = useRef(0)

  // Stable note identifier (path, falling back to id) — a background graph reload
  // (e.g. after the summarizer runs) produces a new `node` object for the same note,
  // which must not re-trigger this effect or discard in-progress edits.
  const noteKey = node ? (node.path || node.id) : null

  useEffect(() => {
    if (!node) return
    setIsEditing(false)
    let cancelled = false
    startTransition(async () => {
      try {
        const { content: c, mtimeMs } = await fetchContent(node.id, node.path)
        if (!cancelled) {
          setContent(c)
          setBaseMtime(mtimeMs)
          setError(null)
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message)
      }
    })
    return () => { cancelled = true }
  // Keyed on noteKey (not the `node` object) so a same-note reload with a new object
  // identity doesn't re-run this effect and reset isEditing / refetch content.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [noteKey, fetchContent])

  const handleStartEdit = useCallback(() => {
    if (!content) return
    const { fields, body } = parseFrontmatter(content)
    setEditFields(fields)
    setEditBody(body)
    setSaveError(null)
    setIsEditing(true)
  }, [content])

  // ⌘E to enter edit mode — only while this pane is actually visible
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!visible) return
      if ((e.metaKey || e.ctrlKey) && e.key === 'e' && !isEditing) {
        e.preventDefault()
        handleStartEdit()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [isEditing, handleStartEdit, visible])

  useEffect(() => {
    if (refreshTrigger === 0 || !node) return
    if (isEditing) {
      setExternallyModified(true)
      return
    }
    savedScrollRef.current = scrollContainerRef.current?.scrollTop ?? 0
    let cancelled = false
    startTransition(async () => {
      try {
        const { content: c, mtimeMs } = await fetchContent(node.id, node.path)
        if (!cancelled) {
          setContent(c)
          setBaseMtime(mtimeMs)
          setError(null)
        }
      } catch { /* ignore refresh errors */ }
    })
    return () => { cancelled = true }
  // QA-017: Only re-fetches when refreshTrigger changes (a counter incremented
  // by the WebSocket file-watcher).  Including `stem`, `vault`, or `setContent`
  // would cause re-fetches when switching tabs, which is handled by the initial
  // load effect above this one.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshTrigger])

  useEffect(() => {
    if (savedScrollRef.current > 0 && scrollContainerRef.current) {
      scrollContainerRef.current.scrollTop = savedScrollRef.current
      savedScrollRef.current = 0
    }
  }, [content])

  const handleCancelEdit = useCallback(() => {
    setIsEditing(false)
    setPreviewMode(false)
    setSaveError(null)
    setExternallyModified(false)
  }, [])

  const handleSave = useCallback(async () => {
    if (!node || !editFields) return
    setIsSaving(true)
    setSaveError(null)
    try {
      const fullContent = serializeFrontmatter(editFields, editBody)
      const result = await onSave(node.id, fullContent, baseMtime, node.path)
      if ('conflict' in result && result.conflict) {
        setConflictData({ serverContent: result.serverContent, mtimeMs: result.mtimeMs })
        return
      }
      setContent(fullContent)
      setBaseMtime(result.mtimeMs)
      setIsEditing(false)
      setPreviewMode(false)
      setExternallyModified(false)
    } catch (e) {
      setSaveError((e as Error).message)
    } finally {
      setIsSaving(false)
    }
  }, [node, editFields, editBody, onSave, baseMtime])

  const handleConfirmDelete = useCallback(async () => {
    if (!node) return
    setIsDeleting(true)
    setDeleteError(null)
    try {
      await onDelete(node.id, node.path)
      setShowDeleteConfirm(false)
    } catch (e) {
      setDeleteError((e as Error).message)
      setIsDeleting(false)
    }
  }, [node, onDelete])

  const handleConflictResolve = useCallback(async (resolved: string) => {
    if (!node || !conflictData) return
    // Base the retry on the mtime seen at conflict time — the file the user just
    // reviewed — so the overwrite succeeds unless it changed again in the meantime.
    const retryBaseMtime = conflictData.mtimeMs
    setConflictData(null)
    setIsSaving(true)
    setSaveError(null)
    try {
      const result = await onSave(node.id, resolved, retryBaseMtime, node.path)
      if ('conflict' in result && result.conflict) {
        // Modified again during resolution — surface the new conflict instead of silently overwriting.
        setConflictData({ serverContent: result.serverContent, mtimeMs: result.mtimeMs })
        return
      }
      setContent(resolved)
      setBaseMtime(result.mtimeMs)
      setIsEditing(false)
      setPreviewMode(false)
      setExternallyModified(false)
    } catch (e) {
      setSaveError((e as Error).message)
    } finally {
      setIsSaving(false)
    }
  }, [node, onSave, conflictData])

  const handleWikilink = useCallback((stem: string, e: React.MouseEvent) => {
    onNavigate(stem, e.metaKey || e.ctrlKey)
  }, [onNavigate])

  if (!node) {
    return <ReadingPaneEmptyState />
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
  // Not a useMemo: this runs after the early `if (!node)` return above, so a
  // hook here would be called conditionally, violating Rules of Hooks. It's a
  // plain derived const like relatedStems/displayContent below it.
  const relatedSet = new Set(relatedStems)
  const linkedOnly = linkedStems.filter(s => !relatedSet.has(s) && s !== node.id)

  const displayContent = content
    .replace(/^---[\s\S]*?---\n/, '')
    .replace(/\[\[([^\]]+)\]\]/g, (_, stem) => `[${stem}](wikilink:${encodeURIComponent(stem)})`)

  if (isEditing && editFields) {
    return (
      <NoteEditor
        node={node}
        nodes={nodes}
        editFields={editFields}
        editBody={editBody}
        previewMode={previewMode}
        isSaving={isSaving}
        saveError={saveError}
        externallyModified={externallyModified}
        conflictData={conflictData}
        setEditFields={setEditFields}
        setEditBody={setEditBody}
        setPreviewMode={setPreviewMode}
        setConflictData={setConflictData}
        handleSave={handleSave}
        handleCancelEdit={handleCancelEdit}
        handleConflictResolve={handleConflictResolve}
        onWikilink={handleWikilink}
      />
    )
  }

  return (
    <div ref={scrollContainerRef} style={{ flex: 1, overflow: 'auto', padding: '32px 48px', fontFamily: "'Syne', sans-serif" }}>
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
          <NoteLinkCluster
            title="Related"
            stems={relatedStems}
            onWikilink={handleWikilink}
            variant="related"
          />
        )}

        {!isPending && !error && linkedOnly.length > 0 && (
          <NoteLinkCluster
            title={`Linked Notes (${linkedOnly.length})`}
            stems={linkedOnly}
            onWikilink={handleWikilink}
            variant="linked"
          />
        )}

        {!isPending && !error && displayContent && (
          <NoteMarkdown content={displayContent} onWikilink={handleWikilink} />
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
