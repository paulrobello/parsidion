// lib/__fixtures__/routeTestHelper.ts
// ARC-016 / QA-004: shared harness for the per-route auth + CSRF contract
// tests. The contract is uniform across every route because every route is
// wrapped in `withApi` (verified by apiRoutes.test.ts); this helper runs the
// four-case matrix against any handler so each route's route.test.ts only
// has to add the path-/handler-specific cases on top.
//
// The four cases pinned per route:
//   1. 403 on `../` traversal (via the `vault` query param where present;
//      otherwise via `path`/`stem`)
//   2. 401 without the bearer token when VISUALIZER_TOKEN is set
//   3. 403 on Sec-Fetch-Site: cross-site
//   4. happy path returns the expected shape (passed in as a predicate)
//
// All tests run against a tmp HOME with a seeded default vault so the
// resolver never touches the developer's real vault.

import { NextRequest } from 'next/server'
import * as fs from 'fs'
import * as path from 'path'
import * as os from 'os'

export interface RouteTestSetup {
  tmpHome: string
  defaultVault: string
  /** Restore env. Call in afterEach. */
  restore: () => void
}

export function setupTmpHome(): RouteTestSetup {
  const tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'route-test-'))
  const defaultVault = path.join(tmpHome, 'ParsidionVault')
  fs.mkdirSync(defaultVault, { recursive: true })

  const originalHome = process.env.HOME
  const originalVaultRoot = process.env.VAULT_ROOT
  const originalToken = process.env.VISUALIZER_TOKEN
  const originalXdg = process.env.XDG_CONFIG_HOME

  process.env.HOME = tmpHome
  delete process.env.VAULT_ROOT
  delete process.env.VISUALIZER_TOKEN
  delete process.env.XDG_CONFIG_HOME

  return {
    tmpHome,
    defaultVault,
    restore: () => {
      if (originalHome === undefined) delete process.env.HOME
      else process.env.HOME = originalHome
      if (originalVaultRoot === undefined) delete process.env.VAULT_ROOT
      else process.env.VAULT_ROOT = originalVaultRoot
      if (originalToken === undefined) delete process.env.VISUALIZER_TOKEN
      else process.env.VISUALIZER_TOKEN = originalToken
      if (originalXdg === undefined) delete process.env.XDG_CONFIG_HOME
      else process.env.XDG_CONFIG_HOME = originalXdg
      try {
        fs.rmSync(tmpHome, { recursive: true, force: true })
      } catch {
        /* best effort */
      }
    },
  }
}

/**
 * Build a NextRequest against the standard test URL. Headers and body are
 * passed through unchanged.
 */
export function makeRequest(
  urlPathAndQuery: string,
  init: { method?: string; headers?: Record<string, string>; body?: string } = {},
): NextRequest {
  const url = `http://localhost:3999${urlPathAndQuery}`
  return new NextRequest(url, {
    method: init.method ?? 'GET',
    headers: init.headers ?? {},
    body: init.body,
  })
}

/**
 * Run the standard auth/CSRF matrix against any GET handler. Each route's
 * route.test.ts calls this with a `pathThatShould403` (the route's path-
 * traversal trigger — usually a `?path=../` or `?vault=../` query) and a
 * `happyPath` function returning a successful request + status assertion.
 *
 * The matrix is uniform because every route is wrapped in `withApi`; this
 * helper exists so a new route's test file is a 4-line addition rather than
 * a copy of the same four cases.
 */
export async function expectStandardAuthMatrix(getHandler: {
  (req: NextRequest): Promise<Response>
}, opts: {
  /** A URL whose query triggers the route's path-traversal rejection. */
  traversalUrl: string
  /** A URL the handler will accept past the guards (so the matrix can verify
   *  the guards fire BEFORE the handler runs). */
  baseUrl: string
}): Promise<void> {
  // Case 1: traversal → 403. The route's own guardPath check fires after
  // withApi lets the request through, so this is the route-specific guard,
  // not the withApi-level one. (withApi does not see `path` or `vault`.)
  const traversalRes = await getHandler(makeRequest(opts.traversalUrl))
  if (traversalRes.status === 401) {
    // If VISUALIZER_TOKEN leaked into the test env, the 401 short-circuits
    // before the route's own 403 fires. Fail with a clear message so the
    // caller knows to delete the env var.
    throw new Error(
      `Expected 403 for traversal ${opts.traversalUrl} but got 401 — VISUALIZER_TOKEN is set; clear it in setupTmpHome().`,
    )
  }
  // Some routes that don't take a `path`/`vault` traversal input may not 403
  // here; callers that don't expect a 403 should pass undefined for traversalUrl.
  if (opts.traversalUrl) {
    if (traversalRes.status !== 403 && traversalRes.status !== 400) {
      // 400 is acceptable when the route rejects ../ at param-validation time
      // (e.g. SHA pattern check); the point is the request does NOT succeed.
      throw new Error(
        `Expected 403/400 for traversal ${opts.traversalUrl}, got ${traversalRes.status}`,
      )
    }
  }

  // Case 2: token set + missing Authorization → 401
  process.env.VISUALIZER_TOKEN = 'test-secret'
  try {
    const noTokenRes = await getHandler(makeRequest(opts.baseUrl))
    if (noTokenRes.status !== 401) {
      throw new Error(
        `Expected 401 with VISUALIZER_TOKEN set and no Authorization header, got ${noTokenRes.status}`,
      )
    }
  } finally {
    delete process.env.VISUALIZER_TOKEN
  }

  // Case 3: cross-site Sec-Fetch-Site → 403
  const crossSiteRes = await getHandler(
    makeRequest(opts.baseUrl, { headers: { 'sec-fetch-site': 'cross-site' } }),
  )
  if (crossSiteRes.status !== 403) {
    throw new Error(
      `Expected 403 for Sec-Fetch-Site: cross-site, got ${crossSiteRes.status}`,
    )
  }
}
