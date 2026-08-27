# ENH-020 — Managed lifecycle for the warm embedding service

## Goal

Make `vault_embed_serve` (ENH-003's warm-model daemon) effectively zero-config: when the local-embeddings backend is actually in use, short-lived callers (`vault-search`, the SessionStart semantic leg's embeddings fallback, summarizer dedup) get a warm ~10 ms embed instead of a 2–5 s cold ONNX load, without the user ever running the service by hand.

## Current state

- `vault_embed_serve.py` exists (AF_UNIX socket, idle-exit) but is opt-in: `embeddings.service_enabled` defaults to `false` (`core/vault_schema.py:659-664`), so the shipped default pays the ~67 MB ONNX cold load in every short-lived process.
- The service is deliberately never used while parsight serves retrieval (documented interaction), and PRF-101 (audit 2026-08-26) moves the parsight path in-process — so this enhancement matters exactly when the embeddings backend is the active one.
- Client-side selection lives in `vault_search.py` / `cli/search/embeddings.py`; the audit's QA-103/PRF-101 finding quantified the cost (up to 3–8 s of SessionStart wall time on the embeddings path, sometimes hitting the 10 s kill for zero results).

## Implementation

1. **Auto-spawn on first client miss.** In the embeddings client path (where the code currently falls back to in-process model load when the socket is absent), add a `_maybe_spawn_service(vault)` step: if `embeddings.service_enabled` (see step 3) and no live socket, spawn `uv run --no-project vault_embed_serve.py` detached via the existing fire-and-forget pattern (`core/subproc_util` helpers; `start_new_session=True`), then proceed with the in-process load for *this* request (don't wait). Subsequent callers hit the warm socket.
2. **Single-flight guard.** Protect the spawn with an flock on `<vault>/.embed_serve.lock` (pattern: `vault_fs.try_singleton_lock`) so parallel cold callers spawn one service, not N. The service itself should also take the lock (or an equivalent socket-bind check — binding the AF_UNIX socket is already atomic; verify and prefer that).
3. **Flip the default with a guard.** Change `embeddings.service_enabled` default to `true` in `core/vault_schema.py`, keeping `service_idle_exit` (existing) so an unused service exits on its own. Users can still set `false` to forbid the daemon. Run `make config-docs` and commit regenerated docs.
4. **Observability.** On spawn, and on client fallback-to-cold-load, `write_hook_event` entries (`EmbedServiceSpawn`, `EmbedColdLoad`) so `vault-stats --hooks` shows how often the cold path is still hit.
5. **Doc pass.** Update `docs/EMBEDDINGS.md`'s service section (auto-start semantics, lock file, how to disable).

## Files to touch

- `skills/parsidion/scripts/cli/search/embeddings.py` (client fallback path)
- `skills/parsidion/scripts/vault_embed_serve.py` (bind-atomicity/single-flight check)
- `skills/parsidion/scripts/core/vault_schema.py` (default flip) + regenerated config docs
- `skills/parsidion/scripts/core/vault_fs.py` (only if a new lock helper variant is needed)
- `docs/EMBEDDINGS.md`
- `tests/` (new: single-flight spawn test with monkeypatched Popen; default-flip schema test)

## Verification

- `uv run --extra search pytest tests/ -k "embed" -q` passes, including a test that two concurrent cold clients spawn exactly one service (monkeypatched spawn counter).
- `make config-docs-check` passes with the regenerated default.
- Manual: with parsight disabled and no service running, `time vault-search -B embeddings "query"` twice — second run is warm (sub-second) and `EmbedServiceSpawn` appears once in `vault-stats --hooks 20`.
- `make checkall` green.

## Rollback

Set `embeddings.service_enabled: false` in config (behavior reverts to today's cold loads); or revert the commit — the service protocol is unchanged, so mixed states are safe. The lock file is inert if orphaned (flock released on process death).
