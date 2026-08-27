# ENH-021 — Persist reverse-link adjacency in note_index

## Goal

Store each note's *incoming* wikilinks in `note_index` at index time, so graph retrieval (SessionStart Tier 1/2), `vault-stats --graph`, and backlink tooling read an O(1)-per-note adjacency instead of re-deriving incoming links by scanning every note's `related` field per query.

## Current state

- `note_index` (built by `update_index.py`, schema in `core/vault_index.py`) stores each note's outgoing `related` links; incoming links exist only implicitly.
- `session_start/graph_retrieval.py:91-105` builds `related_sets` for every note and does an O(N × seeds) incoming-link scan per SessionStart. PRF-104 (audit 2026-08-26) mitigates this with a per-run in-memory reverse adjacency; this enhancement moves that computation to index time so *no* per-session pass over all notes is needed and other consumers benefit.
- `vault_links.py` already computes bidirectional links at write time; the DB just doesn't store the reverse direction.

## Implementation

1. **Schema.** Add an `incoming_links` TEXT column (JSON array of source stems) to `note_index`, with a schema-version bump handled the way existing `note_index` migrations are (check `core/vault_index.py` for the CREATE/upgrade pattern; the table is rebuilt by `update_index.py`, so a version check + full rebuild on mismatch is acceptable and simplest).
2. **Population.** In the index build (`update_index.py` / `vault_index` build path), after all notes' outgoing links are parsed, invert the map in one O(N × avg_links) pass and write `incoming_links` per row. Incremental rebuilds must re-invert affected rows: simplest correct approach is recompute the inversion for the union of (changed notes' old targets ∪ new targets); if that complicates the incremental path, full re-inversion each run is fine (it is an in-memory dict build over rows already loaded).
3. **Consumers.**
   - `session_start/graph_retrieval.py`: read `incoming_links` from the snapshot rows instead of building the inversion per run (supersedes the PRF-104 in-memory build once landed).
   - `vault-stats --graph` (`cli/stats/` graph metrics): use the column for degree/orphan computations where it currently re-parses.
   - `vault_links.add_backlinks_to_existing`: optionally consult the column to find link targets faster (keep file parsing as source of truth for mutation — the DB is a cache).
4. **Staleness rule.** Same as the rest of `note_index`: DB-first with the documented walk fallback; mutation paths keep using the authoritative walk.
5. **Docs.** Update the `note_index` schema description in `CLAUDE.md`/`docs/ARCHITECTURE.md` (Key File Paths / embeddings.db section).

## Files to touch

- `skills/parsidion/scripts/core/vault_index.py` (schema, build, snapshot exposure)
- `skills/parsidion/scripts/update_index.py` (population pass)
- `skills/parsidion/scripts/session_start/graph_retrieval.py` (consumer)
- `skills/parsidion/scripts/cli/stats/` graph module (consumer)
- `docs/ARCHITECTURE.md`, `CLAUDE.md` (schema docs)
- `tests/` (inversion correctness incl. incremental rebuild; graph_retrieval parity test old-vs-new adjacency)

## Verification

- New test: build index over a fixture vault, assert `incoming_links` of note B lists exactly the notes whose `related` contains B; edit one note's links, incremental rebuild, assert inversion updated.
- Parity test: `_graph_neighbors` output identical using the column vs the legacy per-run inversion on the same fixture.
- `uv run pytest tests/ -k "index or graph" -q`; `make checkall` green.
- Manual: `uv run --no-project ~/.claude/skills/parsidion/scripts/update_index.py` on the real vault completes; `vault-stats --graph` values unchanged.

## Rollback

The column is additive; consumers keep the legacy inversion as fallback when the column is NULL/absent (one `if` per consumer). Reverting the commit and rebuilding the index restores the prior schema (the table is regenerable at any time).
