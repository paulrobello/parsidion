'use client'

import { useState, useEffect, useRef } from 'react'

interface VaultInfo {
  name: string
  path: string
  isDefault: boolean
}

interface VaultsResponse {
  vaults: VaultInfo[]
  defaultVault: string
}

interface VaultSelectorProps {
  selectedVault: string | null
  onSelect: (vault: string | null) => void
}

export function VaultSelector({ selectedVault, onSelect }: VaultSelectorProps) {
  const [vaults, setVaults] = useState<VaultInfo[]>([])
  const [isOpen, setIsOpen] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)

  // Fetch vaults on mount
  useEffect(() => {
    fetch('/api/vaults')
      .then(r => r.json())
      .then((data: VaultsResponse) => {
        setVaults(data.vaults ?? [])
        setLoading(false)
      })
      .catch(err => {
        setError(err.message)
        setLoading(false)
      })
  }, [])

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Determine display name
  const displayName = selectedVault
    ? vaults.find(v => v.name === selectedVault)?.name ?? selectedVault
    : vaults.find(v => v.isDefault)?.name ?? 'default'

  if (loading) {
    return (
      <div className="flex items-center gap-1.5 px-2 py-1 text-xs text-zinc-400">
        <svg className="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
        <span>Loading...</span>
      </div>
    )
  }

  if (error || vaults.length === 0) {
    // Single vault mode - no selector needed
    return null
  }

  return (
    <div ref={dropdownRef} className="relative">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-1.5 px-2 py-1 text-xs font-medium text-zinc-300 bg-zinc-800/50 hover:bg-zinc-700/50 rounded border border-zinc-700/50 transition-colors"
      >
        <svg className="w-3.5 h-3.5 text-zinc-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
        </svg>
        <span className="max-w-[100px] truncate">{displayName}</span>
        <svg className={`w-3 h-3 text-zinc-500 transition-transform ${isOpen ? 'rotate-180' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M6 9l6 6 6-6" />
        </svg>
      </button>

      {isOpen && (
        <div className="absolute top-full left-0 mt-1 w-48 bg-zinc-900 border border-zinc-700 rounded-md shadow-lg z-50 py-1">
          {vaults.map(vault => (
            <button
              key={vault.name}
              onClick={() => {
                // Always carry the explicit vault name, even for the default
                // vault: a null mapping would degrade the request to unnamed
                // default resolution instead of the vault the user picked.
                onSelect(vault.name)
                setIsOpen(false)
              }}
              className={`w-full text-left px-3 py-1.5 text-xs flex items-center gap-2 transition-colors ${
                (selectedVault === vault.name || (!selectedVault && vault.isDefault))
                  ? 'bg-blue-500/20 text-blue-300'
                  : 'text-zinc-300 hover:bg-zinc-800'
              }`}
            >
              <svg className="w-3.5 h-3.5 flex-shrink-0 text-zinc-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
              </svg>
              <span className="truncate flex-1">{vault.name}</span>
              {vault.isDefault && (
                <span className="text-[10px] text-zinc-500 uppercase tracking-wide">default</span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
