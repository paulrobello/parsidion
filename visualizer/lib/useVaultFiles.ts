// lib/useVaultFiles.ts
'use client'

import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import type { VaultFile } from '@/lib/vaultFile'

export type WsStatus = 'connecting' | 'connected' | 'disconnected'
export type VaultFileTree = Map<string, Map<string, VaultFile[]>>

interface Opts {
  /** Called when a vault .md file is modified externally. Receives the vault-relative path. */
  onNoteModified: (notePath: string) => void
  /** Called when the server emits graph:rebuilt — caller should refetch graph.json. */
  onGraphRebuilt: () => void
}

function buildTree(files: VaultFile[]): VaultFileTree {
  const tree: VaultFileTree = new Map()
  for (const file of files) {
    const parts = file.path.replace(/\.md$/, '').split('/')
    const folder = parts[0] || 'Root'
    const subfolder = parts.length > 2 ? parts[1] : ''
    if (!tree.has(folder)) tree.set(folder, new Map())
    const folderMap = tree.get(folder)!
    if (!folderMap.has(subfolder)) folderMap.set(subfolder, [])
    folderMap.get(subfolder)!.push(file)
  }
  for (const [, subMap] of tree) {
    for (const [, notes] of subMap) {
      notes.sort((a, b) => a.stem.localeCompare(b.stem))
    }
  }
  return tree
}

export function useVaultFiles(opts: Opts): {
  fileTree: VaultFileTree
  wsStatus: WsStatus
  totalFiles: number
} {
  const [files, setFiles] = useState<VaultFile[]>([])
  const [wsStatus, setWsStatus] = useState<WsStatus>('connecting')

  // Stable refs for callbacks — avoids stale closures in WS handler
  const onNoteModifiedRef = useRef(opts.onNoteModified)
  const onGraphRebuiltRef = useRef(opts.onGraphRebuilt)
  useEffect(() => { onNoteModifiedRef.current = opts.onNoteModified }, [opts.onNoteModified])
  useEffect(() => { onGraphRebuiltRef.current = opts.onGraphRebuilt }, [opts.onGraphRebuilt])

  const wsRef = useRef<WebSocket | null>(null)
  const retryDelayRef = useRef(1_000)
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const mountedRef = useRef(true)
  // Ref to hold the connect function so ws.onclose can reference it without TDZ issues
  const connectRef = useRef<(() => void) | null>(null)

  // Fetch initial file list once on mount
  useEffect(() => {
    fetch('/api/files')
      .then(r => r.json())
      .then((data: { files?: VaultFile[] }) => {
        if (mountedRef.current) setFiles(data.files ?? [])
      })
      .catch(err => console.warn('[useVaultFiles] /api/files failed:', err))
  }, [])

  const connect = useCallback(() => {
    if (!mountedRef.current) return
    setWsStatus('connecting')

    const protocol = typeof window !== 'undefined' && window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = typeof window !== 'undefined' ? window.location.host : 'localhost:3999'
    const ws = new WebSocket(`${protocol}//${host}/ws/vault`)
    wsRef.current = ws

    ws.onopen = () => {
      if (!mountedRef.current) return
      retryDelayRef.current = 1_000 // reset backoff on successful connect
      setWsStatus('connected')
    }

    ws.onmessage = (event: MessageEvent<string>) => {
      if (!mountedRef.current) return
      try {
        const msg = JSON.parse(event.data) as {
          type: string
          path?: string
          stem?: string
          noteType?: string
        }
        switch (msg.type) {
          case 'ping':
            ws.send(JSON.stringify({ type: 'pong' }))
            break

          case 'file:created':
            if (msg.path && msg.stem) {
              setFiles(prev => {
                // Deduplicate by path
                if (prev.some(f => f.path === msg.path)) return prev
                return [...prev, { stem: msg.stem!, path: msg.path!, noteType: msg.noteType }]
              })
            }
            break

          case 'file:deleted':
            if (msg.path) {
              setFiles(prev => prev.filter(f => f.path !== msg.path))
            }
            break

          case 'file:modified':
            if (msg.path) {
              onNoteModifiedRef.current(msg.path)
            }
            break

          case 'graph:rebuilt':
            onGraphRebuiltRef.current()
            break
        }
      } catch { /* ignore malformed messages */ }
    }

    ws.onclose = () => {
      if (!mountedRef.current) return
      wsRef.current = null
      setWsStatus('disconnected')
      const delay = retryDelayRef.current
      retryDelayRef.current = Math.min(delay * 2, 30_000)
      retryTimerRef.current = setTimeout(() => connectRef.current?.(), delay)
    }

    ws.onerror = () => ws.close()
  }, []) // stable — no deps change after mount

  useEffect(() => {
    mountedRef.current = true
    // Keep connectRef in sync with the stable connect callback so ws.onclose can reference it
    connectRef.current = connect
    connect() // eslint-disable-line react-hooks/set-state-in-effect
    return () => {
      mountedRef.current = false
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  const fileTree = useMemo(() => buildTree(files), [files])

  return { fileTree, wsStatus, totalFiles: files.length }
}
