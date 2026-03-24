'use client'

import { useRef, useCallback, useEffect, useState } from 'react'
import { useLocalStorage } from '@/lib/useLocalStorage'
import type { VaultFile } from '@/lib/vaultFile'
import { getNodeColor } from '@/lib/sigma-colors'

interface Props {
  fileTree: Map<string, Map<string, VaultFile[]>>
  activeTab: string | null
  onSelectNote: (stem: string, newTab: boolean) => void
  onOpenHistory: (stem: string) => void
  onDeleteNote?: (stem: string) => void
  width: number
  onWidthChange: (w: number) => void
  collapsed: boolean
  totalNotes: number
}

export function FileExplorer({ fileTree, activeTab, onSelectNote, onOpenHistory, onDeleteNote, width, onWidthChange, collapsed, totalNotes }: Props) {
  const [expandedFolders, setExpandedFolders] = useLocalStorage<string[]>('vv:expandedFolders', [])
  const expandedSet = new Set(expandedFolders)
  const isDragging = useRef(false)
  const startX = useRef(0)
  const startWidth = useRef(width)
  const [contextMenu, setContextMenu] = useState<{ stem: string; x: number; y: number } | null>(null)

  const toggleFolder = useCallback((folder: string) => {
    setExpandedFolders(prev => {
      const set = new Set(prev)
      if (set.has(folder)) set.delete(folder)
      else set.add(folder)
      return [...set]
    })
  }, [setExpandedFolders])

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    isDragging.current = true
    startX.current = e.clientX
    startWidth.current = width
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    e.preventDefault()
  }, [width])

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!isDragging.current) return
      const newWidth = Math.min(400, Math.max(180, startWidth.current + (e.clientX - startX.current)))
      onWidthChange(newWidth)
    }
    const onUp = () => {
      if (!isDragging.current) return
      isDragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [onWidthChange])

  useEffect(() => {
    if (!contextMenu) return
    const dismiss = () => setContextMenu(null)
    window.addEventListener('click', dismiss)
    return () => window.removeEventListener('click', dismiss)
  }, [contextMenu])

  if (collapsed) return null

  const sortedFolders = [...fileTree.keys()].sort()

  return (
    <div
      style={{
        width, minWidth: 180, maxWidth: 400,
        background: '#0c1021',
        borderRight: '1px solid #1e293b',
        display: 'flex', flexDirection: 'column',
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 11, color: '#e8e8f0',
        flexShrink: 0, position: 'relative',
        overflow: 'hidden',
      }}
    >
      <div style={{
        padding: '12px 12px 10px',
        borderBottom: '1px solid #1e293b',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      }}>
        <span style={{ color: '#00FFC8', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.1em', fontWeight: 600 }}>◈ Vault</span>
        <span style={{ color: '#4b5563', fontSize: 9, fontVariantNumeric: 'tabular-nums' }}>{totalNotes}</span>
      </div>

      <div style={{ flex: 1, padding: '8px 0', overflowY: 'auto', overflowX: 'hidden' }}>
        {/* Root-level files rendered inline without a collapsible folder wrapper */}
        {fileTree.has('Root') && (() => {
          const subMap = fileTree.get('Root')!
          const rootFiles = subMap.get('') ?? []
          return rootFiles.map(file => (
            <NoteItem
              key={file.path}
              file={file}
              isActive={file.stem === activeTab}
              indent={12}
              onSelect={onSelectNote}
              onContextMenu={(stem, x, y) => setContextMenu({ stem, x, y })}
            />
          ))
        })()}

        {sortedFolders.filter(f => f !== 'Root').map(folder => {
          const subMap = fileTree.get(folder)!
          const isExpanded = expandedSet.has(folder)
          let count = 0
          for (const [, notes] of subMap) count += notes.length
          const sortedSubs = [...subMap.keys()].sort()

          return (
            <div key={folder}>
              <div
                onClick={() => toggleFolder(folder)}
                style={{
                  padding: '5px 10px 5px 10px',
                  display: 'flex', alignItems: 'center', gap: 6,
                  cursor: 'pointer',
                  transition: 'background 0.1s',
                }}
                onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.03)')}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
              >
                <span style={{ color: isExpanded ? '#f59e0b' : '#6b7a99', fontSize: 10, width: 12, textAlign: 'center' }}>
                  {isExpanded ? '▾' : '▸'}
                </span>
                <span style={{ flex: 1 }}>{folder}/</span>
                <span style={{ color: '#4b5563', fontSize: 9 }}>{count}</span>
              </div>

              {isExpanded && sortedSubs.map(sub => {
                const notes = subMap.get(sub)!
                if (sub) {
                  const subKey = `${folder}/${sub}`
                  const subExpanded = expandedSet.has(subKey)
                  return (
                    <div key={subKey}>
                      <div
                        onClick={() => toggleFolder(subKey)}
                        style={{
                          padding: '4px 10px 4px 24px',
                          display: 'flex', alignItems: 'center', gap: 5,
                          cursor: 'pointer', color: '#8892a8', fontSize: 10,
                          transition: 'background 0.1s',
                        }}
                        onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.03)')}
                        onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                      >
                        <span style={{ color: subExpanded ? '#f59e0b' : '#6b7a99', fontSize: 9, width: 10, textAlign: 'center' }}>
                          {subExpanded ? '▾' : '▸'}
                        </span>
                        <span>{sub}/</span>
                        <span style={{ color: '#4b5563', fontSize: 9, marginLeft: 'auto' }}>{notes.length}</span>
                      </div>
                      {subExpanded && notes.map(file => (
                        <NoteItem
                          key={file.path}
                          file={file}
                          isActive={file.stem === activeTab}
                          indent={38}
                          onSelect={onSelectNote}
                          onContextMenu={(stem, x, y) => setContextMenu({ stem, x, y })}
                        />
                      ))}
                    </div>
                  )
                }
                // Use folder+sub as key prefix to avoid duplicate keys across subfolders
                return (
                  <div key={`${folder}/_root`}>
                    {notes.map(file => (
                      <NoteItem
                        key={file.path}
                        file={file}
                        isActive={file.stem === activeTab}
                        indent={24}
                        onSelect={onSelectNote}
                        onContextMenu={(stem, x, y) => setContextMenu({ stem, x, y })}
                      />
                    ))}
                  </div>
                )
              })}
            </div>
          )
        })}
      </div>

      <div
        onMouseDown={onMouseDown}
        style={{
          position: 'absolute', right: 0, top: 0, bottom: 0, width: 4,
          cursor: 'col-resize', zIndex: 10,
        }}
        onMouseEnter={e => (e.currentTarget.style.background = 'rgba(99,102,241,0.5)')}
        onMouseLeave={e => { if (!isDragging.current) e.currentTarget.style.background = 'transparent' }}
      />

      {contextMenu && (
        <div
          style={{
            position: 'fixed', left: contextMenu.x, top: contextMenu.y,
            background: '#0a0e1a', border: '1px solid #1a2040', borderRadius: 4,
            zIndex: 1000, minWidth: 140, boxShadow: '0 4px 16px rgba(0,0,0,0.6)',
            fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
          }}
          onClick={e => e.stopPropagation()}
        >
          <div
            style={{ padding: '6px 12px', cursor: 'pointer', color: '#ccc' }}
            onMouseEnter={e => (e.currentTarget.style.background = '#1a2040')}
            onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
            onClick={() => { onSelectNote(contextMenu.stem, false); setContextMenu(null) }}
          >
            Open
          </div>
          <div
            style={{ padding: '6px 12px', cursor: 'pointer', color: '#00FFC8' }}
            onMouseEnter={e => (e.currentTarget.style.background = '#1a2040')}
            onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
            onClick={() => { onOpenHistory(contextMenu.stem); setContextMenu(null) }}
          >
            View History
          </div>
          {onDeleteNote && (
            <div
              style={{ padding: '6px 12px', cursor: 'pointer', color: '#ef4444', borderTop: '1px solid #1a2040' }}
              onMouseEnter={e => (e.currentTarget.style.background = '#1a2040')}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
              onClick={() => { onDeleteNote(contextMenu.stem); setContextMenu(null) }}
            >
              Delete
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function NoteItem({ file, isActive, indent, onSelect, onContextMenu }: {
  file: VaultFile
  isActive: boolean
  indent: number
  onSelect: (stem: string, newTab: boolean) => void
  onContextMenu: (stem: string, x: number, y: number) => void
}) {
  return (
    <div
      onClick={(e) => onSelect(file.stem, e.metaKey || e.ctrlKey)}
      onContextMenu={(e) => {
        e.preventDefault()
        onContextMenu(file.stem, e.clientX, e.clientY)
      }}
      style={{
        padding: `4px 10px 4px ${indent}px`,
        fontSize: 10, cursor: 'pointer',
        color: isActive ? '#e8e8f0' : '#8892a8',
        background: isActive ? 'rgba(99,102,241,0.12)' : 'transparent',
        borderLeft: isActive ? '2px solid #6366f1' : '2px solid transparent',
        borderRadius: isActive ? '0 3px 3px 0' : 0,
        display: 'flex', alignItems: 'center', gap: 5,
        overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis',
        transition: 'background 0.1s, color 0.1s',
      }}
      onMouseEnter={e => {
        if (!isActive) {
          e.currentTarget.style.background = 'rgba(255,255,255,0.04)'
          e.currentTarget.style.color = '#c0c8d8'
        }
      }}
      onMouseLeave={e => {
        if (!isActive) {
          e.currentTarget.style.background = 'transparent'
          e.currentTarget.style.color = '#8892a8'
        }
      }}
    >
      <span style={{ color: getNodeColor(file.noteType ?? 'pattern'), fontSize: 7, flexShrink: 0 }}>●</span>
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {file.stem}.md
      </span>
    </div>
  )
}
