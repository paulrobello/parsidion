// bun.test.setup.ts
// ARC-041: preload run before every `bun test` invocation.
//
// `import 'server-only'` is a marker that throws when a module is bundled into
// the client graph by `next build`. At bundle time Next.js swaps the package
// for a no-op under its `react-server` export condition, and leaves the throw
// in place under the default condition — so a `'use client'` file that imports
// a server-guarded module fails the build rather than silently shipping
// `child_process`/`fs` to the browser.
//
// Bun's test runner is not Next's bundler and uses the default export
// condition, so the marker throws at import time. That breaks the
// route-enumeration test (apiRoutes.test.ts) and the searchServer unit tests,
// which need to load the guarded modules without any client graph being
// involved. We register a no-op module mock so those imports succeed; the
// real guarantee is enforced by `next build`, which the gate runs separately.
import { mock } from 'bun:test'

mock.module('server-only', () => ({}))


// QA-006: register happy-dom as the test DOM before any component test
// imports React DOM. Registration is idempotent; library-only tests are
// unaffected (they never touch the DOM globals).
//
// The registrator also copies happy-dom's fetch/Request/Response onto
// globalThis, which breaks the API-route tests (bun's native Request
// semantics differ — the route guards read headers off native Request
// objects). Restore bun's natives right after registering so both worlds
// coexist: window/document come from happy-dom, the fetch family stays
// bun's.
import { GlobalRegistrator } from '@happy-dom/global-registrator'

if (typeof globalThis.window === 'undefined') {
  const bunNatives = {
    fetch: globalThis.fetch,
    Request: globalThis.Request,
    Response: globalThis.Response,
    Headers: globalThis.Headers,
    FormData: globalThis.FormData,
    Blob: globalThis.Blob,
    AbortController: globalThis.AbortController,
    AbortSignal: globalThis.AbortSignal,
  }
  GlobalRegistrator.register()
  Object.assign(globalThis, bunNatives)
}
