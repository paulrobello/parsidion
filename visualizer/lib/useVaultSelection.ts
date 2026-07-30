// ARC-037: vault-selection slice extracted from useVisualizerState.ts.
//
// Owns only the persisted selected-vault storage. The cross-cutting cleanup
// that runs when the vault actually changes (clearing tabs, content cache,
// neighborhood focus) lives in the orchestrator, which composes this hook's
// resetters with the other slices' resetters. Keeping that wiring above the
// slice boundary prevents a circular dependency between the vault hook and
// the tab hook.
'use client'

import { useMemo } from 'react'
import { useLocalStorage } from '@/lib/useLocalStorage'

export interface VaultSelectionSlice {
  selectedVault: string | null
  /** Raw persisted setter; the orchestrator wraps this with cross-hook cleanup. */
  setSelectedVaultInternal: (vault: string | null) => void
}

export function useVaultSelection(): VaultSelectionSlice {
  const [selectedVault, setSelectedVaultInternal] = useLocalStorage<string | null>(
    'vv:selectedVault',
    null,
  )

  return useMemo(
    () => ({ selectedVault, setSelectedVaultInternal }),
    [selectedVault, setSelectedVaultInternal],
  )
}
