# ENHANCEMENTS.md

> Performance, functionality, and maintainability opportunities for parsidion — **beyond** the
> defect findings in `AUDIT.md`. These are net-new improvements, not bug fixes.
>
> **Consumed by `/enhancement-all`** (and `/enhancement-next`). An item is marked `[x]` only once
> its verification passes; finished items are marked rather than deleted so this file stays a
> standing record of what shipped. Numbering never changes once assigned — an ID appears on a
> board card and in a `docs/opus/` plan filename.
>
> Format: `- [ ] **ENH-NNN — Title** — one paragraph (what + why). (impact: high|medium|low, effort: small|medium|large, plan: docs/opus/ENH-NNN-<slug>.md)`
>
> _Cycle: 2026-08-01 (Opus audit). Mined from the par-mem graph (`find_hotspots`,
> `find_dead_code`, `find_central_symbols`, `get_repository_stats`) plus the audit's
> opportunity surface._
>
> **Numbering note:** ENH-001…ENH-008 are from a prior cycle and **shipped in 0.15.0** (the
> CHANGELOG maps them; their `docs/opus/ENH-00{1..8}-*.md` plans and `done` board cards remain as
> the historical record). This cycle's tracking file was removed once all eight shipped (commit
> `e0faf8c`). New items continue from **ENH-009** so no existing ID is reused. Six new items below.

- [x] **ENH-009 — Make vault resolution Python-canonical** *(done 2026-08-01)* — The vault-precedence contract was implemented twice (Python `core/vault_path.py:resolve_vault` and TS `visualizer/lib/vaultResolver.ts:resolveVault`), and the two diverged (the TS resolver supported fewer channels — see ARC-007/SEC-P001). Resolved by adding `resolve_vault_server()` (the narrower server contract) to `core/vault_path.py` + a stdlib `vault_resolve.py` CLI, and making the visualizer's `vaultResolver.ts` delegate to it over the existing `runScript` subprocess path. The cross-language duplication and drift class are eliminated; MCP stays optional and untouched. (impact: high, effort: large, plan: docs/opus/ENH-009-python-canonical-vault-resolution.md)

- [ ] **ENH-010 — Incremental graph/embedding rebuild** — `build_graph.py` (1011 LOC) does a full re-embed of the vault on every run; `graph.json` is rebuilt wholesale even when only a few notes changed. Serving is already streamed + ETag-cached (ARC-015), but the *rebuild* cost is unaddressed. Track note mtimes/hashes and re-embed only changed notes, writing an incremental delta the visualizer's `graph/delta` route can consume. Biggest win for large vaults. (impact: medium, effort: large, plan: docs/opus/ENH-010-incremental-graph-rebuild.md)

- [ ] **ENH-011 — Generated API reference under `docs/api/`** — The public Python surface (`vault_common`, `core/*`, the `vault_*` CLIs) and the TS visualizer lib have Google-style docstrings/JSDoc but no generated reference; `docs/MCP.md` documents only the MCP tools. Add a pdoc (Python) + TypeDoc (visualizer) generated API reference under `docs/api/` wired into the Makefile (`make docs-api`), so the reference never drifts from the code. (impact: medium, effort: small, plan: docs/opus/ENH-011-generated-api-reference.md)

- [ ] **ENH-012 — SSE route integration tests for the visualizer** — The `vault/events` and `graph` Server-Sent-Events routes (which drive live vault updates) have no integration test exercising a real watcher — they are only covered indirectly. The `note/route.test.ts` pattern shows how to stand up a temp vault + dispatch a request; extend it to open an SSE connection, mutate the vault, and assert an event frame arrives. Catches watcher/serialization regressions. (impact: medium, effort: small, plan: docs/opus/ENH-012-sse-route-tests.md)

- [ ] **ENH-013 — Wire-or-delete dead helpers in `tools/eval/`** — par-mem's `find_dead_code` flags ~37 functions, but most are false positives (MCP tools registered via decorator, JSX components, Next config methods, SSE controller `start`/`cancel`). The genuinely-dead ones cluster in `tools/eval/` (`embed_eval_report.display_results`/`save_json_results`, per-evaluator `_load_inputs`, `_base.version_stamp`) — a developer-only eval harness. Do a focused wire-or-delete sweep so `find_dead_code` noise drops and the eval CLI's live surface is honest. (impact: low, effort: small, plan: docs/opus/ENH-013-eval-dead-code-sweep.md)

- [ ] **ENH-014 — Typed config schema via stdlib dataclasses** — `core/vault_config.py` parses `config.yaml` with a hand-rolled stdlib YAML parser (one level of nesting, inline comments, scalars) returning untyped dicts; config keys are read throughout via `get_config(section, key, default)` with no validation, so a typo silently falls back to the default. Define dataclass schemas per section (`SessionStartHookConfig`, `SummarizerConfig`, …) with defaults and a `validate()` that runs at load, keeping the parser stdlib-only (the constraint forbids pydantic/ruamel). Surfaces misconfigured keys at startup instead of as silent wrong behavior. (impact: medium, effort: medium, plan: docs/opus/ENH-014-typed-config-schema.md)
