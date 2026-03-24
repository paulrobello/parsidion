'use client'

import { useState, useEffect, useCallback, useMemo, type CSSProperties } from 'react'
import { CommitList } from './CommitList'
import { DiffViewer } from './DiffViewer'
import type { DiffMode } from './DiffViewer'
import { parseDiff } from '@/lib/parseDiff'
import type { CommitEntry } from '@/app/api/note/history/route'
import type { NoteNode } from '@/lib/graph'

interface Props {
  stem: string
  node: NoteNode | null
  onClose: () => void
}

const BUTTON_BASE: CSSProperties = {
  padding: '2px 7px', fontSize: 9, cursor: 'pointer', border: 'none',
  fontFamily: "'JetBrains Mono', monospace",
}

export function HistoryView({ stem, node, onClose }: Props) {
  const [commits, setCommits] = useState<CommitEntry[]>([])
  const [loadingCommits, setLoadingCommits] = useState(true)
  const [commitsError, setCommitsError] = useState<string | null>(null)

  const [fromHash, setFromHash] = useState<string | null>(null)
  const [toHash, setToHash] = useState<string | null>(null)

  const [rawDiff, setRawDiff] = useState<string>('')
  const [truncated, setTruncated] = useState(false)
  const [loadingDiff, setLoadingDiff] = useState(false)
  const [diffError, setDiffError] = useState<string | null>(null)

  const [diffMode, setDiffMode] = useState<DiffMode>('split')

  // Load commit history on mount
  useEffect(() => {
    setLoadingCommits(true) // eslint-disable-line react-hooks/set-state-in-effect
    setCommitsError(null)
    fetch(`/api/note/history?stem=${encodeURIComponent(stem)}`)
      .then(r => r.json())
      .then(data => {
        if (data.error) { setCommitsError(data.error as string); return }
        const list: CommitEntry[] = data.commits ?? []
        setCommits(list)
        // Default: FROM = latest (index 0), TO = previous (index 1)
        if (list.length >= 2) {
          setFromHash(list[0].hash)
          setToHash(list[1].hash)
        } else if (list.length === 1) {
          setFromHash(list[0].hash)
          setToHash(null)
        }
      })
      .catch(e => setCommitsError((e as Error).message))
      .finally(() => setLoadingCommits(false))
  }, [stem])

  // Fetch diff whenever from/to changes; abort stale in-flight requests
  useEffect(() => {
    if (!fromHash || !toHash) { setRawDiff(''); return } // eslint-disable-line react-hooks/set-state-in-effect
    const controller = new AbortController()
    setLoadingDiff(true)
    setDiffError(null)
    fetch(`/api/note/diff?stem=${encodeURIComponent(stem)}&from=${encodeURIComponent(fromHash)}&to=${encodeURIComponent(toHash)}`, { signal: controller.signal })
      .then(r => r.json())
      .then(data => {
        if (data.error) { setDiffError(data.error as string); return }
        setRawDiff(data.diff ?? '')
        setTruncated(data.truncated ?? false)
      })
      .catch(e => { if ((e as Error).name !== 'AbortError') setDiffError((e as Error).message) })
      .finally(() => setLoadingDiff(false))
    return () => controller.abort()
  }, [stem, fromHash, toHash])

  const hunks = useMemo(() => parseDiff(rawDiff), [rawDiff])
  const filename = useMemo(() => node?.path.split('/').pop() ?? `${stem}.md`, [node?.path, stem])

  const handleSetFrom = useCallback((hash: string) => {
    setFromHash(hash)
    // If new FROM matches current TO, clear TO
    if (hash === toHash) setToHash(null)
  }, [toHash])

  const handleSetTo = useCallback((hash: string) => {
    setToHash(hash)
    if (hash === fromHash) setFromHash(null)
  }, [fromHash])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden', fontFamily: "'JetBrains Mono', monospace" }}>

      {/* HistoryView toolbar */}
      <div style={{
        height: 34, background: '#0a0e1a', borderBottom: '1px solid #1a2040',
        display: 'flex', alignItems: 'center', padding: '0 12px', gap: 10, flexShrink: 0,
      }}>
        <button onClick={onClose} style={{
          ...BUTTON_BASE,
          background: 'none', border: '1px solid #1a2040', borderRadius: 3,
          color: '#00FFC8',
        }}>
          ← Back
        </button>
        <span style={{ color: '#888', fontSize: 10 }}>{stem}</span>
        <span style={{ color: '#333', fontSize: 10 }}>—</span>
        <span style={{ color: '#4b6bfb', fontSize: 10 }}>Version History</span>

        <div style={{ flex: 1 }} />

        {/* Diff mode toggle */}
        <div style={{
          display: 'flex', gap: 1,
          background: '#0C0F1E', border: '1px solid #1a2040', borderRadius: 4, overflow: 'hidden',
        }}>
          {(['unified', 'split', 'words'] as DiffMode[]).map(m => (
            <button key={m} onClick={() => setDiffMode(m)} style={{
              ...BUTTON_BASE,
              background: diffMode === m ? '#4b6bfb' : 'transparent',
              color: diffMode === m ? '#0C0F1E' : '#555',
            }}>
              {m.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {/* Body: commit list + diff */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>

        {/* Left: commit list */}
        {loadingCommits ? (
          <div style={{ width: 240, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#555', fontSize: 11, background: '#080b16', borderRight: '1px solid #1a2040' }}>
            Loading history...
          </div>
        ) : commitsError ? (
          <div style={{ width: 240, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#ef4444', fontSize: 11, padding: 12, background: '#080b16', borderRight: '1px solid #1a2040', textAlign: 'center' }}>
            {commitsError}
          </div>
        ) : (
          <CommitList
            commits={commits}
            fromHash={fromHash}
            toHash={toHash}
            onSetFrom={handleSetFrom}
            onSetTo={handleSetTo}
          />
        )}

        {/* Right: diff viewer */}
        <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          {!fromHash || !toHash ? (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#555', fontSize: 12 }}>
              {commits.length <= 1 ? 'Only one version — no diff available.' : 'Select FROM and TO commits to compare.'}
            </div>
          ) : loadingDiff ? (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#555', fontSize: 12 }}>
              Loading diff...
            </div>
          ) : diffError ? (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#ef4444', fontSize: 12, padding: 24, textAlign: 'center' }}>
              {diffError}
            </div>
          ) : (
            <DiffViewer hunks={hunks} mode={diffMode} filename={filename} truncated={truncated} />
          )}
        </div>
      </div>
    </div>
  )
}
