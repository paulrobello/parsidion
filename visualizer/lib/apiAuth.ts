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
