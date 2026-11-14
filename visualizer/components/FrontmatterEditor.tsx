'use client'

import { useState, useCallback, useMemo } from 'react'
import type { FrontmatterFields } from '@/lib/frontmatter'
import type { NoteNode } from '@/lib/graph'
import { TYPE_COLORS } from '@/lib/sigma-colors'
import { ChipInput, RelatedInput } from './FrontmatterEditorInputs'

// 'daily' intentionally excluded — daily notes follow the Daily/YYYY-MM/DD-user.md
// convention and are written by hooks, not created/retyped through this editor.
const TYPES = ['pattern', 'debugging', 'research', 'project', 'tool', 'language', 'framework', 'knowledge', 'rule', 'fork']
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

// ─── Shared styles ──────────────────────────────────────────

const smallInputStyle: React.CSSProperties = {
  background: 'rgba(15,23,42,0.8)',
  border: '1px solid #1e293b',
  borderRadius: 4, color: '#e8e8f0',
  fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
  padding: '4px 8px', outline: 'none',
}

const addFieldBtnStyle: React.CSSProperties = {
  background: 'none', border: '1px dashed #1e293b',
  color: '#4b5563', cursor: 'pointer',
  borderRadius: 4, padding: '4px 10px',
  fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
  alignSelf: 'flex-end',
}
