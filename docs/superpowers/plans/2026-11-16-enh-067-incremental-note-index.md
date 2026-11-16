# ENH-067 Incremental note_index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop rebuilding the full `note_index` on every vault write — a write-path index update that touches only changed notes, byte-for-byte equivalent to a full rebuild.

**Architecture:** An `index_state` table (path, mtime, sha256, indexed_at) in `embeddings.db` drives an mtime-gated content diff; changed notes get delete+reinsert of their row plus reverse-link (`incoming_stems`/`incoming_links`) propagation computed from the old row's stored `related` vs the new one's, all inside one transaction that also updates `index_state`. Unreliable diffs (missing/corrupt state, EXCLUDE_DIRS change, schema marker mismatch) fall back to the full rebuild with a logged reason. Mode: `--incremental` default, `--full` escape hatch, config key `search.incremental_note_index` (default true).

**Tech Stack:** Python stdlib only (stdlib gate), SQLite, pytest.

**Spec:** `docs/fable/ENH-067-incremental-note-index-write-path.md` (fable audit spec; prerequisite ARC-202 satisfied — extension point is `run_index_rebuild` + `update_index.py` internals + `parsidion/core/note_index.py`).

## Global Constraints

- stdlib-only for `skills/parsidion/scripts/` and `installer/` (gate poisons third-party).
- Incremental output must be byte-for-byte equivalent to full rebuild on the same tree (equivalence test is the card's criterion 1).
- Existing index tests stay green unchanged; `note_index` schema is unchanged (`index_state` is additive).
- Full-rebuild fallback must trigger on: missing/corrupt `index_state`, EXCLUDE_DIRS change, schema migration marker mismatch — reason logged to `hook_events.log`.
- Crash mid-run leaves a consistent "not yet indexed" state (transaction covers row reinsert + index_state).
- New config key `search.incremental_note_index` (default true) flows through `vault_schema.py` + `make config-docs` regeneration (drift gate).
- Tests numpy-free; done gate `make checkall-ci`.

## Review Focus

1. Reverse-link propagation: adding a `[[link]]` to note A must update B's `incoming_stems`/`incoming_links`; REMOVING one must too (old-row symmetric diff) — pinned by Task 2 tests both directions.
2. Deleted/renamed notes: a note removed from disk must drop its row AND its outgoing contributions to others' incoming sets — pinned by Task 2.
3. Equivalence on adversarial mutations (edit, add, delete, rename, link-add, link-remove in one batch) — pinned by Task 2 equivalence test.
4. Fallback safety: corrupt `index_state` (garbage rows) must produce a correct full rebuild, not a wrong incremental — pinned by Task 3.
5. No-change run cost: with no mutations, the incremental path must touch zero note rows — pinned by Task 1 (diff returns empty) and measured at scale in Task 5 (R12).

---

### Task 1: `index_state` table + mtime-gated diff

**Files:**
- Modify: `skills/parsidion/scripts/parsidion/core/note_index.py` (schema + diff)
- Test: `tests/test_incremental_note_index.py` (new)

**Interfaces:**
- Produces: `ensure_index_state_table(conn)` (CREATE TABLE IF NOT EXISTS `index_state(path TEXT PRIMARY KEY, mtime REAL, sha256 TEXT, indexed_at REAL)`); `diff_changed_notes(vault: Path) -> ChangedSet(added: list[Path], modified: list[Path], deleted: list[Path])` — walks the vault (minus `EXCLUDE_DIRS`), compares stored mtime, hashes only mtime-changed files, detects deletions by absence from the walk.

- [ ] **Step 1: Failing tests** — `test_index_state_table_created`, `test_no_changes_returns_empty_diff`, `test_modified_note_detected_by_mtime_then_hash`, `test_added_and_deleted_detected`, `test_ignores_exclude_dirs`.
- [ ] **Step 2:** Run — FAIL (names undefined).
- [ ] **Step 3:** Implement per Interfaces.
- [ ] **Step 4:** Run — PASS.
- [ ] **Step 5:** Commit `feat(index): index_state table + mtime-gated change diff`.

### Task 2: Incremental recompute + reverse-link propagation

**Files:**
- Modify: `skills/parsidion/scripts/parsidion/core/note_index.py`
- Test: `tests/test_incremental_note_index.py` (extend)

**Interfaces:**
- Consumes: Task 1's `diff_changed_notes`.
- Produces: `apply_incremental_update(vault: Path, conn) -> IncrementalResult(changed: int, fallback_reason: str | None)` — for each changed note: read OLD row's `related` before overwrite, reinsert the row (frontmatter, tags, outgoing links), then symmetric-diff the link set and update every linked/unlinked target's `incoming_stems`/`incoming_links`; deleted notes drop rows and their outgoing contributions; one transaction wrapping rows + `index_state`.

- [ ] **Step 1: Failing tests** — `test_link_add_updates_target_incoming`, `test_link_remove_updates_target_incoming`, `test_deleted_note_drops_row_and_contributions`, `test_equivalence_incremental_equals_full_rebuild` (fixture vault; batch of edit/add/delete/rename/link-add/link-remove; incremental run vs full rebuild on a copy; assert `note_index` rows identical incl. incoming sets).
- [ ] **Step 2:** Run — FAIL.
- [ ] **Step 3:** Implement per Interfaces.
- [ ] **Step 4:** Run — PASS.
- [ ] **Step 5:** Commit `feat(index): incremental recompute with reverse-link propagation`.

### Task 3: Fallback triggers + mode plumbing + config key

**Files:**
- Modify: `skills/parsidion/scripts/parsidion/core/note_index.py` + `core/vault_index.py` (`run_index_rebuild`), `skills/parsidion/scripts/update_index.py` (flags), `core/vault_schema.py`, regenerated config docs via `make config-docs`
- Test: `tests/test_incremental_note_index.py` (extend); `tests/test_config_schema.py` (key)

**Interfaces:**
- Produces: fallback triggers (missing/corrupt `index_state`, EXCLUDE_DIRS value change vs stored marker, schema marker mismatch) returning `fallback_reason` and running the existing full rebuild, reason appended to `hook_events.log` (use `write_hook_event`); `update_index.py` gains `--incremental` (default) / `--full`; `run_index_rebuild` passes the mode through; `SearchConfig.incremental_note_index: bool = True` + config-docs regen.

- [ ] **Step 1: Failing tests** — `test_corrupt_index_state_falls_back_to_full`, `test_exclude_dirs_change_falls_back`, `test_full_flag_forces_rebuild`, `test_config_key_defaults_true`, `test_fallback_reason_logged`.
- [ ] **Step 2:** Run — FAIL.
- [ ] **Step 3:** Implement; `make config-docs`.
- [ ] **Step 4:** Run tests + `make config-docs-check` — PASS.
- [ ] **Step 5:** Commit `feat(index): incremental mode plumbing, fallback triggers, search.incremental_note_index`.

### Task 4: Post-merge hook incremental mode

**Files:**
- Modify: `installer/vault.py` (post-merge hook template — bump template version so reinstalls pick it up)
- Test: extend installer hook-template test (find it via grep for post-merge in tests/)

**Interfaces:**
- Produces: template invokes the rebuild without forcing full mode (incremental default); `PARSIDION_STATIC_ONLY=1` flow unchanged.

- [ ] **Step 1: Failing test** — template content asserts the incremental-capable invocation and bumped version marker.
- [ ] **Step 2:** Run — FAIL.
- [ ] **Step 3:** Implement template change.
- [ ] **Step 4:** Run — PASS.
- [ ] **Step 5:** Commit `feat(installer): post-merge hook uses incremental index mode`.

### Task 5: Docs + R12 measurement + done gate

**Files:**
- Modify: `CLAUDE.md` (index-rebuild section), `docs/ARCHITECTURE.md` (note lifecycle), config reference (regenerated in Task 3 — verify)
- Measurement: `~/ParsidionVault` no-change timing before/after (R12 — record actual numbers in the report and card)

- [ ] **Step 1:** Docs sections per spec step 8.
- [ ] **Step 2:** R12: time `update_index.py --full` vs `--incremental` (default) on `~/ParsidionVault` with no changes; record both numbers.
- [ ] **Step 3:** `make checkall-ci` — PASS.
- [ ] **Step 4:** Commit `docs(index): incremental note_index documentation + measured timings`.
