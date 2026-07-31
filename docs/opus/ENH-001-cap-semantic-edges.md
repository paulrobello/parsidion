# ENH-001 — Cap semantic edges per node instead of emitting all pairs above a threshold

> **Impact**: high · **Effort**: small · **Status**: ✅ done
> Source: Opus deep audit, 2026-07-28, commit `8e5d549`
>
> **Completed.** `--max-neighbors` flag, top-K `build_semantic_edges`, `meta.max_neighbors`,
> and all six plan tests shipped (run `make test-graph` → 27 passed). Measured on the live
> vault at regeneration: mean degree ~67.6 → ~15.8 (≈ the `max_neighbors=15` target), max
> degree 1258 → 324, and the file shrank from 47.5 MB / 376,060 edges to ~15.6 MB / 110,414
> edges. Wiki edges untouched. `--max-neighbors 0` restores the all-pairs behaviour.

## Goal

Reduce `graph.json` from ~47.5 MB to roughly 8–12 MB by replacing the current
"emit every pair above a similarity floor" edge policy with a **top-K nearest neighbours per node**
policy, without losing any relationship a reader would actually navigate.

Success is measured, not asserted: the rebuilt `graph.json` must be at least 4× smaller, and every
`wiki` edge (the explicit `[[wikilink]]` relationships) must survive untouched.

## Current state

`skills/parsidion/scripts/build_graph.py` computes a full pairwise cosine similarity matrix and then
walks its upper triangle, emitting an edge for every pair at or above `--min-threshold` (default `0.70`):

```python
sim = normalized @ normalized.T  # shape (N, N)

n = len(stems)
edges = []
# Extract upper triangle (i < j)
for i in range(n):
    for j in range(i + 1, n):
        w = float(sim[i, j])
        if w >= min_threshold:
            edges.append({"s": stems[i], "t": stems[j], "w": round(w, 4), "kind": "semantic"})
```

Measured against the live vault at audit time:

| | |
|---|---|
| `graph.json` | 47,501,974 bytes (45.3 MiB) |
| nodes | 5,563 |
| edges | 376,060 |
| mean degree | ~67.6 |
| `meta.min_semantic_threshold` | 0.7 |
| `meta.parmem_body_links` | 202 |

The problem is structural rather than parametric. A fixed similarity floor produces a degree
distribution that scales with vault size and with how topically clustered the vault is — this vault is
59% `pattern` notes, which are mutually similar by construction, so the 0.7 floor admits a dense
core. Raising the threshold alone would fix the size but strip *sparse* notes of all edges, because a
single global cutoff cannot serve both a note in a dense cluster and a note in a thin one.

Top-K is the standard answer: each node keeps its K strongest neighbours regardless of how dense its
neighbourhood is, so sparse notes stay connected and dense clusters stop emitting quadratic noise.

Note that `write_graph_json` already writes via tmp + atomic replace — do not change that.

## Implementation

### Step 1 — Add the CLI flag

In `parse_args()` in `skills/parsidion/scripts/build_graph.py`, add:

```python
parser.add_argument(
    "--max-neighbors",
    type=int,
    default=15,
    metavar="INT",
    help=(
        "Maximum semantic edges kept per note, strongest first (default: 15). "
        "Pass 0 to disable the cap and emit every pair above --min-threshold."
    ),
)
```

Keep `--min-threshold` and its 0.70 default. The two compose: the threshold is a floor, the cap is a
ceiling, and a node with fewer than K neighbours above the floor simply keeps fewer.

### Step 2 — Rewrite `build_semantic_edges`

Replace the upper-triangle loop with a top-K selection. Signature gains `max_neighbors: int`.

```python
def build_semantic_edges(
    stems: list[str],
    normalized: np.ndarray,
    min_threshold: float,
    max_neighbors: int = 15,
) -> list[dict]:
    """Semantic edges: each note keeps its strongest `max_neighbors` neighbours.

    A fixed similarity floor alone produces a degree distribution that scales with
    topical density, so densely-clustered note types dominate the edge count while
    sparse notes stay under-connected. Capping per node keeps both ends sane.
    Edges are undirected; a pair selected by either endpoint is kept once.
    """
    n = len(stems)
    if n == 0:
        return []

    sim = normalized @ normalized.T
    np.fill_diagonal(sim, -1.0)  # never select self

    if max_neighbors <= 0 or max_neighbors >= n:
        candidate_cols = [np.arange(n)] * n
    else:
        # argpartition is O(n) per row vs O(n log n) for a full sort.
        top_idx = np.argpartition(-sim, max_neighbors - 1, axis=1)[:, :max_neighbors]
        candidate_cols = list(top_idx)

    seen: set[tuple[int, int]] = set()
    edges: list[dict] = []
    for i in range(n):
        for j in candidate_cols[i]:
            j = int(j)
            if j == i:
                continue
            w = float(sim[i, j])
            if w < min_threshold:
                continue
            a, b = (i, j) if i < j else (j, i)
            if (a, b) in seen:
                continue
            seen.add((a, b))
            edges.append(
                {"s": stems[a], "t": stems[b], "w": round(w, 4), "kind": "semantic"}
            )
    return edges
```

