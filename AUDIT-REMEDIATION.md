# Audit Remediation Report

> **Project**: Parsidion
> **Audit Date**: 2026-06-12
> **Remediation Date**: 2026-06-12
> **Severity Filter Applied**: all
> **Branch**: `fix/audit-remediation`

---

## Execution Summary

| Phase | Status | Agent | Issues Targeted | Resolved | Partial | Manual |
|-------|--------|-------|----------------|----------|---------|--------|
| 1 — Critical Security | ✅ | fix-security | 1 (SEC-001, +SEC-009 co-fix) | 2 | 0 | 0 |
| 2 — Critical Architecture | ✅ | fix-architecture | 2 (ARC-001, ARC-005) | 2 | 0 | 0 |
| 3a — Remaining Security | ✅ | fix-security | 13 (SEC-002..014) | 13 | 0 | 0 |
| 3b — Remaining Architecture | ✅ | fix-architecture | 14 (ARC-002..016) | 6 (+2 already done) | 0 | 6 deferred |
| 3c — All Code Quality | ✅ | fix-code-quality | 15 (QA-001..015) | 14 | 1 (QA-004) | 1 follow-up |
| 3d — All Documentation | ✅ | fix-documentation | 17 (DOC-001..017) | 17 | 0 | 0 |
| 4 — Verification | ✅ Pass | orchestrator | — | — | — | — |

**Overall**: 54 of 62 issues resolved, 1 partial, 7 deferred to manual follow-up.

---

## Resolved Issues ✅

### Security (15 of 15 — including the 2 critical-path items)
- **[SEC-001]** TS `resolveVault()` forbidden-path validation — `visualizer/lib/vaultResolver.ts` — added `VAULT_FORBIDDEN_PREFIXES` + exported `validateVaultPath()` / `VaultConfigError` mirroring Python's `_VAULT_FORBIDDEN_PREFIXES`; applied on every resolution branch.
- **[SEC-002]** `.md`-only enforcement on `PUT /api/note` — `visualizer/app/api/note/route.ts` — 400 on non-`.md` paths.
- **[SEC-003]** Subprocess stderr no longer returned to clients — rebuild/history/diff routes log server-side, return generic errors.
- **[SEC-004]** Auth/CSRF hardening — new `visualizer/lib/apiAuth.ts`: mutation endpoints require `Content-Type: application/json`; optional `VISUALIZER_TOKEN` bearer-token check on all routes (off when unset — local UX unchanged).
- **[SEC-005]** Vault path validated (+ exists/isDirectory check) before `build_graph.py` spawn.
- **[SEC-006]** Security headers (`X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`) — `visualizer/next.config.ts`.
- **[SEC-007]** `<content>` prompt-injection isolation for note stems sent to AI — `vault_doctor.py`.
- **[SEC-008]** `hook_events.log` / `pending_summaries.jsonl` created with `0o600` — `vault_hooks.py`, `vault_fs.py`.
- **[SEC-009]** WebSocket vault validation — `visualizer/server.ts` rejects upgrade with HTTP 400 on `VaultConfigError` (done in Phase 1 with SEC-001).
- **[SEC-010]** `.jsonl` suffix required in shared `is_allowed_transcript_path()` — covers both stop hooks.
- **[SEC-011]** `chmod 600 visualizer/.env.local`.
- **[SEC-012]** Shared `guardPath()` exported from `vaultResolver.ts` (per-route copies retained as identical; new code imports the shared one).
- **[SEC-013]** Real Windows file locking via `msvcrt.locking()` with ImportError fallback — `vault_fs.py` (stdlib-only preserved).
- **[SEC-014]** 64 KB cap on subprocess stderr accumulation — rebuild route.
- **[carry-over]** All API routes now return 400 (`Invalid vault path`) instead of 500 on `VaultConfigError`.

