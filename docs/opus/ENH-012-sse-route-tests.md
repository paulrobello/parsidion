# ENH-012 — SSE route integration tests for the visualizer

> **Status**: shipped 2026-08-01

## Goal
Add integration tests for the visualizer's Server-Sent-Events routes (`vault/events`, `graph`) that exercise a real vault watcher end-to-end — closing the test-coverage gap the code-quality audit flagged ("the SSE `vault/events` and `graph` routes have no integration test that exercises a real watcher").

## Current-state context
- `visualizer/app/api/vault/events/route.ts:145` (`start`) and `:210` (`cancel`) implement the SSE stream; `visualizer/app/api/graph/route.ts:74` streams `graph.json`. These are bridge symbols (betweenness) but are only covered indirectly.
- `visualizer/app/api/note/route.test.ts` is the existing pattern: it stands up a temp vault and dispatches a request.
- The watcher logic lives in `visualizer/lib/` (e.g. `useVaultFiles.ts`, the chokidar/fs watch integration). An SSE test must open a connection, mutate the vault on disk, and assert an event frame arrives.
- `make visualizer-check` runs `bun test`; the SSE routes currently contribute no assertions.

## Step-by-step implementation
1. **`visualizer/app/api/vault/events/route.test.ts`**: create a temp vault (reuse the helper from `note/route.test.ts`), `fetch` the `/api/vault/events?vault=<temp>` endpoint with a streaming reader, then write/append a note in the temp vault, and assert a parsed SSE frame (`data: ...`) arrives within a timeout. Close the reader; assert the route's `cancel` cleans up the watcher (no leaked fs handles — use `bun --detect-leaks` or assert process exits).
2. **`visualizer/app/api/graph/route.test.ts`**: assert the ETag flow — first GET returns 200 + body + `ETag`; a second GET with `If-None-Match: <etag>` returns 304; a GET after mutating `graph.json` (new mtime/size) returns 200 with a new ETag.
3. **Factoring**: extract a `withTempVault(fn)` + `openSSE(url)` test helper into `visualizer/lib/testHarness.ts` (or `__fixtures__/`) so future route tests reuse it.
4. **CI**: ensure the SSE tests are deterministic — use a fake clock / short poll interval in the watcher for the test, or assert on the first frame only with a generous timeout. Mark them `.only`-free and ensure they clean up temp dirs.

## Files to touch
- `visualizer/app/api/vault/events/route.test.ts` (new)
- `visualizer/app/api/graph/route.test.ts` (new — ETag behavior)
- `visualizer/lib/testHarness.ts` or `__fixtures__/` (temp-vault + SSE helpers)
- `Makefile` (`make visualizer-check` already runs `bun test` — no change needed beyond the new files)

## Verification
- `make visualizer-check` (tsc + lint + bun test + build) green, with the new tests passing.
- Deliberately break the SSE `start`/`cancel` (e.g. remove the client-disconnect abort) and confirm the new test fails — proving it has teeth.
- Run twice in a row to confirm no leaked watchers / flakiness.

## Rollback
- Delete the two new test files + the test helper. The routes are unchanged; the tests are purely additive. No runtime impact.
