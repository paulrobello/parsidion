// app/api/health/route.ts
// ENH-007: composite vault health report. Subprocesses vault-stats
// --health --json via lib/vaultStatsServer.ts so the visualizer and the CLI
// see the same scoring code. Expensive on large vaults (the metadata-quality
// dimension walks every note), so the client polls on demand rather than on
// the cadence used by /api/stats.
import { NextRequest, NextResponse } from 'next/server'
import { resolveVault, VaultConfigError } from '@/lib/vaultResolver'
import {
  getVaultHealth,
  HealthReportFailedError,
  HealthScriptMissingError,
} from '@/lib/vaultStatsServer'
import { withApi } from '@/lib/apiAuth'

export const GET = withApi(async (req: NextRequest) => {
  const vault = req.nextUrl.searchParams.get('vault')
  const fast = req.nextUrl.searchParams.get('fast') === '1'
  let vaultPath: string
  try {
    vaultPath = resolveVault(vault)
  } catch (err) {
    if (err instanceof VaultConfigError) {
      return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
    }
    return NextResponse.json({ error: 'Failed to resolve vault' }, { status: 500 })
  }

  try {
    const report = await getVaultHealth(vaultPath, { fast })
    return NextResponse.json(report)
  } catch (err) {
    if (err instanceof HealthScriptMissingError) {
      return NextResponse.json({ error: err.message }, { status: 503 })
    }
    if (err instanceof HealthReportFailedError) {
      return NextResponse.json({ error: err.message }, { status: 500 })
    }
    return NextResponse.json({ error: 'Failed to compute health' }, { status: 500 })
  }
})
