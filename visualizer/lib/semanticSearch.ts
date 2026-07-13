// lib/semanticSearch.ts — client-side helper for GET /api/search.
export interface SemanticResult {
  stem: string
  title: string
  folder: string
  path: string
  tags: string[]
  note_type: string
  score: number
  summary: string
}

export function buildSearchUrl(query: string, vault: string | null, top = 8): string {
  const params = new URLSearchParams({ q: query, top: String(top) })
  if (vault) params.set('vault', vault)
  return `/api/search?${params.toString()}`
}

export async function fetchSemanticResults(
  query: string,
  vault: string | null,
  signal: AbortSignal,
  top = 8,
): Promise<SemanticResult[]> {
  const res = await fetch(buildSearchUrl(query, vault, top), { signal })
  if (!res.ok) {
    let message = `Semantic search failed (${res.status})`
    try {
      const body = await res.json()
      if (typeof body?.error === 'string') message = body.error
    } catch { /* keep the default message */ }
    throw new Error(message)
  }
  const body = await res.json()
  return Array.isArray(body?.results) ? body.results : []
}
