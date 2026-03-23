import { NextResponse } from 'next/server'
import { spawn } from 'child_process'
import path from 'path'

export async function POST() {
  // build_graph.py lives at {repo_root}/scripts/build_graph.py
  // The visualizer runs from {repo_root}/visualizer/, so repo root is one level up
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
        resolve(NextResponse.json({ ok: true }))
      } else {
        resolve(NextResponse.json({ error: `build_graph.py exited ${code}`, detail: stderr }, { status: 500 }))
      }
    })

    proc.on('error', err => {
      resolve(NextResponse.json({ error: err.message }, { status: 500 }))
    })
  })
}
