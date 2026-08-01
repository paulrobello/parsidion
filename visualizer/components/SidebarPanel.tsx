'use client'

// ARC-008: Sidebar container extracted out of `app/page.tsx::Home`.
// Owns the sidebar-initiated delete confirmation flow (pendingDelete /
// isSidebarDeleting / sidebarDeleteError) that previously lived as three
// useState calls plus two callbacks in `Home`. The destructive action
// itself (`onDelete` -> state.deleteNote + state.closeTab) stays in `Home`
// because the reading pane's delete uses the same path; the confirmation
// dialog state is purely a sidebar concern and moves here.
//
// The `onSelectNote` callback also stays in `Home` (it writes
// `selectedVaultPath`, which feeds the `activeNode` memo used by both the
// reading pane and the graph), so this panel is presentational for
// selection and owns only the delete-confirm state.

import { useState, useCallback } from 'react'
import { FileExplorer } from '@/components/FileExplorer'
import { ConfirmDialog } from '@/components/ConfirmDialog'
import type { VaultFile } from '@/lib/vaultFile'

export interface SidebarPanelProps {
  fileTree: Map<string, Map<string, VaultFile[]>>
  activeTab: string | null
  /** Vault-relative path of the active note — used for highlighting when stems collide. */
  activePath: string | null
  totalNotes: number
  width: number
  onWidthChange: (w: number) => void
  collapsed: boolean
  onSelectNote: (stem: string, newTab: boolean, path?: string) => void
  onOpenHistory: (stem: string, notePath?: string) => void
  /** Destructive delete — confirms before invoking. */
  onDelete: (stem: string, path?: string) => Promise<void> | void
}

export function SidebarPanel({
  fileTree,
  activeTab,
  activePath,
  totalNotes,
  width,
  onWidthChange,
  collapsed,
  onSelectNote,
  onOpenHistory,
  onDelete,
}: SidebarPanelProps) {
  const [pendingDelete, setPendingDelete] = useState<{ stem: string; path: string } | null>(null)
  const [isSidebarDeleting, setIsSidebarDeleting] = useState(false)
  const [sidebarDeleteError, setSidebarDeleteError] = useState<string | null>(null)

  const handleRequestDelete = useCallback((stem: string, path: string) => {
    setSidebarDeleteError(null)
    setPendingDelete({ stem, path })
  }, [])

  const handleConfirmDelete = useCallback(async () => {
    if (!pendingDelete) return
    setIsSidebarDeleting(true)
    setSidebarDeleteError(null)
    try {
      await onDelete(pendingDelete.stem, pendingDelete.path)
      setPendingDelete(null)
    } catch (e) {
      setSidebarDeleteError((e as Error).message)
    } finally {
      setIsSidebarDeleting(false)
    }
  }, [pendingDelete, onDelete])

  return (
    <>
      <FileExplorer
        fileTree={fileTree}
        activeTab={activeTab}
        activePath={activePath}
        onSelectNote={onSelectNote}
        width={width}
        onWidthChange={onWidthChange}
        collapsed={collapsed}
        totalNotes={totalNotes}
        onOpenHistory={onOpenHistory}
        onDeleteNote={handleRequestDelete}
      />

      {pendingDelete && (
        <ConfirmDialog
          title="Delete note"
          message={`"${pendingDelete.stem}" will be permanently deleted from the vault. This cannot be undone.${sidebarDeleteError ? ` Error: ${sidebarDeleteError}` : ''}`}
          confirmLabel={isSidebarDeleting ? 'Deleting…' : 'Delete'}
          cancelLabel="Cancel"
          danger
          onConfirm={handleConfirmDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </>
  )
}
