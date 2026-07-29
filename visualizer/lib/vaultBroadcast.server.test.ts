// lib/vaultBroadcast.server.test.ts
// ARC-039: the EventEmitter shared across SSE subscribers must have its
// default 10-listener cap raised, otherwise 10+ tabs against the same vault
// print a MaxListenersExceededWarning on every emit.
import { describe, it, expect } from 'bun:test'
import { vaultBroadcast } from './vaultBroadcast.server'

describe('ARC-039 — vaultBroadcast listener cap', () => {
  it('maxListeners is raised above the Node default of 10', () => {
    expect(vaultBroadcast.getMaxListeners()).toBeGreaterThan(10)
  })

  it('registered listeners still fire on emit', () => {
    let fired = 0
    const listener = () => { fired++ }
    vaultBroadcast.on('graph:rebuilt', listener)
    try {
      vaultBroadcast.emit('graph:rebuilt', { vault: '/tmp/x' })
      expect(fired).toBe(1)
    } finally {
      vaultBroadcast.off('graph:rebuilt', listener)
    }
  })

  it('payload is forwarded to the listener (used by SSE route for vault scoping)', () => {
    let captured: { vault?: string } | undefined
    const listener = (p?: { vault?: string }) => { captured = p }
    vaultBroadcast.on('graph:rebuilt', listener)
    try {
      vaultBroadcast.emit('graph:rebuilt', { vault: '/tmp/some-vault' })
      expect(captured).toEqual({ vault: '/tmp/some-vault' })
    } finally {
      vaultBroadcast.off('graph:rebuilt', listener)
    }
  })
})
