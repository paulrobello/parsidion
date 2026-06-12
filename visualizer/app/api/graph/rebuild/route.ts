// app/api/graph/rebuild/route.ts
import { NextRequest, NextResponse } from 'next/server'
import { spawn } from 'child_process'
import path from 'path'
import fs from 'fs'
import { vaultBroadcast } from '@/lib/vaultBroadcast.server'
import { resolveVault, VaultConfigError } from '@/lib/vaultResolver'
import { requireAuth } from '@/lib/apiAuth'

// SEC-014: Cap stderr accumulation to avoid unbounded memory growth.
const MAX_STDERR_BYTES = 64 * 1024

/** Locate build_graph.py — checks alongside this app, then in the source repo. */
function findBuildGraphScript(): string | null {
  // 1. Installed alongside the app (symlinked or copied from parsidion repo)
  const installed = path.join(
    process.env.HOME || '~',
    '.claude', 'skills', 'parsidion', 'scripts', 'build_graph.py'
  )
  if (fs.existsSync(installed)) return installed

  // 2. Source repo: app lives at <repo>/visualizer/, script at <repo>/skills/parsidion/scripts/
  const repoRoot = path.join(process.cwd(), '..')
  const source = path.join(repoRoot, 'skills', 'parsidion', 'scripts', 'build_graph.py')
  if (fs.existsSync(source)) return source

  return null
}

export async function POST(req: NextRequest) {
  const authError = requireAuth(req)
  if (authError) return authError
  const vault = req.nextUrl.searchParams.get('vault')

  // SEC-005: Validate vault path before passing it to the subprocess.
  // SEC-001 forbidden-prefix check is enforced inside resolveVault().
  let vaultPath: string
  try {
    vaultPath = resolveVault(vault)
  } catch (err) {
    if (err instanceof VaultConfigError) {
      return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
    }
    return NextResponse.json({ error: 'Failed to resolve vault' }, { status: 500 })
  }

  // SEC-005: Verify the resolved path exists and is a directory before spawning.
  if (!fs.existsSync(vaultPath) || !fs.statSync(vaultPath).isDirectory()) {
    return NextResponse.json({ error: 'Vault directory not found' }, { status: 400 })
  }

  const scriptPath = findBuildGraphScript()
  if (!scriptPath) {
    return NextResponse.json(
      { error: 'build_graph.py not found. Install parsidion or run from the source repo.' },
      { status: 500 }
    )
  }

  const outputPath = path.join(vaultPath, 'graph.json')
  const args = ['run', '--no-project', scriptPath, '--vault', vaultPath, '--output', outputPath]

  return new Promise<NextResponse>(resolve => {
    const proc = spawn('uv', args, { stdio: 'pipe' })

    // SEC-014: Cap stderr accumulation to avoid unbounded memory growth.
    let stderrBytes = 0
    let stderr = ''
    proc.stderr?.on('data', (chunk: Buffer) => {
      const remaining = MAX_STDERR_BYTES - stderrBytes
      if (remaining > 0) {
        const slice = chunk.slice(0, remaining)
        stderr += slice.toString()
        stderrBytes += slice.length
      }
    })

    proc.on('close', code => {
      if (code === 0) {
        vaultBroadcast.emit('graph:rebuilt')
        resolve(NextResponse.json({ ok: true }))
      } else {
        // SEC-003: Log stderr server-side; return a generic error to the client.
        console.error('[graph/rebuild] build_graph.py exited', code, ':', stderr)
        resolve(NextResponse.json(
          { error: `Graph rebuild failed (exit code ${code})` },
          { status: 500 }
        ))
      }
    })

    proc.on('error', err => {
      console.error('[graph/rebuild] spawn error:', err.message)
      resolve(NextResponse.json({ error: 'Failed to start graph rebuild' }, { status: 500 }))
    })
  })
}
