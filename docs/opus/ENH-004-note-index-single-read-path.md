# ENH-004 — Make `note_index` the single read path and retire vault-wide `os.walk`

> **Impact**: medium · **Effort**: medium · **Status**: shipped in 0.15.0
> Source: Opus deep audit, 2026-07-28, commit `8e5d549`
> **Sequencing: land audit item SEC-106 first.** It adds symlink containment to the walk; doing this
> enhancement first risks moving the read path before the check exists and losing it.

## Goal

Make `embeddings.db`'s `note_index` table the authoritative source for note *metadata* reads, so that
metadata queries are SQL rather than a full-vault filesystem walk with per-file frontmatter parsing.
Keep `os.walk` for exactly two jobs: building the index itself, and an explicit no-database fallback.

The win is threefold — speed on a 5,563-note vault, one canonical answer instead of two code paths that
can disagree, and a single chokepoint where containment checks actually apply.

## Current state

par-mem centrality puts `vault_index.parse_frontmatter` at in-degree **32** and
`vault_index.all_vault_notes` at in-degree **26** — both among the highest-fan-in symbols in the
codebase. They are hot because the metadata read path is a walk:

- `vault_index._walk_vault_notes` (`vault_index.py:486-497`) enumerates the tree; callers then read
  (`:519`) and parse each file's frontmatter.
- `find_notes_by_project:534`, `find_notes_by_tag:542`, `find_notes_by_type:550`,
  `find_recent_notes:558`, and `all_vault_notes:620` all sit on top of that walk.
- `summarize_sessions.read_project_names` (`:359-385`) reads every note in the vault on every
  summarizer run purely to collect `project` values.
- `vault_metrics.py:516,589` walks independently for its own purposes.

Meanwhile `note_index` **already stores exactly these fields**. From `build_graph.py`'s own query:

```sql
SELECT stem, title, note_type, folder, tags, incoming_links, related, mtime, path
FROM note_index
```

So `build_graph.py` reads metadata from SQL while `vault_index.py`'s query functions walk the
filesystem for the same data. `vault_common.query_note_index()` exists and is used — the DB path is
already built and proven, it is simply not the path the query helpers take.

Three consequences follow. It is slow. The two paths can disagree (a stale index versus fresh files),
and nothing detects that. And audit item SEC-106 found that walk-discovered symlinks bypass every
containment check precisely because they arrive from discovery rather than from a caller — a class of
problem that shrinks dramatically when there is one read path instead of several.

## Implementation

### Step 1 — Confirm the schema covers every field the walk-based functions return

Before changing anything, dump the table and diff its columns against what each `find_notes_by_*`
function returns:

```bash
sqlite3 ~/ParsidionVault/embeddings.db ".schema note_index"
sqlite3 ~/ParsidionVault/embeddings.db "SELECT COUNT(*) FROM note_index"
```

Compare that count against the on-disk note count. If they differ materially, the index is stale and
must be rebuilt before any comparison testing is meaningful:

```bash
uv run --no-project skills/parsidion/scripts/update_index.py
```

If a field a caller needs is genuinely absent from the schema, add it to `update_index.py`'s index
build **first**, as its own commit, and rebuild. Do not proceed on a partial schema.

### Step 2 — Add DB-backed implementations behind the existing signatures

Keep every public signature identical. Inside each of `find_notes_by_project`, `find_notes_by_tag`,
`find_notes_by_type`, `find_recent_notes`, and `all_vault_notes`, add a DB-first branch:

```python
def find_notes_by_project(project: str, vault: Path | None = None) -> list[Path]:
    """Notes belonging to a project.

    Reads note_index when available; falls back to a filesystem walk so behaviour
    is unchanged on a vault with no embeddings.db.
    """
    rows = query_note_index(
        "SELECT path FROM note_index WHERE project = ?", (project,), vault=vault
    )
    if rows is not None:
        return _paths_from_rows(rows, vault)
    return _find_notes_by_project_walk(project, vault=vault)
```

Rules for this step:

- `query_note_index` must return `None` (not `[]`) to signal "no database", so an empty result set is
  distinguishable from an unavailable index. Check the existing implementation and adjust if it
  conflates them — that distinction is what makes the fallback correct.
- Preserve the existing walk implementations verbatim as `_*_walk` private functions. They are the
  fallback and the differential-test oracle.
- `_paths_from_rows` must re-validate every path coming out of SQLite against the vault root. The
  codebase already does this (`vault_index.py:373-420` re-validates paths from the DB, guarding a
  tampered `embeddings.db`) — follow that precedent, do not trust the DB.

### Step 3 — Add an explicit escape hatch

Add a `--no-db` flag to the CLIs that expose these queries, and a `search.use_note_index` config key
(default `true`), so a user can force the walk path when they suspect index staleness. Add the key to
`_CONFIG_SCHEMA` in `vault_config.py` **and** to `skills/parsidion/templates/config.yaml` — audit item
ARC-011 adds a test asserting `validate_config()` returns `[]` for the shipped template, so a key in
one and not the other will fail the gate.

