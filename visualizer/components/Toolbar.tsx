'use client'

import { useEffect, useState } from 'react'
import type { NoteNode } from '@/lib/graph'
import { TabBar } from './TabBar'
import { UnifiedSearch } from './UnifiedSearch'
import type { WsStatus } from '@/lib/useVaultFiles'

interface Props {
  onToggleSidebar: () => void
  tabs: string[]
  activeTab: string | null
  nodeMap: Map<string, NoteNode>
  onSwitchTab: (stem: string) => void
  onCloseTab: (stem: string) => void
  nodes: NoteNode[]
  onSearchSelect: (stem: string, newTab: boolean) => void
  graphTabActive: boolean
  onGraphTabClick: () => void
  onNewNote: () => void
  wsStatus: WsStatus
}

export function Toolbar({
  onToggleSidebar,
  tabs, activeTab, nodeMap, onSwitchTab, onCloseTab,
  nodes, onSearchSelect,
  graphTabActive, onGraphTabClick,
  onNewNote,
  wsStatus,
}: Props) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'b') {
        e.preventDefault()
        onToggleSidebar()
      }
      if ((e.metaKey || e.ctrlKey) && e.key === '\\') {
        e.preventDefault()
        onGraphTabClick()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onToggleSidebar, onGraphTabClick])

  const [wsHover, setWsHover] = useState(false)

  return (
    <div style={{
      background: 'linear-gradient(180deg, #0d1224 0%, #0a0f1e 100%)',
      padding: '0 12px',
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'center',
      borderBottom: '1px solid #1e293b',
      flexShrink: 0,
      height: 'var(--toolbar-height, 42px)',
      boxShadow: '0 1px 8px rgba(0,0,0,0.3)',
      zIndex: 5,
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
          graphTabActive={graphTabActive}
          onGraphTabClick={onGraphTabClick}
        />
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
        {/* WebSocket status indicator */}
        <div
          style={{ position: 'relative', display: 'flex', alignItems: 'center', flexShrink: 0 }}
          onMouseEnter={() => setWsHover(true)}
          onMouseLeave={() => setWsHover(false)}
        >
          <div style={{
            width: 8, height: 8, borderRadius: '50%',
            background:
              wsStatus === 'connected' ? '#10b981' :
              wsStatus === 'connecting' ? '#f59e0b' : '#ef4444',
            animation: wsStatus === 'connecting' ? 'vault-pulse 1.2s ease-in-out infinite' : 'none',
            cursor: 'default',
          }} />
          {wsHover && (
            <div style={{
              position: 'absolute', top: 'calc(100% + 8px)', right: 0,
              background: '#0d1224',
              border: '1px solid #1e293b',
              borderRadius: 5,
              padding: '5px 10px',
              whiteSpace: 'nowrap',
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 10,
              color: wsStatus === 'connected' ? '#10b981' : wsStatus === 'connecting' ? '#f59e0b' : '#ef4444',
              boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
              pointerEvents: 'none',
              zIndex: 100,
            }}>
              {wsStatus === 'connected' ? '● Vault sync connected' :
               wsStatus === 'connecting' ? '● Vault sync reconnecting…' :
               '● Vault sync disconnected'}
            </div>
          )}
        </div>
        <button
          onClick={onNewNote}
          title="New note"
          style={{
            background: 'rgba(0,255,200,0.08)',
            border: '1px solid rgba(0,255,200,0.2)',
            color: '#00FFC8', cursor: 'pointer', borderRadius: 5,
            padding: '2px 8px', fontSize: 14, lineHeight: 1,
            fontFamily: 'monospace',
          }}
        >
          +
        </button>
        <UnifiedSearch nodes={nodes} onSelect={onSearchSelect} />
      </div>
    </div>
  )
}
