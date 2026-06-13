# ARC-015: Custom Express Server Evaluation

**Date:** 2026-06-12
**Status:** Deferred — migration is viable but timing is wrong (concurrent agent work in progress)

---

## What `server.ts` does

`server.ts` is a Node.js entry point (`tsx server.ts`) that wraps Next.js with four
responsibilities beyond what `next dev` / `next start` provide:

| Responsibility | Detail |
|---|---|
| WebSocket server | Attaches a `ws` WebSocketServer to the same port (3999) via `noServer: true`, intercepting HTTP upgrade requests for `/ws/vault` before Next.js HMR can claim them |
| Per-vault chokidar watcher | Lazy-creates one `chokidar` watcher per vault path; shared across all clients subscribed to that vault; watches `.md` files only; ignores `.obsidian`, `.git`, `.trash`, `TagsRoutes`, dot-files |
| SEC-009 vault validation at upgrade time | Calls `resolveVault()` from `lib/vaultResolver.ts` during the TCP upgrade handshake; destroys the socket with `HTTP/1.1 400 Bad Request` before the WebSocket opens if the vault path is forbidden |
| Heartbeat | Pings all clients every 30 s; drops clients that miss a pong |

The `vaultBroadcast` EventEmitter (`lib/vaultBroadcast.server.ts`) bridges the
`/api/graph/rebuild` route handler (different file, same process) to WebSocket
clients. When the rebuild POST completes it emits `graph:rebuilt`; `server.ts`
listens and forwards that to all connected WebSocket clients.

The client-side consumer is `lib/useVaultFiles.ts`, which opens a `new WebSocket`
to `ws://host/ws/vault?vault=<name>` and handles five message types:
`ping` (responds with `pong`), `file:created`, `file:deleted`, `file:modified`,
`graph:rebuilt`. It reconnects with exponential backoff on close.

---

## Why a custom server is required today

Next.js App Router route handlers return `Response` objects. They cannot intercept
the HTTP `upgrade` event, so native WebSocket connections are impossible without a
custom server. The framework has no hook for WebSocket lifecycle.

---

## Concrete migration path (SSE route handler)

The only viable Next.js-native replacement is **Server-Sent Events** (SSE) over a
`GET` route handler returning `text/event-stream`. SSE is unidirectional
(server → client), which is sufficient because:

- The client's only upload is a `pong` reply to heartbeat pings — the heartbeat
  can be eliminated or replaced by server-side timeout on `request.signal`.
- Vault subscription is per-connection, set via `?vault=` query param on the
  `EventSource` URL — identical to the current WebSocket query param.

### New files

**`app/api/vault/events/route.ts`** (~80 LOC):

```ts
import { NextRequest } from 'next/server'
import { watch } from 'chokidar'
import path from 'path'
import fs from 'fs'
import { resolveVault, VaultConfigError } from '@/lib/vaultResolver'
import { vaultBroadcast } from '@/lib/vaultBroadcast.server'

// Module-level watcher registry — survives across requests in the same process
const watchers = new Map<string, ReturnType<typeof watch>>()

export async function GET(req: NextRequest) {
  const vault = req.nextUrl.searchParams.get('vault')

  // SEC-009: validate before opening the stream
  let vaultPath: string
  try {
    vaultPath = resolveVault(vault)
  } catch (err) {
    return new Response(
      err instanceof VaultConfigError ? 'Forbidden vault path' : 'Vault resolution error',
      { status: 400 }
    )
  }

  const encoder = new TextEncoder()

  const stream = new ReadableStream({
    start(controller) {
      function send(data: object) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(data)}\n\n`))
      }

      // File watcher (lazy, shared)
      if (!watchers.has(vaultPath)) {
        const watcher = watch(vaultPath, { /* same options as server.ts */ })
        watcher.on('add', fp => fp.endsWith('.md') && send({ type: 'file:created', path: path.relative(vaultPath, fp) }))
        watcher.on('unlink', fp => fp.endsWith('.md') && send({ type: 'file:deleted', path: path.relative(vaultPath, fp) }))
        watcher.on('change', fp => fp.endsWith('.md') && send({ type: 'file:modified', path: path.relative(vaultPath, fp) }))
        watchers.set(vaultPath, watcher)
      }

      // graph:rebuilt bridge
      const onRebuilt = () => send({ type: 'graph:rebuilt' })
      vaultBroadcast.on('graph:rebuilt', onRebuilt)

      // Cleanup when client disconnects
      req.signal.addEventListener('abort', () => {
        vaultBroadcast.off('graph:rebuilt', onRebuilt)
        // Note: do NOT close the watcher here — it is shared across clients.
        // Watcher cleanup on process exit is handled by Node.js naturally.
        controller.close()
      })
    },
  })

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
    },
  })
}
```

### Client changes

`lib/useVaultFiles.ts` needs a full rewrite: replace `new WebSocket(...)` +
`onmessage`/`onclose`/reconnect with `new EventSource(...)` + `onmessage`/
`onerror`/reconnect. The message payload shape is identical (same JSON, same
`type` field). Estimated ~60 LOC change.

### Dependency and script changes

- Remove `ws`, `tsx` from `package.json` (and `@types/ws` from devDependencies).
- `chokidar` stays (still used by the SSE route).
- `"dev": "tsx server.ts"` → `"dev": "next dev --port 3999"`.
- `"start": "node dist/server.js"` → `"start": "next start --port 3999"`.
- `"build"`: remove `&& tsc --project tsconfig.server.json`.
- Delete `server.ts`, `tsconfig.server.json`.
- `lib/vaultBroadcast.server.ts` stays unchanged — same global singleton pattern
  works with `next dev` (same process as route handlers).

---

## Risks

| Risk | Severity | Notes |
|---|---|---|
| Watcher cleanup on process exit | Low | Node.js terminates all handles; the `Map` has no `server.on('close')` equivalent but is acceptable for a local dev tool |
| Shared watcher across SSE connections | Low | Same design as today; no per-connection watcher |
| SSE reconnect on network glitch | Low | `EventSource` auto-reconnects natively; no custom backoff needed (browser handles it), unlike WebSocket which requires manual backoff |
| `bun dev` HMR + long-lived SSE streams | Medium | Next.js dev HMR may reset module state, evicting the `watchers` Map — in-flight SSE clients would need to reconnect. Acceptable for a dev tool; no impact in production (`next start`) |
| `next dev` does not expose raw `http.Server` | Low | Not needed post-migration |
| `request.signal` availability | Low | Available in Next.js App Router GET handlers; confirmed in docs |

---

## Effort estimate

2–4 hours:

1. Write `app/api/vault/events/route.ts` (~80 LOC)
2. Rewrite `lib/useVaultFiles.ts` (~60 LOC delta)
3. Update `package.json` scripts and dependencies
4. Delete `server.ts` and `tsconfig.server.json`
5. Run `bunx tsc --noEmit`, `bun run lint`, `bun run build`

---

## Recommendation

**Defer.** The migration is technically clean and has no framework blockers.
However:

- `lib/useVaultFiles.ts` is currently in scope for concurrent agent work — editing
  it now risks a merge conflict.
- The existing `server.ts` is correct, stable, and well-tested.

Revisit after the concurrent `GraphCanvas.tsx` / `vaultResolver.ts` agent work
is merged. At that point the migration is a self-contained 2–4 hour task with
clear verification steps.
