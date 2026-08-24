/**
 * QA-006: state-transition tests for the useNoteTabs slice — open/close/
 * switch tab transitions, the content cache contract (save warms it, a
 * later fetch hits it), and history-mode view restoration.
 */
import { describe, test, expect, vi, beforeEach, afterEach } from 'bun:test'
import { renderHook, act, cleanup } from '@testing-library/react'
import { useNoteTabs } from './useNoteTabs'
import type { GraphData, NoteNode } from './graph'

function node(id: string, path: string): NoteNode {
  return {
    id,
    title: id,
    type: 'pattern',
    folder: path.split('/')[0],
    path,
    tags: [],
    incoming_links: 0,
    mtime: 1700000000000,
  }
}

const GRAPH: GraphData = {
  nodes: [
    node('note-a', 'Patterns/note-a.md'),
    node('note-b', 'Patterns/note-b.md'),
    node('note-c', 'Debugging/note-c.md'),
    // Same filename as note-a in a different folder — exercises the
    // filename→stem lookup preferring the first-seen stem.
    node('note-d', 'Projects/note-a.md'),
  ],
  links: [],
} as unknown as GraphData

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const originalFetch = globalThis.fetch

beforeEach(() => {
  cleanup()
  localStorage.clear()
})

afterEach(() => {
  globalThis.fetch = originalFetch
  vi.restoreAllMocks()
})

function setupTabs(selectedVault: string | null = 'default') {
  return renderHook(() => useNoteTabs({ graphData: GRAPH, selectedVault }))
}

describe('useNoteTabs — tab transitions', () => {
  test('opening the first note opens and activates a tab', () => {
    const { result } = setupTabs()
    act(() => result.current.openNote('note-a', false))
    expect(result.current.openTabs).toEqual(['note-a'])
    expect(result.current.activeTab).toBe('note-a')
    expect(result.current.activeNode?.id).toBe('note-a')
  })

  test('opening an already-open note only switches to it', () => {
    const { result } = setupTabs()
    act(() => result.current.openNote('note-a', false))
    act(() => result.current.openNote('note-b', true))
    expect(result.current.openTabs).toEqual(['note-a', 'note-b'])
    act(() => result.current.openNote('note-a', false))
    expect(result.current.openTabs).toEqual(['note-a', 'note-b'])
    expect(result.current.activeTab).toBe('note-a')
  })

  test('newTab=false replaces the current tab; newTab=true appends', () => {
    const { result } = setupTabs()
    act(() => result.current.openNote('note-a', false))
    act(() => result.current.openNote('note-b', false)) // replaces note-a
    expect(result.current.openTabs).toEqual(['note-b'])
    act(() => result.current.openNote('note-c', true)) // appends
    expect(result.current.openTabs).toEqual(['note-b', 'note-c'])
    expect(result.current.activeTab).toBe('note-c')
  })

  test('closing the active tab activates the neighbor', () => {
    const { result } = setupTabs()
    act(() => result.current.openNote('note-a', true))
    act(() => result.current.openNote('note-b', true))
    act(() => result.current.openNote('note-c', true))
    act(() => result.current.closeTab('note-b')) // closes the middle tab
    expect(result.current.openTabs).toEqual(['note-a', 'note-c'])
    // Active was note-c already (last opened) — closing a non-active tab
    // must not move it.
    expect(result.current.activeTab).toBe('note-c')
    act(() => result.current.closeTab('note-c'))
    // The closed tab was active at index 2 of [a,c]; the neighbor (note-a)
    // takes over.
    expect(result.current.activeTab).toBe('note-a')
  })

  test('switchTab changes the active stem', () => {
    const { result } = setupTabs()
    act(() => result.current.openNote('note-a', true))
    act(() => result.current.openNote('note-b', true))
    act(() => result.current.switchTab('note-a'))
    expect(result.current.activeTab).toBe('note-a')
    expect(result.current.activeNode?.id).toBe('note-a')
  })

  test('wikilink stems resolve through the filename lookup', () => {
    const { result } = setupTabs()
    expect(result.current.resolveWikilink('note-b')).toBe('note-b')
    // "note-a" as a filename maps to the first node with that id.
    expect(result.current.resolveWikilink('note-a')).toBe('note-a')
    expect(result.current.resolveWikilink('no-such-stem')).toBeNull()
  })
})

