// app/api/summarize/route.ts
import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import { resolveVault, VaultConfigError } from '@/lib/vaultResolver'
import { withApi } from '@/lib/apiAuth'
import { spawnSummarizer } from '@/lib/vaultStatsServer'

export const POST = withApi(async (req: NextRequest) => {
  const vault = req.nextUrl.searchParams.get('vault')

  // SEC-005: Validate vault path before spawning the subprocess.
  let vaultPath: string
  try {
    vaultPath = resolveVault(vault)
  } catch (err) {
    if (err instanceof VaultConfigError) {
      return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
    }
    return NextResponse.json({ error: 'Failed to resolve vault' }, { status: 500 })
  }

  if (!fs.existsSync(vaultPath) || !fs.statSync(vaultPath).isDirectory()) {
    return NextResponse.json({ error: 'Vault directory not found' }, { status: 400 })
  }

  try {
    const result = spawnSummarizer(vaultPath)
    if ('alreadyRunning' in result) {
      // ARC-040: 409 + {error, ...} so the conflict encoding matches the
      // note/route.ts PUT/POST conflicts. `alreadyRunning` is retained for
      // backward compatibility with existing clients.
      return NextResponse.json(
        { error: 'Summarizer already running', alreadyRunning: true },
        { status: 409 },
      )
    }
    return NextResponse.json({ started: true, pid: result.pid })
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Failed to start summarizer'
    console.error('[summarize] spawn failed:', message)
    return NextResponse.json({ error: message }, { status: 500 })
  }
}, { mutation: true })
