'use client'

import { useState, useCallback, useTransition, useRef } from 'react'
import type { NoteNode } from '@/lib/graph'
import { useConfirmDelete } from '@/lib/useConfirmDelete'
import { useNoteContent } from '@/lib/useNoteContent'
import { useNoteEditing } from '@/lib/useNoteEditing'
import { NoteEditor } from './reading-pane/NoteEditor'
import { ReadingPaneEmptyState } from './reading-pane/ReadingPaneEmptyState'
import { NoteHeader } from './reading-pane/NoteHeader'
import { NoteBody } from './reading-pane/NoteBody'
import { ConfirmDeleteDialog } from './reading-pane/ConfirmDeleteDialog'
import { deriveNoteView } from './reading-pane/noteView'

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
  const [isPending, startTransition] = useTransition()
  const [isEditing, setIsEditing] = useState(false)
  const {
    pendingDelete,
    isDeleting,
    deleteError,
    requestDelete,
    cancelDelete,
    handleConfirmDelete,
  } = useConfirmDelete(onDelete)
  const scrollContainerRef = useRef<HTMLDivElement>(null)

  // Stable note identifier (path, falling back to id) — a background graph reload
  // (e.g. after the summarizer runs) produces a new `node` object for the same note,
  // which must not re-trigger the content fetch or discard in-progress edits.
  const noteKey = node ? (node.path || node.id) : null

  const {
    content,
    setContent,
    error,
    baseMtime,
    setBaseMtime,
    externallyModified,
    setExternallyModified,
  } = useNoteContent({
    noteKey,
    noteId: node?.id ?? null,
    notePath: node?.path,
    fetchContent,
    startTransition,
    refreshTrigger,
    isEditing,
    onNoteChange: () => setIsEditing(false),
    scrollRef: scrollContainerRef,
  })

  const {
    editFields, setEditFields, editBody, setEditBody, isSaving, saveError,
    previewMode, setPreviewMode, conflictData, setConflictData,
    handleStartEdit, handleCancelEdit, handleSave, handleConflictResolve,
  } = useNoteEditing({
    node,
    content,
    setContent,
    setBaseMtime,
    setExternallyModified,
    baseMtime,
    onSave,
    visible,
    isEditing,
    setIsEditing,
  })

  const handleWikilink = useCallback((stem: string, e: React.MouseEvent) => {
    onNavigate(stem, e.metaKey || e.ctrlKey)
  }, [onNavigate])

  if (!node) {
    return <ReadingPaneEmptyState />
  }

  // Frontmatter/related/linked derivations live in a pure helper so the
  // component function itself stays shallow (parsight complexity).
  const view = deriveNoteView(content, linkedStems, node.id)

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
        <NoteHeader
          node={node}
          noteDate={view.noteDate}
          confidence={view.confidence}
          isPending={isPending}
          error={error}
          isEditing={isEditing}
          onStartEdit={handleStartEdit}
          onRequestDelete={() => requestDelete(node.id, node.path)}
          onOpenHistory={onOpenHistory}
        />

        <NoteBody
          node={node}
          isPending={isPending}
          error={error}
          relatedStems={view.relatedStems}
          linkedOnly={view.linkedOnly}
          displayContent={view.displayContent}
          onWikilink={handleWikilink}
          deleteError={deleteError}
        />
      </div>

      <ConfirmDeleteDialog
        active={!!pendingDelete}
        noteTitle={node.title}
        isDeleting={isDeleting}
        onConfirm={handleConfirmDelete}
        onCancel={cancelDelete}
      />
    </div>
  )
}
