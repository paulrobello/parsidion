import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { resolveVault, VaultConfigError, guardPath } from '@/lib/vaultResolver'
import { withApi } from '@/lib/apiAuth'
import { runScript, ScriptFailedError } from '@/lib/runScript'

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

const MAX_DIFF_LINES = 5000

export const GET = withApi(async (req: NextRequest) => {
  const stem = req.nextUrl.searchParams.get('stem')
  const notePathParam = req.nextUrl.searchParams.get('path')
  const from = req.nextUrl.searchParams.get('from')
  const to = req.nextUrl.searchParams.get('to')
  const vault = req.nextUrl.searchParams.get('vault')

  if ((!stem && !notePathParam) || !from || !to) {
    return NextResponse.json({ error: 'stem or path, from, and to are required' }, { status: 400 })
  }

  // Validate SHAs: alphanumeric only (short or full) or the sentinel "working"
  const shaPattern = /^[a-f0-9]{4,40}$|^working$/
  if (!shaPattern.test(from) || !shaPattern.test(to)) {
    return NextResponse.json({ error: 'Invalid commit reference' }, { status: 400 })
  }

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
    : findNote(vaultRoot, stem!)
  if (!notePath) return NextResponse.json({ error: `Note not found: ${stem}` }, { status: 404 })

  if (!guardPath(notePath, vaultRoot)) {
    return NextResponse.json({ error: 'Path traversal rejected' }, { status: 403 })
  }

  const relPath = path.relative(vaultRoot, notePath)

  // Build git args:
  // Normal:       git diff <from> <to> -- <relPath>
  // Working tree: git diff <from> -- <relPath>  (no second SHA)
  const gitArgs = to === 'working'
    ? ['diff', from, '--', relPath]
    : ['diff', from, to, '--', relPath]

  // ARC-036: shared subprocess wrapper — enforces timeout, aborts on client
  // disconnect, caps stderr. `git diff` exits 1 for "differences found",
  // which is a success, so pass it through successExitCodes.
  let stdout: string
  try {
    ({ stdout } = await runScript('git', gitArgs, {
      cwd: vaultRoot,
      signal: req.signal,
      successExitCodes: [0, 1],
    }))
  } catch (err) {
    if (err instanceof ScriptFailedError) {
      console.error('[note/diff] git diff failed:', err.stderr)
    } else {
      console.error('[note/diff] git diff error:', err)
    }
    return NextResponse.json({ error: 'Failed to compute diff' }, { status: 500 })
  }

  // Truncate very large diffs
  const lines = stdout.split('\n')
  let diff = stdout
  let truncated = false
  if (lines.length > MAX_DIFF_LINES) {
    diff = lines.slice(0, MAX_DIFF_LINES).join('\n')
    truncated = true
  }
  return NextResponse.json({ diff, truncated })
})
