# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- **2026-08-23 audit remediation** — all 93 findings from the 2026-08-23 audit resolved (0 Critical / 19 High / 34 Medium / 40 Low; details in `AUDIT-REMEDIATION.md`). Highlights: DNS-rebinding-to-RCE chain in the visualizer closed (Host allowlist + note-only mutation paths on `/api/note`); pi/omp extension no longer resolves hook scripts from a cwd-relative sibling; `atomic_write_text` uses `O_EXCL|O_NOFOLLOW`; config-sourced binaries and API endpoints ownership-checked; `anthropic_env` network keys honored only from `config.local.yaml` or an untracked `config.yaml`; MCP `vault_read` restricted to notes; GitHub Actions pinned to SHAs; 17 further hardening items.

### Changed

- **Unified session-end pipeline** (ARC-002) — `session_stop_hook.py` is now a shim over `agent_adapter.run_session_end`; Codex/Gemini/omp gain config-gated AI classification and auto-summarize; every runtime uses one byte-bounded transcript reader.
- **Wheel manifest complete** (ARC-003) — non-editable installs no longer ship three broken console scripts; CI imports every script target and package from the built wheel.
- **Python floor raised to 3.13** (ARC-009); ruff target `py313`.
- **Single frontmatter emitter** (ARC-005) with a Python+TypeScript parity fixture; **single index-rebuild launcher** (ARC-004); `ai_backend`/`parmem_backend` moved under `core/` (ARC-006); typed config defaults on the schema (ARC-007); `install.py` decomposed into `installer/plan.py` + `installer/cli.py` (ARC-008).
- **Doctor pipeline refactored** (QA-005) — `DoctorOptions`/`ScanContext`/rule registry; `run_scan_and_repair` complexity 31→12 with byte-identical dry-run output.
- **Deterministic test gate** (QA-001) — the doctor e2e suite no longer flakes under coverage; 1599 tests green.
- **`docs/api` regenerated and made checkout-path-invariant** (DOC-003) — typedoc `gitRevision: main`, new `docs-api-checks` CI job, `docs/api` excluded from par-mem.
- Documentation synced across README/CLAUDE.md/docs for grok-cli, config keys, 60 s hook timeouts, `--fix-all`, and the omp runtime (DOC-001..027).

## [0.20.0] - 2026-08-20

Grok Build CLI backend release. The prompt-AI layer gains a third backend (`grok-cli`, grok-4.6), both CLI backends now run hermetically (no CLAUDE.md/AGENTS.md/skill-catalog ingestion), the AI selector's candidate pool is ranked and pruned Python-side, and every runtime gives the SessionStart hook the same 60 s budget.

### Added

- **grok-cli prompt AI backend** — `ai.backend: grok-cli` (also auto-resolved from `PARSIDION_RUNTIME=grok`). Single-turn prompts run via `grok --prompt-file <tmp> --verbatim -m <model>` (prompt on file, never argv — SEC-123 parity) using the CLI's own OAuth login; grok-4.6 headless measures 8–40 s per prompt, so `grok_cli.timeout` defaults to 120 s. New `grok_cli` config section (`command`, `timeout`, `minimal_context`, `system_prompt`); `ai_models.grok.{small,large}` default to `grok-4.6`. Binary resolution shares the SEC-117 gate with codex (`_resolve_configured_binary`).
- **Minimal-context mode for claude-cli and grok-cli** (`claude_cli.minimal_context` / `grok_cli.minimal_context`, default true). Verified live: grok appends every `CLAUDE.md`/`AGENTS.md` found from repo root to cwd plus its full skill catalog to the system prompt (injected instructions leaked verbatim into replies), and `--system-prompt-override` alone does **not** stop project-doc ingestion — so minimal mode also runs from an empty non-git scratch cwd (`--cwd` for grok, process cwd for claude) and, for grok, disables tools/subagents/web search. Claude's `--bare` was rejected for this purpose (it forbids OAuth/keychain auth). Both prompts are configurable via `<backend>_cli.system_prompt`.
- **Ranked + pruned AI-selector candidate pool** — `_build_candidates` now scores candidates Python-side (project match 30 > graph adjacency to project seeds 15 > adaptive usefulness ≤10 > recency ≤10 over a 10-day linear window > hubness ≤5, ties to newer mtime then path — deterministic, no embeddings) and caps the pool at `session_start_hook.ai_candidates_max` (default 48, `0` = unlimited ranked). Previously the pool was unordered and `_select_context_with_ai` embedded whichever prefix fit the 8000-char budget — on a 15k-note vault the selector saw an arbitrary prefix of 839 candidates. Live: pool 839 → 48 ranked in 0.56 s.
- **`claude_cli` config section** (`minimal_context`, `system_prompt`, `timeout`) and `ai_models.grok` in the typed config schema (golden snapshot 18 → 20 sections).

### Fixed

- **omp/pi "SessionStart hook exited with code 143"** — the extension killed `session_start_hook.py` at 12 s while the hook's documented worst case (AI selection + semantic blend + startup) is ~37 s; the fire-and-forget kill discarded all vault context. The AI cooldown stamp is now written after *any* completed backend attempt (success or failure), so a slow/hung backend rate-limits itself instead of re-paying the full `ai_timeout` on every session start.

### Changed

- **All SessionStart hook timeouts unified at 60 s** — Claude Code (`installer.paths._HOOK_OPTIONS`, applied to new registrations *and* existing lower-valued handlers on reinstall — legacy 10 s installs are raised automatically), gemini adapter (`entry_timeout` 10 s → 60 s), omp/pi extension (`HOOK_TIMEOUT_SESSION_START_MS`; was 12 s before 0.20.0). Codex already ran 60 s. The ARC-025 `--enable-ai-mode` 30 s special case is superseded and removed; `--enable-ai-mode` only drives the vault-config half now.

