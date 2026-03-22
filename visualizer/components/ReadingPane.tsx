'use client'

import { useState, useEffect, useCallback, useTransition } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { getNodeColor } from '@/lib/sigma-colors'
import type { NoteNode } from '@/lib/graph'

interface Props {
  node: NoteNode | null
  fetchContent: (stem: string) => Promise<string>
  onNavigate: (stem: string, newTab: boolean) => void
}

export function ReadingPane({ node, fetchContent, onNavigate }: Props) {
  const [content, setContent] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [isPending, startTransition] = useTransition()

  useEffect(() => {
    if (!node) return
    let cancelled = false
    startTransition(async () => {
      try {
        const c = await fetchContent(node.id)
        if (!cancelled) { setContent(c); setError(null) }
      } catch (e) {
        if (!cancelled) setError((e as Error).message)
      }
    })
    return () => { cancelled = true }
  }, [node, fetchContent])

  const handleWikilink = useCallback((stem: string, e: React.MouseEvent) => {
    onNavigate(stem, e.metaKey || e.ctrlKey)
  }, [onNavigate])

  if (!node) {
    return (
      <div style={{
        flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
        color: '#6b7a99', fontFamily: "'JetBrains Mono', monospace", fontSize: 12,
        flexDirection: 'column', gap: 8,
      }}>
        <div style={{ fontSize: 24, opacity: 0.3 }}>◈</div>
        <div>Open a note from the sidebar or press ⌘K to search.</div>
      </div>
    )
  }

  const fm = content.match(/^---\n([\s\S]*?)\n---/)
  const fmBody = fm?.[1] ?? ''
  const noteDate = fmBody.match(/^date:\s*(.+)$/m)?.[1]?.trim() ?? null
  const confidence = fmBody.match(/^confidence:\s*(.+)$/m)?.[1]?.trim() ?? null
  const relatedStems: string[] = (() => {
    const relatedLine = fmBody.match(/^related:\s*(.+)$/m)
    if (!relatedLine) return []
    const stems: string[] = []
    const re = /\[\[([^\]]+)\]\]/g
    let m: RegExpExecArray | null
    while ((m = re.exec(relatedLine[1])) !== null) stems.push(m[1])
    return stems
  })()

  const displayContent = content
    .replace(/^---[\s\S]*?---\n/, '')
    .replace(/\[\[([^\]]+)\]\]/g, (_, stem) => `[${stem}](wikilink:${encodeURIComponent(stem)})`)

  return (
    <div style={{ flex: 1, overflow: 'auto', padding: '32px 48px', fontFamily: "'Syne', sans-serif" }}>
      <div style={{ maxWidth: 720, margin: '0 auto' }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
          <span style={{
            background: `${getNodeColor(node.type)}22`,
            border: `1px solid ${getNodeColor(node.type)}55`,
            color: getNodeColor(node.type),
            padding: '2px 8px', borderRadius: 3,
            fontSize: 10, fontFamily: "'Oxanium', sans-serif",
            fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em',
          }}>
            {node.type}
          </span>
          {noteDate && <span style={{ color: '#6b7a99', fontSize: 11 }}>{noteDate}</span>}
          {confidence && (
            <>
              <span style={{ color: '#6b7a99', fontSize: 11 }}>·</span>
              <span style={{
                color: confidence === 'high' ? '#10b981' : confidence === 'medium' ? '#f59e0b' : '#6b7a99',
                fontSize: 11,
              }}>
                {confidence} confidence
              </span>
            </>
          )}
        </div>

        <h1 style={{
          fontFamily: "'Oxanium', sans-serif",
          fontSize: 24, fontWeight: 700,
          color: '#e8e8f0', lineHeight: 1.3,
          margin: '0 0 12px',
        }}>
          {node.title}
        </h1>

        <div style={{ display: 'flex', gap: 6, marginBottom: 20, flexWrap: 'wrap' }}>
          {node.tags.map(tag => (
            <span key={tag} style={{
              background: '#1e293b', color: '#9ca3af',
              padding: '2px 8px', borderRadius: 12, fontSize: 10,
            }}>
              #{tag}
            </span>
          ))}
        </div>

        {isPending && (
          <div style={{ color: '#6b7a99', fontFamily: "'JetBrains Mono', monospace", fontSize: 12, paddingTop: 20 }}>
            Loading...
          </div>
        )}
        {error && (
          <div style={{ color: '#ef4444', fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }}>
            Could not load note: {node.id}
          </div>
        )}

        {!isPending && !error && relatedStems.length > 0 && (
          <div style={{
            marginBottom: 16, padding: '8px 12px',
            background: 'rgba(123,97,255,0.06)',
            border: '1px solid rgba(123,97,255,0.15)',
            borderRadius: 6,
          }}>
            <div style={{
              fontSize: 9, fontFamily: "'Oxanium', sans-serif", color: '#6b7a99',
              textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6,
            }}>Related</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {relatedStems.map(stem => (
                <span
                  key={stem}
                  className="wikilink"
                  onClick={(e) => handleWikilink(stem, e)}
                  style={{ fontSize: 12 }}
                >
                  {stem}
                </span>
              ))}
            </div>
          </div>
        )}

        {!isPending && !error && displayContent && (
          <div className="note-markdown">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                a: ({ href, children }: any) => {
                  if (href?.startsWith('wikilink:')) {
                    const stem = decodeURIComponent(href.slice(9))
                    return (
                      <span
                        className="wikilink"
                        onClick={(e: React.MouseEvent) => handleWikilink(stem, e)}
                      >
                        {children}
                      </span>
                    )
                  }
                  return <a href={href} target="_blank" rel="noreferrer">{children}</a>
                },
              }}
            >
              {displayContent}
            </ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  )
}