### Architecture (8 of 14; 6 deferred — see Manual section)
- **[ARC-001]** `load_config.cache_clear()` + `resolve_vault.cache_clear()` at every `VAULT_ROOT` set/restore site in all six multi-vault scripts (`vault_doctor`, `update_index`, `vault_export`, `vault_merge`, `vault_review`, `build_embeddings`).
- **[ARC-005]** Import-time `PID_FILE` / `_PENDING_PATH` constants converted to call-time `pid_file()` / `_pending_path()` functions — `update_index.py`, `vault_review.py`.
- **[ARC-004]** New `tests/test_vault_resolver_parity.py` (6 tests) asserting Python↔TypeScript forbidden-prefix parity — drift now fails CI.
- **[ARC-006]** Deprecation-risk comment + documented override paths for hardcoded model defaults — `ai_backend.py` (config template already covers them).
- **[ARC-008]** Already protected: `tests/test_vault_dirs_sync.py` asserts regex-parsed `VAULT_DIRS` matches `vault_common.VAULT_DIRS` (verified passing).
- **[ARC-010]** `parsidion-mcp/Makefile` existed; root `Makefile` gained `checkall-mcp` wired into `checkall` — `make checkall` now gates the MCP package too.
- **[ARC-011]** `get_default_queries_file(vault=None)` call-time resolution — `embed_eval_common.py`, `embed_eval.py` (backward-compat alias kept).
- **[ARC-013]** Codex tier no-op documented — `ai_backend.py`.
- **[ARC-014]** Already implemented: `--vault`/`-V` flag exists in `summarize_sessions.py` (audit finding was stale).
- **[ARC-016]** Already implemented: `.github/workflows/ci.yml` has an `mcp-checks` job (audit finding was stale).

### Code Quality (14 of 15 resolved, 1 partial)
- **[QA-001]** `vars(anyio)["to_thread"]` → public `from anyio import to_thread` — `summarize_sessions.py`.
- **[QA-002]** Shared `_write_json_atomic()` helper replaces three duplicate atomic-write blocks — `vault_doctor.py`.
- **[QA-003] + [QA-011]** Removed inline `import os as _os_import`; renumbered `install()` step comments to be contiguous — `install.py`.
- **[QA-005]** New `tests/test_vault_stats.py` (10 tests) and `tests/test_vault_search.py` (17 tests).
- **[QA-006]** New `tests/test_vault_merge.py` (21 tests) and `tests/test_vault_export.py` (22 tests).
- **[QA-007]** 10 new tests for AI cooldown + delta-section logic — `tests/test_session_start_hook.py`.
- **[QA-008]** stderr warning when `parse_frontmatter()` skips a nested YAML mapping — `vault_index.py`.
- **[QA-009]** `traceback.format_exc()` logging in summarizer exception handlers; queue-preservation noted — `summarize_sessions.py`.
- **[QA-010]** Single `related`/`related_str` computation in `check_note()` — `vault_doctor.py`.
- **[QA-012]** Public `SAFE_ENV_KEYS` alias exported; private name kept working — `vault_hooks.py`, `vault_common.py`.
- **[QA-013]** `ComponentPropsWithoutRef<'a'>` replaces `any` in markdown callbacks; both eslint-disables removed — `ReadingPane.tsx`.
- **[QA-014]** Private facade re-exports documented as backward-compat-only — `vault_common.py` (no importer broken).
- **[QA-015]** Racy `trap EXIT` removed; cleanup owned by the background subshell — `session_stop_wrapper.sh`.

### Documentation (17 of 17)
- **[DOC-001]** `__version__` 0.6.0 → 0.7.6 — `vault_common.py`.
- **[DOC-002]** README Scripts table: facade row + six submodule rows.
- **[DOC-003]** `docs/MCP.md` legacy `~/ClaudeVault/` → `~/ParsidionVault/` (+ legacy note).
- **[DOC-004]** 36 per-node `style` lines → 10 `classDef` groups, colors preserved — `docs/ARCHITECTURE.md`.
- **[DOC-005]** Agent-agnostic framing in ARCHITECTURE.md subtitle.
- **[DOC-006]** Legacy path sweep in `EMBEDDINGS_EVAL.md` / `EMBEDDINGS.md`.
- **[DOC-007]/[DOC-008]** Language tags added to actually-unlabeled blocks (audit overcounted: real unlabeled openers were 2 per file; the rest were closing fences).
- **[DOC-009]** Emoji callouts removed — README.md, SECURITY.md.
- **[DOC-010]** 26 Keep-a-Changelog comparison footer links added — CHANGELOG.md.
- **[DOC-011]/[DOC-012]** Docstrings added to `vprint` (`install.py`) and `_score` (`session_start_hook.py`).
- **[DOC-013]** Historical-document callout added to all 25 `docs/superpowers/` plans/specs.
- **[DOC-014]** Stale TOC anchor fixed — `docs/ARCHITECTURE.md`.
- **[DOC-015]** Multi-runtime adapter bullet added to ARCHITECTURE.md Overview.
- **[DOC-016]** `docs/ideas.md` legacy path updated.
- **[DOC-017]** `parsidion-cc` rename note added under CHANGELOG `[0.1.0]`.