## [0.19.0] - 2026-08-19

### Added

- **omp (oh my pi) runtime support** — `install.py connect omp` / `disconnect omp`. omp is an extension-only runtime like pi: it reuses the same TypeScript extension source (`extensions/pi/parsidion/`), installed into `$PI_CONFIG_DIR/agent/extensions` (default `~/.omp/agent/extensions`; `--omp-home` overrides). Verified against omp 17.3.8: its extension loader resolves the extension's `@mariozechner/*` imports, emits every lifecycle event the extension binds (`session_start`, `before_agent_start`, `session_before_compact`, `session_compact`, `turn_end`, `session_shutdown`), and a headless run wrote a `SessionStart` event to `hook_events.log` with vault context injected. omp's task tool emits no `subagent:result` custom messages, so subagent-transcript capture is a graceful no-op there. `scripts/install-pi-extension` gained `--agent-name` so the post-install hint names the right runtime.

## [0.18.0] - 2026-08-12

Vault-doctor auto-repair release. Two malformed-frontmatter shapes that 0.17.0 taught the doctor to *detect* but deliberately left for a human (`NESTED_FM_KEY` and `SCALAR_LIST_FIELD`) are now repaired deterministically; and the prefix-subfolder migration no longer mangles compound slugs.

### Added
- **Deterministic frontmatter repair** — `NESTED_FM_KEY` (the `metadata:` mapping-wrapper shape) and `SCALAR_LIST_FIELD` (scalar `tags`/`sources`/`related`) now have Python-only repairs in a new orchestrator pre-pass that runs before issue classification. The doctor fixes them instead of leaving them for a human, with no AI backend call. The wrapper fix is byte-equivalent (`parse_frontmatter` already flattens indented keys to top level, so the rewrite just matches the parser's interpretation); a child key that duplicates a top-level key is dropped. Both codes stay in `DETECTION_ONLY_CODES` — the AI path is still never turned loose on structurally broken frontmatter.

### Fixed
- **Prefix-subfolder migration no longer mangles compound slugs** — first-word clustering plus the prompt-AI generic-word filter accepted common modifier prefixes (`client`, `code`, `env`, `id`, `admin`, `asset`) as "subjects", so `--fix-all` split compounds (`client-side-x` → `client/side-x`, `code-quality-x` → `code/quality-x`, `env-var-x` → `env/var-x`) and re-mangled the same notes on every run. A deterministic denylist (`_GENERIC_PREFIX_DENYLIST`) now blocks generic modifier / common-abbreviation prefixes before the AI filter, in both `find_prefix_clusters` and `find_subfolder_candidates`. Real subjects are coined / proper-noun terms (`serde`, `redis`, `extractor`, `token`, `ttl`, `subprocess`) and pass untouched; the AI filter still gates novel prefixes.

## [0.17.0] - 2026-08-11

Vault-doctor integrity release. Five malformed-frontmatter shapes that `parse_frontmatter` silently mis-read — and that the scan therefore reported as clean — are now detected; the backlink writer no longer appends a duplicate `related:` key; and broken-wikilink repair no longer substitutes a daily journal note for a real target. 85 notes in the reference vault were repaired as a result, recovering 50 tags that had been collapsed into unsearchable strings.

### Added
- **Frontmatter-syntax checks** — five new issue codes read the raw frontmatter text, the only place these defects are still visible: `NESTED_FM_KEY` (indented mapping key, previously a stderr warning only), `UNTERMINATED_FM_LIST` (inline list opened with `[` but never closed, stored as the scalar `[`), `ORPHAN_FM_BRACKET` (stray `]` as the first body line), `SCALAR_LIST_FIELD` (`tags`/`related`/`sources` holding a bare scalar, which collapses to one string), and `DUPLICATE_FM_KEY` (same top-level key twice; the parser is last-wins). Measured over 7951 notes: 56 `SCALAR_LIST_FIELD` (45 of them `tags`, so those notes contributed nothing to `TAGS.md` and were unfindable by tag), 35 `DUPLICATE_FM_KEY`, 3 `ORPHAN_FM_BRACKET`. The checks mirror the parser's own state machine (block scalars, block sequences) so supported shapes stay silent.
- **`--retry-dead-letters`** — re-queues retryable dead-lettered sessions into the pending queue, removing them from `dead_letters.jsonl` first so `_dead_lettered_ids` no longer feeds them straight back to `_early_gate`. Pairs with `--reason` / `--min-age-days` / `--max-count`. Most `no_result` dead-letters are transient and succeed on a later attempt; the 3-strike graveyard was losing them permanently.

### Changed
- **`no_result` dead-letters are disambiguated** — the opaque kind splits into `no_result_timeout` / `no_result_empty` / `no_result_backend`, so the retry filter can target a specific cause.
- **Frontmatter-syntax codes are detection-only** — none are in `REPAIRABLE_CODES`; the AI repair path is deliberately not turned loose on structurally broken frontmatter.

### Fixed
- **`inject_related_links` no longer appends a duplicate `related:` key** — `_RELATED_FIELD_RE` required the line to end right after an optional `[...]`, so two shapes present in the vault never matched and fell through to the append branch: the daily-note template placeholder (which carried a trailing `#` comment) and a bare scalar value. 35 notes accumulated duplicate top-level keys; `parse_frontmatter` is last-wins, so the data stayed correct and nothing surfaced it. Replaced with a line-scan (`_related_field_spans` / `_replace_related_field`) recognising all seven shapes — clean inline, trailing comment, scalar, block sequence, bare key, empty inline, and an inline list wrapped across lines — that stops at the next top-level key so a malformed field cannot swallow the fields below it, rewrites the first field in place, and drops any others so an already-duplicated note is healed rather than grown. The generating trailing comment is gone from `templates/daily.md`.
- **Broken-wikilink repair no longer substitutes a daily note** — `_find_link_replacement`'s semantic `vault-search` fallback ranked journal pages highest for a link that is really a project name (`[[par-rt-db]]`, `[[fix-audit-remediation]]`), and its only success test was "does it resolve". One `--fix-all` run rewrote all four of its repairs to `[[10-probello]]`, `[[30-probello]]`, `[[29-probello]]`, `[[08-probello]]`, after which the re-scan reported the notes clean so nothing surfaced the downgrade. `Daily/` notes are now skipped in the semantic fallback and in `_find_semantic_candidates`; an explicit exact link to a daily note still resolves. The repair prefers dropping an unreplaceable link over substituting a loosely-related one, and `BROKEN_WIKILINK` now qualifies for a candidate list (a broken-link-only note previously got an empty list while still being told every link must resolve, so the model invented a target it could guess from the note's own `date:`).
- **Doctor repairs commit themselves** — the repair phase now commits under a message naming the repair. The reindex that follows stages only `CLAUDE.md`/`TAGS.md`/`MANIFEST.md`, so repaired notes previously sat uncommitted until an unrelated later hook swept them into a `chore(vault): session notes` commit that never mentioned them.
- **Detection-only defects keep re-reporting** — `should_skip` treats state status `skipped` as permanent (unlike `ok`, which expires after `STATE_STALE_DAYS`), so a note whose only issues were the new codes would be announced on one run and hidden forever after. Such notes are now left out of `doctor_state.json` entirely.
- **Misleading `--run-doctor` help string** — it claimed the doctor ran "before summarizing" in terms that overstated the coupling.

