import { EventEmitter } from 'events'

// ARC-039: raise the default 10-listener cap. Each per-vault SSE subscriber
// registers its own 'graph:rebuilt' listener; with 10+ tabs open against the
// same vault Node would print a MaxListenersExceededWarning on every emit,
// and at 11+ the warning becomes a leak signal that's hard to tell apart
// from a real bug. 50 covers heavy multi-window use without hiding a true
// leak (an actual leak would climb past it eventually and we'd see the
// warning again). Set on the shared global so reconnecting dev servers
// inherit it.
const MAX_SSE_LISTENERS = 50

declare global {
  var __vaultBroadcast__: EventEmitter | undefined
}

if (!global.__vaultBroadcast__) {
  global.__vaultBroadcast__ = new EventEmitter()
  global.__vaultBroadcast__.setMaxListeners(MAX_SSE_LISTENERS)
}

export const vaultBroadcast: EventEmitter = global.__vaultBroadcast__