---

## Requires Manual Intervention 🔧

These were intentionally deferred by the orchestrator — AUDIT.md's own roadmap marks them
long-term backlog, and running them concurrently with the Phase 3 fixes to the same files
would have guaranteed conflicts.

### [ARC-002] Decompose `install.py` (3,119 lines) into an `installer/` package
- **Why**: Large structural refactor; conflicts with concurrent in-file fixes; needs phased execution with review gates.
- **Recommended approach**: Extract `hooks.py` (3-runtime hook merging), `scheduler.py` (launchd/cron), `vault_setup.py` (vault dirs/git), then thin `install()` orchestrator. Do Step-0 dead-code cleanup first per repo conventions.
- **Estimated effort**: Large.

### [ARC-003] Refactor `vault_doctor.py` (2,714 lines; 508-line `main()`) into mode dispatch + explicit vault threading
- **Why**: Same as above; ARC-001's minimal cache fix landed first as planned.
- **Recommended approach**: `run_<mode>()` functions + dispatch table; replace `_vault_path` global with a `vault: Path` parameter; shared PID-lock context manager.
- **Estimated effort**: Large.

### [ARC-007] / [ARC-012] Split CLI display layer from data layer in `vault_search.py` / `vault_stats.py`
- **Why**: Conflicted with the new test suites (QA-005) written this pass; tests-first was the safer order and is now done.
- **Recommended approach**: Move DB-query logic into `vault_common` submodules; keep the CLIs as thin presenters. The new tests act as the refactor safety net.
- **Estimated effort**: Medium.

### [ARC-009] Decouple `resolve_vault()` caching from test monkey-patching
- **Why**: Requires touching every test using `monkeypatch.setattr(vault_common, "VAULT_ROOT", ...)`; too broad to change atomically alongside everything else.
- **Recommended approach**: Switch tests to `monkeypatch.setenv("CLAUDE_VAULT", ...)` + `cache_clear()`, then remove the `sys.modules` inspection branch from `_resolve_vault_cached`.
- **Estimated effort**: Medium.

### [ARC-015] Evaluate replacing the custom Express server with SSE-based Next.js routes
- **Why**: Design evaluation, not a mechanical fix.
- **Estimated effort**: Medium (investigation + prototype).

### [QA-004 remainder] Full `GraphCanvas.tsx` split with end-to-end sigma typing
- **Why**: Typing the reducers requires declaring the graph as `MultiGraph<NodeDisplayData>` across graph construction in multiple files — sigma API constraint, larger refactor.
- **Recommended approach**: Extract `useForceLayout.ts` / `useGraphReducers.ts` / `lib/sigma-renderers.ts` with co-typed graph attributes in one dedicated pass.
- **Estimated effort**: Medium.

### [SEC-012 remainder] Migrate the 3 route-local `guardPath` copies to the shared export
- **Why**: Shared `guardPath()` was added; the identical local copies were left to keep the diff surgical.
- **Recommended approach**: Replace each local copy with `import { guardPath } from '@/lib/vaultResolver'` and delete the duplicates.
- **Estimated effort**: Small.

---

## Verification Results

- Build/format: ✅ `ruff format` clean
- Lint (Python): ✅ `ruff check` — all checks passed
- Type Check (Python): ✅ `pyright` — 0 errors, 0 warnings (after excluding `.claude/` agent worktrees from the scan — a stale locked worktree was being picked up)
- Tests (root): ✅ 456 passed (376 pre-existing + 80 added by this remediation)
- Tests (parsidion-mcp): ✅ 35 passed (now wired into `make checkall` via new `checkall-mcp` target)
- Type Check (TypeScript): ✅ `tsc --noEmit` — only the pre-existing `bun:test` types error in `lib/parseDiff.test.ts` (present before remediation)
- Lint (TypeScript): ✅ eslint — 0 errors, 4 pre-existing warnings (unchanged files)

