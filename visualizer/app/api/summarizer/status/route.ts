// app/api/summarizer/status/route.ts
import { NextRequest, NextResponse } from 'next/server'
import { resolveVault, VaultConfigError } from '@/lib/vaultResolver'
import { getSummarizerStatus, countPendingSummaries } from '@/lib/vaultStatsServer'

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
  const status = getSummarizerStatus()
  return NextResponse.json({
    ...status,
    pendingSummaries: countPendingSummaries(vaultPath),
  })
}
