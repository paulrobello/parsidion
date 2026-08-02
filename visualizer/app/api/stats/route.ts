// app/api/stats/route.ts
import { NextRequest, NextResponse } from 'next/server'
import { resolveVault, VaultConfigError } from '@/lib/vaultResolver'
import { countPendingSummaries } from '@/lib/vaultStatsServer'
import { withApi } from '@/lib/apiAuth'

export const GET = withApi(async (req: NextRequest) => {
  // SEC-102 / SEC-118 / QA-011: previously this route imported neither guard
  // — the only route in the app without them. ARC-014 routes the guards
  // through `withApi`, which a test enumerates across every route module so
  // a new route physically cannot skip them.
  const vault = req.nextUrl.searchParams.get('vault')
  let vaultPath: string
  try {
    vaultPath = await resolveVault(vault)
  } catch (err) {
    if (err instanceof VaultConfigError) {
      return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
    }
    return NextResponse.json({ error: 'Failed to resolve vault' }, { status: 500 })
  }
  const pendingSummaries = countPendingSummaries(vaultPath)
  return NextResponse.json({ pendingSummaries })
})
