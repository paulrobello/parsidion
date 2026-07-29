import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs/promises'
import path from 'path'
import { resolveVault, VaultConfigError, guardPath } from '@/lib/vaultResolver'
import { withApi } from '@/lib/apiAuth'
import { runScript, ScriptFailedError } from '@/lib/runScript'
import { findNote } from '@/lib/findNote'
import type { CommitEntry } from '@/lib/types'

// Re-export for backward compatibility — existing imports from
// '@/app/api/note/history/route' (type-only) keep working. New consumers
// should import from '@/lib/types' directly. ARC-041.
export type { CommitEntry }

// QA-006: findNote now imported from @/lib/findNote (async). The previous
// sync readdirSync copy blocked the event loop on every history request.
// QA-018: rename notPathParam → notePathParam to match diff/route.ts.

export const GET = withApi(async (req: NextRequest) => {
  const stem = req.nextUrl.searchParams.get('stem')
  const notePathParam = req.nextUrl.searchParams.get('path')
  const vault = req.nextUrl.searchParams.get('vault')
  if (!stem && !notePathParam) return NextResponse.json({ error: 'stem or path required' }, { status: 400 })

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
  const notePath = notePathParam
    ? path.join(vaultRoot, notePathParam)
    : await findNote(vaultRoot, stem!)
  if (!notePath) return NextResponse.json({ error: `Note not found: ${stem}` }, { status: 404 })

  if (!guardPath(notePath, vaultRoot)) {
    return NextResponse.json({ error: 'Path traversal rejected' }, { status: 403 })
  }

  // Check git is available
  const gitDir = path.join(vaultRoot, '.git')
  try {
    await fs.access(gitDir)
  } catch {
    return NextResponse.json({ commits: [] })
  }

  const relPath = path.relative(vaultRoot, notePath)

  let stdout: string
  try {
    ({ stdout } = await runScript(
      'git',
      ['log', '--follow', '--format=%H|%ai|%s', '--', relPath],
      { cwd: vaultRoot, signal: req.signal },
    ))
  } catch (err) {
    if (err instanceof ScriptFailedError) {
      // SEC-003: Log stderr server-side; return a generic error to the client.
      console.error('[note/history] git log failed:', err.stderr)
    } else {
      console.error('[note/history] git log error:', err)
    }
    return NextResponse.json({ error: 'Failed to retrieve commit history' }, { status: 500 })
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

  return NextResponse.json({ commits })
})
