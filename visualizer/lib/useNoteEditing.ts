'use client'

import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from 'react'
import { parseFrontmatter, serializeFrontmatter } from '@/lib/frontmatter'
import type { FrontmatterFields } from '@/lib/frontmatter'

/** Shape of the vault API's save response: a conflict payload or an ok mtime. */
export type SaveNoteFn = (
  stem: string,
  content: string,
  baseMtimeMs?: number,
  notePath?: string,
) => Promise<
  | { conflict: true; serverContent: string; mtimeMs: number }
  | { ok: true; mtimeMs: number }
>

interface UseNoteEditingOptions {
  /** Selected note (fetch stem + optional path); null disables saving. */
  node: { id: string; path?: string } | null
  /** The currently loaded markdown, parsed into fields + body on edit start. */
  content: string
  /** Content/mtime/modified accessors from useNoteContent, updated on a successful save. */
  setContent: Dispatch<SetStateAction<string>>
  setBaseMtime: Dispatch<SetStateAction<number | undefined>>
  setExternallyModified: Dispatch<SetStateAction<boolean>>
  /** mtime of the note as currently loaded; conflict-detection token for saves. */
  baseMtime: number | undefined
  /** The vault API's save call. */
  onSave: SaveNoteFn
  /** Whether this pane is actually visible — gates the ⌘E shortcut. */
  visible: boolean
  /** Owner-owned editing flag (useNoteContent's refresh gating reads it). */
  isEditing: boolean
  setIsEditing: Dispatch<SetStateAction<boolean>>
}

export interface UseNoteEditingResult {
  editFields: FrontmatterFields | null
  setEditFields: Dispatch<SetStateAction<FrontmatterFields | null>>
  editBody: string
  setEditBody: Dispatch<SetStateAction<string>>
  isSaving: boolean
  saveError: string | null
  previewMode: boolean
  setPreviewMode: Dispatch<SetStateAction<boolean>>
  conflictData: { serverContent: string; mtimeMs: number } | null
  setConflictData: Dispatch<SetStateAction<{ serverContent: string; mtimeMs: number } | null>>
  handleStartEdit: () => void
  handleCancelEdit: () => void
  handleSave: () => Promise<void>
  handleConflictResolve: (resolved: string) => Promise<void>
}

/**
 * Edit/save state machine for the reading pane (ARC-204 pattern, sibling of
 * useNoteContent): edit-mode field/body state, the ⌘E shortcut, and the two
 * save paths — a plain save and a conflict resolution retry. Both paths share
 * ``runSave``: identical conflict/ok/error handling, differing only in the
 * content and base mtime handed to the API. The owner keeps ``isEditing``
 * itself because useNoteContent's refresh gating reads it.
 */
export function useNoteEditing({
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
}: UseNoteEditingOptions): UseNoteEditingResult {
  const [editFields, setEditFields] = useState<FrontmatterFields | null>(null)
  const [editBody, setEditBody] = useState<string>('')
  const [isSaving, setIsSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [previewMode, setPreviewMode] = useState(false)
  const [conflictData, setConflictData] = useState<{ serverContent: string; mtimeMs: number } | null>(null)

  const handleStartEdit = useCallback(() => {
    if (!content) return
    const { fields, body } = parseFrontmatter(content)
    setEditFields(fields)
    setEditBody(body)
    setSaveError(null)
    setIsEditing(true)
  }, [content, setIsEditing])

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

  const handleCancelEdit = useCallback(() => {
    setIsEditing(false)
    setPreviewMode(false)
    setSaveError(null)
    setExternallyModified(false)
  }, [setIsEditing, setExternallyModified])

  // Shared body of both save paths: attempt the API save, surface a conflict
  // instead of overwriting, and on success sync content/mtime and exit edit
  // mode. ``base`` is the conflict-detection token for THIS attempt.
  const runSave = useCallback(async (full: string, base: number | undefined) => {
    if (!node) return
    setIsSaving(true)
    setSaveError(null)
    try {
      const result = await onSave(node.id, full, base, node.path)
      if ('conflict' in result && result.conflict) {
        setConflictData({ serverContent: result.serverContent, mtimeMs: result.mtimeMs })
        return
      }
      setContent(full)
      setBaseMtime(result.mtimeMs)
      setIsEditing(false)
      setPreviewMode(false)
      setExternallyModified(false)
    } catch (e) {
      setSaveError((e as Error).message)
    } finally {
      setIsSaving(false)
    }
  }, [node, onSave, setContent, setBaseMtime, setIsEditing, setExternallyModified])

  const handleSave = useCallback(async () => {
    if (!node || !editFields) return
    const fullContent = serializeFrontmatter(editFields, editBody)
    await runSave(fullContent, baseMtime)
  }, [node, editFields, editBody, baseMtime, runSave])

  const handleConflictResolve = useCallback(async (resolved: string) => {
    if (!node || !conflictData) return
    // Base the retry on the mtime seen at conflict time — the file the user just
    // reviewed — so the overwrite succeeds unless it changed again in the meantime.
    const retryBaseMtime = conflictData.mtimeMs
    setConflictData(null)
    await runSave(resolved, retryBaseMtime)
  }, [node, conflictData, runSave])

  return {
    editFields,
    setEditFields,
    editBody,
    setEditBody,
    isSaving,
    saveError,
    previewMode,
    setPreviewMode,
    conflictData,
    setConflictData,
    handleStartEdit,
    handleCancelEdit,
    handleSave,
    handleConflictResolve,
  }
}