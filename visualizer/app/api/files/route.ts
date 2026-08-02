import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs/promises'
import path from 'path'
import type { VaultFile } from '@/lib/vaultFile'
import { resolveVault, VaultConfigError } from '@/lib/vaultResolver'
import { withApi } from '@/lib/apiAuth'

const EXCLUDED_DIRS = new Set(['.obsidian', 'Templates', '.git', '.trash', 'TagsRoutes'])

function parseFrontmatterType(content: string): string | undefined {
  const match = content.match(/^---\n[\s\S]*?^type:\s*(.+)$/m)
  return match?.[1]?.trim()
}

async function walkVault(dir: string, vaultRoot: string, results: VaultFile[]): Promise<void> {
  let entries: import('fs').Dirent[]
  try {
    entries = await fs.readdir(dir, { withFileTypes: true })
  } catch {
    return
  }

  for (const entry of entries) {
    if (entry.name.startsWith('.')) continue
    const full = path.join(dir, entry.name)

    if (entry.isDirectory()) {
      if (EXCLUDED_DIRS.has(entry.name)) continue
      await walkVault(full, vaultRoot, results)
    } else if (entry.isFile() && entry.name.endsWith('.md')) {
      const relPath = path.relative(vaultRoot, full)
      const stem = entry.name.replace(/\.md$/, '')
      let noteType: string | undefined
      try {
        const content = await fs.readFile(full, 'utf-8')
        noteType = parseFrontmatterType(content)
      } catch { /* skip unreadable */ }
      results.push({ stem, path: relPath, noteType })
    }
  }
}

export const GET = withApi(async (req: NextRequest) => {
  const vault = req.nextUrl.searchParams.get('vault')
  let vaultRoot: string
  try {
    vaultRoot = await resolveVault(vault)
  } catch (err) {
    if (err instanceof VaultConfigError) {
      return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
    }
    return NextResponse.json({ error: 'Failed to resolve vault' }, { status: 500 })
  }
  const files: VaultFile[] = []
  await walkVault(vaultRoot, vaultRoot, files)
  return NextResponse.json({ files })
})
