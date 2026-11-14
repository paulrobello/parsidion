'use client'

import { useState, useRef, useCallback, useMemo } from 'react'
import type { NoteNode } from '@/lib/graph'
import { TYPE_COLORS } from '@/lib/sigma-colors'
import { useKeyboardShortcuts } from '@/lib/useKeyboardShortcuts'

// ARC-210: ChipInput and RelatedInput extracted from FrontmatterEditor.tsx;
// both consume the shared useKeyboardShortcuts protocol instead of carrying
// byte-identical handleKeyDown clones.

export function ChipInput({ values, onChange, placeholder, normalize, suggestions }: {
  values: string[]
  onChange: (v: string[]) => void
  placeholder?: string
  normalize?: (s: string) => string
  suggestions?: string[]
}) {
  const [input, setInput] = useState('')
  const [selectedIdx, setSelectedIdx] = useState(0)
  const [showDropdown, setShowDropdown] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const filtered = useMemo(() => {
    if (!suggestions || !input.trim()) return []
    const q = input.trim().toLowerCase()
    return suggestions
      .filter(s => s.toLowerCase().includes(q) && !values.includes(s))
      .slice(0, 8)
  }, [suggestions, input, values])

  // Adjust-during-render (React docs) instead of a reset effect: when the
  // suggestion list changes, clamp the highlight back to the first row in
  // this same render.
  const [prevFiltered, setPrevFiltered] = useState(filtered)
  if (prevFiltered !== filtered) {
    setPrevFiltered(filtered)
    setSelectedIdx(0)
  }

  const add = useCallback((raw: string) => {
    const val = normalize ? normalize(raw) : raw.trim()
    if (val && !values.includes(val)) onChange([...values, val])
    setInput('')
    setShowDropdown(false)
  }, [values, onChange, normalize])

  const remove = useCallback((val: string) => {
    onChange(values.filter(v => v !== val))
  }, [values, onChange])

  const handleKeyDown = useKeyboardShortcuts({
    itemCount: filtered.length,
    setSelectedIdx,
    showDropdown,
    setShowDropdown,
    input,
    values,
    removeLast: () => remove(values[values.length - 1]),
    extraCommitKeys: [','],
    handleCommit: (e) => {
      if (!input.trim()) return
      e.preventDefault()
      if (filtered.length > 0 && showDropdown) {
        add(filtered[selectedIdx])
      } else {
        add(input)
      }
    },
  })

  return (
    <div style={{ position: 'relative' }}>
      <div
        onClick={() => inputRef.current?.focus()}
        style={{
          display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center',
          background: 'rgba(15,23,42,0.8)',
          border: '1px solid #1e293b',
          borderRadius: 4, padding: '4px 6px',
          cursor: 'text', minHeight: 28,
        }}
      >
        {values.map(v => (
          <span key={v} style={chipStyle}>
            {v}
            <span onClick={(e) => { e.stopPropagation(); remove(v) }} style={chipXStyle}>×</span>
          </span>
        ))}
        <input
          ref={inputRef}
          value={input}
          onChange={e => { setInput(e.target.value); setShowDropdown(true) }}
          onFocus={() => setShowDropdown(true)}
          onBlur={() => setTimeout(() => { if (input.trim()) add(input); setShowDropdown(false) }, 200)}
          onKeyDown={handleKeyDown}
          placeholder={values.length === 0 ? placeholder : ''}
          style={{
            background: 'none', border: 'none', outline: 'none',
            color: '#e8e8f0', fontFamily: "'JetBrains Mono', monospace",
            fontSize: 11, flex: 1, minWidth: 60, padding: '2px 0',
          }}
        />
      </div>

      {/* Autocomplete dropdown */}
      {showDropdown && filtered.length > 0 && (
        <div style={{
          position: 'absolute', top: '100%', left: 0, right: 0,
          marginTop: 4,
          background: '#111827',
          border: '1px solid #1e293b',
          borderRadius: 6,
          boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
          zIndex: 100, overflow: 'hidden',
          fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
        }}>
          {filtered.map((tag, i) => {
            const q = input.trim().toLowerCase()
            const idx = tag.toLowerCase().indexOf(q)
            return (
              <div
                key={tag}
                onMouseDown={() => add(tag)}
                onMouseEnter={() => setSelectedIdx(i)}
                style={{
                  padding: '5px 10px',
                  background: i === selectedIdx ? 'rgba(99,102,241,0.12)' : 'transparent',
                  cursor: 'pointer', color: '#e8e8f0',
                }}
              >
                {idx >= 0 ? (
                  <>
                    {tag.slice(0, idx)}
                    <span style={{ color: '#f97316' }}>{tag.slice(idx, idx + q.length)}</span>
                    {tag.slice(idx + q.length)}
                  </>
                ) : tag}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export function RelatedInput({ values, onChange, nodes }: {
  values: string[]
  onChange: (v: string[]) => void
  nodes: NoteNode[]
}) {
  const [input, setInput] = useState('')
  const [selectedIdx, setSelectedIdx] = useState(0)
  const [showDropdown, setShowDropdown] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)

  const results = useMemo(() => {
    const q = input.trim().toLowerCase()
    if (!q) return []
    return nodes
      .filter(n => !values.includes(n.id) && (n.title.toLowerCase().includes(q) || n.id.toLowerCase().includes(q)))
      .slice(0, 8)
  }, [input, nodes, values])

  // Reset selectedIdx when results change
  const [prevResults, setPrevResults] = useState(results)
  if (prevResults !== results) {
    setPrevResults(results)
    setSelectedIdx(0)
  }

  const add = useCallback((stem: string) => {
    if (stem && !values.includes(stem)) onChange([...values, stem])
    setInput('')
    setShowDropdown(false)
    inputRef.current?.focus()
  }, [values, onChange])

  const remove = useCallback((stem: string) => {
    onChange(values.filter(v => v !== stem))
  }, [values, onChange])

  const handleKeyDown = useKeyboardShortcuts({
    itemCount: results.length,
    setSelectedIdx,
    showDropdown,
    setShowDropdown,
    input,
    values,
    removeLast: () => remove(values[values.length - 1]),
    handleCommit: (e) => {
      e.preventDefault()
      if (results.length > 0) {
        add(results[selectedIdx].id)
      } else if (input.trim()) {
        add(input.trim())
      }
    },
  })

  return (
    <div style={{ position: 'relative' }}>
      <div
        onClick={() => inputRef.current?.focus()}
        style={{
          display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center',
          background: 'rgba(15,23,42,0.8)',
          border: '1px solid #1e293b',
          borderRadius: 4, padding: '4px 6px',
          cursor: 'text', minHeight: 28,
        }}
      >
        {values.map(stem => (
          <span key={stem} style={{ ...chipStyle, color: '#7b61ff', background: 'rgba(123,97,255,0.12)', borderColor: 'rgba(123,97,255,0.25)' }}>
            [[{stem}]]
            <span onClick={(e) => { e.stopPropagation(); remove(stem) }} style={chipXStyle}>×</span>
          </span>
        ))}
        <input
          ref={inputRef}
          value={input}
          onChange={e => { setInput(e.target.value); setShowDropdown(true) }}
          onFocus={() => setShowDropdown(true)}
          onBlur={() => setTimeout(() => setShowDropdown(false), 200)}
          onKeyDown={handleKeyDown}
          placeholder={values.length === 0 ? 'search notes…' : ''}
          style={{
            background: 'none', border: 'none', outline: 'none',
            color: '#e8e8f0', fontFamily: "'JetBrains Mono', monospace",
            fontSize: 11, flex: 1, minWidth: 80, padding: '2px 0',
          }}
        />
      </div>

      {/* Autocomplete dropdown */}
      {showDropdown && results.length > 0 && (
        <div
          ref={dropdownRef}
          style={{
            position: 'absolute', top: '100%', left: 0, right: 0,
            marginTop: 4,
            background: '#111827',
            border: '1px solid #1e293b',
            borderRadius: 6,
            boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
            zIndex: 100, overflow: 'hidden',
            fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
          }}
        >
          {results.map((node, i) => {
            const q = input.trim().toLowerCase()
            const titleLower = node.title.toLowerCase()
            const hlIdx = titleLower.indexOf(q)
            return (
              <div
                key={node.id}
                onMouseDown={() => add(node.id)}
                onMouseEnter={() => setSelectedIdx(i)}
                style={{
                  padding: '6px 10px',
                  background: i === selectedIdx ? 'rgba(123,97,255,0.12)' : 'transparent',
                  cursor: 'pointer',
                  display: 'flex', alignItems: 'center', gap: 8,
                }}
              >
                <span style={{ color: TYPE_COLORS[node.type] ?? '#6b7280', fontSize: 8 }}>●</span>
                <div style={{ minWidth: 0, overflow: 'hidden' }}>
                  <div style={{ color: '#e8e8f0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {hlIdx >= 0 ? (
                      <>
                        {node.title.slice(0, hlIdx)}
                        <span style={{ color: '#f97316' }}>{node.title.slice(hlIdx, hlIdx + q.length)}</span>
                        {node.title.slice(hlIdx + q.length)}
                      </>
                    ) : node.title}
                  </div>
                  <div style={{ color: '#4b5563', fontSize: 9 }}>
                    {node.folder}/ · {node.id}
                  </div>
                </div>
              </div>
            )
          })}
          <div style={{
            padding: '4px 10px', borderTop: '1px solid #1e293b',
            color: '#4b5563', fontSize: 9,
          }}>
            ↑↓ navigate · ⏎ select · esc close
          </div>
        </div>
      )}
    </div>
  )
}

const chipStyle: React.CSSProperties = {
  background: 'rgba(30,41,59,0.8)',
  border: '1px solid #334155',
  borderRadius: 3, padding: '1px 6px',
  color: '#9ca3af', fontSize: 10,
  fontFamily: "'JetBrains Mono', monospace",
  display: 'flex', alignItems: 'center', gap: 4,
  whiteSpace: 'nowrap',
}

const chipXStyle: React.CSSProperties = {
  cursor: 'pointer', color: '#6b7a99',
  fontSize: 11, lineHeight: 1,
  marginLeft: 2,
}

