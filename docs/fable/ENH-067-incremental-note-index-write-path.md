# ENH-067 — Incremental note_index updates on vault write paths

## Goal

Stop rebuilding the full `note_index` on every vault write. Today each hook write
(daily-note update, summarizer note write, doctor fix) pays an O(vault) rescan via
`update_index.py` plus a `git_commit_vault` pass, even when one note changed. The graph
rebuild is already incremental (ENH-010, `graph_incremental`); the metadata index is the
remaining O(vault)-per-write surface. Target: a write-path index update that touches only
changed notes, keeping `note_index` byte-for-byte equivalent to a full rebuild.

## Current-state context

- `update_index.py` (and `vault_common`'s callers) rescan the entire vault on every run;
  callers invoke it after each write (session-stop pipeline, summarizer, doctor, MCP ops).
- `note_index` lives in `<vault>/embeddings.db` (SQLite): per-note metadata with `related`,
  `incoming_links`, `incoming_stems` (ENH-021 inverted adjacency). A single-note change
  also affects *other* notes' `incoming_stems`/`incoming_links` when links change — the
  incrementality must handle reverse-link propagation, which is the real design work.
- ENH-010's incremental graph pass (`summarizer.graph_incremental`, full-rebuild fallback)
  is the proven precedent: diff changed notes against the previous build, recompute those,
  fall back to full rebuild when the diff is unreliable.
- `PARSIDION_STATIC_ONLY=1` / `embeddings.enabled: false` already exist to skip the
  embedding stage — the same env/config channels should gate the incremental path.
- Write hot spots (from the audit): `summarizer/queue.py` and `installer/skill.py` both
  define a `rebuild_index`, but since ARC-202 both delegate to the shared
  `parsidion.core.vault_index.run_index_rebuild` — the consolidation this spec waited on
  is in place (verified 2026-11-16).

## Implementation

1. **Prerequisite**: satisfied — extend `run_index_rebuild` and the `update_index.py`
   internals rather than either caller.
2. Add a `--incremental` mode to the consolidated rebuild entry (default on, `--full`
   escape hatch), mirroring `graph_incremental`'s config surface
   (`search.use_note_index` neighbors may reference it; keep the default behavior
   identical when the diff is empty).
3. Diff source: mtimes + content hash per note, stored in a small `index_state` table in
   `embeddings.db` (path, mtime, sha256, indexed_at). Hash only files whose mtime changed
   since the last run — O(changed) not O(vault) for the common case.
4. Recompute per changed note: its row (frontmatter, tags, outgoing links) and the
   `incoming_stems` rows of every note it links to or unlinked from (read the *old* row's
   links from the stored `related` before overwrite to compute the symmetric diff).
5. Delete+reinsert changed rows inside one transaction; update `index_state` in the same
   transaction so a crash mid-run leaves a consistent "not yet indexed" state (next run
   picks it up).
6. Full-rebuild fallback triggers: missing/corrupt `index_state`, EXCLUDE_DIRS change, or
   a schema migration marker mismatch. Log the fallback reason to `hook_events.log`.
7. Update the post-merge hook path (`PARSIDION_STATIC_ONLY=1` git pull flow) to use the
   same incremental mode — it currently forces unconditional rebuilds.
8. Docs: CLAUDE.md index-rebuild section, `docs/ARCHITECTURE.md` note-lifecycle section,
   config reference if a new key is added (`search.incremental_note_index`, default true).

## Files to touch

- `skills/parsidion/scripts/parsidion/core/vault_index.py` and
  `skills/parsidion/scripts/parsidion/core/note_index.py` (rebuild core, post-ARC-201
  re-namespace; ARC-203 split)
- `run_index_rebuild` in `parsidion/core/vault_index.py` (the consolidated entry)
- `update_index.py` (CLI flag plumbing)
- `skills/parsidion/templates/config.yaml` + `core/vault_schema.py` (if adding a config key)
- `installer/vault.py` post-merge hook template
- tests: new `tests/test_incremental_note_index.py`; existing index suites must stay green unchanged

## Verification

- `uv run pytest tests/ -k "index"` — all existing index tests green.
- New equivalence test: build a fixture vault, mutate N notes, run incremental, run full
  rebuild on a copy, assert both `note_index` tables compare equal (rows and
  `incoming_stems`).
- Scale check: on `~/ParsidionVault` (16k files), time `update_index.py` with no changes
  before vs after — report actual numbers (R12: measured, not estimated; expect O(changed)≈0).
- `make checkall-ci` green.

## Rollback

`--full` flag restores the old behavior per invocation; the config key (if added) flips it
off vault-wide. The `index_state` table is additive — drop it and every path falls back to
full rebuild. No data migration risk: `note_index` schema is unchanged.
