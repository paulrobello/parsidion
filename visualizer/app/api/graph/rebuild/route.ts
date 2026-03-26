// app/api/graph/rebuild/route.ts
import { NextRequest, NextResponse } from 'next/server'
import { spawn } from 'child_process'
import path from 'path'
import fs from 'fs'
import { vaultBroadcast } from '@/lib/vaultBroadcast.server'
import { resolveVault } from '@/lib/vaultResolver'

/** Locate build_graph.py — checks alongside this app, then in the source repo. */
function findBuildGraphScript(): string | null {
  // 1. Installed alongside the app (symlinked or copied from parsidion-cc repo)
  const installed = path.join(
    process.env.HOME || '~',
    '.claude', 'skills', 'parsidion-cc', 'scripts', 'build_graph.py'
  )
  if (fs.existsSync(installed)) return installed

  // 2. Source repo: app lives at <repo>/visualizer/, script at <repo>/skills/parsidion-cc/scripts/
  const repoRoot = path.join(process.cwd(), '..')
  const source = path.join(repoRoot, 'skills', 'parsidion-cc', 'scripts', 'build_graph.py')
  if (fs.existsSync(source)) return source

  return null
}

export async function POST(req: NextRequest) {
  const vault = req.nextUrl.searchParams.get('vault')
  const vaultPath = resolveVault(vault)

  const scriptPath = findBuildGraphScript()
  if (!scriptPath) {
    return NextResponse.json(
      { error: 'build_graph.py not found. Install parsidion-cc or run from the source repo.' },
      { status: 500 }
    )
  }

  const outputPath = path.join(vaultPath, 'graph.json')
  const args = ['run', '--no-project', scriptPath, '--vault', vaultPath, '--output', outputPath]

  return new Promise<NextResponse>(resolve => {
    const proc = spawn('uv', args, { stdio: 'pipe' })

    let stderr = ''
    proc.stderr?.on('data', (chunk: Buffer) => { stderr += chunk.toString() })

    proc.on('close', code => {
      if (code === 0) {
        vaultBroadcast.emit('graph:rebuilt')
        resolve(NextResponse.json({ ok: true }))
      } else {
        resolve(NextResponse.json(
          { error: `build_graph.py exited ${code}`, detail: stderr },
          { status: 500 }
        ))
      }
    })

    proc.on('error', err => {
      resolve(NextResponse.json({ error: err.message }, { status: 500 }))
    })
  })
}
