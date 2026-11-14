'use client'

import { useCallback, useState } from 'react'

export interface PendingDelete {
  stem: string
  path: string
}

export interface UseConfirmDeleteResult {
  /** Target of the open confirmation dialog, or null when none is pending. */
  pendingDelete: PendingDelete | null
  isDeleting: boolean
  deleteError: string | null
  /**
   * Open the confirmation dialog for (stem, path). Clears any error left by
   * a previous failed attempt (both former call sites did this at request
   * time).
   */
  requestDelete: (stem: string, path: string) => void
  /** Close the dialog without deleting. */
  cancelDelete: () => void
  /**
   * Run the pending delete. Closes the dialog on success; on failure keeps
   * it open with `deleteError` set so the caller can render the message.
   */
  handleConfirmDelete: () => Promise<void>
}

/**
 * Shared note-delete confirmation flow (ARC-210) — state, guard, and the
 * confirm/cancel/error handling previously duplicated across ReadingPane
 * and SidebarPanel. Callers keep their own ConfirmDialog rendering.
 *
 * The delete target is captured at request time, not at confirm time:
 * ConfirmDialog is a modal (fixed full-screen backdrop + focus trap), so no
 * interaction can retarget the note while the dialog is open — the only way
 * the underlying note object changes is a background graph reload of the
 * same note, which carries the same stem/path.
 */
export function useConfirmDelete(onDelete: (stem: string, path: string) => Promise<void> | void): UseConfirmDeleteResult {
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const requestDelete = useCallback((stem: string, path: string) => {
    setDeleteError(null)
    setPendingDelete({ stem, path })
  }, [])

  const cancelDelete = useCallback(() => {
    setPendingDelete(null)
  }, [])

  const handleConfirmDelete = useCallback(async () => {
    if (!pendingDelete) return
    setIsDeleting(true)
    setDeleteError(null)
    try {
      await onDelete(pendingDelete.stem, pendingDelete.path)
      setPendingDelete(null)
    } catch (e) {
      setDeleteError((e as Error).message)
    } finally {
      setIsDeleting(false)
    }
  }, [pendingDelete, onDelete])

  return { pendingDelete, isDeleting, deleteError, requestDelete, cancelDelete, handleConfirmDelete }
}
