// app/api/stats/route.ts
import { NextRequest, NextResponse } from 'next/server'
import { resolveVault, VaultConfigError } from '@/lib/vaultResolver'
import { countPendingSummaries } from '@/lib/vaultStatsServer'

export async function GET(req: NextRequest) {
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
