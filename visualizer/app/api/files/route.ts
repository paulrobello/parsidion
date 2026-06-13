import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import type { VaultFile } from '@/lib/vaultFile'
import { resolveVault, VaultConfigError } from '@/lib/vaultResolver'

const EXCLUDED_DIRS = new Set(['.obsidian', 'Templates', '.git', '.trash', 'TagsRoutes'])

function parseFrontmatterType(content: string): string | undefined {
  const match = content.match(/^---\n[\s\S]*?^type:\s*(.+)$/m)
  return match?.[1]?.trim()
}

function walkVault(dir: string, vaultRoot: string, results: VaultFile[]): void {
  let entries: fs.Dirent[]
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true })
  } catch {
    return
  }

  for (const entry of entries) {
    if (entry.name.startsWith('.')) continue
    const full = path.join(dir, entry.name)

    if (entry.isDirectory()) {
      if (EXCLUDED_DIRS.has(entry.name)) continue
      walkVault(full, vaultRoot, results)
    } else if (entry.isFile() && entry.name.endsWith('.md')) {
      const relPath = path.relative(vaultRoot, full)
      const stem = entry.name.replace(/\.md$/, '')
      let noteType: string | undefined
      try {
        const content = fs.readFileSync(full, 'utf-8')
        noteType = parseFrontmatterType(content)
      } catch { /* skip unreadable */ }
      results.push({ stem, path: relPath, noteType })
    }
  }
}

export async function GET(req: NextRequest) {
  const vault = req.nextUrl.searchParams.get('vault')
  let vaultRoot: string
  try {
    vaultRoot = resolveVault(vault)
  } catch (err) {
    if (err instanceof VaultConfigError) {
      return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
    }
    return NextResponse.json({ error: 'Failed to resolve vault' }, { status: 500 })
  }
  const files: VaultFile[] = []
  walkVault(vaultRoot, vaultRoot, files)
  return NextResponse.json({ files })
}
