// app/api/graph/route.ts
import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { resolveVault, VaultConfigError } from '@/lib/vaultResolver'

export async function GET(req: NextRequest) {
  const vault = req.nextUrl.searchParams.get('vault')
  let vaultPath: string
  try {
    vaultPath = resolveVault(vault)
  } catch (err) {
    if (err instanceof VaultConfigError) {
      return NextResponse.json({ error: 'Invalid vault path' }, { status: 400 })
    }
    return NextResponse.json({ error: 'Failed to resolve vault' }, { status: 500 })
  }
  const graphPath = path.join(vaultPath, 'graph.json')

  if (!fs.existsSync(graphPath)) {
    return NextResponse.json(
      { error: `graph.json not found in vault: ${vaultPath}` },
      { status: 404 }
    )
  }

  const content = fs.readFileSync(graphPath, 'utf-8')
  return new NextResponse(content, {
    headers: { 'Content-Type': 'application/json' },
  })
}