## [0.16.1] - 2026-08-08

Session-start semantic search is decoupled from the local `embeddings.db`: under `search.backend: par-mem` the retrieval path no longer silently no-op'd when the local fastembed DB was absent or deleted — the gate is now the configured backend, so par-mem serves retrieval without it.

### Fixed
- **Session-start semantic search no longer depends on the local `embeddings.db`** — `_run_semantic_search` and its caller `_select_seed_notes` gated `vault_search` on the local fastembed DB existing, so under `search.backend: par-mem` the par-mem retrieval path still silently no-op'd when the DB was absent or deleted. The gate is now the configured backend: `embeddings.db` is required only for the local-embeddings path (an explicit `embeddings` backend, or `auto` when par-mem is unavailable). par-mem serves retrieval without it, so removing the now-unused `embeddings.db` is safe.

## [0.16.0] - 2026-08-02

Vault memory-capture and persistence hardening: the summarizer no longer silently drops valuable sessions to a single stochastic write-gate skip, and vault git auto-commit self-heals from stale locks (the root causes of a 4-day commit stall, fixed). Plus the visualizer `Home` container decomposition finishes and a `GraphCanvas` interactions hook lands.

### Added
- **Stale `.git/index.lock` self-heal** — `git_commit_vault` detects and clears a lock left by a killed git process before staging, only when it is older than 300 s and no live process holds it (best-effort `lsof` cross-check). A stale lock had silently blocked every vault commit for days.
- **`GraphCanvas` interactions hook** (`QA-008`) — right-click context-menu actions, path-finding, and the toast banner extracted into `useGraphCanvasInteractions`; `GraphCanvas.tsx` 706 → 618 LOC.
- **Visualizer SSE route integration tests** (`ENH-012`) — end-to-end coverage for the `vault/events` Server-Sent-Events stream (open connection → write a note → assert a `file:created` frame arrives; abort → assert no further frame, proving the chokidar watcher is released) and ETag/304 + content-change cases for the `graph` route, via a shared `readNextSSEData` helper. Routes unchanged; 258 visualizer tests pass.

### Changed
- **Visualizer `Home` container triad complete** (`ARC-008`) — `ReadingPanePanel` extracted as the third sibling container (after `GraphPanel`/`SidebarPanel`), prop-drilled to match them; `Home` 382 → 373 LOC. The audit's suggested context/provider lift was judged unnecessary (`noteRefreshTrigger` threads cleanly as a single integer prop).

### Fixed
- **Summarizer no longer loses sessions to a single write-gate skip** — the write-gate decision is stochastic on borderline sessions (a session dead-lettered "skip" produced a high-quality note when re-evaluated with identical input), but a skip was made permanently sticky on the first decision, silently dropping valuable sessions. Skips now get a retry budget: re-queued (bumping a `skips` counter) up to `_MAX_SKIPS` (2) before sticky dead-lettering, mirroring the failure retry path.
- **Vault git auto-commit stall** — `git_commit_vault` exited 1 on its `:(exclude)config.yaml` pathspec because `config.yaml` is gitignored (the installer default), so it silently bailed before committing. The exclude is now emitted only when `config.yaml` is not already gitignored (checked via `git check-ignore`); secrets stay protected either way.
- **Misleading session-start warning** — "dead-lettered after repeated failures" overstated it (~78% of dead-letters are write-gate skips, many at 0 attempts); reworded to "(write-gate skips or failed summarization)".

## [0.15.0] - 2026-07-31

The ENH-001…008 enhancement backlog ships — a ~3× smaller `graph.json`, incremental graph rebuilds, an opt-in persistent embedding service, a DB-first metadata read path, shared Python↔TypeScript parity fixtures, a documented agent-adapter registry, a composite vault-health score, and externalized versioned prompts with a full eval harness. Plus a determinism fix for par-mem body-link enrichment.

