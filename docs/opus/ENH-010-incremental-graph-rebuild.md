# ENH-010 — Incremental graph/embedding rebuild

## Resolution (2026-08-01)

Investigation found the plan's premise substantially already shipped, so this
enhancement closed with a small default-flip rather than the full build-out:

- **Embeddings (the dominant cost — 67 MB ONNX load + inference on N notes)
  were already incremental-by-default.** `build_embeddings.py:incremental_update()`
  does mtime-based add/change/delete with a model-dimension-mismatch guard, and
  `update_index.py` runs it with `--incremental` whenever `embeddings.db` exists
  (the post-merge sync hook does the same). Plan steps 1–2 were already done
  using the existing `note_embeddings.mtime` column instead of a new
  `note_state` table.
- **The graph N×N rebuild was already incremental** — shipped as **ENH-002**
  (`load_previous_graph`, `compute_changed_stems`, `extend_recompute_closure`,
  `build_semantic_edges_incremental`), with `GRAPH_SCHEMA_VERSION=2` and a
  test suite asserting the incremental edge set equals a full rebuild (plan
  step 3).
- **The one genuine gap was plan step 5**: graph incremental was opt-in/off by
  default (`summarizer.graph_incremental: false`). This change flips it to
  **on by default** so the nightly `--rebuild-graph` path is incremental
  unless explicitly disabled. The `--graph-incremental` CLI flag became a
  tri-state (`--no-graph-incremental` forces a full rebuild). `build_graph.py`
  still falls back to a full rebuild on any compatibility mismatch, so
  defaulting to incremental is always safe. On a dense vault (one giant
  semantic component) the recompute closure expands to ~all notes and the win
  is modest; on sparse vaults or small change sets it is real. `make graph`
  (the manual one-off) stays a full rebuild by design.

**Deferred (Phase 2, not done):** the optional `graph.delta.json` delta output
the visualizer's `graph/delta` route could merge. Serving is already streamed +
ETag-cached (ARC-015), so the payoff is minor; revisit only if the full-file
write becomes a measured bottleneck.

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
