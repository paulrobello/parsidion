# Enhancement Backlog

> **What this file is.** A standing, prioritized list of *opportunities* — performance, capability, and
> developer-experience work that goes beyond fixing defects. The 2026-07-28 audit was fully remediated and
> its tracking files removed; re-run `/audit` to generate a fresh `AUDIT.md` baseline. `/fix-audit`
> deliberately ignores this file.
>
> **Who works it.** `/enhancement-all` picks up the unchecked items, hands each one's plan to an Opus 5
> subagent, gates on the project's own verification, and only then ticks the box.
>
> **Rules.**
> - An item is marked `[x]` **only once its plan's verification commands actually pass** — not when the
>   code is written.
> - Finished items are **marked, never deleted**. This file is a standing record of what was considered
>   and what shipped.
> - Each item links a full implementation plan under `docs/opus/`. Those plans are written to be executed
>   without further analysis.
>
> Generated 2026-07-28 from the Opus deep audit at `8e5d549`, mined from the par-mem knowledge graph
> (centrality, bridge symbols, community structure), measured git churn, and direct measurement of the
> live vault artifacts.

---

## Measurements these ideas are grounded in

Taken at `8e5d549` against the live vault, so the numbers are real rather than estimated:

| Signal | Value |
|---|---|
| `graph.json` size | **47,501,974 bytes** (45.3 MiB) |
| `graph.json` contents | 5,563 nodes / **376,060 edges** / `min_semantic_threshold: 0.7` |
| Edges per node (mean) | **~67.6** — the semantic all-pairs pass dominates the file |
| `graph.json` freshness | generated `2026-07-13`, i.e. **16 days stale** at audit time |
| Embedding model load | ~67 MB ONNX, loaded **per `vault_search.py` spawn**, up to 5 concurrent |
| Highest-fan-in symbol | `vault_path.resolve_vault` — in-degree **62**, articulation point |
| Its TypeScript twin | `vaultResolver.resolveVault` — in-degree **25**, articulation point |
| Next hubs | `vault_index.parse_frontmatter` (32), `vault_config.get_config` (31), `vault_path.get_embeddings_db_path` (26), `vault_index.all_vault_notes` (26) |
| Churn × size hotspots (6 mo) | `install.py` 919 LOC / 58 commits · `summarize_sessions.py` 2,242 / 27 · `vault_doctor.py` 3,127 / 20 · `GraphCanvas.tsx` 1,055 / 20 · `useVisualizerState.ts` 571 / 21 |
| Note types present | pattern 3,309 · debugging 1,338 · research 494 · project 210 · tool 136 · daily 31 · framework 24 · language 7 · **knowledge 3** |

That last row is worth noting on its own: only **3** `knowledge` notes exist across a 5,563-note vault, which
independently corroborates audit finding ARC-010 — the summarizer structurally cannot produce that type, so
the only ones present were made by hand.

---

## Enhancements

- [x] **ENH-001 — Cap semantic edges per node instead of emitting all pairs above a threshold** — `build_graph.py` currently emits an edge for every note pair whose cosine similarity exceeds `min_semantic_threshold` (0.7), producing 376,060 edges for 5,563 notes — roughly 68 per node, and the single reason `graph.json` is 47.5 MB. Switch to a top-K nearest-neighbour policy per node (K ≈ 12–15, configurable) with the threshold retained as a floor. This preserves every strong relationship a reader actually navigates while cutting the file roughly 4–5×, which in turn makes the visualizer's cold load, the SSE-triggered refetch, and every `JSON.parse` proportionally cheaper. The lowest-effort, highest-payoff item on this list. (impact: high, effort: small, plan: `docs/opus/ENH-001-cap-semantic-edges.md`)

- [x] **ENH-002 — Incremental graph generation** — `build_graph.py` has no `--incremental` flag; every rebuild recomputes the full all-pairs semantic pass over the entire vault, which is why the nightly job is expensive enough that `rebuild_graph` is opt-in and why the live `graph.json` was 16 days stale at audit time. Add an incremental mode that reads the previous `graph.json`, recomputes only notes whose mtime changed since `meta.generated`, and re-derives just those nodes' edges. Pair it with a `meta.schema_version` so a format change forces a full rebuild. This is what makes graph freshness cheap enough to be on by default. (impact: high, effort: medium, plan: `docs/opus/ENH-002-incremental-graph-generation.md`)

