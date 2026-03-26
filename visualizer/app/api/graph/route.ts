// app/api/graph/route.ts
import { NextRequest, NextResponse } from 'next/server'
import fs from 'fs'
import path from 'path'
import { resolveVault } from '@/lib/vaultResolver'

export async function GET(req: NextRequest) {
  const vault = req.nextUrl.searchParams.get('vault')
  const vaultPath = resolveVault(vault)
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
