// lib/apiAuth.ts
// SEC-004 / SEC-102: auth + CSRF hardening for the visualizer API.
//
// Three layers of protection:
//
// 1. Content-Type guard (always active, mutation methods only):
//    Mutation endpoints (POST/PUT/DELETE) must send Content-Type: application/json.
//    This blocks simple-form-based CSRF from any browser tab because browsers
//    cannot set a custom Content-Type on a cross-origin request without a
//    CORS preflight, and the visualizer's CORS policy only allows same-origin.
//
// 2. Bearer token guard (optional, activates when VISUALIZER_TOKEN env var is set):
//    When VISUALIZER_TOKEN is set at server start, every API request — including
//    GETs — must carry the header `Authorization: Bearer <token>`. The
//    comparison uses crypto.timingSafeEqual so the token cannot be recovered
//    via a timing side channel. The local UX is unchanged when the variable
//    is absent (single-user workstation default). SEC-102 closed this gap on
//    read routes; previously requireAuth() only ran on mutations, so the
//    documented hardening step protected nothing on reads.
//
// 3. Same-origin guard for browser GETs:
//    requireSameOrigin rejects requests whose Sec-Fetch-Site header is
//    'cross-site'. Non-browser clients (curl) omit the header and are allowed
//    through this layer — layer 2 (the token) is what protects reads against
//    non-browser clients on a hostile network.
//
// Usage in a route handler:
//
//   import { withApi } from '@/lib/apiAuth'
//   ...
//   export const GET = withApi(async (req) => { ... })
//   export const POST = withApi(async (req) => { ... }, { mutation: true })
//
// ARC-014: routes should use `withApi` rather than calling the guards by
// hand. Hand-application is what allowed `/api/stats` to ship with zero
// guards (the only such route), so the wrapper plus an enumeration test
// (`apiRoutes.test.ts`) is the durable fix: a new route physically cannot
// forget the guards because the test imports every module under `app/api/`
// and asserts each HTTP-method export was created by `withApi`.
//
// Direct call (still exported for unusual cases — middleware, tests):
//
//   import { requireAuth, requireSameOrigin, requireToken } from '@/lib/apiAuth'
//   ...
//   // SEC-102: token first (covers non-browser clients), then same-origin
//   // (covers browser drive-by). Mutations additionally call requireAuth,
//   // which performs the Content-Type + token checks.
//   const tokenError = requireToken(req)
//   if (tokenError) return tokenError
//   const originError = requireSameOrigin(req)
//   if (originError) return originError

import { NextRequest, NextResponse } from 'next/server'
import crypto from 'crypto'

const MUTATION_METHODS = new Set(['POST', 'PUT', 'DELETE', 'PATCH'])

// SEC-001: the visualizer binds to loopback only, so every legitimate request
// — browser same-origin or curl from the same machine — carries a loopback
// Host header. A DNS-rebinding page defeats Sec-Fetch-Site (the browser sees
// its rebound origin as same-origin), but it cannot rewrite the Host header
// the browser sends for the rebound name, so Host is the one signal that
// survives the attack.
const LOCAL_HOSTNAMES = new Set(['127.0.0.1', 'localhost', '::1'])
const DEFAULT_VISUALIZER_PORT = '3999'

function expectedServerPort(): string {
  return process.env.PORT ?? DEFAULT_VISUALIZER_PORT
}

/**
 * Rejects requests whose `Host` header is not a loopback address on the port
 * the server is bound to (SEC-001, DNS-rebinding defence).
 *
 * Allowed hosts: `127.0.0.1`, `localhost`, `::1` (bracketed or bare). The
 * port must match `process.env.PORT` (default `3999`, matching the pinned
 * `--port 3999` in the dev/start scripts); a portless Host header is accepted
 * only when the default port is in effect. An absent Host header is allowed
 * through this layer: browsers always send Host (the rebinding page cannot
 * forge it, which is the whole point of this guard), so an absent header
 * means a non-browser client — the same class requireSameOrigin passes to
 * requireToken, and the token is that class's defence.
 *
 * @returns A 403 NextResponse when the Host is present and not
 *   loopback-on-this-port, or null when the request is permitted.
 */
