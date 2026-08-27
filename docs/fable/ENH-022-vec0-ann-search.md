# ENH-022 — sqlite-vec vec0 ANN table for embeddings search

## Goal

Replace the brute-force full-table cosine scan in the embeddings search backend with sqlite-vec's `vec0` virtual-table KNN (`MATCH`) query, removing the ~10k-note scan ceiling the audit documented (ARC-107) while keeping results identical within float tolerance at today's scale.

## Current state

- `cli/search/embeddings.py:260-272` computes `vec_distance_cosine` for every row of `note_embeddings` and sorts — exact, fine at current vault sizes, the documented ceiling at ~50k+ notes. The sqlite-vec extension is already loaded; its ANN path is unused.
- `build_embeddings.py` writes 384-dim float32 vectors into a plain table (`note_embeddings`).
- ARC-102 (audit 2026-08-26) fixes decay ordering with an over-fetch + re-sort; this enhancement must preserve that contract (over-fetch from the ANN query, then decay/sort/truncate).

## Implementation

1. **Storage.** In `build_embeddings.py`, create a `vec0` virtual table (`CREATE VIRTUAL TABLE note_vec USING vec0(embedding float[384])`) alongside `note_embeddings`, with a rowid↔stem mapping table (or reuse `note_embeddings`' rowids). Populate during the same build pass. Keep `note_embeddings` as-is for one release (dual-write) so old readers keep working.
2. **Query.** In `_search_embeddings`, query `note_vec` with `WHERE embedding MATCH ? AND k = ?` (k = `top * 3` per the ARC-102 over-fetch), join back to metadata, then apply the decay → min_score → sort → truncate pipeline unchanged.
3. **Fallback.** If the `vec0` table is absent (old DB, partial build), fall back to the existing scan — same graceful-degradation style the module already uses for a missing DB. Log once via the existing debug channel.
4. **Version/migration.** Bump the embeddings DB schema marker (find the existing version convention in `build_embeddings.py`); `build_embeddings.py` rebuilds both representations; the vault post-merge hook already triggers rebuilds after pulls.
5. **Comment/doc.** The ARC-107 comment correction ("exact scan") gets superseded: update the comment to describe the vec0 path + scan fallback; update `docs/EMBEDDINGS.md`'s Performance and Limits section (the ~10k ceiling paragraph).

## Files to touch

- `skills/parsidion/scripts/build_embeddings.py` (vec0 table build, dual-write, version)
- `skills/parsidion/scripts/cli/search/embeddings.py` (KNN query + fallback)
- `docs/EMBEDDINGS.md`
- `tests/` (parity + fallback tests; guarded by the `search` extra like existing embedding tests)

## Verification

- Parity test: on a fixture DB (~200 notes), top-10 results from the vec0 path equal the brute-force path (same stems, scores within 1e-5), decay on and off.
- Fallback test: drop the `note_vec` table; search still returns brute-force results and logs the fallback.
- `uv run --extra search pytest tests/ -k "search or embed" -q`; `make checkall` green.
- Manual scale probe (optional): `time vault-search -B embeddings "query"` before/after on the real vault — must not regress.

## Rollback

The `vec0` table is additive and the scan fallback remains in code: deleting the virtual table (or reverting the commit) restores today's behavior. Dual-write means an old binary reading a new DB still works; a new binary reading an old DB falls back.
