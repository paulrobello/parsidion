import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs/promises'
import path from 'path'
import { resolveVault, guardPath } from '@/lib/vaultResolver'
import { withApi } from '@/lib/apiAuth'
import { findNote } from '@/lib/findNote'

// QA-006: findNote moved to lib/findNote.ts so note/, note/history/, and
// note/diff/ share one async implementation. The previous triplicated copies
// had already diverged once (the first async conversion only updated this
// file); centralising prevents the next drift.

export const GET = withApi(async (req: NextRequest) => {
  const stem = req.nextUrl.searchParams.get('stem')
  const relPath = req.nextUrl.searchParams.get('path')
  const vault = req.nextUrl.searchParams.get('vault')
  if (!stem && !relPath) return NextResponse.json({ error: 'stem or path required' }, { status: 400 })

  let vaultRoot: string
  try {
    vaultRoot = resolveVault(vault)
  } catch {
    return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
  }
  let notePath: string | null
  if (relPath) {
    // Direct path lookup — avoids stem collision across folders
    const candidate = path.join(vaultRoot, relPath)
    if (!guardPath(candidate, vaultRoot)) {
      return NextResponse.json({ error: 'Path traversal rejected' }, { status: 403 })
    }
    try {
      await fs.access(candidate)
      notePath = candidate
    } catch {
      notePath = null
    }
  } else {
    notePath = await findNote(vaultRoot, stem!)
  }
  if (!notePath) return NextResponse.json({ error: `Note not found: ${relPath ?? stem}` }, { status: 404 })

  try {
    const [content, stat] = await Promise.all([
      fs.readFile(notePath, 'utf-8'),
      fs.stat(notePath),
    ])
    const relativePath = path.relative(vaultRoot, notePath)
    return NextResponse.json({ content, path: relativePath, mtimeMs: stat.mtimeMs })
  } catch {
    return NextResponse.json({ error: 'Failed to read note' }, { status: 500 })
  }
})

export const POST = withApi(async (req: NextRequest) => {
  // ARC-040: wrap req.json() so malformed bodies return 400 instead of 500.
  let body: unknown
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 })
  }
  // ARC-002: accept vault from EITHER the query string or the JSON body
  // (query string wins for backward compat). The client puts the selected
  // vault in body.vault (useVisualizerState.ts); discarding it caused silent
  // cross-vault writes and a defunct mtime conflict check.
  const vaultParam = req.nextUrl.searchParams.get('vault') ?? (body as { vault?: string } | null)?.vault ?? null
  const { stem, path: relPath, content, baseMtimeMs } = body as {
    stem?: string
    path?: string
    content?: unknown
    baseMtimeMs?: number
    vault?: string
  }
  // ARC-040: type-validate content — must be a string. Without this an object
  // or array body would reach fs.writeFile and throw at runtime → 500.
  if ((!stem && !relPath) || typeof content !== 'string') {
    return NextResponse.json({ error: 'stem or path, and string content required' }, { status: 400 })
  }

  let vaultRoot: string
  try {
    vaultRoot = resolveVault(vaultParam)
  } catch {
    return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
  }

  // Prefer an explicit vault-relative path (avoids stem collision across
  // folders — findNote() below returns only the first depth-first match).
  let notePath: string | null
  if (relPath) {
    const candidate = path.join(vaultRoot, relPath)
    if (!guardPath(candidate, vaultRoot)) {
      return NextResponse.json({ error: 'Path traversal rejected' }, { status: 403 })
    }
    try {
      await fs.access(candidate)
      notePath = candidate
    } catch {
      notePath = null
    }
  } else {
    notePath = await findNote(vaultRoot, stem!)
    if (notePath && !guardPath(notePath, vaultRoot)) {
      return NextResponse.json({ error: 'Path traversal rejected' }, { status: 403 })
    }
  }
  if (!notePath) return NextResponse.json({ error: `Note not found: ${relPath ?? stem}` }, { status: 404 })

  // Conflict detection: if caller provided baseMtimeMs (the server mtime it last
  // fetched) and the file's mtime is now strictly greater, the file was modified
  // externally since then — return the current content instead of saving. This
  // compares server mtimes only, never a client wall-clock timestamp, so it is
  // immune to clock skew between the browser and the machine running the vault.
  //
  // ARC-040: HTTP 409 + {error, conflict, ...} so the encoding is uniform
  // with the PUT "already exists" conflict below. DOC-006 documents 409.
  if (baseMtimeMs !== undefined) {
    try {
      const stat = await fs.stat(notePath)
      if (stat.mtimeMs > baseMtimeMs) {
        const serverContent = await fs.readFile(notePath, 'utf-8')
        return NextResponse.json(
          { error: 'Note modified externally', conflict: true, serverContent, mtimeMs: stat.mtimeMs },
          { status: 409 },
        )
      }
    } catch {
      // If stat fails, proceed with the save
    }
  }

  try {
    await fs.writeFile(notePath, content, 'utf-8')
    const stat = await fs.stat(notePath)
    return NextResponse.json({ ok: true, mtimeMs: stat.mtimeMs })
  } catch {
    return NextResponse.json({ error: 'Failed to write note' }, { status: 500 })
  }
}, { mutation: true })

