'use client'

import type { CommitEntry } from '@/lib/types'

interface Props {
  commits: CommitEntry[]
  fromHash: string | null
  toHash: string | null
  onSetFrom: (hash: string) => void
  onSetTo: (hash: string) => void
}

function relativeTime(isoDate: string): string {
  const diff = Date.now() - new Date(isoDate).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  if (days < 30) return `${days}d ago`
  return new Date(isoDate).toLocaleDateString()
}

export function CommitList({ commits, fromHash, toHash, onSetFrom, onSetTo }: Props) {
  const isSingleCommit = commits.length <= 1

  return (
    <div style={{
      width: 240, background: '#080b16',
      borderRight: '1px solid #1a2040',
      display: 'flex', flexDirection: 'column',
      overflow: 'hidden', flexShrink: 0,
    }}>
      {/* Header */}
      <div style={{
        padding: '6px 10px', borderBottom: '1px solid #111827', flexShrink: 0,
        color: '#555', fontSize: 9, letterSpacing: '1px',
        fontFamily: "'JetBrains Mono', monospace",
      }}>
        COMMITS · {commits.length} total
      </div>

      {/* List */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '6px 8px' }}>
        {commits.length === 0 && (
          <div style={{ color: '#555', fontSize: 11, padding: 8, fontFamily: 'monospace' }}>
            No version history found.
          </div>
        )}

        {commits.map((commit, idx) => {
          const isFrom = commit.hash === fromHash
          const isTo = commit.hash === toHash

          return (
            <div key={commit.hash} style={{
              padding: '6px 8px', borderRadius: 4, marginBottom: 4,
              border: `1px solid ${isFrom ? '#1e3a5f' : isTo ? '#2d4a1e' : 'transparent'}`,
              background: isFrom ? 'rgba(75,107,251,0.08)' : isTo ? 'rgba(76,175,80,0.06)' : 'transparent',
              fontFamily: 'monospace',
            }}>
              {/* FROM/TO badges row */}
              <div style={{ display: 'flex', gap: 4, marginBottom: 3, alignItems: 'center' }}>
                <button
                  onClick={() => { if (commit.hash !== toHash) onSetFrom(commit.hash) }}
                  disabled={commit.hash === toHash}
                  style={{
                    padding: '1px 5px', borderRadius: 2, fontSize: 8,
                    cursor: commit.hash === toHash ? 'not-allowed' : 'pointer',
                    background: isFrom ? '#1e3a5f' : 'rgba(75,107,251,0.1)',
                    color: isFrom ? '#4b6bfb' : '#555',
                    border: `1px solid ${isFrom ? '#4b6bfb' : '#1a2040'}`,
                    fontWeight: isFrom ? 700 : 400,
                    opacity: commit.hash === toHash ? 0.3 : 1,
                  }}
                >
                  FROM
                </button>
                <button
                  onClick={() => { if (!isSingleCommit && commit.hash !== fromHash) onSetTo(commit.hash) }}
                  disabled={isSingleCommit || commit.hash === fromHash}
                  style={{
                    padding: '1px 5px', borderRadius: 2, fontSize: 8,
                    cursor: (isSingleCommit || commit.hash === fromHash) ? 'not-allowed' : 'pointer',
                    background: isTo ? '#2d4a1e' : 'rgba(76,175,80,0.1)',
                    color: isTo ? '#4CAF50' : '#555',
                    border: `1px solid ${isTo ? '#4CAF50' : '#1a2040'}`,
                    fontWeight: isTo ? 700 : 400,
                    opacity: (isSingleCommit || commit.hash === fromHash) ? 0.3 : 1,
                  }}
                >
                  TO
                </button>
                <span style={{ color: '#555', fontSize: 9, marginLeft: 'auto', fontFamily: 'monospace' }}>
                  {commit.shortHash}
                </span>
              </div>

              {/* Commit message */}
              <div style={{
                color: (isFrom || isTo) ? '#aaa' : '#666',
                fontSize: 10, marginBottom: 2,
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
              }}>
                {commit.message || '(no message)'}
              </div>

              {/* Timestamp */}
              <div style={{ color: '#444', fontSize: 9 }}>
                {relativeTime(commit.date)}
                {idx === 0 && <span style={{ color: '#555', marginLeft: 6 }}>· latest</span>}
              </div>
            </div>
          )
        })}

        {commits.length === 1 && (
          <div style={{ color: '#555', fontSize: 10, padding: '4px 8px', fontFamily: 'monospace' }}>
            Only one version — no diff available.
          </div>
        )}
      </div>
    </div>
  )
}