export function requireLocalHost(req: NextRequest): NextResponse | null {
  const hostHeader = req.headers.get('host')
  if (!hostHeader) {
    return null
  }
  let host = hostHeader.trim().toLowerCase()
  let port: string | null = null
  if (host.startsWith('[')) {
    // IPv6 form: "[::1]:3999" (or "[::1]").
    const close = host.indexOf(']')
    if (close === -1) {
      return NextResponse.json({ error: 'Forbidden' }, { status: 403 })
    }
    const suffix = host.slice(close + 1)
    port = suffix.startsWith(':') ? suffix.slice(1) : null
    host = host.slice(1, close)
  } else {
    const colon = host.lastIndexOf(':')
    if (colon !== -1) {
      port = host.slice(colon + 1)
      host = host.slice(0, colon)
    }
  }
  if (!LOCAL_HOSTNAMES.has(host)) {
    return NextResponse.json({ error: 'Forbidden' }, { status: 403 })
  }
  const expected = expectedServerPort()
  if (port === null) {
    // A portless Host header only matches the default-port deployment.
    if (expected !== DEFAULT_VISUALIZER_PORT) {
      return NextResponse.json({ error: 'Forbidden' }, { status: 403 })
    }
  } else if (port !== expected) {
    return NextResponse.json({ error: 'Forbidden' }, { status: 403 })
  }
  return null
}

/**
 * Rejects cross-site GET requests using the `Sec-Fetch-Site` header.
 *
 * GET routes aren't covered by requireAuth()'s Content-Type guard — a simple
 * cross-site `fetch()` GET doesn't trigger a CORS preflight, so a drive-by
 * page could otherwise trigger server-side side effects (recursive directory
 * walks, subprocess spawns) even though it can't read the response body.
 * `Sec-Fetch-Site` is set by the browser and can't be spoofed by page script;
 * Origin is not used here because browsers omit it on many legitimate
 * same-origin GETs. Non-browser clients typically omit `Sec-Fetch-Site`
 * entirely and are allowed through; they are authenticated by
 * {@link requireToken} when VISUALIZER_TOKEN is configured.
 *
 * @returns A 403 NextResponse when the request is cross-site, or null when
 *   the request is permitted.
 */
export function requireSameOrigin(req: NextRequest): NextResponse | null {
  const site = req.headers.get('sec-fetch-site')
  if (site === 'cross-site') {
    return NextResponse.json({ error: 'Cross-site requests are not allowed' }, { status: 403 })
  }
  return null
}

/**
 * Checks the bearer token when `VISUALIZER_TOKEN` is set at server start.
 *
 * The comparison is constant-time via `crypto.timingSafeEqual`; the
 * length-equality check that gates it is also constant-time for the
 * equal-length case and deliberately does not short-circuit. Returns null
 * (no auth required) when the env var is absent — i.e. the single-user
 * workstation default is unchanged.
 *
 * SEC-102: this runs on every GET handler as well as on mutations. Earlier
 * releases only checked the token inside `requireAuth`, which mutation
 * routes call but read routes do not.
 *
 * @returns A 401 NextResponse when the token is configured and missing or
 *   mismatched, or null when the request is permitted.
 */
export function requireToken(req: NextRequest): NextResponse | null {
  const token = process.env.VISUALIZER_TOKEN
  if (!token) {
    return null
  }
  const auth = req.headers.get('authorization') ?? ''
  const provided = auth.startsWith('Bearer ') ? auth.slice('Bearer '.length) : ''
  // Constant-time comparison. timingSafeEqual requires equal-length buffers,
  // so the length check comes first; the comparison itself never short-
  // circuits on the equal-length path, and the length mismatch path is
  // constant for a given configured-token length.
  if (provided.length !== token.length) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }
  const expectedBuf = Buffer.from(token, 'utf8')
  const providedBuf = Buffer.from(provided, 'utf8')
  if (!crypto.timingSafeEqual(expectedBuf, providedBuf)) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
  }
  return null
}

/**
 * Checks auth/CSRF guards for a mutation request.
 *
 * Runs the Content-Type check on mutation methods, then the bearer token
 * check via {@link requireToken}. GET handlers should call `requireToken`
 * then `requireSameOrigin` directly rather than this helper, because the
 * Content-Type check is meaningless on a GET.
 *
 * @returns A NextResponse with a 4xx status when the request should be
 *   rejected, or null when the request is permitted.
 */