### Added
- **Graph: cap semantic edges per node (top-K nearest neighbours)** (`ENH-001`) — `build_graph.py` keeps each note's strongest 15 neighbours (configurable via `--max-neighbors`; `0` restores all-pairs) instead of emitting every pair above the floor, cutting the live vault's `graph.json` from 47.5 MB / 376k edges to ~15.6 MB / 110k edges (mean degree 67.6 → 15.8) while preserving every `[[wikilink]]` edge.
- **Incremental graph generation** (`ENH-002`) — `--incremental` recomputes only notes whose mtime changed since `meta.generated` (a `schema_version` forces a full rebuild on format change), making freshness cheap enough to default on.
- **Persistent embedding service** (`ENH-003`) — an opt-in AF_UNIX daemon (`embeddings.service_enabled`) lets short-lived `vault_search` callers share one warm ~67 MB ONNX model instead of each cold-loading it; an in-process embedding cache is shared across the summarizer's dedup/backlink calls.
- **`note_index` as the metadata read path** (`ENH-004`) — `find_notes_by_project/tag/type/recent` are now DB-first (walk fallback retained for mutation paths), removing the class of walk-vs-DB disagreement.
- **Shared Python↔TypeScript parity fixtures** (`ENH-005`) — vault-resolution vectors and the `graph.json` JSON Schema are emitted from the Python side and consumed by both test suites, so cross-language drift becomes a CI failure (`make parity-fixtures-check`).
- **`AgentAdapter` registry** (`ENH-006`) — a documented, data-only extension point (claude/codex/gemini/pi + opt-in external drop-ins) drives `connect`/`disconnect` and the installer's merge/remove; the five codex/gemini hook shims collapse onto one parameterized module.
- **`vault-stats --health` composite score** (`ENH-007`) — one 0–100 vault-health grade with per-dimension grades and next actions; the default output of bare `vault-stats`.
- **Externalized versioned prompts + eval harness** (`ENH-008`) — the six prompts live as templates under `templates/prompts/` with a strict variable contract (`prompt_templates.render`), and `tools/eval/prompt_eval_run.py` scores all six against golden cases via a per-prompt evaluator dispatch (render/parse/score; no AI billed by the test suite).

### Changed
- **Prompt-eval harness is per-prompt** — the driver dispatches over `tools/eval/evaluators/` (one module per prompt); each prompt has its own output shape and rubric rather than the former note-specific monolith.
- **Documentation synced** — ARCHITECTURE, MCP, PROMPTS, VISUALIZER, EMBEDDINGS, EMBEDDINGS_EVAL, AGENT-ADAPTERS, MCPL, README, VAULT_SYNC reconciled to the current implementation.

### Fixed
- **par-mem body-link enrichment is now deterministic** — `build_graph.py` gates the enrichment on a fresh index (`parmem_backend.vault_index_fresh`) and records the outcome in `meta.parmem_body_status`, so a stale / mid-catch-up index no longer makes two builds over identical input diverge (0 then 234 body-links seconds apart).
- **`note_index` str-tolerance** — consistent type coercion across the read-path surface.

## [0.14.0] - 2026-07-30

### Security
- **Next.js 16.2.10 → 16.2.11** (`8e5d549`) — visualizer dependency bump tracking the upstream security release.
- **Audit remediation (SEC-101…132)** — closed a remote-code-execution path in the vault git `post-merge` hook (`--no-project` now on every `uv run`; stale `parsidion-cc` hooks now regenerate instead of being skipped); made the visualizer no longer expose unauthenticated vault read/write to the LAN (token enforced on every route, server bound to loopback); reverted the shipped config template's default AI endpoint to Anthropic (was a third-party gateway); and hardened `~/.claude/settings.json` handling (bail on parse error instead of reset-to-`{}`; atomic write + `.bak`), vault `.gitignore` (globs so `.bak`/`conflicts/` are covered), filesystem permissions (0600 on the queue/logs/configs, 0700 on the vault root + logs dir), injected-note untrusted-content framing, subprocess argv/`codex_cli` injection guards, symlink-escape prevention in vault walks, and ~15 lower-severity items. The shipped config template no longer routes nightly summarization to a third-party endpoint.

### Fixed
- **`make checkall` no longer rewrites source files** (`ARC-006`) — the `parsidion-mcp` gate now uses non-mutating `fmt-check`/`lint`, so the project's own verification command can be run read-only (unblocking every fix that follows).
- **Non-editable installs import cleanly** (`ARC-001`) — the 7 modules omitted from `[tool.setuptools] py-modules` are now declared; a clean-room `pip install` of the wheel imports `vault_common`/`vault_search`/`vault_links`/`ai_backend` successfully (CI smoke test added).
- **Visualizer note writes no longer target the wrong vault** (`ARC-002`) — POST/PUT now read the vault from the request body as well as the query string, eliminating a silent cross-vault overwrite path.
- **`disconnect codex|gemini` no longer tears down shared infrastructure** (`ARC-003`) — the nightly summarizer schedule, the vault `post-merge` hook, and `vaults.yaml` are now preserved unless the full Claude uninstall runs (and `vaults.yaml` needs an explicit `--purge-config`).
- **Summarizer correctness** (`ARC-010/012/013/027/030`) — the `knowledge` note type is now writable; one raising session no longer cancels its siblings via the task group; dead-letter pruning is lock-safe; non-retryable failures dead-letter on attempt 1 instead of burning 3 AI calls; `--no-project`/`--vault` are forwarded to spawned subprocesses so the index stops going stale / backlinks stop hitting the wrong vault.
- **Custom `--vault` installs are now persisted** (`ARC-019`) — written to `~/.config/parsidion/vaults.yaml` so installed hooks resolve the chosen vault instead of silently falling back to the default.
- **Visualizer performance/robustness** (`ARC-015/036/039/040/041`) — the 47.5 MB `graph.json` is streamed with an mtime ETag (304 on match) plus a delta endpoint; git subprocesses are bounded (timeout/abort/stderr cap); SSE has a cancel handler + keepalive; conflicts return a consistent HTTP 409; server-only modules carry `import 'server-only'`.
- **`vault_doctor --fix-all`** adds a permission-repair pass (0600/0700) and writes generated index files atomically; `vault-merge` writes atomically and inlines note bodies instead of handing a child agent filesystem access.
- **Lifecycle hooks** bound transcript reads by bytes (not just lines) and re-add the transcript-path allowlist.
- Numerous documentation corrections across `CLAUDE.md`, `README.md`, `docs/`, `SECURITY.md`, and `CONTRIBUTING.md` (`DOC-001…040`) — phantom flags, dead symbol references, reversed behavior descriptions, and stale signatures fixed against the current code.

