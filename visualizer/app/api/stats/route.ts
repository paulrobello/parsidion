// app/api/stats/route.ts
import { NextRequest, NextResponse } from 'next/server'
import { resolveVault, VaultConfigError } from '@/lib/vaultResolver'
import { countPendingSummaries } from '@/lib/vaultStatsServer'
import { requireSameOrigin, requireToken } from '@/lib/apiAuth'

export async function GET(req: NextRequest) {
  // SEC-102 / SEC-118 / QA-011: previously this route imported neither guard
  // — the only route in the app without them. Token first, then same-origin.
  const tokenError = requireToken(req)
  if (tokenError) return tokenError
  const originError = requireSameOrigin(req)
  if (originError) return originError
  const vault = req.nextUrl.searchParams.get('vault')
  let vaultPath: string
  try {
    vaultPath = resolveVault(vault)
  } catch (err) {
    if (err instanceof VaultConfigError) {
      return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
    }
    return NextResponse.json({ error: 'Failed to resolve vault' }, { status: 500 })
  }
  const pendingSummaries = countPendingSummaries(vaultPath)
  return NextResponse.json({ pendingSummaries })
}