### Step 4 — Convert the two known hot callers

- `summarize_sessions.read_project_names` → `SELECT DISTINCT project FROM note_index WHERE project != ''`.
  (ENH-003 Step 3 also names this; do it once, in whichever lands first, and mark the other done.)
- `vault_metrics.py:516,589` → route through the shared helpers rather than walking independently.

### Step 5 — Staleness signalling

A DB-first read path makes index staleness user-visible in a way the walk never did. Add a
`note_index_age()` helper comparing the newest `mtime` in `note_index` against the newest `.md` mtime
on disk, and surface it in `vault-stats` (this is a natural input to ENH-007's health score). Do not
auto-rebuild from a read path — that would make a read do surprising work — but do warn.

### Step 6 — Differential tests

This is the part that makes the change safe. For each converted function, assert the DB path and the
walk path return **the same set**:

```python
@pytest.mark.parametrize("fn,arg", [
    (vault_index.find_notes_by_project, "parsidion"),
    (vault_index.find_notes_by_tag, "python"),
    (vault_index.find_notes_by_type, "pattern"),
])
def test_db_and_walk_agree(tmp_vault, fn, arg):
    db_result = set(fn(arg, vault=tmp_vault))
    walk_result = set(getattr(vault_index, f"_{fn.__name__}_walk")(arg, vault=tmp_vault))
    assert db_result == walk_result
```

Plus:

1. **No database** → the walk fallback runs and returns correct results (delete `embeddings.db` in the fixture).
2. **Empty result vs missing DB** are distinguishable — a project with zero notes returns `[]`, not the fallback.
3. **Tampered DB** — insert a row whose `path` points outside the vault; assert it is filtered out.
4. **Stale index** — add a note on disk without reindexing; assert the documented behaviour (DB path
   misses it) and that `note_index_age()` reports non-zero staleness.
5. **`--no-db` forces the walk** and finds the note from test 4.

## Files to touch

| File | Change |
|---|---|
| `skills/parsidion/scripts/vault_index.py` | DB-first branches in the five query functions; preserve walks as `_*_walk`; `_paths_from_rows` with re-validation; `note_index_age()` |
| `skills/parsidion/scripts/vault_common.py` | re-export any new public helper; update the `__all__` list (see audit item DOC-040) |
| `skills/parsidion/scripts/vault_metrics.py` | route through shared helpers |
| `skills/parsidion/scripts/summarize_sessions.py` | DB-backed `read_project_names` |
| `skills/parsidion/scripts/vault_config.py`, `templates/config.yaml` | `search.use_note_index` |
| `tests/test_vault_common.py`, `tests/test_index_enhancements.py` | differential + fallback tests |
| `CLAUDE.md` | document the DB-first read path and `--no-db` |

## Verification

```bash
uv run ruff format --check . && uv run ruff check . && uv run pyright .
uv run pytest tests/ -v          # differential tests are the gate

# Real-vault equivalence and timing — report both
python3 - <<'EOF'
import sys, time, os
sys.path.insert(0, 'skills/parsidion/scripts')
import vault_index
v = os.path.expanduser('~/ParsidionVault')
for name in ("find_notes_by_type",):
    fn, walk = getattr(vault_index, name), getattr(vault_index, f"_{name}_walk")
    t = time.perf_counter(); a = set(fn("pattern", vault=v)); db_t = time.perf_counter()-t
    t = time.perf_counter(); b = set(walk("pattern", vault=v)); wk_t = time.perf_counter()-t
    print(f"{name}: db={len(a)} in {db_t:.3f}s | walk={len(b)} in {wk_t:.3f}s")
    assert a == b, f"{name} diverged: db-only={len(a-b)} walk-only={len(b-a)}"
print("OK — results identical")
EOF
```

The set-equality assertion is the acceptance criterion. A speedup with divergent results is a failure.

## Rollback

Every public signature is unchanged and the walk implementations remain in the tree, so rollback is
either flipping `search.use_note_index: false` in config (no code change) or reverting the DB-first
branches. No data migration, no schema change to `note_index`, nothing persisted differently.

## Risks

- **Index staleness becomes user-visible.** This is the main behavioural change and it is arguably a
  feature — the walk path was papering over a stale index by ignoring it. Step 5's signalling is what
  makes it acceptable rather than confusing. Call this out in the release note.
- **A tampered `embeddings.db` becomes a read-path input.** Mitigated by re-validating every returned
  path against the vault root, following the precedent already set at `vault_index.py:373-420`.
- **Divergence during the transition.** The differential tests are cheap and should stay in the suite
  permanently, not be deleted once the conversion is done — they are the regression guard for the two
  paths drifting apart later.
