'use client'

import { useRef, useEffect } from 'react'
import type { NoteNode } from '@/lib/graph'
import { getNodeColor } from '@/lib/sigma-colors'

interface Props {
  tabs: string[]
  activeTab: string | null
  nodeMap: Map<string, NoteNode>
  onSwitch: (stem: string) => void
  onClose: (stem: string) => void
}

export function TabBar({ tabs, activeTab, nodeMap, onSwitch, onClose }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!scrollRef.current || !activeTab) return
    const el = scrollRef.current.querySelector(`[data-tab="${activeTab}"]`) as HTMLElement
    el?.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' })
  }, [activeTab])

  if (tabs.length === 0) return null

  return (
    <div
      ref={scrollRef}
      style={{
        display: 'flex', gap: 1, overflowX: 'auto', overflowY: 'hidden',
        flex: 1, minWidth: 0, scrollbarWidth: 'none',
      }}
    >
      {tabs.map(stem => {
        const node = nodeMap.get(stem)
        const isActive = stem === activeTab
        return (
          <div
            key={stem}
            data-tab={stem}
            onClick={() => onSwitch(stem)}
            style={{
              background: isActive ? '#111827' : 'transparent',
              padding: '6px 14px',
              borderRadius: '6px 6px 0 0',
              color: isActive ? '#e8e8f0' : '#5a6478',
              fontSize: 11,
              fontFamily: "'JetBrains Mono', monospace",
              cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 7,
              border: isActive ? '1px solid #1e293b' : '1px solid transparent',
              borderBottom: isActive ? '1px solid #111827' : '1px solid transparent',
              whiteSpace: 'nowrap',
              flexShrink: 0,
              maxWidth: 220,
              overflow: 'hidden',
              transition: 'color 0.15s, background 0.15s',
            }
            }
            onMouseEnter={e => { if (!isActive) e.currentTarget.style.color = '#9ca3af' }}
            onMouseLeave={e => { if (!isActive) e.currentTarget.style.color = '#5a6478' }}
          >
            <span style={{ color: getNodeColor(node?.type ?? ''), fontSize: 7 }}>●</span>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {node?.title ?? stem}
            </span>
            <span
              onClick={(e) => { e.stopPropagation(); onClose(stem) }}
              style={{
                color: '#4b5563', fontSize: 9, cursor: 'pointer',
                padding: '0 2px', marginLeft: 2,
                borderRadius: 2,
              }}
              onMouseEnter={e => (e.currentTarget.style.color = '#e8e8f0')}
              onMouseLeave={e => (e.currentTarget.style.color = '#4b5563')}
            >
              ✕
            </span>
          </div>
        )
      })}
    </div>
  )
}
