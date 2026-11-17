'use client'

import type { NoteNode } from '@/lib/graph'
import { NoteLinkCluster } from './NoteLinkCluster'
import { NoteMarkdown } from './NoteMarkdown'

interface Props {
  node: NoteNode
  isPending: boolean
  error: string | null
  relatedStems: string[]
  linkedOnly: string[]
  displayContent: string
  onWikilink: (stem: string, e: React.MouseEvent) => void
  deleteError: string | null
}

export function NoteBody({ node, isPending, error, relatedStems, linkedOnly, displayContent, onWikilink, deleteError }: Props) {
  return (
    <>
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
          onWikilink={onWikilink}
          variant="related"
        />
      )}

      {!isPending && !error && linkedOnly.length > 0 && (
        <NoteLinkCluster
          title={`Linked Notes (${linkedOnly.length})`}
          stems={linkedOnly}
          onWikilink={onWikilink}
          variant="linked"
        />
      )}

      {!isPending && !error && displayContent && (
        <NoteMarkdown content={displayContent} onWikilink={onWikilink} />
      )}

      {deleteError && (
        <div style={{ color: '#ef4444', fontFamily: "'JetBrains Mono', monospace", fontSize: 11, marginTop: 12 }}>
          Delete failed: {deleteError}
        </div>
      )}
    </>
  )
}
