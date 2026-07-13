// app/api/search/route.ts
import { NextRequest, NextResponse } from 'next/server'
import { resolveVault, VaultConfigError } from '@/lib/vaultResolver'
import { requireSameOrigin } from '@/lib/apiAuth'
import {
  runVaultSearch,
  ScriptMissingError,
  SearchBusyError,
  SearchFailedError,
} from '@/lib/searchServer'

const MAX_QUERY_LENGTH = 512

export async function GET(req: NextRequest) {
  const originError = requireSameOrigin(req)
  if (originError) return originError

  const q = (req.nextUrl.searchParams.get('q') ?? '').trim()
  if (!q || q.length > MAX_QUERY_LENGTH) {
    return NextResponse.json({ error: 'Invalid query' }, { status: 400 })
  }

  const topRaw = Number(req.nextUrl.searchParams.get('top') ?? '8')
  const top = Number.isFinite(topRaw) ? Math.min(20, Math.max(1, Math.floor(topRaw))) : 8

  let vaultPath: string
  try {
    vaultPath = resolveVault(req.nextUrl.searchParams.get('vault'))
  } catch (err) {
    if (err instanceof VaultConfigError) {
      return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
    }
    return NextResponse.json({ error: 'Failed to resolve vault' }, { status: 500 })
  }

  const started = Date.now()
  try {
    const results = await runVaultSearch(vaultPath, q, top, { signal: req.signal })
    return NextResponse.json({ results, tookMs: Date.now() - started })
  } catch (err) {
    if (err instanceof SearchBusyError) {
      return NextResponse.json({ error: 'Search busy — try again in a moment' }, { status: 429 })
    }
    if (err instanceof ScriptMissingError) {
      return NextResponse.json(
        { error: 'vault_search.py not found. Install parsidion or run from the source repo.' },
        { status: 503 },
      )
    }
    if (err instanceof SearchFailedError) {
      return NextResponse.json({ error: 'Semantic search failed' }, { status: 502 })
    }
    throw err
  }
}