export const PUT = withApi(async (req: NextRequest) => {
  // ARC-040: wrap req.json() so malformed bodies return 400, not 500.
  let body: unknown
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 })
  }
  // ARC-002: accept vault from EITHER the query string or the JSON body
  // (query string wins for backward compat). See POST handler for rationale.
  const vaultParam = req.nextUrl.searchParams.get('vault') ?? (body as { vault?: string } | null)?.vault ?? null
  const { path: relPath, content } = body as {
    path?: string
    content?: unknown
    vault?: string
  }
  if (!relPath || typeof content !== 'string') {
    return NextResponse.json({ error: 'path and string content required' }, { status: 400 })
  }

  // SEC-002: Only .md files may be created through this endpoint.
  if (!relPath.endsWith('.md')) {
    return NextResponse.json({ error: 'Only .md files may be created' }, { status: 400 })
  }

  let vaultRoot: string
  try {
    vaultRoot = resolveVault(vaultParam)
  } catch {
    return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
  }
  const notePath = path.join(vaultRoot, relPath)

  if (!guardPath(notePath, vaultRoot)) {
    return NextResponse.json({ error: 'Path traversal rejected' }, { status: 403 })
  }

  try {
    await fs.access(notePath)
    return NextResponse.json({ error: 'Note already exists' }, { status: 409 })
  } catch {
    // File doesn't exist — proceed to create it
  }

  try {
    await fs.mkdir(path.dirname(notePath), { recursive: true })
    await fs.writeFile(notePath, content, 'utf-8')
    return NextResponse.json({ ok: true, path: relPath })
  } catch {
    return NextResponse.json({ error: 'Failed to create note' }, { status: 500 })
  }
}, { mutation: true })

export const DELETE = withApi(async (req: NextRequest) => {
  const stem = req.nextUrl.searchParams.get('stem')
  const relPath = req.nextUrl.searchParams.get('path')
  const vault = req.nextUrl.searchParams.get('vault')
  if (!stem && !relPath) return NextResponse.json({ error: 'stem or path required' }, { status: 400 })

  let vaultRoot: string
  try {
    vaultRoot = resolveVault(vault)
  } catch {
    return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
  }

  // Prefer an explicit vault-relative path (avoids stem collision across
  // folders — findNote() below returns only the first depth-first match).
  let notePath: string | null
  if (relPath) {
    const candidate = path.join(vaultRoot, relPath)
    if (!guardPath(candidate, vaultRoot)) {
      return NextResponse.json({ error: 'Path traversal rejected' }, { status: 403 })
    }
    try {
      await fs.access(candidate)
      notePath = candidate
    } catch {
      notePath = null
    }
  } else {
    notePath = await findNote(vaultRoot, stem!)
    if (notePath && !guardPath(notePath, vaultRoot)) {
      return NextResponse.json({ error: 'Path traversal rejected' }, { status: 403 })
    }
  }
  if (!notePath) return NextResponse.json({ error: `Note not found: ${relPath ?? stem}` }, { status: 404 })

  try {
    await fs.unlink(notePath)
    return NextResponse.json({ ok: true })
  } catch {
    return NextResponse.json({ error: 'Failed to delete note' }, { status: 500 })
  }
}, { mutation: true })