### Added
- **Tests** — Python suite grew 840 → 1010 and the visualizer suite 60 → 226: first-ever coverage for the `SubagentStop`/`PostCompact` lifecycle hooks, every visualizer API route, the vault path-traversal guards (`vaultResolver`), the `vault-review` destructive paths, and a per-route auth-enforcement test that prevents a new route from forgetting its guards.
- **Shared modules** — `agent_adapter` registry (the 5 codex/gemini agent-extension hooks collapse to thin shims and now emit hook events), `subproc_util.run_with_pgkill` (one process-group-kill implementation shared by the Claude and Codex backends), async `findNote` (de-triplicated across note routes), a bounded `runScript` helper, and an extracted Brandes-betweenness utility.

### Changed
- **par-mem flagged as coming soon across docs** (`66c06f5`) — the README, hook reference, and visualizer docs now state plainly that par-mem itself is not yet publicly available. The integration is ready in parsidion and activates once par-mem ships; parsidion works fully without it.
- **Config schema reconciled** (`ARC-011`) — the six keys the code genuinely reads are now declared in `_CONFIG_SCHEMA` (eliminating six spurious validation warnings at every session start); the `ai`/`ai_models`/`codex_cli` backend-selection sections are documented in the shipped template.

## [0.13.0] - 2026-07-24

