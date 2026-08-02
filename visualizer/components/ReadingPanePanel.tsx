'use client'

// ARC-008: Reading pane extracted out of `app/page.tsx::Home` as the third
// sibling container (after GraphPanel and SidebarPanel), completing the
// container triad the audit named. Owns the reading-pane JSX wrapper and the
// `linkedStems` memo (a reading-pane-only derivation over graphData.edges).
//
// Mirrors the prop-drilled pattern of its siblings: `Home` keeps the shared
// state — `activeNode` (also consumed by the sidebar's active-path highlight),
// `graphData`, and `noteRefreshTrigger` (bumped by `Home`'s `useVaultFiles`
// `onNoteModified` handler when the active note changes on disk). The audit
// floated lifting `noteRefreshTrigger`/`useVaultFiles` into a context/provider;
// that was judged unnecessary here because `noteRefreshTrigger` is a single
// integer that threads cleanly as a prop, consistent with how GraphPanel and
// SidebarPanel receive their data. Behaviour is identical to the prior inline
// JSX: same props wired to the same <ReadingPane>, panel stays mounted (only
// visibility-toggled) so editor state survives switching to graph view.

import { useMemo } from 'react'
import { ReadingPane } from '@/components/ReadingPane'
import { computeLinkedStems } from '@/lib/linkedNotes'
import type { GraphData, NoteNode } from '@/lib/graph'
import type { useVisualizerState } from '@/lib/useVisualizerState'

type VisualizerState = ReturnType<typeof useVisualizerState>

export interface ReadingPanePanelProps {
  /** Active note node, synthesized from graph.json or the vault file tree. */
  activeNode: NoteNode | null
  graphData: GraphData
  /** Bumped by Home when the active note is modified externally (forces a refetch). */
  refreshTrigger: number
  /** Whether read mode is active (panel stays mounted but hidden otherwise). */
  visible: boolean
  fetchContent: VisualizerState['fetchNoteContent']
  onNavigate: (stem: string, newTab: boolean) => void
  onSave: VisualizerState['saveNote']
  onDelete: (stem: string, path?: string) => Promise<void>
  onOpenHistory: VisualizerState['openHistory']
}

export function ReadingPanePanel({
  activeNode,
  graphData,
  refreshTrigger,
  visible,
  fetchContent,
  onNavigate,
  onSave,
  onDelete,
  onOpenHistory,
}: ReadingPanePanelProps) {
  const linkedStems = useMemo(
    () => (activeNode ? computeLinkedStems(graphData.edges, activeNode.id) : []),
    [graphData, activeNode],
  )

  return (
    <div style={{
      flex: 1,
      display: visible ? 'flex' : 'none',
      flexDirection: 'column', minWidth: 0, minHeight: 0, overflow: 'hidden',
    }}>
      <ReadingPane
        node={activeNode}
        fetchContent={fetchContent}
        onNavigate={onNavigate}
        onSave={onSave}
        onDelete={onDelete}
        onOpenHistory={onOpenHistory}
        nodes={graphData.nodes}
        refreshTrigger={refreshTrigger}
        visible={visible}
        linkedStems={linkedStems}
      />
    </div>
  )
}
