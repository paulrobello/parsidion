'use client'

import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import type { NoteNode } from '@/lib/graph'
import { getNodeColor } from '@/lib/sigma-colors'

interface Props {
  nodes: NoteNode[]
  onSelect: (stem: string, newTab: boolean) => void
}

export function UnifiedSearch({ nodes, onSelect }: Props) {
  const [query, setQuery] = useState('')
  const [dismissed, setDismissed] = useState(false)
  const [selectedIdx, setSelectedIdx] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        inputRef.current?.focus()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  // Compute results as derived state (no useEffect + setState)
  const results = useMemo(() => {
    const q = query.trim()
    if (!q) return []

    let filtered: NoteNode[]

    if (q.startsWith('#')) {
      const tagQ = q.slice(1).toLowerCase()
      filtered = nodes.filter(n => n.tags.some(t => t.toLowerCase().includes(tagQ)))
    } else if (q.startsWith('/')) {
      const pathQ = q.slice(1).toLowerCase()
      filtered = nodes.filter(n => n.path.toLowerCase().includes(pathQ))
    } else {
      const lq = q.toLowerCase()
      filtered = nodes.filter(n => n.title.toLowerCase().includes(lq) || n.id.toLowerCase().includes(lq))
    }

    return filtered.slice(0, 8)
  }, [query, nodes])

  // Derive open state: auto-open when results exist, unless manually dismissed
  const open = !dismissed && results.length > 0 && query.trim().length > 0

  const handleSelect = useCallback((stem: string, newTab: boolean) => {
    setQuery('')
    setDismissed(true)
    onSelect(stem, newTab)
  }, [onSelect])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIdx(i => Math.min(i + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIdx(i => Math.max(i - 1, 0))
    } else if (e.key === 'Enter' && results.length > 0) {
      e.preventDefault()
      handleSelect(results[selectedIdx].id, e.metaKey || e.ctrlKey)
    } else if (e.key === 'Escape') {
      setQuery('')
      setDismissed(true)
      inputRef.current?.blur()
    }
  }, [results, selectedIdx, handleSelect])

  const highlight = (title: string) => {
    const q = query.startsWith('#') || query.startsWith('/') ? '' : query.trim().toLowerCase()
    if (!q) return <>{title}</>
    const idx = title.toLowerCase().indexOf(q)
    if (idx < 0) return <>{title}</>
    return (
      <>
        {title.slice(0, idx)}
        <span style={{ color: '#f97316' }}>{title.slice(idx, idx + q.length)}</span>
        {title.slice(idx + q.length)}
      </>
    )
  }

  return (
    <div style={{ position: 'relative' }}>
      {open && (
        <div
          style={{
            position: 'fixed', inset: 0, zIndex: 150,
            background: 'rgba(0,0,0,0.3)',
          }}
          onClick={() => { setDismissed(true); setQuery(''); inputRef.current?.blur() }}
        />
      )}
      <input
        ref={inputRef}
        value={query}
        onChange={e => { setQuery(e.target.value); setDismissed(false) }}
        onFocus={() => setDismissed(false)}
        onBlur={() => setTimeout(() => setDismissed(true), 200)}
        onKeyDown={handleKeyDown}
        placeholder="⌘K  Search titles, #tags, /folders..."
        style={{
          width: 240, padding: '4px 10px',
          background: '#111827',
          border: open ? '1px solid #6366f1' : '1px solid #1e293b',
          borderRadius: 5, color: '#e8e8f0',
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 10, outline: 'none',
          boxShadow: open ? '0 0 12px rgba(99,102,241,0.2)' : 'none',
          transition: 'border-color 0.15s, box-shadow 0.15s',
          position: 'relative', zIndex: 160,
        }}
      />

      {open && results.length > 0 && (
        <div style={{
          position: 'absolute', top: '100%', right: 0,
          width: 360, marginTop: 4,
          background: '#111827',
          border: '1px solid #1e293b',
          borderRadius: 8,
          boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
          zIndex: 200, overflow: 'hidden',
          fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
        }}>
          <div style={{
            padding: '6px 12px', borderBottom: '1px solid #1e293b',
            color: '#6b7a99', fontSize: 9, textTransform: 'uppercase', letterSpacing: '1px',
          }}>
            {results.length} results · <span style={{ color: '#4b5563' }}>Cmd+click for new tab</span>
          </div>
          <div style={{ padding: 4 }}>
            {results.map((node, i) => (
              <div
                key={node.id}
                onMouseDown={(e) => handleSelect(node.id, e.metaKey || e.ctrlKey)}
                onMouseEnter={() => setSelectedIdx(i)}
                style={{
                  padding: '8px 10px',
                  background: i === selectedIdx ? 'rgba(99,102,241,0.1)' : 'transparent',
                  borderRadius: 4, cursor: 'pointer',
                  display: 'flex', alignItems: 'center', gap: 8,
                  marginBottom: i < results.length - 1 ? 2 : 0,
                }}
              >
                <span style={{ color: getNodeColor(node.type), fontSize: 9 }}>●</span>
                <div style={{ minWidth: 0, overflow: 'hidden' }}>
                  <div style={{ color: '#e8e8f0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {highlight(node.title)}
                  </div>
                  <div style={{ color: '#4b5563', fontSize: 9, marginTop: 1 }}>
                    {node.folder}/ · {node.tags.slice(0, 3).map(t => `#${t}`).join(' ')}
                  </div>
                </div>
              </div>
            ))}
          </div>
          <div style={{
            padding: '6px 12px', borderTop: '1px solid #1e293b',
            color: '#4b5563', fontSize: 9,
            display: 'flex', gap: 12,
          }}>
            <span>↑↓ navigate</span>
            <span>⏎ open</span>
            <span>⌘⏎ new tab</span>
            <span>esc close</span>
          </div>
        </div>
      )}
    </div>
  )
}
