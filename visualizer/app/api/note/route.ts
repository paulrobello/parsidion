import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'

function getVaultRoot() {
  return process.env.VAULT_ROOT || path.join(process.env.HOME || '~', 'ClaudeVault')
}

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
        const fileStem = entry.name.replace(/\.md$/, '')
        if (fileStem === stemToFind) return full
      }
    }
  } catch { /* skip unreadable dirs */ }
  return null
}

export async function GET(req: NextRequest) {
  const stem = req.nextUrl.searchParams.get('stem')
  if (!stem) return NextResponse.json({ error: 'stem required' }, { status: 400 })

  const vaultRoot = getVaultRoot()
  const notePath = findNote(vaultRoot, stem)
  if (!notePath) return NextResponse.json({ error: `Note not found: ${stem}` }, { status: 404 })

  try {
    const content = fs.readFileSync(notePath, 'utf-8')
    const relativePath = path.relative(vaultRoot, notePath)
    return NextResponse.json({ content, path: relativePath })
  } catch {
    return NextResponse.json({ error: 'Failed to read note' }, { status: 500 })
  }
}

function guardPath(notePath: string, vaultRoot: string): boolean {
  const resolved = path.resolve(notePath)
  const resolvedRoot = path.resolve(vaultRoot)
  return resolved.startsWith(resolvedRoot + path.sep)
}

export async function POST(req: NextRequest) {
  const body = await req.json()
  const { stem, content } = body as { stem?: string; content?: string }
  if (!stem || content === undefined) {
    return NextResponse.json({ error: 'stem and content required' }, { status: 400 })
  }

  const vaultRoot = getVaultRoot()
  const notePath = findNote(vaultRoot, stem)
  if (!notePath) return NextResponse.json({ error: `Note not found: ${stem}` }, { status: 404 })

  if (!guardPath(notePath, vaultRoot)) {
    return NextResponse.json({ error: 'Path traversal rejected' }, { status: 403 })
  }

  try {
    fs.writeFileSync(notePath, content, 'utf-8')
    return NextResponse.json({ ok: true })
  } catch {
    return NextResponse.json({ error: 'Failed to write note' }, { status: 500 })
  }
}

export async function PUT(req: NextRequest) {
  const body = await req.json()
  const { path: relPath, content } = body as { path?: string; content?: string }
  if (!relPath || content === undefined) {
    return NextResponse.json({ error: 'path and content required' }, { status: 400 })
  }

  const vaultRoot = getVaultRoot()
  const notePath = path.join(vaultRoot, relPath)

  if (!guardPath(notePath, vaultRoot)) {
    return NextResponse.json({ error: 'Path traversal rejected' }, { status: 403 })
  }

  if (fs.existsSync(notePath)) {
    return NextResponse.json({ error: 'Note already exists' }, { status: 409 })
  }

  try {
    fs.mkdirSync(path.dirname(notePath), { recursive: true })
    fs.writeFileSync(notePath, content, 'utf-8')
    return NextResponse.json({ ok: true, path: relPath })
  } catch {
    return NextResponse.json({ error: 'Failed to create note' }, { status: 500 })
  }
}

export async function DELETE(req: NextRequest) {
  const stem = req.nextUrl.searchParams.get('stem')
  if (!stem) return NextResponse.json({ error: 'stem required' }, { status: 400 })

  const vaultRoot = getVaultRoot()
  const notePath = findNote(vaultRoot, stem)
  if (!notePath) return NextResponse.json({ error: `Note not found: ${stem}` }, { status: 404 })

  if (!guardPath(notePath, vaultRoot)) {
    return NextResponse.json({ error: 'Path traversal rejected' }, { status: 403 })
  }

  try {
    fs.unlinkSync(notePath)
    return NextResponse.json({ ok: true })
  } catch {
    return NextResponse.json({ error: 'Failed to delete note' }, { status: 500 })
  }
}
