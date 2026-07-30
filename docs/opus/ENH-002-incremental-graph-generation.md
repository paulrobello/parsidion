# ENH-002 — Incremental graph generation

> **Impact**: high · **Effort**: medium · **Status**: not started
> Source: Opus deep audit, 2026-07-28, commit `8e5d549`
> **Sequencing: land ENH-001 first.** Both edit `build_semantic_edges`, and ENH-001 shrinks the working
> set this feature has to reason about.

## Goal

Make rebuilding `graph.json` cheap enough that it can run on every index update instead of being an
opt-in nightly extra. Concretely: a rebuild after a handful of notes changed should complete in a small
fraction of the full-rebuild time and allocate a small fraction of its peak memory.

## Current state

`build_graph.py` has exactly six flags — `--include-daily` / `--no-daily`, `--min-threshold`,
`--output`, `--vault`, `--no-parmem`. There is no incremental mode. Every invocation:

1. loads all `note_index` rows,
2. loads all embeddings,
3. materializes a full `N × N` similarity matrix (`sim = normalized @ normalized.T`),
4. walks it, and
5. rewrites the entire file.

At 5,563 notes that matrix is ~124 MB in float32 and the output is 47.5 MB. The cost is why
`summarizer.rebuild_graph` defaults off and why the graph is regenerated rarely — the live
`graph.json` carried `meta.generated: 2026-07-13`, **16 days stale** at audit time. A knowledge graph
that is two weeks behind the vault it describes is actively misleading, and the visualizer's whole
value proposition depends on it being current.

So the real deliverable here is *freshness*, achieved via cost reduction.

## Design

Incremental rebuild rests on one property: a note's semantic edges depend only on its own embedding
and the embeddings of other notes. If note X's embedding is unchanged and note Y's is unchanged, the
X–Y similarity is unchanged. So on a rebuild where the changed set is `C`:

- Edges between two unchanged notes → **reusable verbatim** from the previous `graph.json`.
- Edges touching any note in `C` → **recompute**, which needs `|C| × N` similarities, not `N × N`.
- Notes deleted since last run → drop their nodes and every edge touching them.
- Notes added → members of `C` by definition.

The `|C| × N` product is the whole win: for `|C| = 20` and `N = 5563` that is ~111K similarities
instead of ~15.5M, and it never materializes the square matrix.

