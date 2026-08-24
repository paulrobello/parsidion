/**
 * QA-006: render tests for ReadingPane — the note-reading container.
 *
 * ReadingPane owns the edit/save/conflict loop: fetch content on node
 * change, enter edit mode, serialize frontmatter + body on save, and
 * surface a 409-style conflict through ConflictDialog. The data props
 * (fetchContent / onSave / onDelete / onNavigate / onOpenHistory) are
 * injected, so the tests drive the loop with spies instead of a server.
 */
import { describe, test, expect, vi, beforeEach } from 'bun:test'
import { render, screen, waitFor, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ReadingPane } from './ReadingPane'
import type { NoteNode } from '@/lib/graph'

const NODE: NoteNode = {
  id: 'my-note',
  title: 'My Note',
  type: 'pattern',
  folder: 'Patterns',
  path: 'Patterns/my-note.md',
  tags: ['python', 'vault'],
  incoming_links: 2,
  mtime: 1700000000000,
}

const CONTENT = [
  '---',
  'type: pattern',
  'date: 2026-01-01',
  'tags: [python]',
  'related: ["[[other-note]]"]',
  '---',
  '# Heading',
  '',
  'Body text with a [[other-note]] link.',
].join('\n')

function makeHandlers(overrides: Partial<Parameters<typeof makeProps>[0]> = {}) {
  return makeProps(overrides)
}

function makeProps(overrides: {
  fetchContent?: ReturnType<typeof vi.fn>
  onSave?: ReturnType<typeof vi.fn>
} = {}) {
  const fetchContent =
    overrides.fetchContent ??
    vi.fn(async () => ({
      content: CONTENT,
      mtimeMs: 1000,
      fromCache: false,
    }))
  const onSave =
    overrides.onSave ??
    (vi.fn(async () => ({ ok: true as const, mtimeMs: 2000 })))
  const onDelete = vi.fn(async () => {})
  const onNavigate = vi.fn()
  const onOpenHistory = vi.fn()
  return { fetchContent, onSave, onDelete, onNavigate, onOpenHistory }
}

beforeEach(() => {
  cleanup()
  localStorage.clear()
})

describe('ReadingPane', () => {
  test('renders empty state when no node is selected', () => {
    const p = makeHandlers()
    render(
      <ReadingPane
        node={null}
        nodes={[]}
        fetchContent={p.fetchContent}
        onSave={p.onSave}
        onDelete={p.onDelete}
        onNavigate={p.onNavigate}
        onOpenHistory={p.onOpenHistory}
      />,
    )
    expect(
      screen.getByText(/Open a note from the sidebar/i),
    ).toBeDefined()
    expect(p.fetchContent).not.toHaveBeenCalled()
  })

  test('renders title, tags, and body once content resolves', async () => {
    const p = makeHandlers()
    render(
      <ReadingPane
        node={NODE}
        nodes={[NODE]}
        fetchContent={p.fetchContent}
        onSave={p.onSave}
        onDelete={p.onDelete}
        onNavigate={p.onNavigate}
        onOpenHistory={p.onOpenHistory}
      />,
    )
    expect(await screen.findByText('My Note')).toBeDefined()
    expect(screen.getByText('#python')).toBeDefined()
    expect(screen.getByText('#vault')).toBeDefined()
    // Frontmatter is stripped from the rendered body.
    expect(screen.queryByText(/type: pattern/)).toBeNull()
  })

  test('edit → save calls onSave with serialized content and base mtime', async () => {
    const user = userEvent.setup()
    const p = makeHandlers()
    render(
      <ReadingPane
        node={NODE}
        nodes={[NODE]}
        fetchContent={p.fetchContent}
        onSave={p.onSave}
        onDelete={p.onDelete}
        onNavigate={p.onNavigate}
        onOpenHistory={p.onOpenHistory}
      />,
    )
    await user.click(await screen.findByTitle('Edit note (⌘E)'))
    const save = await screen.findByRole('button', { name: 'Save' })
    await user.click(save)
    await waitFor(() => {
      expect(p.onSave).toHaveBeenCalledTimes(1)
    })
    const [stem, savedContent, baseMtime] = p.onSave.mock.calls[0] as [
      string,
      string,
      number | undefined,
      string | undefined,
    ]
    expect(stem).toBe('my-note')
    expect(baseMtime).toBe(1000)
    // Serialized round-trip: frontmatter block + original body.
    expect(savedContent.startsWith('---')).toBeTrue()
    expect(savedContent).toContain('# Heading')
    // Save succeeded → edit mode closes and the Save button is gone.
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Save' })).toBeNull()
    })
  })

  test('save conflict surfaces the ConflictDialog with the server content', async () => {
    const user = userEvent.setup()
    const p = makeHandlers({
      onSave: vi.fn(async () => ({
        conflict: true as const,
        serverContent: '---\ntype: pattern\n---\nServer changed this.',
        mtimeMs: 3000,
      })),
    })
    render(
      <ReadingPane
        node={NODE}
        nodes={[NODE]}
        fetchContent={p.fetchContent}
        onSave={p.onSave}
        onDelete={p.onDelete}
        onNavigate={p.onNavigate}
        onOpenHistory={p.onOpenHistory}
      />,
    )
    await user.click(await screen.findByTitle('Edit note (⌘E)'))
    await user.click(await screen.findByRole('button', { name: 'Save' }))
    expect(
      await screen.findByLabelText('Edit conflict for my-note.md'),
    ).toBeDefined()
    expect(screen.getByText(/Server changed this/)).toBeDefined()
  })

  test('delete confirmation flow calls onDelete with stem and path', async () => {
    const user = userEvent.setup()
    const p = makeHandlers()
    render(
      <ReadingPane
        node={NODE}
        nodes={[NODE]}
        fetchContent={p.fetchContent}
        onSave={p.onSave}
        onDelete={p.onDelete}
        onNavigate={p.onNavigate}
        onOpenHistory={p.onOpenHistory}
      />,
    )
    await user.click(await screen.findByTitle('Delete note'))
    // Disambiguate the dialog's confirm button from the toolbar Delete
    // button by scoping to the dialog role.
    const dialog = await screen.findByRole('dialog')
    const confirmBtn = Array.from(dialog.querySelectorAll('button')).find(
      b => b.textContent === 'Delete',
    )!
    await user.click(confirmBtn)
    await waitFor(() => {
      expect(p.onDelete).toHaveBeenCalledWith('my-note', 'Patterns/my-note.md')
    })
  })
})