### Added
- **Optional par-mem code-memory search backend** — vault semantic search can now be served by [par-mem](docs/PAR-MEM.md), a local Rust code-memory daemon, instead of the local embeddings-only cosine search. _(par-mem itself is not yet publicly available — coming soon; the integration is ready in parsidion and activates once par-mem ships, with silent embeddings fallback until then.)_ `search.backend` selects `auto` (par-mem when available and indexed, silent fallback to embeddings — the default), `par-mem` (par-mem only, no fallback), `embeddings` (today's path unconditionally), or `none`; a new `par_mem:` config section (`enabled`/`binary`/`timeout_s`) and a `vault-search --backend/-B` flag control it per query. par-mem absent means parsidion behaves byte-for-byte as before.
- **Hybrid BM25+vector+graph vault search** — when routed to par-mem, queries hit its always-on daemon (MCP over HTTP) instead of the local `embeddings.db` cosine index, and results are enriched from `note_index` and re-scored with parsidion's existing temporal decay.
- **par-mem freshness triggers** — `update_index.py`/`rebuild_index()` kick a detached background `par-mem index` on every rebuild, and the SessionStart/SessionEnd hooks hold/release a live `par-mem watch` on the vault, so the par-mem-side index tracks vault edits without manual intervention.
- **vault-explorer code-memory bridge** — the agent now also consults par-mem's code graph (`par-mem find-code`/`find-symbol`) for code-shaped questions, merging hits into its `## Answer`/`## Sources` response alongside vault notes.
- **parsidion-mcp `code_search` tool** — exposes the par-mem code-memory bridge to Claude Desktop and other MCP clients, with backend-aware pre-checks that raise a clear error instead of degrading silently (MCP callers can choose another tool).
- **docs/PAR-MEM.md** — the full integration guide: configuration, requirements, score semantics, degradation matrix, index freshness, and troubleshooting.
- **graph.json body-link enrichment** — `build_graph.py` merges par-mem's in-body doc links (`par-mem doc-links`) into wiki edges when the integration is enabled; `--no-parmem` opts out.
- Visualizer: semantic vault search behind the `?` search prefix (`GET /api/search` → `vault_search.py`, par-mem backend with embeddings fallback), Linked Notes section in the reading pane (wiki-edge neighbors incl. par-mem body links), `body links` HUD stat from `meta.parmem_body_links`, and a `make visualizer-check` gate (tsc + eslint + bun test) wired into `make checkall`.
- **Configurable dead-letter retention** — `dead_letters.jsonl` no longer grows without bound: write-gate-skipped sessions are now sticky (a stop-hook re-queue is caught by the `_DEAD` guard), so every transient session was retained forever. A new `summarizer.dead_letter_retention_days` config (default `7`; `<=0` disables) prunes entries older than N days once per summarizer run.

### Fixed
- **Summarizer chunk explosion on huge-line transcripts** — large codex subagent transcripts with few-but-huge lines (e.g. a 9.2 MB rollout across 339 lines) bypassed `transcript_tail_lines` and produced hundreds of hierarchical chunks, timing out the AI backend ("no result") and dead-lettering the session after 3 attempts. A new `transcript_tail_bytes` config (default `262144`) bounds the raw tail by bytes (`read_last_n_lines` drops oldest lines until the budget is met, always keeping the most recent line), collapsing ~325 chunks to ~10 on the worst case.
- **vault-doctor flagged fenced/inline code as broken wikilinks** — the `BROKEN_WIKILINK` scanner ran over the entire document including code blocks, so legitimate config syntax (TOML array-of-tables like `[[bin]]`, `[[keys.command]]`) was reported as broken links — 11 false-positive warnings and 3 spurious repair "failures" every run. It now scans only text outside protected regions, sharing the `vault_links._iter_unprotected_spans` tracker so the scanner and the migration rewriter never disagree about what counts as a link.
- **Dangling wikilinks, in-flight transcripts, and sticky dead-letters** — three recurring queue/link-hygiene fixes: (1) a post-write validator strips `[[link]]` wikilinks the summarizer backend invents that resolve to no vault note (the recurring `[[<project>]]` "hub" link that mirrors the `project` field but points at nothing), dropping them from `related:` and reducing body links to display text outside code; (2) a transcript whose mtime is within a 120 s grace window is treated as still being written and deferred to a later run, stopping racy partial notes from resumed/long-lived sessions; (3) write-gate-skipped sessions are now recorded in `dead_letters.jsonl` so a stop-hook re-queue is caught by a new `_DEAD` guard instead of re-billing an AI call to re-evaluate a session already judged transient.
- **Vault auto-commit clobbered unrelated staged changes** — committing vault writes staged and committed changes that were already staged for an unrelated reason; it now preserves unrelated staged work.

### Changed
- **Documentation synced to the implementation** — `CLAUDE.md`, `README.md`, and the `docs/` set were verified against the codebase: corrected vault path defaults (`~/ParsidionVault/` with legacy `~/ClaudeVault/` fallback), the `parsidion-mcp` tool count (six → seven, +`code_search`), `config.local.yaml` precedence, the `vault_common` ARC-005 module split, the Makefile target list, and the reversed `SCRIPTS_DIR`/`TEMPLATES_DIR` mapping; documented the visualizer Path Finder (BFS wiki-link path tracing).

## [0.12.2] - 2026-07-12

### Fixed
- **Summarizer dead-lettered notes the model emitted with empty or absent `tags`** — a recurring failure mode on long, dense transcripts (notably read-only audit/review subagents) was valid frontmatter with `tags: []` or no `tags` line at all. `inject_project_tag` only repaired the inline `tags: []` case when a usable project was known, so these notes were refused at validation, re-queued, and dead-lettered after 3 attempts even though their content was fine. `write_note` now runs a `_backfill_tags_if_empty` salvage step (sibling to the existing preamble / closing-delimiter / related-field salvages) that derives a non-empty tag list from the note `type`, the session `project`, and `categories` before validation — normalizing to vault form (lowercase, underscores → hyphens, leading dots stripped). Existing non-empty tags are never clobbered. Tests: `test_backfill_*` in `test_summarizer_queue_fixes.py`.

## [0.12.1] - 2026-07-03

### Fixed
- **Summarizer misclassified fenced write-gate decisions as failures** — when the prompt-AI backend wrapped its `{"decision": "skip"|"merge"}` JSON in a ```` ```json ```` markdown fence, the write-gate's `startswith("{")` check missed it; the decision fell through to `write_note`, failed frontmatter validation, and the session was reported as failed (or "No result from AI backend") instead of a deliberate skip. The summarizer now strips one surrounding code fence before parsing the decision (regression test: `test_summarizer_one_preserves_skip_write_gate_when_fenced`).
- **`claude -p` failures were silently swallowed** — `_run_claude_prompt` returned `None` on either a non-zero exit *or* empty stdout and discarded stderr entirely, so empty-result summarizer failures were impossible to diagnose. It now logs `rc`/`stdout_len`/`stderr` to stderr on any empty or failed call.

### Changed
- **`claude -p` now runs with `--output-format json`** — the assistant's final answer is read from the envelope's `result` field (populated even when the response includes thinking, which is not emitted to `-p` stdout), with a raw-stdout fallback for older/non-JSON output. The envelope also yields `subtype`/`session_id` for diagnostics.

## [0.12.0] - 2026-07-02

### Added
- **Visualizer force-layout performance** — the per-frame simulation now snapshots visible nodes' positions/velocities into flat typed arrays once per frame and runs gravity, repulsion, and edge-attraction over those arrays instead of doing per-pair graphology attribute lookups (a large constant-factor win with identical physics). Above `BARNES_HUT_THRESHOLD` (1000 visible nodes) exact O(n²) repulsion is replaced by grid-based (linked-cell half-shell) approximate repulsion; below it the exact path is retained. The `hideIsolated` visibility rule is now a single shared `isEffectivelyIsolated()` predicate used by both the physics loop and the node reducer.
- **Visualizer SSE live-reload** — the custom `ws` Node server (`server.ts`) is retired in favor of native `next dev`/`next start` plus a Server-Sent Events route (`app/api/vault/events`). The route preserves SEC-009 vault validation, the `Sec-Fetch-Site` cross-origin guard, reference-counted per-vault `chokidar` watchers, and identical `file:created/deleted/modified` + `graph:rebuilt` payloads; `useVaultFiles` moves to `EventSource` with an unchanged public contract. `ws`/`tsx`/`@types/ws` and `tsconfig.server.json` are removed.
- **Visualizer modal focus traps** — a shared `useFocusTrap` hook (Tab/Shift+Tab cycling, focus restoration, optional initial-focus target) now traps keyboard focus in `ConfirmDialog` (defaulting to the safe Cancel action), `NewNoteDialog`, and `ConflictDialog`. `ConflictDialog` also gains `role="dialog"`/`aria-modal`/`aria-label` and Escape-to-cancel.
- **Dead-letter queue for failed summarizations** — pending sessions that fail 3 consecutive summarizer runs are purged to `<vault>/dead_letters.jsonl` (full entry + `attempts` + `last_failure` + timestamp) instead of retrying — and re-billing an AI call — forever. `vault-stats --pending` shows a Dead Letters section (count + 3 most recent with project/reason), and the session-start hook warns when dead letters exist. The file is gitignored by the installer.
- **Pre-mutation backups in `vault_doctor`** — before every execute-mode note mutation or rename (including the nightly `--fix-all` cron), the original file is copied to `<vault>/.trash/backup/<YYYY-MM-DD>/<relative-path>` (first version of the day wins; prune freely). All 9 note-write sites also converted to a new shared `vault_fs.atomic_write_text()` (tmp + rename, preserves permission bits), so a killed run can no longer truncate a note.
- **Fence-aware wikilink rewriting** — new `vault_links.sub_wikilinks_outside_code()` / `replace_wikilinks_outside_code()` skip fenced code blocks (```` ``` ````/`~~~`, unclosed fence protects the remainder) and inline code spans while still processing frontmatter. Adopted at every naive `[[stem]]` replacement site in `vault_doctor` (prefix clusters, strip-prefixes, daily-note migration, broken-wikilink repair) and `vault_merge`, so notes documenting wikilink syntax survive renames/merges untouched.
- **`vault-merge --from-preview`** — a dry-run merge now caches the AI-merged body under `<vault>/.merge_previews/` keyed by sha256 of both source notes; `--execute --from-preview` applies exactly the previewed merge without a second AI call (clear fallback to a fresh call if either note changed), deleting the preview on success. The whole execute mutation sequence is also guarded by a non-blocking flock so concurrent merges fail fast instead of interleaving.
- **`config.local.yaml` overlay** — an optional, always-gitignored local config deep-merged over `config.yaml` at load time (precedence: defaults → config.yaml → config.local.yaml → CLI args), so secrets or machine-specific overrides can stay local while a secret-free `config.yaml` is git-synced.
- **Frontmatter parse warnings surfaced as hook events** — `update_index.py` now emits an `IndexRebuild` event via `write_hook_event` including `parse_warnings` count + up to 5 samples (nested-mapping keys, non-string tags), making previously stderr-only warnings visible via `vault-stats --hooks N`.
- **Non-ASCII-safe `slugify()`** — accented titles transliterate via NFKD (`Café Notes` → `cafe-notes`); titles that are entirely non-ASCII (e.g. CJK) fall back to a stable `note-<sha1[:8]>` slug so distinct titles never collide or produce empty filenames. ASCII behavior unchanged (`vault-new` still rejects unrepresentable ASCII titles).

