'use client'

import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import type { NoteNode } from '@/lib/graph'
import { getNodeColor } from '@/lib/sigma-colors'
import { fetchSemanticResults } from '@/lib/semanticSearch'

interface Props {
  nodes: NoteNode[]
  vault: string | null
  onSelect: (stem: string, newTab: boolean) => void
}

interface ResultItem {
  node: NoteNode
  summary?: string
}

const SEMANTIC_DEBOUNCE_MS = 500
const SEMANTIC_MIN_CHARS = 2

type SemanticStatus = 'idle' | 'loading' | 'done' | 'error'

export function UnifiedSearch({ nodes, vault, onSelect }: Props) {
  const [query, setQuery] = useState('')
  const [dismissed, setDismissed] = useState(false)
  const [selectedIdx, setSelectedIdx] = useState(0)
  const [semanticItems, setSemanticItems] = useState<ResultItem[]>([])
  const [semanticStatus, setSemanticStatus] = useState<SemanticStatus>('idle')
  const [semanticError, setSemanticError] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const isSemantic = query.trimStart().startsWith('?')
  const semanticQuery = isSemantic ? query.trimStart().slice(1).trim() : ''

  const nodeMap = useMemo(() => new Map(nodes.map(n => [n.id, n])), [nodes])

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

  // Lexical results as derived state (unchanged behavior; inert in semantic mode)
  const lexicalItems = useMemo<ResultItem[]>(() => {
    if (isSemantic) return []
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

    return filtered.slice(0, 8).map(node => ({ node }))
  }, [query, nodes, isSemantic])

  // Semantic fetch: debounce keystrokes, abort superseded requests.
  useEffect(() => {
    if (!isSemantic || semanticQuery.length < SEMANTIC_MIN_CHARS) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSemanticItems([])
      setSemanticStatus('idle')
      return
    }
    setSemanticStatus('loading')
    setSemanticItems([])
    const controller = new AbortController()
    const timer = setTimeout(async () => {
      try {
        const results = await fetchSemanticResults(semanticQuery, vault, controller.signal)
        const items: ResultItem[] = []
        for (const r of results) {
          const node = nodeMap.get(r.stem)
          if (node) items.push({ node, summary: r.summary })
        }
        setSemanticItems(items)
        setSemanticStatus('done')
      } catch (e) {
        if (controller.signal.aborted) return
        setSemanticError((e as Error).message)
        setSemanticStatus('error')
      }
    }, SEMANTIC_DEBOUNCE_MS)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [isSemantic, semanticQuery, vault, nodeMap])

  const items = isSemantic ? semanticItems : lexicalItems

  // Narrowing the query after arrowing down can leave selectedIdx pointing past
  // the new (shorter) results array — reset it whenever results change.
  useEffect(() => { setSelectedIdx(0) }, [items]) // eslint-disable-line react-hooks/set-state-in-effect

  // Semantic mode opens the dropdown for loading/error/empty states too.
  const open = !dismissed && query.trim().length > 0 && (
    isSemantic ? semanticQuery.length >= SEMANTIC_MIN_CHARS : items.length > 0
  )

  const handleSelect = useCallback((stem: string, newTab: boolean) => {
    setQuery('')
    setDismissed(true)
    onSelect(stem, newTab)
  }, [onSelect])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIdx(i => Math.min(i + 1, items.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIdx(i => Math.max(i - 1, 0))
    } else if (e.key === 'Enter' && items.length > 0) {
      e.preventDefault()
      handleSelect(items[selectedIdx].node.id, e.metaKey || e.ctrlKey)
    } else if (e.key === 'Escape') {
      setQuery('')
      setDismissed(true)
      inputRef.current?.blur()
    }
  }, [items, selectedIdx, handleSelect])

  const highlight = (title: string) => {
    const q = query.startsWith('#') || query.startsWith('/') || isSemantic
      ? '' : query.trim().toLowerCase()
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

  const statusRowStyle: React.CSSProperties = {
    padding: '10px 12px', color: '#6b7a99', fontSize: 11,
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
        placeholder="⌘K  Search titles, #tags, /folders, ?semantic..."
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

      {open && (
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
            {isSemantic
              ? (semanticStatus === 'done' ? `${items.length} semantic matches` : 'semantic search')
              : <>{items.length} results · <span style={{ color: '#4b5563' }}>Cmd+click for new tab</span></>}
          </div>

          {isSemantic && semanticStatus === 'loading' && items.length === 0 && (
            <div style={statusRowStyle}>Searching…</div>
          )}
          {isSemantic && semanticStatus === 'error' && (
            <div style={{ ...statusRowStyle, color: '#ef4444' }}>{semanticError}</div>
          )}
          {isSemantic && semanticStatus === 'done' && items.length === 0 && (
            <div style={statusRowStyle}>No semantic matches</div>
          )}

          {items.length > 0 && (
            <div style={{ padding: 4 }}>
              {items.map((item, i) => (
                <div
                  key={item.node.id}
                  onMouseDown={(e) => handleSelect(item.node.id, e.metaKey || e.ctrlKey)}
                  onMouseEnter={() => setSelectedIdx(i)}
                  style={{
                    padding: '8px 10px',
                    background: i === selectedIdx ? 'rgba(99,102,241,0.1)' : 'transparent',
                    borderRadius: 4, cursor: 'pointer',
                    display: 'flex', alignItems: 'center', gap: 8,
                    marginBottom: i < items.length - 1 ? 2 : 0,
                  }}
                >
                  <span style={{ color: getNodeColor(item.node.type), fontSize: 9 }}>●</span>
                  <div style={{ minWidth: 0, overflow: 'hidden' }}>
                    <div style={{ color: '#e8e8f0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {highlight(item.node.title)}
                    </div>
                    <div style={{ color: '#4b5563', fontSize: 9, marginTop: 1 }}>
                      {item.node.folder}/ · {item.node.tags.slice(0, 3).map(t => `#${t}`).join(' ')}
                    </div>
                    {item.summary && (
                      <div style={{ color: '#6b7a99', fontSize: 9, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {item.summary}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          <div style={{
            padding: '6px 12px', borderTop: '1px solid #1e293b',
            color: '#4b5563', fontSize: 9,
            display: 'flex', gap: 12,
          }}>
            <span>↑↓ navigate</span>
            <span>⏎ open</span>
            <span>⌘⏎ new tab</span>
            <span>? semantic</span>
            <span>esc close</span>
          </div>
        </div>
      )}
    </div>
  )
}
