'use client'

import { useState, useRef, useCallback, useMemo, useEffect } from 'react'
import type { FrontmatterFields } from '@/lib/frontmatter'
import type { NoteNode } from '@/lib/graph'
import { TYPE_COLORS } from '@/lib/sigma-colors'

const TYPES = ['pattern', 'debugging', 'research', 'project', 'tool', 'language', 'framework']
const CONFIDENCE_LEVELS = ['low', 'medium', 'high'] as const
const CONFIDENCE_COLORS: Record<string, string> = { low: '#6b7a99', medium: '#f59e0b', high: '#10b981' }

interface Props {
  fields: FrontmatterFields
  onChange: (fields: FrontmatterFields) => void
  nodes: NoteNode[]
}

export function FrontmatterEditor({ fields, onChange, nodes }: Props) {
  const update = useCallback(<K extends keyof FrontmatterFields>(key: K, value: FrontmatterFields[K]) => {
    onChange({ ...fields, [key]: value })
  }, [fields, onChange])

  const [showProject, setShowProject] = useState(!!fields.project)
  const [showSources, setShowSources] = useState(fields.sources.length > 0)

  // Collect all unique tags from graph for autocomplete
  const allTags = useMemo(() => {
    const tagSet = new Set<string>()
    for (const node of nodes) {
      for (const tag of node.tags) tagSet.add(tag)
    }
    return [...tagSet].sort()
  }, [nodes])

  return (
    <div style={{
      background: 'rgba(15,23,42,0.5)',
      border: '1px solid #1e293b',
      borderRadius: 6,
      padding: '14px 16px',
      display: 'flex', flexDirection: 'column', gap: 12,
      flexShrink: 0,
    }}>
      {/* Row 1: Type + Date + Confidence */}
      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        {/* Type */}
        <div style={{ flex: 1, minWidth: 200 }}>
          <Label>Type</Label>
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
            {TYPES.map(t => (
              <button
                key={t}
                onClick={() => update('type', t)}
                style={{
                  background: fields.type === t ? `${TYPE_COLORS[t]}22` : 'transparent',
                  border: `1px solid ${fields.type === t ? TYPE_COLORS[t] + '66' : '#1e293b'}`,
                  color: fields.type === t ? TYPE_COLORS[t] : '#4b5563',
                  cursor: 'pointer', borderRadius: 4,
                  padding: '2px 8px',
                  fontFamily: "'Oxanium', sans-serif", fontSize: 9, fontWeight: 600,
                  textTransform: 'uppercase', letterSpacing: '0.05em',
                }}
              >
                {t}
              </button>
            ))}
          </div>
        </div>

        {/* Date */}
        <div>
          <Label>Date</Label>
          <input
            value={fields.date}
            onChange={e => update('date', e.target.value)}
            placeholder="YYYY-MM-DD"
            style={{ ...smallInputStyle, width: 100 }}
          />
        </div>

        {/* Confidence */}
        <div>
          <Label>Confidence</Label>
          <div style={{ display: 'flex', borderRadius: 4, overflow: 'hidden', border: '1px solid #1e293b' }}>
            {CONFIDENCE_LEVELS.map(level => (
              <button
                key={level}
                onClick={() => update('confidence', level)}
                style={{
                  background: fields.confidence === level ? `${CONFIDENCE_COLORS[level]}22` : 'transparent',
                  border: 'none',
                  borderRight: level !== 'high' ? '1px solid #1e293b' : 'none',
                  color: fields.confidence === level ? CONFIDENCE_COLORS[level] : '#4b5563',
                  cursor: 'pointer',
                  padding: '3px 10px',
                  fontFamily: "'JetBrains Mono', monospace", fontSize: 9,
                }}
              >
                {level}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Row 2: Tags */}
      <div>
        <Label>Tags</Label>
        <ChipInput
          values={fields.tags}
          onChange={tags => update('tags', tags)}
          placeholder="add tag…"
          normalize={normalizeTag}
          suggestions={allTags}
        />
      </div>

      {/* Row 3: Related */}
      <div>
        <Label>Related</Label>
        <RelatedInput
          values={fields.related}
          onChange={related => update('related', related)}
          nodes={nodes}
        />
      </div>

      {/* Optional rows */}
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        {showProject ? (
          <div style={{ flex: 1, minWidth: 160 }}>
            <Label>Project</Label>
            <input
              value={fields.project}
              onChange={e => update('project', e.target.value)}
              placeholder="my-project"
              style={{ ...smallInputStyle, width: '100%' }}
            />
          </div>
        ) : (
          <button onClick={() => setShowProject(true)} style={addFieldBtnStyle}>+ Project</button>
        )}

        {showSources ? (
          <div style={{ flex: 2, minWidth: 200 }}>
            <Label>Sources</Label>
            <ChipInput
              values={fields.sources}
              onChange={sources => update('sources', sources)}
              placeholder="add URL or path…"
            />
          </div>
        ) : (
          <button onClick={() => setShowSources(true)} style={addFieldBtnStyle}>+ Sources</button>
        )}
      </div>
    </div>
  )
}

// ─── Sub-components ─────────────────────────────────────────

function Label({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontFamily: "'JetBrains Mono', monospace",
      fontSize: 9, color: '#4b5563',
      textTransform: 'uppercase', letterSpacing: '0.06em',
      marginBottom: 4,
    }}>
      {children}
    </div>
  )
}

