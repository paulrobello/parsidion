// lib/apiAuth.ts
// SEC-004: Minimal auth / CSRF hardening for the visualizer API.
//
// Two layers of protection:
//
// 1. Content-Type guard (always active):
//    Mutation endpoints (POST/PUT/DELETE) must send Content-Type: application/json.
//    This blocks simple-form-based CSRF from any browser tab because browsers
//    cannot set a custom Content-Type on a cross-origin request without a
//    CORS preflight, and the visualizer's CORS policy only allows same-origin.
//
// 2. Bearer token guard (optional, activates when VISUALIZER_TOKEN env var is set):
//    When VISUALIZER_TOKEN is set at server start, every API request must carry
//    the header `Authorization: Bearer <token>`.  The local UX is unchanged when
//    the variable is absent (single-user workstation default).
//
// Usage in a route handler:
//
//   import { requireAuth } from '@/lib/apiAuth'
//   ...
//   const authError = requireAuth(req)
//   if (authError) return authError

import { NextRequest, NextResponse } from 'next/server'

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
 * entirely and are allowed through.
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
 * Checks auth/CSRF guards for an incoming API request.
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

  // 2. Bearer token check (only when VISUALIZER_TOKEN is configured)
  const token = process.env.VISUALIZER_TOKEN
  if (token) {
    const auth = req.headers.get('authorization') ?? ''
    const expected = `Bearer ${token}`
    if (auth !== expected) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
    }
  }

  return null
}