describe('useNoteTabs — content cache contract', () => {
  test('fetchNoteContent hits the network once, then serves from cache', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ content: 'note body', mtimeMs: 42 }),
    )
    globalThis.fetch = fetchMock as unknown as typeof fetch
    const { result } = setupTabs()

    const first = await result.current.fetchNoteContent('note-a')
    expect(first.fromCache).toBeFalse()
    expect(first.content).toBe('note body')
    expect(first.mtimeMs).toBe(42)

    const second = await result.current.fetchNoteContent('note-a')
    expect(second.fromCache).toBeTrue()
    expect(second.mtimeMs).toBe(42)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  test('saveNote posts the note and warms the cache with the new mtime', async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({ ok: true, mtimeMs: 99 }),
    )
    globalThis.fetch = fetchMock as unknown as typeof fetch
    const { result } = setupTabs()

    const res = await result.current.saveNote('note-a', 'new body', 42)
    expect(res).toEqual({ ok: true, mtimeMs: 99 })
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/note')
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body as string)).toEqual({
      stem: 'note-a',
      content: 'new body',
      baseMtimeMs: 42,
      vault: 'default',
    })

    const cached = await result.current.fetchNoteContent('note-a')
    expect(cached.fromCache).toBeTrue()
    expect(cached.content).toBe('new body')
    expect(cached.mtimeMs).toBe(99)
  })

  test('saveNote surfaces a 409 as a conflict result, not an error', async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({ conflict: true, serverContent: 'server body', mtimeMs: 7 }, 409),
    ) as unknown as typeof fetch
    const { result } = setupTabs()
    const res = await result.current.saveNote('note-a', 'my body', 1)
    expect(res).toEqual({ conflict: true, serverContent: 'server body', mtimeMs: 7 })
  })

  test('invalidateNote forces the next fetch back to the network', async () => {
    let call = 0
    globalThis.fetch = vi.fn(async () => {
      call += 1
      return jsonResponse({ content: `body ${call}`, mtimeMs: call })
    }) as unknown as typeof fetch
    const { result } = setupTabs()
    await result.current.fetchNoteContent('note-a')
    act(() => result.current.invalidateNote('note-a', 'Patterns/note-a.md'))
    const fresh = await result.current.fetchNoteContent('note-a')
    expect(fresh.fromCache).toBeFalse()
    expect(fresh.content).toBe('body 2')
  })
})

describe('useNoteTabs — history mode', () => {
  test('openHistory remembers the prior view mode; closeHistory restores it', () => {
    const { result } = setupTabs()
    act(() => result.current.setViewMode('graph'))
    act(() => result.current.openHistory('note-a', 'Patterns/note-a.md'))
    expect(result.current.historyMode).toBeTrue()
    expect(result.current.historyNote).toBe('note-a')
    expect(result.current.historyPath).toBe('Patterns/note-a.md')
    act(() => result.current.closeHistory())
    expect(result.current.historyMode).toBeFalse()
    expect(result.current.viewMode).toBe('graph')
  })

  test('openHistory while already in history mode swaps the note only', () => {
    const { result } = setupTabs()
    act(() => result.current.setViewMode('read'))
    act(() => result.current.openHistory('note-a'))
    act(() => result.current.openHistory('note-b'))
    expect(result.current.historyNote).toBe('note-b')
    expect(result.current.historyMode).toBeTrue()
  })
})

describe('useNoteTabs — vault reset', () => {
  test('_resetForVaultChange clears tabs, active stem, and focus', () => {
    const { result } = setupTabs()
    act(() => result.current.openNote('note-a', true))
    act(() => result.current.setNeighborhoodCenter('note-a'))
    act(() => result.current._resetForVaultChange())
    expect(result.current.openTabs).toEqual([])
    expect(result.current.activeTab).toBeNull()
    expect(result.current.neighborhoodCenter).toBeNull()
  })
})