### Fixed
- **Visualizer cross-vault data safety** — note read/save/create/delete and the history/diff views never sent the selected `?vault=`, so editing while a non-default vault was selected silently read, overwrote, or deleted files in the *default* vault. All note API calls are now vault-scoped, and save/delete resolve by explicit vault-relative `path` (server-side too) instead of a first-match stem, so same-stem notes in different folders (e.g. multiple `MANIFEST.md`) no longer clobber each other.
- **Visualizer editing correctness** — a background graph reload (summarizer finishing, a `graph:rebuilt` event) no longer drops the reading pane out of edit mode and discards unsaved changes; body wikilinks render and navigate again (react-markdown v10 was stripping the `wikilink:` protocol); saving a note no longer deletes unrecognized frontmatter keys such as `provenance`/`session_id`; and the content cache no longer resurrects a deleted note's text over a freshly recreated file. Conflict detection is now based on the server file's `mtime` (echoed by the client) rather than a client wall-clock timestamp, so it is correct under clock skew (LAN/tunnel access) and across consecutive saves.
- **Visualizer graph interactions** — layout auto-stop now notifies the HUD (the pause/run button no longer desyncs on a settled sim); the type and "show daily" filter chips take effect immediately instead of only on the next graph rebuild; gradient edge coloring uses the live similarity threshold; dragging a settled node reheats the simulation so neighbors react; and residual canvas shadow/path-highlight state no longer leaks across renders.
- **Visualizer local-network exposure** — the WebSocket/SSE live-reload endpoint now rejects cross-origin connections, all read-only API routes enforce a `Sec-Fetch-Site` same-origin guard, and `resolveVault` is an allowlist (previously a denylist that accepted `/` and `$HOME`), so a malicious page the user visits can no longer drive localhost filesystem walks or surveil vault activity. `guardPath` now resolves symlinks (`realpath`) before the containment check, and `chokidar` watchers are reference-counted and closed on disconnect instead of leaking.
- **Summarizer queue integrity** — `remove_processed()` rewrote `pending_summaries.jsonl` via in-place truncate (a kill mid-rewrite lost queued sessions, including ones appended concurrently by other Claude instances); now atomic tmp + rename under the exclusive flock. An AI "merge" decision with a missing/unresolvable target fell through to the generic write path with raw decision JSON (misleading frontmatter error, entry wedged forever); now fails explicitly with the real reason. `claim_summarizer_lock()` was an unlocked read-check-write (two near-simultaneous SessionEnd hooks could run duplicate summarizers); now flock-guarded. Progress counters mislabeled stale/skipped entries as "written" and never incremented errors; `vault-stats --summarizer-progress` now reports written/skipped/errors accurately.
- **`build_embeddings.py` full rebuild could wipe the semantic index** — `DELETE FROM note_embeddings` committed in its own transaction *before* loading the embedding model (a fallible network fetch); any failure left search empty until a manual rebuild. Delete + inserts now commit as one transaction after the model loads.
- **`migrate_pending_paths()` raced live hooks** — it read the pending queue unlocked and flocked the brand-new tmp file instead of the real one, so the nightly doctor run could silently drop a session queued by a concurrent session-stop hook. Now locks the real file for the whole read-transform-replace (plus an inode re-check in `append_to_pending()`).
- **Nightly `--fix-all` subfolder migration bypassed the AI cluster filter** — `run_migrate_subfolders` grouped notes by first hyphen-word with no `_filter_clusters_with_claude` call, so cron could move `fixing-a/b/c.md` into a junk `Debugging/fixing/` folder and auto-commit it. The filter now applies in both dry-run and execute; if the AI backend is unavailable, unvetted clusters are skipped. Also: `--fix-all` now reindexes after AI frontmatter repairs (it never did — the index stayed stale nightly), underscore→hyphen tag normalization no longer rewrites later block-sequence fields (e.g. `sources:` URLs), and `run_strip_prefixes` survives mid-batch rename failures without leaving vault-wide broken wikilinks.
- **`vault-merge` could destroy a note on AI failure** — any backend output ≥50 chars (including refusal/error prose) was accepted and written over note A while note B was trashed. Output is now validated against the merge contract and an invalid merge aborts (exit 1) before any write or trash; the keeper write is atomic (tmp + rename) and note B is only trashed after it succeeds. Dangling `[[loser]]` links inside the keeper's own body are now unwrapped to plain text.
- **Silent tag loss via YAML scalar coercion** — `tags: [2026, python]` parsed `2026` as `int`, which the indexer silently dropped (unfindable via `vault-search --tag 2026`, and `note_index` disagreed with `note_embeddings`). Frontmatter list items (`tags`/`sources`/`related`) now stay strings; non-string legacy tags are coerced with a warning. Config nesting deeper than 2 levels now warns and skips the key instead of silently mis-attaching it.
- **Unlocked read-modify-write races** — `vault_adaptive.py`'s shared cross-project JSON state (`vault_last_seen.json`, `note_usefulness.json`) and the hook-event log rotation lost updates under parallel sessions; both are now flock-guarded with atomic writes. `graph.json` is written via tmp + rename so the live-reading visualizer can't observe truncated JSON. `update_index.py`'s singleton PID guard is now an atomic `O_CREAT|O_EXCL` claim with stale-PID recovery instead of a check-then-write race.
- **`inject_related_links()` matched the whole file** — a body line starting with `related:` (e.g. a note quoting the frontmatter schema) could be clobbered instead of the frontmatter field; the regex is now scoped to the frontmatter block (fixing a latent newline-swallowing bug in the process) and the write is atomic.
- **Curses TUI crashes** — `vault-review`: rejecting the last entry from the transcript popup crashed with IndexError on the next keypress (surfaced as a misleading "terminal does not support curses"); popup dimensions went negative on tiny terminals; approving the final entry never auto-closed the popup. `vault-search --interactive`: hard-coded row offsets crashed on ≤3-row terminals, and returning from `$EDITOR` leaked arrow-key escape bytes into the query buffer. All fixed.
- **`vault-stats --timeline` mixed clocks** — buckets used rolling 24-hour windows while labels used calendar dates, so the histogram shifted depending on time of day; now buckets by local calendar day.
- **SQLite connections leaked on error paths** in `update_index.py` and `vault_index.py`; `vault-new` silently wrote a hidden `.md` file for titles with no slug-safe characters (now a clear error); `session_stop_wrapper.sh` silently dropped the session when `mktemp` failed (now logs and degrades cleanly).

