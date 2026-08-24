// lib/frontmatter.parity.test.ts
// ARC-005: the TypeScript half of the shared frontmatter serialization
// contract.
//
// Reads the SAME fixture as the Python suite
// (tests/fixtures/parity/frontmatter.json) — the ENH-005 vault-resolution
// pattern — and asserts that lib/frontmatter.ts serializeFrontmatter() and
// parseFrontmatter() agree with core.vault_index.serialize_frontmatter() /
// parse_frontmatter() on every vector: same expected block, same round-trip.
//
// Fixture model mapping (see the fixture's $comment): `related` holds bare
// stems (this editor stores stems and quotes them as [[wikilinks]] on
// serialize); `project`/`provenance`/`session_id` are '' when absent —
// provenance/session_id ride the `extra` verbatim round-trip here while the
// Python suite treats them as regular canonical-order keys.

import { describe, expect, it } from 'bun:test'
import * as fs from 'fs'
import * as path from 'path'
import { parseFrontmatter, serializeFrontmatter, FrontmatterFields } from './frontmatter'

const FIXTURE = path.join(__dirname, '..', '..', 'tests', 'fixtures', 'parity', 'frontmatter.json')

interface FixtureVector {
  name: string
  description: string
  fields: {
    date: string
    type: string
    tags: string[]
    project: string
    confidence: string
    sources: string[]
    related: string[]
    provenance: string
    session_id: string
  }
  expected: string
}

const fixture = JSON.parse(fs.readFileSync(FIXTURE, 'utf-8')) as {
  version: number
  vectors: FixtureVector[]
}

function modelToFields(v: FixtureVector): FrontmatterFields {
  const extraLines: string[] = []
  if (v.fields.provenance) extraLines.push(`provenance: ${v.fields.provenance}`)
  if (v.fields.session_id) extraLines.push(`session_id: ${v.fields.session_id}`)
  return {
    date: v.fields.date,
    type: v.fields.type,
    tags: [...v.fields.tags],
    confidence: v.fields.confidence,
    project: v.fields.project,
    sources: [...v.fields.sources],
    related: [...v.fields.related],
    extra: extraLines.join('\n'),
  }
}

describe('frontmatter parity fixture (ARC-005)', () => {
  it('fixture version is the one this suite understands', () => {
    expect(fixture.version).toBe(1)
  })

  it('fixture loads with vectors', () => {
    expect(fixture.vectors.length).toBeGreaterThanOrEqual(8)
  })

  for (const vector of fixture.vectors) {
    it(`serialize matches expected: ${vector.name}`, () => {
      // body='' keeps the serialized output to exactly the frontmatter block
      // (serializeFrontmatter appends '\n' + body after the closing '---').
      expect(serializeFrontmatter(modelToFields(vector), '')).toBe(vector.expected)
    })

    it(`parse round-trips expected: ${vector.name}`, () => {
      const { fields, body } = parseFrontmatter(vector.expected)
      expect(body).toBe('')
      expect(fields.date).toBe(vector.fields.date)
      expect(fields.type).toBe(vector.fields.type)
      expect(fields.tags).toEqual(vector.fields.tags)
      expect(fields.confidence).toBe(vector.fields.confidence)
      expect(fields.project).toBe(vector.fields.project)
      expect(fields.sources).toEqual(vector.fields.sources)
      expect(fields.related).toEqual(vector.fields.related)
      const extraLines: string[] = []
      if (vector.fields.provenance) extraLines.push(`provenance: ${vector.fields.provenance}`)
      if (vector.fields.session_id) extraLines.push(`session_id: ${vector.fields.session_id}`)
      expect(fields.extra).toBe(extraLines.join('\n'))
    })
  }
})
