// components/VaultStats.tsx
'use client'

import { useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { ConfirmDialog } from './ConfirmDialog'

interface Props {
  /** Vault name or path — same value passed to the vault selector. */
  vault: string | null
  /** Live total note count (from useVaultFiles, WS-driven). */
  totalNotes: number
}

const POLL_MS = 60_000
const RUN_POLL_MS = 5_000
const FLASH_MS = 4_000

interface Progress {
  total: number
  processed: number
  written: number
  skipped: number
  errors: number
  current: string
  pct: string
}

interface StatusResp {
  running: boolean
  error?: string
  progress: Progress | null
  pendingSummaries: number
}

/**
 * Compact header chips for at-a-glance vault health:
 *   • PEND  — sessions queued in pending_summaries.jsonl (amber when >0);
 *             click to run the summarizer. Morphs into a live progress chip
 *             while a run is in flight.
 *   • NOTES — total note count (passed in live from useVaultFiles).
 */
export function VaultStats({ vault, totalNotes }: Props) {
  const [pending, setPending] = useState(0)
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [flash, setFlash] = useState<string | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [hover, setHover] = useState(false)
  const mountedRef = useRef(true)
  const runningRef = useRef(false)
  const prevRunningRef = useRef(false)
  const flashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const q = vault ? `?vault=${encodeURIComponent(vault)}` : ''
  const statsUrl = `/api/stats${q}`
  const statusUrl = `/api/summarizer/status${q}`
  const summarizeUrl = `/api/summarize${q}`

  useEffect(() => {
    runningRef.current = running
  }, [running])

  const flashMsg = (msg: string) => {
    if (!mountedRef.current) return
    setFlash(msg)
    if (flashTimerRef.current) clearTimeout(flashTimerRef.current)
    flashTimerRef.current = setTimeout(() => {
      if (mountedRef.current) setFlash(null)
    }, FLASH_MS)
  }

  const fetchPending = () => {
    fetch(statsUrl)
      .then(r => r.json())
      .then((d: { pendingSummaries?: number }) => {
        if (mountedRef.current) setPending(d.pendingSummaries ?? 0)
      })
      .catch(err => console.warn('[VaultStats] /api/stats failed:', err))
  }

  const fetchStatus = async (): Promise<boolean> => {
    try {
      const res = await fetch(statusUrl)
      if (!res.ok) return runningRef.current
      const data = (await res.json()) as StatusResp
      if (!mountedRef.current) return data.running
      setRunning(data.running)
      setProgress(data.progress ?? null)
      setPending(data.pendingSummaries ?? 0)
      return data.running
    } catch (err) {
      console.warn('[VaultStats] /api/summarizer/status failed:', err)
      return runningRef.current
    }
  }

  // Mount / vault-change: detect an in-progress run, then baseline pending poll.
  useEffect(() => {
    mountedRef.current = true
    void fetchStatus()
    fetchPending()
    const interval = setInterval(fetchPending, POLL_MS)
    const onVisible = () => {
      if (document.hidden) return
      fetchPending()
      if (runningRef.current) void fetchStatus()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      mountedRef.current = false
      clearInterval(interval)
      document.removeEventListener('visibilitychange', onVisible)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vault])

  // Fast poll while a run is active.
  useEffect(() => {
    if (!running) return
    const id = setInterval(() => {
      void fetchStatus()
    }, RUN_POLL_MS)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running])

  // Detect run finish (true → false): refresh the view, sync pending, flash feedback.
  useEffect(() => {
    if (prevRunningRef.current && !running) {
      void refreshVaultView()
      fetchPending()
      const failed = !!progress && progress.errors > 0
      flashMsg(failed ? '⚠ Summarizer finished with errors' : '✓ Summarizer finished')
    }
    prevRunningRef.current = running
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running])

  // Rebuild graph.json after a summarizer run. The summarizer doesn't rebuild
  // graph.json by default and can't signal this server; /api/graph/rebuild
  // rebuilds it AND broadcasts graph:rebuilt, which reloads the graph and
  // (via useVaultFiles) refreshes the file list — so new notes appear in both.
  const refreshVaultView = async () => {
    try {
      await fetch(`/api/graph/rebuild${q}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
    } catch (err) {
      console.warn('[VaultStats] post-run graph rebuild failed:', err)
    }
  }

  const handleConfirm = async () => {
    setConfirmOpen(false)
    try {
      const res = await fetch(summarizeUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
      const data = (await res.json().catch(() => ({}))) as { started?: boolean; alreadyRunning?: boolean; error?: string }
      if (res.ok && data.started) {
        setRunning(true)
        void fetchStatus()
      } else if (res.status === 409 || data.alreadyRunning) {
        setRunning(true)
        void fetchStatus()
      } else {
        flashMsg(`⚠ ${data.error ?? 'Failed to start summarizer'}`)
      }
    } catch {
      flashMsg('⚠ Failed to start summarizer')
    }
  }

  const pendingActive = pending > 0
  const clickable = !running && pendingActive

  // ---- popover text/color ----
  const popoverText =
    flash ??
    (running
      ? progress
        ? `Running — ${progress.processed}/${progress.total} done (${progress.written} written)`
        : 'Summarizer starting…'
      : pendingActive
        ? `${pending} session${pending === 1 ? '' : 's'} queued — click to run summarizer`
        : 'No sessions pending summarization')
  const popoverColor = flash
    ? flash.startsWith('⚠')
      ? '#ef4444'
      : '#10b981'
    : running
      ? '#f59e0b'
      : pendingActive
        ? '#f59e0b'
        : '#10b981'

  const chipBase: CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    background: 'rgba(15,23,42,0.92)',
    border: '1px solid #1e293b',
    borderRadius: 5,
    padding: '4px 10px',
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 10,
    lineHeight: 1,
    whiteSpace: 'nowrap',
  }

  const dotStyle = (color: string, pulse: boolean): CSSProperties => ({
    color,
    fontSize: 8,
    animation: pulse ? 'vault-pulse 1.2s ease-in-out infinite' : 'none',
  })

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
      {/* Pending / summarizer chip */}
      <div
        style={{ position: 'relative', cursor: clickable ? 'pointer' : 'default', ...chipBase }}
        onClick={clickable ? () => setConfirmOpen(true) : undefined}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
      >
        {running ? (
          <>
            <span style={dotStyle('#f59e0b', true)}>●</span>
            <span style={{ color: '#f59e0b' }}>
              {progress ? `${progress.processed}/${progress.total}` : 'RUN…'}
            </span>
          </>
        ) : (
          <>
            <span style={dotStyle(pendingActive ? '#f59e0b' : '#10b981', false)}>●</span>
            <span style={{ color: pendingActive ? '#f59e0b' : '#6b7a99' }}>PEND</span>
            <span style={{ color: '#e8e8f0' }}>{pending}</span>
          </>
        )}
        {(hover || flash) && (
          <div
            style={{
              position: 'absolute',
              top: 'calc(100% + 8px)',
              right: 0,
              background: '#0d1224',
              border: '1px solid #1e293b',
              borderRadius: 5,
              padding: '5px 10px',
              whiteSpace: 'nowrap',
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: 10,
              color: popoverColor,
              boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
              pointerEvents: 'none',
              zIndex: 100,
            }}
          >
            {popoverText}
          </div>
        )}
      </div>

      {/* Total notes chip */}
      <div style={chipBase}>
        <span style={dotStyle('#00FFC8', false)}>●</span>
        <span style={{ color: '#6b7a99' }}>NOTES</span>
        <span style={{ color: '#e8e8f0' }}>{totalNotes}</span>
      </div>

      {confirmOpen && (
        <ConfirmDialog
          title="Run summarizer now?"
          message={
            pending > 0
              ? `Summarize ${pending} pending session${pending === 1 ? '' : 's'}? This runs the parsidion summarizer via your configured AI backend — it writes notes to the vault and may take a few minutes.`
              : 'There are no pending sessions to summarize.'
          }
          confirmLabel="Run summarizer"
          cancelLabel="Cancel"
          onConfirm={() => {
            void handleConfirm()
          }}
          onCancel={() => setConfirmOpen(false)}
        />
      )}
    </div>
  )
}
