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
