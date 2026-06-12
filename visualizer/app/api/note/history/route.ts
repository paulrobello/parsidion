import { NextRequest, NextResponse } from 'next/server'
import { spawn } from 'child_process'
import fs from 'fs'
import path from 'path'
import { resolveVault, VaultConfigError } from '@/lib/vaultResolver'

function findNote(dir: string, stemToFind: string): string | null {
  try {
    const entries = fs.readdirSync(dir, { withFileTypes: true })
    for (const entry of entries) {
      if (entry.name.startsWith('.')) continue
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        const found = findNote(full, stemToFind)
        if (found) return found
      } else if (entry.isFile() && entry.name.endsWith('.md')) {
        if (entry.name.replace(/\.md$/, '') === stemToFind) return full
      }
    }
  } catch { /* skip */ }
  return null
}

function guardPath(notePath: string, vaultRoot: string): boolean {
  return path.resolve(notePath).startsWith(path.resolve(vaultRoot) + path.sep)
}

export interface CommitEntry {
  hash: string
  shortHash: string
  date: string
  message: string
}

export async function GET(req: NextRequest) {
  const stem = req.nextUrl.searchParams.get('stem')
  const notPathParam = req.nextUrl.searchParams.get('path')
  const vault = req.nextUrl.searchParams.get('vault')
  if (!stem && !notPathParam) return NextResponse.json({ error: 'stem or path required' }, { status: 400 })

  let vaultRoot: string
  try {
    vaultRoot = resolveVault(vault)
  } catch (err) {
    if (err instanceof VaultConfigError) {
      return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
    }
    return NextResponse.json({ error: 'Failed to resolve vault' }, { status: 500 })
  }
  // Prefer explicit vault-relative path (avoids stem collision for MANIFEST.md etc.)
  const notePath = notPathParam
    ? path.join(vaultRoot, notPathParam)
    : findNote(vaultRoot, stem!)
  if (!notePath) return NextResponse.json({ error: `Note not found: ${stem}` }, { status: 404 })

  if (!guardPath(notePath, vaultRoot)) {
    return NextResponse.json({ error: 'Path traversal rejected' }, { status: 403 })
  }

  // Check git is available
  const gitDir = path.join(vaultRoot, '.git')
  if (!fs.existsSync(gitDir)) {
    return NextResponse.json({ commits: [] })
  }

  const relPath = path.relative(vaultRoot, notePath)

  return new Promise<NextResponse>(resolve => {
    const proc = spawn('git', ['log', '--follow', '--format=%H|%ai|%s', '--', relPath], {
      cwd: vaultRoot,
      stdio: 'pipe',
    })

    let stdout = ''
    let stderr = ''
    proc.stdout?.on('data', (chunk: Buffer) => { stdout += chunk.toString() })
    proc.stderr?.on('data', (chunk: Buffer) => { stderr += chunk.toString() })

    proc.on('close', code => {
      if (code !== 0) {
        // SEC-003: Log stderr server-side; return a generic error to the client.
        console.error('[note/history] git log failed:', stderr)
        resolve(NextResponse.json({ error: 'Failed to retrieve commit history' }, { status: 500 }))
        return
      }

      const commits: CommitEntry[] = stdout
        .split('\n')
        .filter(Boolean)
        .map(line => {
          const [hash, date, ...msgParts] = line.split('|')
          return {
            hash,
            shortHash: hash.slice(0, 7),
            date,
            message: msgParts.join('|'),
          }
        })

      resolve(NextResponse.json({ commits }))
    })

    proc.on('error', err => {
      resolve(NextResponse.json({ error: err.message }, { status: 500 }))
    })
  })
}
