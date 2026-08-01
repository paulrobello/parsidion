# ENH-010 — Incremental graph/embedding rebuild

## Goal
Make `build_graph.py` re-embed only notes that changed since the last build, instead of re-embedding the whole vault every run — the dominant cost for large vaults. The serving side is already streamed + ETag-cached (ARC-015); the rebuild side is still full.

## Current-state context
- `skills/parsidion/scripts/build_graph.py` (1011 LOC) rebuilds `embeddings.db` + `graph.json` from scratch on each invocation. `--max-neighbors` caps semantic edges per node (default 15).
- `embeddings.db` has `note_embeddings` (384-dim float32 vectors via `fastembed`) and `note_index` (metadata). There is no per-note mtime/hash tracking today, so the builder cannot tell what changed.
- The visualizer already consumes incremental updates via `visualizer/app/api/graph/delta/route.ts` (a top bridge symbol) — the *client* is ready for deltas; the *producer* is not.
- `make graph` runs `build_graph.py`; the nightly summarizer can optionally rebuild the graph (`--rebuild-graph`).
- `build_embeddings.py` is the lower-level embedder; `build_graph.py` builds the graph on top.

## Step-by-step implementation
1. **Add a change-detection table** to `embeddings.db`: `note_state(path TEXT PRIMARY KEY, mtime_ms INTEGER, content_hash TEXT, embedded_at INTEGER)`. Populate on first run from existing data.
2. **In `build_embeddings.py`**, before embedding a note, compute `(mtime, sha256(content))`; if `note_state` matches, reuse the stored vector and skip the `fastembed` call. Only changed/new notes get re-embedded; deleted notes get their vectors + `note_state` rows pruned.
3. **In `build_graph.py`**, reuse cached vectors; only recompute pairwise semantic edges for changed notes (a changed note's edges to all others must be recomputed, but unchanged pairs are stable). Write `graph.json` as before (full file) so the serving contract is unchanged — the win is compute, not output format.
4. **Optional delta output**: add `--incremental` that emits a `graph.delta.json` the visualizer's `graph/delta` route can merge, so even the write is incremental. (Phase 2 of this enhancement; ship the compute win first.)
5. **Wire into the nightly job** (`install.py --schedule-summarizer --rebuild-graph`) — incremental rebuild makes nightly graph refresh cheap enough to run always.

## Files to touch
- `skills/parsidion/scripts/build_embeddings.py` (change detection + reuse)
- `skills/parsidion/scripts/build_graph.py` (reuse cached vectors; optional delta output)
- `embeddings.db` schema (new `note_state` table; migration handles existing DBs)
- `tests/test_build_graph_parmem.py` (numpy-gated; add incremental-correctness tests: embed vault, change one note, rebuild, assert only that note's vectors/edges changed)
- `Makefile` (`make graph` unchanged; document `--incremental`)

## Verification
- `make test-graph` (numpy-gated par-mem body-link tests).
- `make checkall`.
- Benchmark: on a vault with N notes, time `make graph` cold vs. warm (no changes) — warm should be near-instant (DB reads only). Then change one note and confirm only its vectors/edges differ (`sqlite3 embeddings.db` diff).
- Correctness: rebuild full (current path) and incremental on the same vault, assert `graph.json` is byte-identical (modulo the unchanged-output-format guarantee).

## Rollback
- The `note_state` table is additive; drop it to restore full-rebuild behavior. `build_graph.py`/`build_embeddings.py` changes are localized to the change-detection branch; a `--full` flag (or env override) forces the old path during rollout. `graph.json` output format is unchanged, so the visualizer is unaffected by a rollback.
