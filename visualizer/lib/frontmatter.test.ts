import { describe, it, expect } from 'bun:test'
import { parseFrontmatter, serializeFrontmatter, defaultFields } from './frontmatter'

describe('parseFrontmatter / serializeFrontmatter', () => {
  it('round-trips a note with only known fields byte-stably', () => {
    const content = [
      '---',
      'date: 2026-01-01',
      'type: pattern',
      'tags: [a, b]',
      'confidence: high',
      'sources: []',
      'related: ["[[note-one]]"]',
      '---',
      '',
      '# Title\nBody text\n',
    ].join('\n')

    const { fields, body } = parseFrontmatter(content)
    expect(fields.extra).toBe('')
    expect(serializeFrontmatter(fields, body)).toBe(content)
  })

  it('preserves unrecognized frontmatter fields through an edit-save cycle', () => {
    const content = [
      '---',
      'date: 2026-01-01',
      'type: research',
      'tags: [x]',
      'confidence: medium',
      'sources: []',
      'related: []',
      'provenance: explicit',
      'session_id: abc-123',
      '---',
      '',
      'Body\n',
    ].join('\n')

    const { fields, body } = parseFrontmatter(content)
    expect(fields.extra).toContain('provenance: explicit')
    expect(fields.extra).toContain('session_id: abc-123')

    // Simulate an edit (e.g. changing confidence) then save
    const edited = { ...fields, confidence: 'high' }
    const saved = serializeFrontmatter(edited, body)
    expect(saved).toContain('provenance: explicit')
    expect(saved).toContain('session_id: abc-123')
    expect(saved).toContain('confidence: high')

    // Round-tripping the saved content should preserve the same unknown fields
    const reparsed = parseFrontmatter(saved)
    expect(reparsed.fields.extra).toContain('provenance: explicit')
    expect(reparsed.fields.extra).toContain('session_id: abc-123')
  })

  it('preserves a block-style YAML list under an unrecognized key', () => {
    const content = [
      '---',
      'date: 2026-01-01',
      'type: pattern',
      'tags: []',
      'confidence: low',
      'sources: []',
      'related: []',
      'custom_list:',
      '  - one',
      '  - two',
      '---',
      '',
      'Body\n',
    ].join('\n')

    const { fields, body } = parseFrontmatter(content)
    expect(fields.extra).toBe('custom_list:\n  - one\n  - two')
    const saved = serializeFrontmatter(fields, body)
    expect(saved).toContain('custom_list:\n  - one\n  - two')
  })

  it('defaultFields has no extra content', () => {
    expect(defaultFields().extra).toBe('')
  })
})