function normalizeTag(raw: string): string {
  return raw.toLowerCase().replace(/[^a-z0-9-]/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '')
}

// ─── ChipInput (tags, sources) ──────────────────────────────

function ChipInput({ values, onChange, placeholder, normalize, suggestions }: {
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

  useEffect(() => { setSelectedIdx(0) }, [filtered])

  const add = useCallback((raw: string) => {
    const val = normalize ? normalize(raw) : raw.trim()
    if (val && !values.includes(val)) onChange([...values, val])
    setInput('')
    setShowDropdown(false)
  }, [values, onChange, normalize])

  const remove = useCallback((val: string) => {
    onChange(values.filter(v => v !== val))
  }, [values, onChange])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown' && filtered.length > 0) {
      e.preventDefault()
      setSelectedIdx(i => Math.min(i + 1, filtered.length - 1))
    } else if (e.key === 'ArrowUp' && filtered.length > 0) {
      e.preventDefault()
      setSelectedIdx(i => Math.max(i - 1, 0))
    } else if ((e.key === 'Enter' || e.key === ',') && input.trim()) {
      e.preventDefault()
      if (filtered.length > 0 && showDropdown) {
        add(filtered[selectedIdx])
      } else {
        add(input)
      }
    } else if (e.key === 'Escape') {
      setShowDropdown(false)
    } else if (e.key === 'Backspace' && !input && values.length > 0) {
      e.preventDefault()
      remove(values[values.length - 1])
    }
  }, [input, values, filtered, selectedIdx, showDropdown, add, remove])

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
          onBlur={() => setTimeout(() => { if (input.trim() && !showDropdown) add(input); setShowDropdown(false) }, 200)}
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

// ─── RelatedInput (wikilink chips with autocomplete) ────────

function RelatedInput({ values, onChange, nodes }: {
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
  useEffect(() => { setSelectedIdx(0) }, [results])

  const add = useCallback((stem: string) => {
    if (stem && !values.includes(stem)) onChange([...values, stem])
    setInput('')
    setShowDropdown(false)
    inputRef.current?.focus()
  }, [values, onChange])

  const remove = useCallback((stem: string) => {
    onChange(values.filter(v => v !== stem))
  }, [values, onChange])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown' && results.length > 0) {
      e.preventDefault()
      setSelectedIdx(i => Math.min(i + 1, results.length - 1))
    } else if (e.key === 'ArrowUp' && results.length > 0) {
      e.preventDefault()
      setSelectedIdx(i => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (results.length > 0) {
        add(results[selectedIdx].id)
      } else if (input.trim()) {
        add(input.trim())
      }
    } else if (e.key === 'Escape') {
      setShowDropdown(false)
    } else if (e.key === 'Backspace' && !input && values.length > 0) {
      e.preventDefault()
      remove(values[values.length - 1])
    }
  }, [results, selectedIdx, input, values, add, remove])

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

// ─── Shared styles ──────────────────────────────────────────

const smallInputStyle: React.CSSProperties = {
  background: 'rgba(15,23,42,0.8)',
  border: '1px solid #1e293b',
  borderRadius: 4, color: '#e8e8f0',
  fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
  padding: '4px 8px', outline: 'none',
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

const addFieldBtnStyle: React.CSSProperties = {
  background: 'none', border: '1px dashed #1e293b',
  color: '#4b5563', cursor: 'pointer',
  borderRadius: 4, padding: '4px 10px',
  fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
  alignSelf: 'flex-end',
}
