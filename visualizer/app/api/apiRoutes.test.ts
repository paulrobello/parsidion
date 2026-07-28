// app/api/apiRoutes.test.ts
// ARC-014: enumeration test that proves every HTTP-method export from every
// module under app/api/ was created by `withApi`. This is the part that
// matters — the wrapper alone just relocates the same fragility that
// hand-applying the guards produced (the original sin was `/api/stats`
// shipping with zero guards because every other route hand-applied them and
// this one was missed). With this test in place a new route physically
// cannot forget the guards: importing its module and inspecting each
// HTTP-method export's `name` reveals whether `withApi` produced it
// (`withApi` returns an anonymous arrow, so its name is empty) versus a
// plain `async function GET` (which keeps its name).
//
// Concretely: a wrapped export is `function (req)` with an empty `name`
// because `withApi` returns an arrow expression. An unwrapped export is
// `function GET(req)` with `name === 'GET'`. We assert the former for every
// exported HTTP method on every route module.

import { describe, it, expect } from 'bun:test'
import * as fs from 'fs'
import * as path from 'path'

const HTTP_METHODS = ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'] as const

const ROUTE_DIR = path.resolve(import.meta.dir)

function* walkRoutes(dir: string): Generator<string> {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      yield* walkRoutes(full)
    } else if (entry.name === 'route.ts' || entry.name === 'route.tsx') {
      yield full
    }
  }
}

describe('ARC-014 — every API route export is wrapped in withApi', () => {
  const routeFiles = [...walkRoutes(ROUTE_DIR)]

  // Sanity: we know there are at least 12 route files; if this drops it
  // likely means the walk missed a directory.
  it('enumerates every route module under app/api/', () => {
    expect(routeFiles.length).toBeGreaterThanOrEqual(12)
  })

  for (const file of routeFiles) {
    const rel = path.relative(ROUTE_DIR, file)
    it(`${rel}: every exported HTTP method is wrapped in withApi`, async () => {
      const mod = await import(file)
      for (const method of HTTP_METHODS) {
        const fn = (mod as Record<string, unknown>)[method]
        if (fn === undefined) continue
        // withApi returns an arrow function expression (anonymous). A plain
        // `export async function GET` declaration would keep its name as
        // 'GET'. The wrapper is the only path that produces an anonymous
        // function here, so an empty name ⇔ wrapped.
        expect(typeof fn).toBe('function')
        expect((fn as { name?: string }).name).toBe('')
      }
    })
  }
})