### Security
- **`config.yaml` can no longer leak API keys into vault git history** — the file is documented as a home for `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` (`anthropic_env`), but was never gitignored and `git_commit_vault()`'s `git add -A` swept it into auto-commits (and, via multi-machine sync, onto remotes). The installer now gitignores `config.yaml`/`config.local.yaml`/`dead_letters.jsonl`, auto-commits exclude the vault-root `config.yaml` via git pathspec, and the template documents how to untrack a pre-existing copy. Also replaced a fragile `str.startswith()` path-containment check in `summarize_sessions.py` with `Path.is_relative_to()`.


---

## Archive

Releases **0.1.0 through 0.11.x** have been moved to a separate archive file to keep
this changelog scannable:

➜ **[docs/archive/CHANGELOG-0.11-and-older.md](docs/archive/CHANGELOG-0.11-and-older.md)**

That archive covers `parsidion-cc` (the pre-0.7.0 project name), the 0.6.0 rebrand to
`parsidion`, and every patch through 0.11.1.

[Unreleased]: https://github.com/paulrobello/parsidion/compare/v0.19.0...HEAD
[0.20.0]: https://github.com/paulrobello/parsidion/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/paulrobello/parsidion/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/paulrobello/parsidion/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/paulrobello/parsidion/compare/v0.16.0...v0.17.0
[0.16.0]: https://github.com/paulrobello/parsidion/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/paulrobello/parsidion/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/paulrobello/parsidion/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/paulrobello/parsidion/compare/v0.12.2...v0.13.0
[0.12.2]: https://github.com/paulrobello/parsidion/compare/v0.12.1...v0.12.2
[0.12.1]: https://github.com/paulrobello/parsidion/compare/v0.12.0...v0.12.1
[0.12.0]: https://github.com/paulrobello/parsidion/compare/v0.11.1...v0.12.0
