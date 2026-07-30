'use client'

import type { ComponentPropsWithoutRef } from 'react'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'

// react-markdown v10's defaultUrlTransform strips unrecognized protocols (like our
// wikilink: pseudo-protocol) to an empty href before the custom `a` component runs.
// Pass wikilink: URLs through untouched so the custom handler below still sees them.
function urlTransform(url: string): string {
  return url.startsWith('wikilink:') ? url : defaultUrlTransform(url)
}

interface Props {
  content: string
  onWikilink: (stem: string, e: React.MouseEvent) => void
}

export function NoteMarkdown({ content, onWikilink }: Props) {
  return (
    <div className="note-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={urlTransform}
        components={{
          a: ({ href, children }: ComponentPropsWithoutRef<'a'>) => {
            if (href?.startsWith('wikilink:')) {
              const stem = decodeURIComponent(href.slice(9))
              return (
                <span
                  className="wikilink"
                  onClick={(e: React.MouseEvent) => onWikilink(stem, e)}
                >
                  {children}
                </span>
              )
            }
            return <a href={href} target="_blank" rel="noreferrer">{children}</a>
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
