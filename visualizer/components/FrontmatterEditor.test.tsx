/**
 * QA-006: render tests for FrontmatterEditor — keyboard handling in the
 * ChipInput used for `tags` (and `sources`): Enter commits a normalized
 * tag, Backspace on an empty input removes the last chip, and the
 * type/date selectors update the fields object.
 *
 * The editor is fully controlled (fields in, onChange out), so the tests
 * mount it inside a stateful harness and assert on the harness state —
 * the same contract the real caller (NoteEditor) provides.
 */
import { describe, test, expect, beforeEach } from 'bun:test'
import { useState } from 'react'
import { render, screen, cleanup } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FrontmatterEditor } from './FrontmatterEditor'
import type { FrontmatterFields } from '@/lib/frontmatter'
import type { NoteNode } from '@/lib/graph'

const INITIAL: FrontmatterFields = {
  type: 'pattern',
  date: '2026-01-01',
  tags: [],
  related: [],
  sources: [],
  confidence: 'high',
  project: '',
  extra: '',
}

const NODES: NoteNode[] = [
  {
    id: 'note-a',
    title: 'Note A',
    type: 'pattern',
    tags: ['python', 'typescript'],
    path: 'Patterns/note-a.md',
  } as NoteNode,
]

function Harness({ initial }: { initial: FrontmatterFields }) {
  const [fields, setFields] = useState(initial)
  return (
    <>
      <FrontmatterEditor fields={fields} onChange={setFields} nodes={NODES} />
      <div data-testid="fields">{JSON.stringify(fields)}</div>
    </>
  )
}

function currentFields(): FrontmatterFields {
  return JSON.parse(screen.getByTestId('fields').textContent ?? '{}')
}

beforeEach(() => {
  cleanup()
})

describe('FrontmatterEditor ChipInput (tags)', () => {
  test('Enter commits the typed tag, normalized to kebab-case', async () => {
    const user = userEvent.setup()
    render(<Harness initial={INITIAL} />)
    const input = screen.getByPlaceholderText('add tag…')
    await user.type(input, 'My Cool Tag{Enter}')
    // Input cleared and the chip rendered.
    expect((input as HTMLInputElement).value).toBe('')
    expect(screen.getByText('my-cool-tag')).toBeDefined()
    expect(currentFields().tags).toEqual(['my-cool-tag'])
  })

  test('comma also commits a tag', async () => {
    const user = userEvent.setup()
    render(<Harness initial={INITIAL} />)
    await user.type(screen.getByPlaceholderText('add tag…'), 'vault,')
    expect(currentFields().tags).toEqual(['vault'])
  })

  test('Backspace on an empty input removes the last chip', async () => {
    const user = userEvent.setup()
    render(<Harness initial={INITIAL} />)
    const input = screen.getByPlaceholderText('add tag…')
    await user.type(input, 'python{Enter}')
    await user.type(input, 'vault{Enter}')
    expect(screen.getByText('vault')).toBeDefined()
    await user.type(input, '{Backspace}') // empty input → removes 'vault'
    expect(screen.queryByText('vault')).toBeNull()
    expect(currentFields().tags).toEqual(['python'])
  })

  test('duplicate tags are not added twice', async () => {
    const user = userEvent.setup()
    render(<Harness initial={INITIAL} />)
    // Hold one element reference: the placeholder disappears once a chip
    // exists, so getByPlaceholderText cannot be re-queried after the add.
    const input = screen.getByPlaceholderText('add tag…')
    await user.type(input, 'python{Enter}')
    await user.type(input, 'Python{Enter}')
    expect(currentFields().tags).toEqual(['python'])
  })
})

describe('FrontmatterEditor selectors', () => {
  test('clicking a type button updates fields.type', async () => {
    const user = userEvent.setup()
    render(<Harness initial={INITIAL} />)
    await user.click(screen.getByRole('button', { name: 'debugging' }))
    expect(currentFields().type).toBe('debugging')
  })

  test('editing the date field updates fields.date', async () => {
    const user = userEvent.setup()
    render(<Harness initial={INITIAL} />)
    const date = screen.getByPlaceholderText('YYYY-MM-DD')
    await user.clear(date)
    await user.type(date, '2026-02-03')
    expect(currentFields().date).toBe('2026-02-03')
  })

  test('autocomplete suggestions come from graph tags', async () => {
    const user = userEvent.setup()
    render(<Harness initial={INITIAL} />)
    await user.type(screen.getByPlaceholderText('add tag…'), 'py')
    // The dropdown item's text is split across spans (matched substring is
    // highlighted), so match on full textContent. Both the dropdown
    // container and the option row match; the option row is the last in
    // tree order (the container's first child).
    const matches = await screen.findAllByText(
      (_, el) => el?.textContent === 'python',
    )
    await user.click(matches.at(-1)!)
    expect(currentFields().tags).toEqual(['python'])
  })
})
