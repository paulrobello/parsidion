'use client'

import { ConfirmDialog } from '@/components/ConfirmDialog'

interface Props {
  /** Whether a delete is awaiting confirmation (gates the whole dialog). */
  active: boolean
  /** Note title, interpolated into the warning message. */
  noteTitle: string
  isDeleting: boolean
  onConfirm: () => void
  onCancel: () => void
}

/**
 * The delete-confirmation seam: ConfirmDialog instance plus the pending
 * gate and the busy label ternary, extracted from ReadingPane's render.
 */
export function ConfirmDeleteDialog({ active, noteTitle, isDeleting, onConfirm, onCancel }: Props) {
  if (!active) return null
  return (
    <ConfirmDialog
      title="Delete note"
      message={`"${noteTitle}" will be permanently deleted from the vault. This cannot be undone.`}
      confirmLabel={isDeleting ? 'Deleting…' : 'Delete'}
      cancelLabel="Cancel"
      danger
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  )
}
