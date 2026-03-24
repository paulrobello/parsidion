'use client'

import { useMemo } from 'react'
import { diffWords } from 'diff'
import type { DiffHunk, DiffLine } from '@/lib/parseDiff'
import { diffStats } from '@/lib/parseDiff'

export type DiffMode = 'unified' | 'split' | 'words'

interface Props {
  hunks: DiffHunk[]
  mode: DiffMode
  filename: string
  truncated?: boolean
}

const COLORS = {
  bg: '#060810',
  addBg: 'rgba(76,175,80,0.12)',
  removeBg: 'rgba(239,68,68,0.12)',
  addText: '#4CAF50',
  removeText: '#ef4444',
  contextText: '#666',
  lineNoText: '#333',
  headerBg: '#0a0e1a',
  headerBorder: '#111827',
  divider: '#111827',
  addLineNo: '#1a3a20',
  removeLineNo: '#5a2020',
}

function LineNo({ n, color }: { n: number | null; color: string }) {
  return (
    <span style={{ color, minWidth: 28, textAlign: 'right', paddingRight: 8, userSelect: 'none', flexShrink: 0 }}>
      {n ?? ''}
    </span>
  )
}

function UnifiedView({ hunks }: { hunks: DiffHunk[] }) {
  return (
    <div style={{ fontFamily: 'monospace', fontSize: 11 }}>
      {hunks.map((hunk, hi) => (
        <div key={hi}>
          <div style={{ padding: '2px 8px', background: '#0a0e1a', color: '#4b6bfb', fontSize: 10 }}>
            {hunk.header}
          </div>
          {hunk.lines.map((line, li) => {
            const isAdd = line.type === 'add'
            const isRemove = line.type === 'remove'
            return (
              <div key={li} style={{
                display: 'flex', gap: 8, padding: '1px 8px',
                background: isAdd ? COLORS.addBg : isRemove ? COLORS.removeBg : 'transparent',
              }}>
                <LineNo n={line.oldLineNo} color={isRemove ? COLORS.removeLineNo : COLORS.lineNoText} />
                <LineNo n={line.newLineNo} color={isAdd ? COLORS.addLineNo : COLORS.lineNoText} />
                <span style={{ color: isAdd ? '#4CAF50' : isRemove ? '#ef4444' : '#555', minWidth: 10 }}>
                  {isAdd ? '+' : isRemove ? '-' : ' '}
                </span>
                <span style={{ color: isAdd ? COLORS.addText : isRemove ? COLORS.removeText : COLORS.contextText, flex: 1, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                  {line.content}
                </span>
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}

function SplitView({ hunks }: { hunks: DiffHunk[] }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', fontFamily: 'monospace', fontSize: 11 }}>
      <div style={{ padding: '3px 8px', background: COLORS.headerBg, color: '#555', fontSize: 9, borderBottom: `1px solid ${COLORS.headerBorder}`, borderRight: `1px solid ${COLORS.divider}` }}>
        FROM
      </div>
      <div style={{ padding: '3px 8px', background: COLORS.headerBg, color: '#555', fontSize: 9, borderBottom: `1px solid ${COLORS.headerBorder}` }}>
        TO
      </div>

      {hunks.map((hunk, hi) => {
        const oldLines: (DiffLine | null)[] = []
        const newLines: (DiffLine | null)[] = []

        let i = 0
        while (i < hunk.lines.length) {
          const line = hunk.lines[i]
          if (line.type === 'context') {
            oldLines.push(line)
            newLines.push(line)
            i++
          } else {
            const run: DiffLine[] = []
            while (i < hunk.lines.length && hunk.lines[i].type !== 'context') {
              run.push(hunk.lines[i++])
            }
            const runRemoves = run.filter(l => l.type === 'remove')
            const runAdds = run.filter(l => l.type === 'add')
            const maxLen = Math.max(runRemoves.length, runAdds.length)
            for (let j = 0; j < maxLen; j++) {
              oldLines.push(runRemoves[j] ?? null)
              newLines.push(runAdds[j] ?? null)
            }
          }
        }

        return (
          <div key={hi} style={{ display: 'contents' }}>
            <div style={{ padding: '2px 8px', background: '#0a0e1a', color: '#4b6bfb', fontSize: 10, gridColumn: '1 / -1' }}>
              {hunk.header}
            </div>
            {oldLines.map((old, li) => {
              const nw = newLines[li]
              return [
                <div key={`old-${li}`} style={{
                  display: 'flex', gap: 6, padding: '1px 8px',
                  background: old?.type === 'remove' ? COLORS.removeBg : 'transparent',
                  borderRight: `1px solid ${COLORS.divider}`,
                }}>
                  <LineNo n={old?.oldLineNo ?? null} color={old?.type === 'remove' ? COLORS.removeLineNo : COLORS.lineNoText} />
                  <span style={{
                    color: old?.type === 'remove' ? COLORS.removeText : COLORS.contextText,
                    flex: 1, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                  }}>
                    {old?.content ?? ''}
                  </span>
                </div>,
                <div key={`new-${li}`} style={{
                  display: 'flex', gap: 6, padding: '1px 8px',
                  background: nw?.type === 'add' ? COLORS.addBg : 'transparent',
                }}>
                  <LineNo n={nw?.newLineNo ?? null} color={nw?.type === 'add' ? COLORS.addLineNo : COLORS.lineNoText} />
                  <span style={{
                    color: nw?.type === 'add' ? COLORS.addText : COLORS.contextText,
                    flex: 1, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                  }}>
                    {nw?.content ?? ''}
                  </span>
                </div>,
              ]
            })}
          </div>
        )
      })}
    </div>
  )
}

function WordsView({ hunks }: { hunks: DiffHunk[] }) {
  return (
    <div style={{ fontFamily: 'monospace', fontSize: 11 }}>
      {hunks.map((hunk, hi) => {
        const rows: React.ReactNode[] = []
        let i = 0
        while (i < hunk.lines.length) {
          const line = hunk.lines[i]
          if (line.type === 'context') {
            rows.push(
              <div key={i} style={{ display: 'flex', gap: 8, padding: '1px 8px' }}>
                <LineNo n={line.newLineNo} color={COLORS.lineNoText} />
                <span style={{ color: COLORS.contextText, whiteSpace: 'pre-wrap' }}>{line.content}</span>
              </div>
            )
            i++
          } else {
            const removes: DiffLine[] = []
            const adds: DiffLine[] = []
            while (i < hunk.lines.length && hunk.lines[i].type !== 'context') {
              if (hunk.lines[i].type === 'remove') removes.push(hunk.lines[i])
              else adds.push(hunk.lines[i])
              i++
            }
            const maxLen = Math.max(removes.length, adds.length)
            for (let j = 0; j < maxLen; j++) {
              const rem = removes[j]
              const add = adds[j]
              if (rem && add) {
                const parts = diffWords(rem.content, add.content)
                rows.push(
                  <div key={`add-${i}-${j}`} style={{ display: 'flex', gap: 8, padding: '1px 8px', background: COLORS.addBg }}>
                    <LineNo n={add.newLineNo} color={COLORS.addLineNo} />
                    <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                      {parts.map((part, pi) => (
                        <span key={pi} style={{
                          color: part.added ? COLORS.addText : part.removed ? 'transparent' : COLORS.contextText,
                          textDecoration: part.removed ? 'line-through' : 'none',
                          display: part.removed ? 'none' : 'inline',
                        }}>
                          {part.value}
                        </span>
                      ))}
                    </span>
                  </div>
                )
              } else if (rem) {
                rows.push(
                  <div key={`rem-${i}-${j}`} style={{ display: 'flex', gap: 8, padding: '1px 8px', background: COLORS.removeBg }}>
                    <LineNo n={rem.oldLineNo} color={COLORS.removeLineNo} />
                    <span style={{ color: COLORS.removeText, textDecoration: 'line-through', whiteSpace: 'pre-wrap' }}>{rem.content}</span>
                  </div>
                )
              } else if (add) {
                rows.push(
                  <div key={`add2-${i}-${j}`} style={{ display: 'flex', gap: 8, padding: '1px 8px', background: COLORS.addBg }}>
                    <LineNo n={add.newLineNo} color={COLORS.addLineNo} />
                    <span style={{ color: COLORS.addText, whiteSpace: 'pre-wrap' }}>{add.content}</span>
                  </div>
                )
              }
            }
          }
        }
        return (
          <div key={hi}>
            <div style={{ padding: '2px 8px', background: '#0a0e1a', color: '#4b6bfb', fontSize: 10 }}>
              {hunk.header}
            </div>
            {rows}
          </div>
        )
      })}
    </div>
  )
}

export function DiffViewer({ hunks, mode, filename, truncated }: Props) {
  const { additions, deletions } = useMemo(() => diffStats(hunks), [hunks])
  const isEmpty = hunks.length === 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', background: COLORS.bg }}>
      <div style={{
        padding: '4px 12px', background: '#060810',
        borderBottom: `1px solid ${COLORS.headerBorder}`,
        display: 'flex', gap: 16, alignItems: 'center', flexShrink: 0,
      }}>
        <span style={{ color: '#4CAF50', fontSize: 10 }}>+{additions} additions</span>
        <span style={{ color: '#ef4444', fontSize: 10 }}>−{deletions} deletions</span>
        <span style={{ color: '#555', fontSize: 10, marginLeft: 'auto' }}>{filename}</span>
        {truncated && <span style={{ color: '#FFC107', fontSize: 9 }}>diff truncated at 5000 lines</span>}
      </div>

      <div style={{ flex: 1, overflowY: 'auto' }}>
        {isEmpty ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 120, color: '#555', fontSize: 12, fontFamily: 'monospace' }}>
            No differences between selected commits
          </div>
        ) : mode === 'unified' ? (
          <UnifiedView hunks={hunks} />
        ) : mode === 'split' ? (
          <SplitView hunks={hunks} />
        ) : (
          <WordsView hunks={hunks} />
        )}
      </div>
    </div>
  )
}
