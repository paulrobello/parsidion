# ENH-003 — Eliminate per-spawn embedding model loads

> **Impact**: high · **Effort**: medium · **Status**: done
> Source: Opus deep audit, 2026-07-28, commit `8e5d549`
> **Overlap note:** Step 1 below is the same change as audit item ARC-027(c). If `/fix-audit` has already
> landed that, start at Step 2.

## Goal

Stop paying a ~67 MB ONNX model load for every semantic search. Summarizer runs should be dominated by
AI-provider latency, not by repeatedly warming an embedding model that was already warm a second ago.

## Current state

`skills/parsidion/scripts/vault_search.py` loads the model **inside** the per-call function, with no
caching at any level:

```python
# vault_search.py:148-150 (inside _search_embeddings)
from fastembed import TextEmbedding  # type: ignore[import-untyped]

model = TextEmbedding(model_name=model_name)
```

There is already a perfectly good in-process entry point — `vault_search.search(...)` at
`vault_search.py:226` — but the hot callers do not use it. They spawn a subprocess instead:

| Caller | Site | Why it spawns |
|---|---|---|
| Summarizer dedup | `summarize_sessions.py:1180` (`_find_dedup_candidates`) | historical |
| Backlink discovery | `vault_links.py:358` (`find_related_by_semantic`) | historical |
| Visualizer search | `visualizer/lib/searchServer.ts` | genuinely needs a subprocess (different runtime) |

Both Python callers run **per queue entry** — dedup before the AI call, backlinks after — so a single
summarizer run over `max_parallel: 5` sessions performs up to **ten** independent model loads, and up
to five of them concurrently. The actual search work behind each is an sqlite-vec ANN lookup, i.e.
milliseconds. The load dominates by orders of magnitude.

Two secondary repeats in the same hot path, worth fixing in the same pass because they share the
"re-derive per entry what could be derived once" shape:

- `_dead_lettered_ids` re-reads and re-parses the entire dead-letter file once per entry
  (`summarize_sessions.py:1299`).
- `read_project_names` (`summarize_sessions.py:359-385`, called at `:1537`) reads **every note in the
  vault** to collect `project` values that already exist as a `note_index` column.

## Implementation

### Step 1 — Process-level model cache (do this first; it is small and self-contained)

Wrap model construction in an LRU cache keyed by model name so repeated calls **within one process**
share the instance:

```python
@functools.lru_cache(maxsize=2)
def _get_embedding_model(model_name: str):
    """Cache the fastembed model per process.

    Loading is ~67 MB and dominates a search whose actual work is an sqlite-vec
    ANN lookup. maxsize=2 covers the default model plus one override without
    pinning an unbounded set.
    """
    from fastembed import TextEmbedding  # type: ignore[import-untyped]

    return TextEmbedding(model_name=model_name)
```

Replace the construction at `:150` with `model = _get_embedding_model(model_name)`. Keep the
`ImportError` guard at `:148` — it is the graceful-degradation path referenced by audit item QA-009 and
must not be lost. Put the `try/except ImportError` around the `_get_embedding_model` call, not inside
the cached function, so a failure is not cached.

This alone does nothing for the subprocess callers — that is Step 2 — but it fixes the interactive TUI,
`vault-search` batch use, and anything else making repeated in-process calls.

### Step 2 — Convert the two Python callers to in-process calls

**`vault_links.find_related_by_semantic` (`vault_links.py:358-368`):**

Replace the `uv run --no-project vault_search.py` subprocess with a direct
`vault_search.search(query, limit=..., vault=vault, min_score=...)` call. Two things to get right:

- `vault_links.py` is **stdlib-only** by project rule. Importing `vault_search` (which imports
  `fastembed` behind a guard) must therefore be a **lazy, guarded, in-function import**, exactly like
  the existing `vault_merge.py:710-717` pattern. On `ImportError`, return `[]` and let the caller
  proceed without semantic backlinks — the current behaviour when the extra is missing.
- The current call **never forwards `--vault`** (this is audit item ARC-027(b)) so multi-vault users
  get backlinks computed against the wrong vault. Pass `vault=` explicitly as part of this change and
  add a test asserting it.

**`summarize_sessions._find_dedup_candidates` (`summarize_sessions.py:1180`):**

Same conversion. `summarize_sessions.py` is a PEP 723 script and is already permitted third-party
imports, so the guard is about the `search` extra being installed, not about the stdlib rule.

Preserve the existing result-shape handling. Note `vault_search.search` sets the module global
`LAST_BACKEND` out of band (audit item ARC-031) — if that global is read anywhere near these call
sites, in-process calls change its timing relative to subprocess calls. Grep for `LAST_BACKEND` before
converting and handle it explicitly rather than discovering it later.

### Step 3 — Hoist the two per-entry re-reads