- [ ] **ENH-003 — Persistent embedding service to eliminate per-spawn model loads** — every summarizer queue entry spawns `vault_search.py` twice (dedup before the AI call, backlinks after), and each spawn lazily loads a ~67 MB ONNX model with no sharing; at `max_parallel: 5` that is up to five concurrent cold loads for work that is otherwise milliseconds of sqlite-vec ANN lookup. Introduce an in-process path first (import `vault_search`'s entry point rather than subprocessing it), then an optional long-lived local embedding daemon for the CLI callers that genuinely need process isolation. Expected effect: summarizer wall time dominated by AI latency rather than model loading. (impact: high, effort: medium, plan: `docs/opus/ENH-003-persistent-embedding-service.md`)

- [x] **ENH-004 — Make `note_index` the single read path and retire vault-wide `os.walk`** — _note: `find_notes_by_project/tag/type/recent` converted to DB-first with walk fallback; `all_vault_notes` deliberately kept walk-based (its callers include mutation paths — `doctor/*`, `vault_merge`, `vault_export` — that require the authoritative filesystem, not a stale index view); that per-caller audit is tracked as a follow-up_ — `vault_index.all_vault_notes` (in-degree 26) and `read_project_names` walk and re-parse every note in the vault on every invocation, even though `embeddings.db`'s `note_index` table already holds the frontmatter fields being extracted. Route the metadata read paths through SQL, keeping `os.walk` only for index construction itself and for the explicit `--no-db` fallback. Beyond the speed win on a 5,563-note vault, this removes the class of bug where a walk-based path and a DB-based path disagree — and it is the natural home for the symlink containment check the audit found missing (SEC-106). (impact: medium, effort: medium, plan: `docs/opus/ENH-004-note-index-single-read-path.md`)

- [x] **ENH-005 — Shared Python↔TypeScript parity fixtures** — `vault_path.resolve_vault` (in-degree 62) and `visualizer/lib/vaultResolver.resolveVault` (in-degree 25) are the two highest-centrality articulation points in their respective languages, they implement the same contract, and both carry source comments saying they "must stay in sync" — yet the only parity test covers `VAULT_FORBIDDEN_PREFIXES`, not resolution precedence. The same hand-maintained duplication exists for the `graph.json` schema (`build_graph.py` ↔ `visualizer/lib/graph.ts`). Emit shared JSON fixtures — resolution test vectors and a JSON Schema — from the Python side and have both test suites consume them, so drift becomes a CI failure instead of a production surprise. (impact: medium, effort: small, plan: `docs/opus/ENH-005-cross-language-parity-fixtures.md`)

- [ ] **ENH-006 — `AgentAdapter` registry with a documented third-party extension point** — the project's stated goal is to be agent-agnostic, but adding a runtime today means copying 2–3 near-identical hook scripts plus four installer functions, and a third mechanism already exists in parallel (the pi extension, installed by a standalone bash script and unknown to `install.py connect`). Turn the adapter shape into a public, documented contract — a declarative descriptor plus a registry — so a new agent is a data-only addition, and bring the pi extension under it. This takes the deduplication that audit item QA-008 performs internally and turns it into an actual extension point others can target. (impact: medium, effort: medium, plan: `docs/opus/ENH-006-agent-adapter-registry.md`)

- [x] **ENH-007 — `vault-stats --health`: one composite vault health score** — the pieces already exist and are scattered across seven flags: graph metrics, orphan and stale-note counts, pending-queue depth, dead-letter backlog, tag fragmentation, index freshness, and embedding coverage. Combine them into a single scored report with per-dimension grades and concrete next actions ("14 orphan notes — run `vault-doctor --fix-all`"). Make it the default output of bare `vault-stats`. This is small, self-contained, and turns a pile of diagnostics into something a user acts on. (impact: medium, effort: small, plan: `docs/opus/ENH-007-vault-health-score.md`)

- [x] **ENH-008 — Externalize prompts and evaluate them with the existing eval harness** — six prompts are inline string literals inside the largest, highest-churn modules, and the note-schema contract is restated in three different vocabularies across two files. Move prompts into versioned template files, then wire them into the `embed_eval_*` harness that already exists in this repo so a prompt change can be measured rather than guessed at. The payoff is compounding: prompt quality directly determines vault note quality, and today there is no way to tell whether a prompt edit helped. (impact: medium, effort: large, plan: `docs/opus/ENH-008-prompt-templates-and-eval.md`)

---

## Sequencing notes

- **ENH-001 before ENH-002.** Capping edges shrinks the working set that incremental generation has to reason about, and the two touch the same `build_graph.py` edge-emission code. Doing ENH-001 first also means ENH-002 can be measured against a sane baseline.
- **ENH-003's first step overlaps audit item ARC-027(c)** (import `vault_search` in-process instead of spawning). If `/fix-audit` has already landed that, ENH-003 starts from the daemon step.
- **ENH-004 should follow audit item SEC-106.** SEC-106 adds symlink containment to the walk; ENH-004 then moves the read path to SQL. Doing them in the other order risks losing the check.
- **ENH-006 should follow audit item QA-008 / ARC-020.** That work collapses the five copy-pasted hooks into one parameterized module; ENH-006 promotes the result into a documented public contract. Doing ENH-006 first means doing the collapse twice.
- **ENH-005 and ENH-007 are fully independent** and can be picked up at any time.
