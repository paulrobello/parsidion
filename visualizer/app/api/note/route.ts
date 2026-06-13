import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs/promises'
import path from 'path'
import { resolveVault, guardPath } from '@/lib/vaultResolver'
import { requireAuth } from '@/lib/apiAuth'

// QA-006: Replaced all synchronous fs calls with async fs.promises equivalents.
// findNote is now async to avoid blocking the Node.js event loop during
// recursive directory walks.

async function findNote(dir: string, stemToFind: string): Promise<string | null> {
  try {
    const entries = await fs.readdir(dir, { withFileTypes: true })
    for (const entry of entries) {
      if (entry.name.startsWith('.')) continue
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        const found = await findNote(full, stemToFind)
        if (found) return found
      } else if (entry.isFile() && entry.name.endsWith('.md')) {
        const fileStem = entry.name.replace(/\.md$/, '')
        if (fileStem === stemToFind) return full
      }
    }
  } catch { /* skip unreadable dirs */ }
  return null
}

export async function GET(req: NextRequest) {
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
    const content = await fs.readFile(notePath, 'utf-8')
    const relativePath = path.relative(vaultRoot, notePath)
    return NextResponse.json({ content, path: relativePath })
  } catch {
    return NextResponse.json({ error: 'Failed to read note' }, { status: 500 })
  }
}

export async function POST(req: NextRequest) {
  const authError = requireAuth(req)
  if (authError) return authError
  const vault = req.nextUrl.searchParams.get('vault')
  const body = await req.json()
  const { stem, content, lastModified } = body as {
    stem?: string
    content?: string
    lastModified?: number
  }
  if (!stem || content === undefined) {
    return NextResponse.json({ error: 'stem and content required' }, { status: 400 })
  }

  let vaultRoot: string
  try {
    vaultRoot = resolveVault(vault)
  } catch {
    return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
  }
  const notePath = await findNote(vaultRoot, stem)
  if (!notePath) return NextResponse.json({ error: `Note not found: ${stem}` }, { status: 404 })

  if (!guardPath(notePath, vaultRoot)) {
    return NextResponse.json({ error: 'Path traversal rejected' }, { status: 403 })
  }

  // Conflict detection: if caller provided lastModified and the file
  // has been modified since then, return the current content instead of saving.
  if (lastModified !== undefined) {
    try {
      const stat = await fs.stat(notePath)
      if (stat.mtimeMs > lastModified) {
        const serverContent = await fs.readFile(notePath, 'utf-8')
        return NextResponse.json({ conflict: true, serverContent })
      }
    } catch {
      // If stat fails, proceed with the save
    }
  }

  try {
    await fs.writeFile(notePath, content, 'utf-8')
    return NextResponse.json({ ok: true })
  } catch {
    return NextResponse.json({ error: 'Failed to write note' }, { status: 500 })
  }
}

export async function PUT(req: NextRequest) {
  const authError = requireAuth(req)
  if (authError) return authError
  const vault = req.nextUrl.searchParams.get('vault')
  const body = await req.json()
  const { path: relPath, content } = body as { path?: string; content?: string }
  if (!relPath || content === undefined) {
    return NextResponse.json({ error: 'path and content required' }, { status: 400 })
  }

  // SEC-002: Only .md files may be created through this endpoint.
  if (!relPath.endsWith('.md')) {
    return NextResponse.json({ error: 'Only .md files may be created' }, { status: 400 })
  }

  let vaultRoot: string
  try {
    vaultRoot = resolveVault(vault)
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
}

export async function DELETE(req: NextRequest) {
  const authError = requireAuth(req)
  if (authError) return authError
  const stem = req.nextUrl.searchParams.get('stem')
  const vault = req.nextUrl.searchParams.get('vault')
  if (!stem) return NextResponse.json({ error: 'stem required' }, { status: 400 })

  let vaultRoot: string
  try {
    vaultRoot = resolveVault(vault)
  } catch {
    return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
  }
  const notePath = await findNote(vaultRoot, stem)
  if (!notePath) return NextResponse.json({ error: `Note not found: ${stem}` }, { status: 404 })

  if (!guardPath(notePath, vaultRoot)) {
    return NextResponse.json({ error: 'Path traversal rejected' }, { status: 403 })
  }

  try {
    await fs.unlink(notePath)
    return NextResponse.json({ ok: true })
  } catch {
    return NextResponse.json({ error: 'Failed to delete note' }, { status: 500 })
  }
}
