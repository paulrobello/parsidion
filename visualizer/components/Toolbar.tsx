'use client'

import { useEffect } from 'react'
import type { NoteNode } from '@/lib/graph'
import { TabBar } from './TabBar'
import { UnifiedSearch } from './UnifiedSearch'
import { ViewToggle } from './ViewToggle'

interface Props {
  onToggleSidebar: () => void
  tabs: string[]
  activeTab: string | null
  nodeMap: Map<string, NoteNode>
  onSwitchTab: (stem: string) => void
  onCloseTab: (stem: string) => void
  nodes: NoteNode[]
  onSearchSelect: (stem: string, newTab: boolean) => void
  viewMode: 'read' | 'graph'
  onViewModeChange: (mode: 'read' | 'graph') => void
}

export function Toolbar({
  onToggleSidebar,
  tabs, activeTab, nodeMap, onSwitchTab, onCloseTab,
  nodes, onSearchSelect,
  viewMode, onViewModeChange,
}: Props) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'b') {
        e.preventDefault()
        onToggleSidebar()
      }
      if ((e.metaKey || e.ctrlKey) && e.key === '\\') {
        e.preventDefault()
        onViewModeChange(viewMode === 'read' ? 'graph' : 'read')
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onToggleSidebar, viewMode, onViewModeChange])

  return (
    <div style={{
      background: '#0a0f1e',
      padding: '6px 12px',
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      borderBottom: '1px solid #1e293b',
      flexShrink: 0,
      height: 'var(--toolbar-height, 42px)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 0 }}>
        <button
          onClick={onToggleSidebar}
          style={{
            background: 'none', border: 'none',
            color: '#6b7a99', cursor: 'pointer',
            fontSize: 14, padding: '2px 4px',
            flexShrink: 0,
          }}
          title="Toggle sidebar (⌘B)"
        >
          ☰
        </button>
        <TabBar
          tabs={tabs}
          activeTab={activeTab}
          nodeMap={nodeMap}
          onSwitch={onSwitchTab}
          onClose={onCloseTab}
        />
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
        <UnifiedSearch nodes={nodes} onSelect={onSearchSelect} />
        <ViewToggle mode={viewMode} onToggle={onViewModeChange} />
      </div>
    </div>
  )
}