export function requireAuth(req: NextRequest): NextResponse | null {
  // 1. Content-Type check for mutation methods
  if (MUTATION_METHODS.has(req.method)) {
    const ct = req.headers.get('content-type') ?? ''
    if (!ct.includes('application/json')) {
      return NextResponse.json(
        { error: 'Content-Type must be application/json' },
        { status: 415 }
      )
    }
  }

  // 2. Bearer token check (delegates to requireToken so the comparison
  //    stays constant-time in one place).
  return requireToken(req)
}

/**
 * Result of running all relevant guards on a request. Routes either return
 * the rejection response directly or use {@link withApi}, which performs the
 * same checks inline and short-circuits on failure.
 */
export type GuardResult = NextResponse | null

/**
 * Runs every guard a handler needs, in the same order SEC-102 established:
 *   1. {@link requireLocalHost} — Host-header allowlist (SEC-001; defeats
 *      DNS rebinding, which presents as same-origin to the other guards).
 *   2. {@link requireToken} — bearer-token check (covers non-browser clients
 *      on every method when VISUALIZER_TOKEN is set).
 *   3. {@link requireSameOrigin} — Sec-Fetch-Site check (covers browser
 *      drive-by on every method).
 *   4. For mutations (POST/PUT/DELETE/PATCH), additionally the Content-Type
 *      check from {@link requireAuth}. The token check inside requireAuth
 *      is a duplicate of step 2, so for read methods we skip requireAuth
 *      entirely (the Content-Type check is meaningless on a GET anyway).
 *
 * @returns A NextResponse when the request must be rejected, or null when
 *   the handler should run.
 */
// SEC-030: mutation bodies are capped at 10 MiB. Note writes hit this before
// req.json() buffers the payload; the check uses Content-Length when the
// client sends it (fetch always does for string/JSON bodies).
const MAX_MUTATION_BODY_BYTES = 10 * 1024 * 1024

export function runGuards(req: NextRequest, opts?: { mutation?: boolean }): GuardResult {
  const hostError = requireLocalHost(req)
  if (hostError) return hostError
  const tokenError = requireToken(req)
  if (tokenError) return tokenError
  const originError = requireSameOrigin(req)
  if (originError) return originError
  if (opts?.mutation) {
    const contentLength = Number(req.headers.get('content-length') ?? '0')
    if (Number.isFinite(contentLength) && contentLength > MAX_MUTATION_BODY_BYTES) {
      return NextResponse.json({ error: 'Request body too large' }, { status: 413 })
    }
    const authError = requireAuth(req)
    // requireAuth re-runs requireToken; the second call is a cheap no-op
    // (constant-time compare against the same env var) and keeps the guard
    // chain structurally uniform.
    if (authError) return authError
  }
  return null
}

/**
 * Wraps a Next.js App Router route handler so the SEC-102 token + same-origin
 * guards (and for mutations, the Content-Type check) cannot be forgotten.
 *
 * ARC-014: SEC-102 applied the guards by hand to every GET handler, and
 * `/api/stats` was caught without them — the only route in the app. A
 * wrapper plus an enumeration test (see `apiRoutes.test.ts`) makes a future
 * route physically unable to skip the guards: the test imports every module
 * under `app/api/` and asserts each HTTP-method export was created by
 * `withApi`.
 *
 * Usage:
 *   export const GET = withApi(async (req) => { ... })
 *   export const POST = withApi(async (req) => { ... }, { mutation: true })
 *
 * The `mutation` flag is optional — when omitted the handler runs as a read.
 * Supplying `{ mutation: true }` for a GET (or omitting it for a POST) is
 * allowed so the wrapper stays trivial to apply; the flag is a hint that
 * determines which guards run, not an enforced contract.
 */
export function withApi(
  handler: (req: NextRequest) => NextResponse | Response | Promise<NextResponse | Response>,
  opts?: { mutation?: boolean },
): (req: NextRequest) => Promise<NextResponse | Response> {
  return async (req: NextRequest): Promise<NextResponse | Response> => {
    const rejection = runGuards(req, opts)
    if (rejection) return rejection
    return handler(req)
  }
}
