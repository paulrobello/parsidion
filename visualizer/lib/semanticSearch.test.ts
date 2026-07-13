import { describe, test, expect, afterEach } from 'bun:test'
import { buildSearchUrl, fetchSemanticResults } from './semanticSearch'

const realFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = realFetch })

describe('buildSearchUrl', () => {
  test('includes query and top; omits vault when null', () => {
    expect(buildSearchUrl('hello world', null, 8)).toBe('/api/search?q=hello+world&top=8')
  })

  test('includes vault when set', () => {
    expect(buildSearchUrl('q', 'work', 5)).toBe('/api/search?q=q&top=5&vault=work')
  })
})

describe('fetchSemanticResults', () => {
  test('returns the results array', async () => {
    globalThis.fetch = (async () => new Response(JSON.stringify({
      results: [{
        stem: 'a', title: 'A', folder: 'F', path: 'F/a.md',
        tags: [], note_type: 'pattern', score: 0.1, summary: 's',
      }],
      tookMs: 5,
    }))) as unknown as typeof fetch
    const rows = await fetchSemanticResults('q', null, new AbortController().signal)
    expect(rows.length).toBe(1)
    expect(rows[0].stem).toBe('a')
  })

  test('non-ok response throws with the server error message', async () => {
    globalThis.fetch = (async () => new Response(
      JSON.stringify({ error: 'Search busy' }), { status: 429 },
    )) as unknown as typeof fetch
    await expect(fetchSemanticResults('q', null, new AbortController().signal))
      .rejects.toThrow('Search busy')
  })
})
