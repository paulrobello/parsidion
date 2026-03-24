import { EventEmitter } from 'events'

declare global {
  var __vaultBroadcast__: EventEmitter | undefined
}

if (!global.__vaultBroadcast__) {
  global.__vaultBroadcast__ = new EventEmitter()
}

export const vaultBroadcast: EventEmitter = global.__vaultBroadcast__
