// app/api/vaults/route.ts
// Lists available vaults from vaults.yaml configuration.

import { NextResponse } from 'next/server'
import { listNamedVaults, getDefaultVault } from '@/lib/vaultResolver'
import { withApi } from '@/lib/apiAuth'

export interface VaultInfo {
  name: string
  path: string
  isDefault: boolean
}

export const GET = withApi(async () => {
  const namedVaults = listNamedVaults()
  const defaultVaultPath = getDefaultVault()

  // Build vault list with default marker
  const vaults: VaultInfo[] = namedVaults.map(v => ({
    name: v.name,
    path: v.path,
    isDefault: v.path === defaultVaultPath
  }))

  // If no named vaults, include the default vault as "default"
  if (vaults.length === 0) {
    vaults.push({
      name: 'default',
      path: defaultVaultPath,
      isDefault: true
    })
  }

  // Ensure at least one vault is marked as default
  const hasDefault = vaults.some(v => v.isDefault)
  if (!hasDefault && vaults.length > 0) {
    // Mark the first one as default if none match the default path
    vaults[0].isDefault = true
  }

  return NextResponse.json({
    vaults,
    defaultVault: vaults.find(v => v.isDefault)?.name || vaults[0]?.name || 'default'
  })
})