No regressions. `make checkall` exits 0.

---

## Files Changed

### Created
- `visualizer/lib/apiAuth.ts` — SEC-004 auth/CSRF helper
- `tests/test_vault_resolver_parity.py` — ARC-004 parity test (6 tests)
- `tests/test_vault_stats.py` (10 tests), `tests/test_vault_search.py` (17), `tests/test_vault_merge.py` (21), `tests/test_vault_export.py` (22) — QA-005/006
- `AUDIT.md`, `AUDIT-REMEDIATION.md`

### Modified — Python / shell
- `install.py` (QA-003, QA-011, DOC-011)
- `pyproject.toml` (pyright `.claude` exclude — verification fix)
- `Makefile` (ARC-010 `checkall-mcp`)
- `skills/parsidion/scripts/`: `vault_doctor.py` (ARC-001, SEC-007, QA-002, QA-010), `update_index.py` (ARC-001, ARC-005), `vault_export.py`, `vault_merge.py`, `build_embeddings.py` (ARC-001), `vault_review.py` (ARC-001, ARC-005), `summarize_sessions.py` (QA-001, QA-009), `vault_hooks.py` (SEC-008, SEC-010, QA-012), `vault_fs.py` (SEC-008, SEC-013), `vault_index.py` (QA-008), `vault_common.py` (DOC-001, QA-012, QA-014), `session_start_hook.py` (DOC-012), `session_stop_wrapper.sh` (QA-015), `ai_backend.py` (ARC-006, ARC-013), `embed_eval_common.py` + `embed_eval.py` (ARC-011)
- `tests/test_session_start_hook.py` (QA-007, +10 tests)

### Modified — TypeScript / visualizer
- `visualizer/lib/vaultResolver.ts` (SEC-001, SEC-012)
- `visualizer/server.ts` (SEC-009)
- `visualizer/next.config.ts` (SEC-006)
- `visualizer/app/api/note/route.ts`, `note/history/route.ts`, `note/diff/route.ts`, `graph/route.ts`, `graph/rebuild/route.ts`, `files/route.ts` (SEC-002/003/004/005/014 + 400-on-forbidden carry-over)
- `visualizer/components/GraphCanvas.tsx` (QA-004 partial), `ReadingPane.tsx` (QA-013)
- `visualizer/.env.local` (chmod 600 only)

### Modified — Documentation
- `README.md`, `CHANGELOG.md`, `SECURITY.md`, `docs/ARCHITECTURE.md`, `docs/MCP.md`, `docs/EMBEDDINGS.md`, `docs/EMBEDDINGS_EVAL.md`, `docs/ideas.md`, 25 files under `docs/superpowers/plans/` + `docs/superpowers/specs/`

### Commits (on `fix/audit-remediation`)
1. `1b365ed` fix(security): resolve critical security issues from audit
2. `7b41770` fix(architecture): resolve critical architecture issues from audit
3. `4cb435b` fix: resolve remaining audit issues (security/architecture/quality/docs)
4. `fc5967d` fix: verification cleanup — exclude .claude from pyright, drop unused type imports

---

## Next Steps

1. Review the **Requires Manual Intervention** items — ARC-002 and ARC-003 are the two big decompositions; each deserves its own planned, phased effort.
2. The new tests (QA-005/006) were written tests-first specifically so the ARC-007 layer split can proceed safely next.
3. `uv run install.py --force --yes` to sync the modified hook scripts to `~/.claude/` (several live hook scripts changed: `vault_hooks.py`, `vault_fs.py`, `session_stop_wrapper.sh`, `vault_index.py`, `session_start_hook.py`, etc.).
4. Re-run `/audit` after merging to get a fresh AUDIT.md reflecting the remediated state.
5. Optional: clean up the stale locked agent worktree at `.claude/worktrees/agent-a64dff775a1f9ee64` (`git worktree remove --force`).