- `_dead_lettered_ids`: compute once before the fan-out in `run_all` and pass the set down, instead of
  re-reading per entry at `:1299`.
- `read_project_names`: replace the vault-wide walk with a `SELECT DISTINCT project FROM note_index`
  against `embeddings.db`, falling back to the existing walk when the DB is absent. (This is a small
  instance of ENH-004's general principle — if ENH-004 has landed, use its helper instead of adding a
  second query.)

### Step 4 — Optional local embedding daemon (only if Steps 1–3 leave a real gap)

Do **not** build this speculatively. Measure after Step 3 first. It is justified only if a
subprocess-based caller — realistically just the visualizer, which is a different runtime and cannot
share a Python process — is still hot enough to matter.

If it is warranted:

- A small `vault_embed_serve.py` listening on a **Unix domain socket** inside the vault directory
  (never a TCP port — this repo's threat model already treats network-bound services as a security
  surface, see audit item SEC-102), with mode `0600` on the socket.
- Protocol: newline-delimited JSON, one request/response per line, `{"text": "...", "model": "..."}` →
  `{"vector": [...]}`.
- `vault_search.py` gains a `--daemon` / `search.use_daemon` config path that tries the socket and
  falls back to in-process loading when it is absent. Absence must be normal, not an error.
- Lifecycle: started lazily by the first client, idle-exits after N minutes. Reuse the existing
  singleton/PID discipline from `summarize_sessions.py` rather than writing a third one — that code
  already handles stale-PID reclaim correctly.
- Register the socket path in `.gitignore` via `installer/vault.py`'s entries list.

### Step 5 — Tests

1. `_get_embedding_model` returns the same object for two calls with the same model name
   (`assert a is b`), and `TextEmbedding` is constructed exactly once (patch and count).
2. A missing `fastembed` still yields the documented graceful degradation — `[]` from
   `find_related_by_semantic`, not a traceback — and the failure is **not** cached.
3. `find_related_by_semantic` forwards the `vault` argument (regression test for ARC-027(b)).
4. Result shape from the in-process path is identical to the previous subprocess path — assert against
   a recorded fixture so the conversion cannot silently change the contract.
5. `read_project_names` returns the same set from the DB path and the walk fallback.

## Files to touch

| File | Change |
|---|---|
| `skills/parsidion/scripts/vault_search.py` | `_get_embedding_model` LRU cache; keep the ImportError guard outside it |
| `skills/parsidion/scripts/vault_links.py` | in-process guarded call; forward `vault=` |
| `skills/parsidion/scripts/summarize_sessions.py` | in-process dedup call; hoist `_dead_lettered_ids`; DB-backed `read_project_names` |
| `tests/test_parmem_search.py`, `tests/test_summarize_sessions.py` | the five tests above |
| `docs/EMBEDDINGS.md` | document the caching behaviour and, if built, the daemon |

## Verification

```bash
uv run ruff format --check . && uv run ruff check . && uv run pyright .
uv run pytest tests/ -v

# Measured effect — report both numbers in the completion note.
# Before: each search pays a cold load. After: only the first does.
python3 - <<'EOF'
import sys, time
sys.path.insert(0, 'skills/parsidion/scripts')
import vault_search
for i in range(3):
    t = time.perf_counter()
    vault_search.search("hook patterns", limit=5)
    print(f"call {i+1}: {time.perf_counter()-t:.2f}s")
EOF
# Expect call 1 slow (cold load) and calls 2-3 to drop by an order of magnitude.
# Before this change all three are slow.

# End-to-end: a real summarizer run should show no repeated model-load lines
env -u CLAUDECODE uv run --no-project skills/parsidion/scripts/summarize_sessions.py --dry-run
```

## Rollback

Step 1 is a decorator plus one line — trivially revertible, and `lru_cache` changes no observable
behaviour beyond object identity. Steps 2–3 are behaviour-preserving call-site conversions; revert by
restoring the subprocess invocations (keep them in git history rather than commented out). Step 4, if
built, is entirely opt-in behind a config key and a socket that is treated as optional, so deleting the
daemon script is a complete rollback.

## Risks

- **Memory retention.** A cached model stays resident for the process lifetime. That is correct for the
  summarizer and the TUI, and irrelevant for one-shot CLI invocations that exit immediately. `maxsize=2`
  bounds it.
- **Thread/async safety.** `run_all` fans out via `anyio` task groups. Confirm `fastembed`'s
  `TextEmbedding` is safe to call concurrently from multiple tasks in one process; if it is not, guard
  the embed call with a lock. Test this explicitly under `max_parallel: 5` — a race here would be far
  worse than the load cost it replaces.
- **Losing graceful degradation.** The `ImportError` guard is load-bearing documented behaviour. Keep
  it outside the cached function so a transient failure is not memoized.
