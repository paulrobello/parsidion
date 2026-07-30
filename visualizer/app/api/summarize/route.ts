// app/api/summarize/route.ts
import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs/promises'
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

  // QA-012: stat the vault via fs/promises so the event loop is not blocked.
  try {
    const stat = await fs.stat(vaultPath)
    if (!stat.isDirectory()) {
      return NextResponse.json({ error: 'Vault directory not found' }, { status: 400 })
    }
  } catch {
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
    // SEC-120: return a generic error to the client and log the detail
    // server-side. The raw exception can leak the vault path or the
    // summarizer's internal command line.
    const message = err instanceof Error ? err.message : String(err)
    console.error('[summarize] spawn failed:', message)
    return NextResponse.json({ error: 'Failed to start summarizer' }, { status: 500 })
  }
}, { mutation: true })
