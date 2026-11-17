'use client'

import { useEffect, useRef, useState, type Dispatch, type RefObject, type SetStateAction, type TransitionStartFunction } from 'react'

/** Shape of the vault API's note-content response. */
export interface NoteContentResult {
  content: string
  mtimeMs?: number
  fromCache: boolean
}

export type FetchContentFn = (stem: string, path?: string) => Promise<NoteContentResult>

interface UseNoteContentOptions {
  /** Stable note identifier (path || id) — null when no note is selected. */
  noteKey: string | null
  /** The selected note's fetch stem and optional path; read inside effects. */
  noteId: string | null
  notePath?: string
  fetchContent: FetchContentFn
  /** startTransition from the owner — fetches stay non-blocking. */
  startTransition: TransitionStartFunction
  /** Counter incremented by the WebSocket file-watcher; 0 disables refresh. */
  refreshTrigger: number
  /** While editing, a refresh marks the note externally modified instead of reloading. */
  isEditing: boolean
  /** Called when a new note starts loading (the owner resets edit state here). */
  onNoteChange: () => void
  /** Ref of the scrolling container, for scroll restoration after a refresh. */
  scrollRef: RefObject<HTMLDivElement | null>
}

export interface UseNoteContentResult {
  content: string
  setContent: Dispatch<SetStateAction<string>>
  error: string | null
  baseMtime: number | undefined
  setBaseMtime: Dispatch<SetStateAction<number | undefined>>
  externallyModified: boolean
  setExternallyModified: Dispatch<SetStateAction<boolean>>
}

/**
 * Note-content loading for the reading pane (ARC-204): initial fetch keyed on
 * the note, watcher-driven refresh keyed on refreshTrigger, and scroll
 * restoration. The two fetch bodies were previously duplicated inline in
 * ReadingPane; they differ only in error handling (initial load surfaces the
 * error, a refresh ignores it) and in the edit-state interaction (a refresh
 * while editing flags the note externally modified instead of reloading).
 */
export function useNoteContent({
  noteKey,
  noteId,
  notePath,
  fetchContent,
  startTransition,
  refreshTrigger,
  isEditing,
  onNoteChange,
  scrollRef,
}: UseNoteContentOptions): UseNoteContentResult {
  const [content, setContent] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  // Conflict-detection token: the server's mtimeMs for the note as currently loaded.
  const [baseMtime, setBaseMtime] = useState<number | undefined>(undefined)
  const [externallyModified, setExternallyModified] = useState(false)
  const savedScrollRef = useRef(0)

  useEffect(() => {
    if (noteId === null) return
    onNoteChange()
    let cancelled = false
    startTransition(async () => {
      try {
        const { content: c, mtimeMs } = await fetchContent(noteId, notePath)
        if (!cancelled) {
          setContent(c)
          setBaseMtime(mtimeMs)
          setError(null)
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message)
      }
    })
    return () => { cancelled = true }
  // Keyed on noteKey (not the node object) so a same-note reload with a new object
  // identity doesn't re-run this effect and reset isEditing / refetch content.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [noteKey, fetchContent])

  useEffect(() => {
    if (refreshTrigger === 0 || noteId === null) return
    if (isEditing) {
      setExternallyModified(true)
      return
    }
    savedScrollRef.current = scrollRef.current?.scrollTop ?? 0
    let cancelled = false
    startTransition(async () => {
      try {
        const { content: c, mtimeMs } = await fetchContent(noteId, notePath)
        if (!cancelled) {
          setContent(c)
          setBaseMtime(mtimeMs)
          setError(null)
        }
      } catch { /* ignore refresh errors */ }
    })
    return () => { cancelled = true }
  // QA-017: Only re-fetches when refreshTrigger changes (a counter incremented
  // by the WebSocket file-watcher).  Including `stem`, `vault`, or `setContent`
  // would cause re-fetches when switching tabs, which is handled by the initial
  // load effect above this one.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshTrigger])

  useEffect(() => {
    if (savedScrollRef.current > 0 && scrollRef.current) {
      scrollRef.current.scrollTop = savedScrollRef.current
      savedScrollRef.current = 0
    }
  }, [content, scrollRef])

  return {
    content,
    setContent,
    error,
    baseMtime,
    setBaseMtime,
    externallyModified,
    setExternallyModified,
  }
}