Two details that matter:

- `np.fill_diagonal(sim, -1.0)` before selection, so a note never selects itself as its own strongest
  neighbour and silently consumes one of its K slots.
- The union-of-both-endpoints behaviour is deliberate. If A lists B in its top-K but B does not list A,
  the edge is still kept. This is what preserves connectivity for sparse notes — a hub note will not
  list a thin note among its 15 strongest, but the thin note will list the hub.

### Step 3 — Thread the argument through `main()`

Pass `args.max_neighbors` into the `build_semantic_edges` call.

### Step 4 — Record the policy in `meta`

The visualizer and any future incremental mode (ENH-002) need to know how the file was produced.
In the `graph` dict assembled in `main()`, add alongside the existing `min_semantic_threshold`:

```python
"max_neighbors": args.max_neighbors,
```

### Step 5 — Tests

Add to `tests/test_build_graph_parmem.py` (already numpy-gated, run by `make test-graph`):

1. **Cap is honoured** — build a synthetic 50-node embedding matrix where every pair exceeds the
   threshold; assert with `max_neighbors=5` that no node has degree > 10 (its own 5 plus up to 5 from
   nodes that selected it) and that the total edge count is far below the 1,225 all-pairs count.
2. **Threshold still floors** — with `max_neighbors=50` and a matrix where only 3 pairs exceed 0.7,
   assert exactly 3 semantic edges.
3. **Sparse notes stay connected** — construct one node whose best similarity is 0.71 while the rest
   of the graph sits above 0.95; assert that node still receives at least one edge.
4. **Wiki edges untouched** — assert the `kind == "wiki"` edge count is identical with the cap on and off.
5. **`max_neighbors=0` disables the cap** — assert output is identical to the pre-change all-pairs behaviour.
6. **No self-edges** — assert no edge has `s == t`.

## Files to touch

| File | Change |
|---|---|
| `skills/parsidion/scripts/build_graph.py` | `--max-neighbors` flag; rewrite `build_semantic_edges`; thread arg; add `meta.max_neighbors` |
| `tests/test_build_graph_parmem.py` | six new tests above |
| `CLAUDE.md` | document `--max-neighbors` in the `make graph` section |
| `docs/VISUALIZER.md` | note the edge policy so the graph's density is explicable to readers |

Do **not** change `write_graph_json` — it already does tmp + atomic replace correctly.

## Verification

```bash
# Unit gate
make test-graph
uv run ruff format --check . && uv run ruff check . && uv run pyright .

# Real measurement — the actual acceptance criterion
ls -l ~/ParsidionVault/graph.json                      # record the BEFORE size
uv run skills/parsidion/scripts/build_graph.py --output /tmp/graph-capped.json
ls -l /tmp/graph-capped.json                           # expect roughly 8-12 MB

python3 - <<'EOF'
import json, os
old = json.load(open(os.path.expanduser('~/ParsidionVault/graph.json')))
new = json.load(open('/tmp/graph-capped.json'))
ow = sum(1 for e in old['edges'] if e.get('kind') == 'wiki')
nw = sum(1 for e in new['edges'] if e.get('kind') == 'wiki')
print(f"nodes  {len(old['nodes'])} -> {len(new['nodes'])}")
print(f"edges  {len(old['edges'])} -> {len(new['edges'])}")
print(f"wiki   {ow} -> {nw}   (MUST be equal)")
assert len(new['nodes']) == len(old['nodes']), "node count changed — regression"
assert nw == ow, "wiki edges lost — regression"
assert len(new['edges']) < len(old['edges']) / 4, "cap did not reduce edges enough"
print("OK")
EOF
```

Then load the visualizer against the new file and confirm the graph is still navigable — clusters
should remain visually distinct, and no note should appear as an isolated island that was previously
connected.

## Rollback

Entirely contained in one function plus one flag. To revert behaviourally without reverting code, run
with `--max-neighbors 0`, which restores the exact all-pairs output. To revert the code, `git revert`
the commit; `graph.json` is a generated artifact and is regenerated by the next `make graph`, so there
is no data migration and no persisted state to unwind.

## Risks and how they are handled

- **A relationship a user relied on disappears.** Mitigated by the union-of-both-endpoints rule and by
  the sparse-note test. If it still happens, `--max-neighbors 25` is a one-flag adjustment.
- **`argpartition` requires `max_neighbors < n`.** Handled by the `max_neighbors >= n` branch.
- **The N×N matrix is still materialized.** True — this change reduces output size, not peak memory
  (at 5,563 notes the matrix is ~124 MB in float32). Reducing peak memory is ENH-002's job, which is
  why ENH-001 should land first and ENH-002 build on it.
