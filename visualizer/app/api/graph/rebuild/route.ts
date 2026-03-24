// app/api/graph/rebuild/route.ts
import { NextResponse } from 'next/server'
import { spawn } from 'child_process'
import path from 'path'
import { vaultBroadcast } from '@/lib/vaultBroadcast.server'

export async function POST() {
  const repoRoot = path.join(process.cwd(), '..')
  const scriptPath = path.join(repoRoot, 'scripts', 'build_graph.py')

  return new Promise<NextResponse>(resolve => {
    const proc = spawn('uv', ['run', '--no-project', scriptPath], {
      cwd: repoRoot,
      stdio: 'pipe',
    })

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