**Correctness caveat that must be respected.** ENH-001's top-K policy makes edge selection *relative* —
adding a strongly-similar note to X's neighbourhood can evict X's previously-15th-strongest neighbour,
an edge between two otherwise-unchanged notes. So the recompute set is not `C` alone but
`C ∪ {notes whose top-K list contains a member of C}`. The practical way to handle this is to recompute
the top-K list for every node in `C` **and** for every node that currently has an edge to a node in `C`
(readable straight from the previous graph's edge list). That closure is still tiny compared to `N`.

## Implementation

### Step 1 — Version the output schema

Add to the `meta` block written by `main()`:

```python
"schema_version": 2,
"generated": <iso8601>,            # already present
"min_semantic_threshold": ...,     # already present
"max_neighbors": ...,              # added by ENH-001
```

Define a module constant `GRAPH_SCHEMA_VERSION = 2`. Any incremental run that reads a previous graph
with a different `schema_version`, a different `min_semantic_threshold`, a different `max_neighbors`,
or a different `include_daily` **must fall back to a full rebuild**. Reusing edges computed under
different parameters is the single most likely way to ship a silently-wrong graph, so make this check
explicit and log the reason.

### Step 2 — Add the flag

```python
parser.add_argument(
    "--incremental",
    action="store_true",
    help=(
        "Reuse the previous graph.json and recompute only notes whose mtime changed "
        "since meta.generated. Falls back to a full rebuild if the previous graph is "
        "missing, unreadable, or was built with different parameters."
    ),
)
```

Default off, so existing callers are unaffected until they opt in.

### Step 3 — Load and validate the previous graph

```python
def load_previous_graph(path: Path, args: argparse.Namespace) -> dict | None:
    """Return the previous graph iff it is safely reusable, else None.

    Returning None means "do a full rebuild" — every failure mode collapses to that,
    because a wrong graph is worse than a slow one.
    """
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    meta = prev.get("meta", {})
    if meta.get("schema_version") != GRAPH_SCHEMA_VERSION:
        return None
    if meta.get("min_semantic_threshold") != args.min_threshold:
        return None
    if meta.get("max_neighbors") != args.max_neighbors:
        return None
    if meta.get("include_daily") != args.include_daily:
        return None
    if not meta.get("generated"):
        return None
    return prev
```

Add `include_daily` to `meta` as part of this step — it is currently not recorded, and it changes which
nodes exist, so it must participate in the compatibility check.

### Step 4 — Compute the changed set

`note_index` already carries `mtime` (it is selected in `load_note_metadata`). Compare against
`meta.generated`:

```python
def compute_changed_stems(notes: list[dict], prev: dict) -> tuple[set[str], set[str], set[str]]:
    """Return (changed, added, removed) stems relative to the previous graph."""
    prev_stems = {n["id"] for n in prev["nodes"]}          # confirm the node id field name
    cur_stems = {n["stem"] for n in notes}
    generated = datetime.datetime.fromisoformat(prev["meta"]["generated"].replace("Z", "+00:00"))
    cutoff = generated.timestamp()

    added = cur_stems - prev_stems
    removed = prev_stems - cur_stems
    modified = {n["stem"] for n in notes if n["stem"] in prev_stems and float(n["mtime"] or 0) > cutoff}
    return modified | added, added, removed
```

**Read the node-writing code in `main()` first** to confirm whether the node identity field is `id`,
`stem`, or something else, and match it. Do not assume.

Subtract a small safety margin (say 2 seconds) from `cutoff` to absorb clock granularity between the
indexer writing `mtime` and the graph writing `generated`.

### Step 5 — Expand to the recompute closure

```python
def expand_recompute_set(changed: set[str], prev_edges: list[dict]) -> set[str]:
    """Add every note currently sharing an edge with a changed note.

    Top-K selection is relative: a new strong neighbour can evict an existing edge
    between two otherwise-unchanged notes. Recomputing both endpoints of every
    edge that touches the changed set closes that hole.
    """
    out = set(changed)
    for e in prev_edges:
        if e.get("kind") != "semantic":
            continue
        s, t = e["s"], e["t"]
        if s in changed:
            out.add(t)
        elif t in changed:
            out.add(s)
    return out
```

### Step 6 — Incremental semantic edge computation

Add a function that computes edges for the recompute set against all notes without forming `N × N`:

```python
def build_semantic_edges_incremental(
    stems: list[str],
    normalized: np.ndarray,
    recompute: set[str],
    min_threshold: float,
    max_neighbors: int,
) -> tuple[list[dict], set[str]]:
    """Compute top-K edges for `recompute` stems only.

    Returns (new_edges, recomputed_stems). Peak extra memory is |recompute| x N,
    not N x N.
    """
    idx_of = {s: i for i, s in enumerate(stems)}
    rows = [idx_of[s] for s in recompute if s in idx_of]
    if not rows:
        return [], set()

    sub = normalized[rows] @ normalized.T          # shape (|recompute|, N)
    for local_i, global_i in enumerate(rows):
        sub[local_i, global_i] = -1.0              # no self-edges

    # ... same top-K selection as ENH-001's build_semantic_edges, but over `sub` rows ...
```

Factor the top-K selection out of ENH-001's `build_semantic_edges` into a shared helper so full and
incremental modes cannot diverge — that divergence is exactly the bug class this repo already has in
its two `findNote` copies and its two vault resolvers.

### Step 7 — Merge

In `main()`, when incremental mode is active and the previous graph validated:

1. Keep previous `semantic` edges where **neither** endpoint is in the recompute set and **neither** is
   in `removed`.
2. Add the newly computed edges.
3. Rebuild `wiki` edges from scratch — they are cheap (a frontmatter scan, no matrix) and their
   correctness depends on the whole `related` graph. Do not try to make wiki edges incremental.
4. Re-run par-mem body-link enrichment as today; it is already fail-soft.
5. Rebuild the node list from current `note_index` rows, so `removed` nodes disappear naturally.
6. Write `meta.generated` to the current time and `meta.incremental: true` for observability.

Log a one-line summary to stderr: `incremental: 18 changed, 214 recomputed, 361,402 edges reused, 14,658 recomputed`.

### Step 8 — Wire it up

- Add `--incremental` pass-through in `update_index.py` where it spawns `build_graph.py`
  (currently around `update_index.py:757-764` — **re-read, this file is also edited by audit items
  DOC-003, QA-005, QA-009, QA-017**).
- Add a `graph_incremental` key to the `summarizer` config section and to
  `skills/parsidion/templates/config.yaml` and `_CONFIG_SCHEMA` in `vault_config.py`. Note audit item
  ARC-011 adds a test asserting `validate_config()` returns `[]` for the shipped template — this new
  key must be added to the schema or that test will fail.
- Once measured stable, consider flipping `summarizer.rebuild_graph` to default on, since the cost
  objection is what this enhancement removes. Make that a separate commit.

### Step 9 — Tests

In `tests/test_build_graph_parmem.py`:

1. **Full and incremental agree.** Build a synthetic vault, run a full build, modify two notes, run
   incremental, then run a full build again — the two final graphs must be **edge-set identical**.
   This is the central correctness test; write it first.
2. **Parameter change forces full rebuild.** A previous graph with `max_neighbors: 15` plus a run at
   `--max-neighbors 25` must not reuse anything.
3. **Schema version mismatch forces full rebuild.**
4. **Deleted note vanishes.** Its node and every edge touching it are absent.
5. **Added note is connected.**
6. **Missing previous graph falls back cleanly** rather than raising.
7. **Eviction is handled.** Add a note strongly similar to an existing dense cluster and assert the
   evicted edge is actually gone — this is the test that proves Step 5's closure works.

## Files to touch

| File | Change |
|---|---|
| `skills/parsidion/scripts/build_graph.py` | `--incremental`; `GRAPH_SCHEMA_VERSION`; `load_previous_graph`; `compute_changed_stems`; `expand_recompute_set`; `build_semantic_edges_incremental`; merge logic in `main()`; extend `meta` |
| `skills/parsidion/scripts/update_index.py` | pass `--incremental` through |
| `skills/parsidion/scripts/vault_config.py` | add `summarizer.graph_incremental` to `_CONFIG_SCHEMA` |
| `skills/parsidion/templates/config.yaml` | document the new key |
| `tests/test_build_graph_parmem.py` | the seven tests above |
| `CLAUDE.md`, `docs/VISUALIZER.md` | document incremental mode and the fallback conditions |

## Verification

```bash
make test-graph
uv run ruff format --check . && uv run ruff check . && uv run pyright .

# Equivalence against the real vault — the acceptance criterion
uv run skills/parsidion/scripts/build_graph.py --output /tmp/g-full-1.json
touch ~/ParsidionVault/Patterns/*.md   # or edit two real notes
uv run skills/parsidion/scripts/build_graph.py --incremental \
    --output /tmp/g-full-1.json         # rewrites in place, incrementally
uv run skills/parsidion/scripts/build_graph.py --output /tmp/g-full-2.json

python3 - <<'EOF'
import json
inc = json.load(open('/tmp/g-full-1.json'))
full = json.load(open('/tmp/g-full-2.json'))
key = lambda e: (e['s'], e['t'], e['kind'])
a, b = {key(e) for e in inc['edges']}, {key(e) for e in full['edges']}
print(f"incremental {len(a)} edges, full {len(b)} edges")
print(f"only-incremental: {len(a-b)}   only-full: {len(b-a)}")
assert a == b, "incremental diverged from full rebuild"
print("OK — edge sets identical")
EOF

# Timing — report both numbers in the completion note
time uv run skills/parsidion/scripts/build_graph.py --output /tmp/t-full.json
time uv run skills/parsidion/scripts/build_graph.py --incremental --output /tmp/t-inc.json
```

The equivalence assertion is the gate. A timing improvement with a divergent edge set is a failure, not
a partial success.

## Rollback

`--incremental` is opt-in and default-off, so not passing it restores current behaviour exactly.
`graph.json` is a generated artifact — deleting it and running a full build recovers from any corrupt
incremental state. Bumping `GRAPH_SCHEMA_VERSION` also forces every existing graph to full-rebuild once,
which is a deliberate safety valve: if a bug is discovered after release, bumping the constant
invalidates every incrementally-produced file in the field.

## Risks

- **Silent divergence** is the real risk, not crashes. Test 1 (full/incremental edge-set equality) is
  the mitigation and must not be weakened to a count comparison.
- **mtime granularity** on some filesystems can miss a same-second edit. The 2-second cutoff margin
  handles this; document that a `--force-full` escape hatch (or just omitting `--incremental`) exists.
- **Top-K eviction** is the subtle one — covered by Step 5 and test 7. If ENH-001 has *not* landed and
  edge selection is still pure-threshold, the closure in Step 5 is unnecessary but harmless.
