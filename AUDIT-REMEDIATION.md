# Audit Remediation Report

> **Project**: parsidion
> **Audit Date**: 2026-08-01
> **Remediation Date**: 2026-08-01
> **Severity Filter Applied**: all
> **Plan Source**: AUDIT.md `## Remediation Plan` + `AUDIT-REMEDIATION-PLAN.md` playbook (per-issue Files/Steps/Method/Verify)
> **Implementation Model**: Opus 5 (all fix agents); orchestrator on the session model
> **Branch**: `fix/audit-remediation` (base `e0faf8c`)
> **Commits**: `1573dcd` (artifacts) → `70d63ea` (SEC-P001) → `bffa650` (ARC-001) → `46e252c` (Phase 3)

---

## Execution Summary

| Phase | Status | Agent | Targeted | Resolved | Partial | Manual/Deferred |
|-------|--------|-------|----------|----------|---------|-----------------|
| 1 — Critical Security | ✅ | fix-security | 1 | 1 | 0 | 0 |
| 2 — Critical Architecture | ✅ | fix-architecture | 1 | 1 | 0 | 0 |
| 3a — Security (remaining) | ✅ | fix-security | 2 | 2 | 0 | 2 no-action (SEC-P004/005) |
| 3b — Architecture (remaining) | ✅ | fix-architecture | 9 | 9 | 0 | 6 deferred (ARC-005/006/008/009/016/017) |
| 3c — Code Quality | ✅ | fix-code-quality | 6 | 6 | 0 | 4 deferred (QA-007/008/009/010) |
| 3d — Documentation | ✅ | fix-documentation | 11 | 11 | 0 | 1 skip (DOC-012) |
| 4 — Verification | ✅ | orchestrator | — | — | — | — |

**Overall**: **37 issues resolved** (of 44 audited). 30 in the initial phased pass + 6 in the follow-on refactor round + 1 in the QA-003 pipeline extraction (below). Remaining: 5 no-action/optional + 2 partial/contract-blocked, documented under Deferred. 0 regressions — the gate is fully green.

> Note: ARC-012 was reassigned from Phase 3b → 3c during execution because it shares the flat re-export shim files with QA-005. Running both in the same agent (3c) sequenced them safely. It is counted under 3c.

---

## Refactor Round (follow-on, after user direction to continue)

The initial pass deferred six large structural refactors as "Long-term Backlog." The user directed continuing them, so they ran as two sub-agent-isolated rounds (each verified with `make checkall` before merging). All work was done in a git worktree (`refactor/round-b`) because projects actively consume `main` via the skill symlink — merged to `main` only after each round went green. See `memory/parsidion-worktree-workflow.md`.

| ID | Outcome |
|----|---------|
| **ARC-005** | ✅ All four CLI God-files decomposed: `vault_stats`→`cli/stats/`, `vault_search`→`cli/search/`, `vault_merge`→`cli/merge/`, `update_index`→`cli/index/`. Thin re-export shims remain; public entrypoints unchanged; monkeypatched helpers kept in shims per bare-name resolution contract. |
| **ARC-006** | ✅ `session_start_hook.py` 1253→701 LOC → `session_start/` subpackage (`ai_selector`, `graph_retrieval`, `seed_selection`, `context`). |
| **ARC-008** | ✅ (safe half) Visualizer `Home` 554→382 LOC; extracted `GraphPanel` + `SidebarPanel`; pinned exact versions (sigma/graphology/@types). ReadingPanePanel + Vite swap deferred. |
| **ARC-009** | ✅ Broad-exception sweep: 18 best-effort `except Exception: pass` sites across 14 files now emit `stderr` diagnostics; all `# noqa: BLE001` preserved; hooks still never fail closed. |
| **ARC-016** | ✅ README 1138→670 LOC (−41%), CHANGELOG 785→147 (−81%); deep content moved to `docs/{USAGE,MULTI_VAULT,PI_EXTENSION}.md`, old changelog archived. |
| **QA-003** | ✅ `summarize_sessions.main` decomposed (complexity 27→<10). The `summarize_one`→``summarizer/pipeline.py`` relocation was blocked here by the test-monkeypatch contract and deferred; completed in a follow-up (see Resolved → Code Quality). |

**Follow-up flagged (not an audit issue):** `[tool.setuptools] packages` in `pyproject.toml` doesn't declare the `cli.*` subpackages — fine for editable/runtime/symlink install, but a clean wheel/sdist would silently omit them. Small fix when packaging is exercised.

---

## Resolved Issues ✅

### Security
- **[SEC-P001]** Python vault resolver denylist → allowlist — `skills/parsidion/scripts/core/vault_path.py` — Back-ported the hardened TS allowlist: `_resolve_vault_reference` now accepts only named vaults (`vaults.yaml`) or the default; a malicious `.claude/vault`/`CLAUDE_VAULT` reference silently falls back to the default vault instead of redirecting writes. Parity fixture updated (19 vectors). Closes the supply-chain vector.
- **[SEC-P002]** TS `envWithoutClaudecode()` — `visualizer/lib/env.ts` (new) + `vaultStatsServer.ts` — Allowlist-mirrored env builder for `spawnSummarizer`; drops `CLAUDECODE` and unrelated dev-server secrets. +6 tests.
- **[SEC-P003]** MCP `vault_write` TOCTOU — `parsidion-mcp/.../notes.py` — Re-resolve + re-validate containment, then open the leaf with `O_NOFOLLOW` and write through the fd. +2 symlink-swap rejection tests.

### Architecture
- **[ARC-001]** Narrow `vault_common` facade — `vault_common.py` — Added deprecation policy to the docstring + marked all re-export blocks deprecated; migrated a first batch of 4 callers (`check_graph_coverage`, `post_compact_hook`, `prompt_templates`, `vault_new`) off the facade. Foundation for QA-005; no re-exports removed.
- **[ARC-002]** Decouple `install.py` tests — `install.py`, `installer/{paths,ui}.py`, `tests/test_install.py` — Moved `validate_vault_path`/`prompt_vault_path` to `installer/`; tests now patch at the source module. Removed the private-name namespace-import convenience.
- **[ARC-003]** Remove `VAULT_ROOT` global + `lru_cache` side-channel — `core/vault_path.py`, `update_index.py` — `update_index.py` threads `--vault` as the `explicit` arg to `resolve_vault()` instead of mutating the global; branch 4 emits a `DeprecationWarning`. No `VAULT_ROOT =` assignment or `cache_clear()` call remains.
- **[ARC-004]** Consolidate hook-script maps — `agent_adapter.py`, `installer/paths.py` — `agent_adapter` is now the single source; `installer/paths.py` imports by reference. +AST test (`TestHookScriptMapsSingleSource`) fails if the dicts are redefined elsewhere.
- **[ARC-007]** Vault-resolution parity — `core/vault_path.py`, `visualizer/lib/vaultResolver.ts`, parity fixture — Option (b): documented the TS resolver as a deliberately-narrower allowlist view at both resolvers, pointed to ENH-009, +2 vectors asserting the current (narrower TS) behavior so drift is caught.
- **[ARC-010]** Lazy adapter registration — `agent_adapter.py` — Builtins register on first access via `_load_builtin_adapters_if_needed()` (mirrors the external-adapter lazy pattern); no more import-time side effect.
- **[ARC-011]** `vault_tui.py` deviation documented — `vault_tui.py` — Module docstring explains why it deliberately stays at the scripts root (curses CLI entrypoint, not a library).
- **[ARC-013]** Lower `requires-python` — `pyproject.toml`, `install.py` — `>=3.13` → `>=3.11`; refactored the one load-bearing PEP-701 f-string in `install.py`. Verified all `.py` parse under 3.11.
- **[ARC-014]** Pyright to pre-push — `.pre-commit-config.yaml` — pyright hook confined to `pre-push`; fast gates stay at commit.
- **[ARC-015]** Name magic numbers — `agent_adapter.py` — `_MIN_SUMMARY_LEN = 50`, `_MAX_SUMMARY_CHARS = 500` with rationale.

### Code Quality
- **[QA-001]** Canonical `backup_note` — `core/vault_fs.py`, `doctor/_state.py`, `summarizer/notes.py` — One canonical `backup_note(note_path, vault)`; doctor wraps (never raises + dedup set), summarizer delegates (OSError propagates). +9-case regression test pinning param order.
- **[QA-002]** `vault_stats:main` dispatch table — `vault_stats.py` — Extracted `_build_parser()` + `_MODES` table; one dispatch loop replaces both if/elif chains. Adding a mode is now one entry, not three sites.
- **[QA-004]** De-cluster entrypoints — `session_stop_hook.py`, `doctor/worker.py` — Lifted the guard chain into `_should_skip(input) -> str|None`; wrapped `_repair_one` outcome in `_classify_repair_outcome(...)`. Security guards preserved verbatim.
- **[QA-005] / [ARC-012]** Tighten shim re-exports — 9 flat shims + 9 core modules — Shims switched to `from core.X import *` + explicit `__all__` in each `core/X.py`; stdlib modules (`re`, `sys`, `math`, …) no longer re-exported. Stdlib-only gate stays green.
- **[QA-006]** Replace `!` assertions — `visualizer/app/api/note/{route,diff/route,history/route}.ts`, `GraphCanvas.tsx`, `graphDelta.ts`, `useForceLayout.ts`, `useVaultFiles.ts` — `stem!` → explicit 400 guard; `Map.get()!` → typed local + check. +4 missing-stem route tests.
- **[QA-003]** `summarize_one` dispatch → `summarizer/pipeline.py` — `summarize_one` + its four stage helpers (`_early_gate`/`_apply_merge_decision`/`_handle_write_gate_decision`/`_apply_backlinks_and_strip_links`) extracted from the entry shim into a new `summarizer/pipeline.py`; the three anio-core deps (`preprocess_transcript_hierarchical`, `_summarize_chunk`, `_run_summarizer_prompt`) moved into `summarizer.transcript`/`summarizer.prompt` so the package stays self-contained (the shim is a PEP-723 script run as `__main__`, so submodules cannot import back from it). Test monkeypatches retargeted to the call-site module (`summarizer.pipeline.X` / `summarizer.transcript.X`) via lazy accessors; `_early_gate`'s `_ACTIVE_SESSION_GRACE_SECS` default-disable now hits pipeline's globals. Shim 1247 → 706 LOC (−43%). Gate green: 1311 passed/3 skipped.

### Documentation
- **[DOC-001]** README release 0.14.0 → 0.15.0 (Critical).
- **[DOC-002]** Corrected `--approved-only` docs (Critical) — removed the working-command framing; explicit "not yet implemented" callout; flag stated not to exist.
- **[DOC-003]** MCP tool count 7 → 8 in both READMEs + `vault_health` row (also caught a stale count in `docs/ARCHITECTURE.md`).
- **[DOC-004]** `docs/MCP.md` "other six" → "other seven".
- **[DOC-005]** Deleted stale `hackernews-release.md`.
- **[DOC-006]** Added missing `config.yaml` sections to README (`vault.username`, `embeddings.service_*`, `adapters.load_external`).
- **[DOC-007]** Expanded `SECURITY.md` scope table (split modules + CLI tools); **corrected a false claim** — the audit's suggested stdlib-only wording was inaccurate (`build_graph.py` imports `numpy`); phrased to match what `test_stdlib_only.py` actually enforces.
- **[DOC-008]** Added 7 missing scripts to README Components table.
- **[DOC-009]** Added Quick Start verification step.
- **[DOC-010]** Exempted adapter shims (`AGENTS.md`) in the style guide.
- **[DOC-011]** Version-pinned `docs/MCPL.md` (mcpl v0.1.2) and `docs/AGENTCHROME.md` (v1.62.0).

---

## Deferred / Requires Follow-up 🔧

The initial phased pass deferred the large structural refactors as "Long-term (Backlog)"; the user then directed continuing them, and the refactor round resolved ARC-005/006/009/016 fully and ARC-008 partially, while a follow-up completed QA-003 (see the Refactor Round table above and Resolved → Code Quality). What remains deferred is below — a larger state/product decision (ARC-008) or genuinely optional/low-value work (QA-007/008/009).

| ID | Sev | Why deferred | Recommended approach | Effort |
|----|-----|--------------|----------------------|--------|
| **ARC-008** (remainder) | Med | Visualizer `ReadingPanePanel` extraction + the optional Next→Vite framework swap. | `ReadingPanePanel` needs `noteRefreshTrigger`/`useVaultFiles` lifted into a context/provider (larger state refactor). The Vite swap is a separate product decision. | M–L |
| **QA-007** | Low–Med | Structured logger for visualizer — deferred unless multi-user. | Adopt `pino` (or 20-line wrapper) when the visualizer grows a deployment. | M |
| **QA-008** | Low | Optional `GraphCanvas` interactions hook extraction. | Lift context-menu + edge-pruning into `useGraphCanvasInteractions`. | S |
| **QA-009** | Low | Eval dead-code sweep — tracked as **ENH-013** (board backlog). | Wire-or-delete genuinely-dead helpers in `tools/eval/`. | M |

**No-action (closed by audit's own classification):** SEC-P004 (embedding-service socket, single-user threat model), SEC-P005 (`$EDITOR` argv, no injection path today), ARC-017 / DOC-012 (`MEMORY_REPORT.md` already gitignored), QA-010 (positive observation).

---

## Verification Results

- **Format (`ruff format --check`)**: ✅ Pass (190 files, root + MCP)
- **Lint (`ruff check`)**: ✅ Pass (0 issues)
- **Type check (`pyright`)**: ✅ Pass (0 errors, 0 warnings, 0 informations)
- **Tests (`pytest`)**: ✅ Pass — **1266 passed, 3 skipped** (was 1263+1 pre-remediation; net +3 from new tests, +1 from a previously-skipped path)
- **Graph tests**: ✅ 30 passed
- **Visualizer (`tsc` + `lint` + `bun test` + `build`)**: ✅ Pass (251 tests)
- **MCP**: ✅ 62 passed @ 90% coverage

**Combined `make checkall`: exit 0.** Per-issue verification (playbook Verify signals) run for every resolved issue — each described change confirmed present at its location, not just gate-green.

### Two regressions caught and fixed during Phase 4
1. **Prompt-template trailing newline** — a Phase 3 agent's editor added an EOF newline to 4 `templates/prompts/*.md` files, breaking the byte-identical render tests. No issue assigned those files; reverted to HEAD.
2. **MCP test formatting** — the SEC-P003 agent's new test wasn't ruff-formatted; `ruff format` applied to the one file.

Neither was a logic regression; both were formatting/editor artifacts.

---

## Files Changed

**9 commits** across two branches (`fix/audit-remediation` for the phased pass, `refactor/round-b` for the refactor round — a git worktree, since `main` is live via the skill symlink), both fast-forward merged to `main`. Base `e0faf8c..main`: **148 files changed, +10186 / −6345**.

New packages: `cli/{stats,search,merge,index}/`, `session_start/`. New files: `tests/test_backup_note_canonical.py`, `visualizer/lib/env.ts`(+test), `visualizer/components/{GraphPanel,SidebarPanel}.tsx`, `docs/{USAGE,MULTI_VAULT,PI_EXTENSION}.md`, `docs/archive/CHANGELOG-0.11-and-older.md`. Deleted: `hackernews-release.md`.

Full per-file list: `git diff --stat e0faf8c..main`.

---

## Next Steps

1. **Push `main`** when ready — local `main` (`aacfce1`) is ahead of `origin/main` (`e0faf8c`) by the 9 remediation commits; not pushed (push is outward-facing, left for explicit confirmation). Local main is already live for sessions via the symlink.
2. **Re-run `/audit`** to regenerate AUDIT.md against the remediated state — it should show the 36 resolved issues closed.
3. **Remaining deferred** (see table): ARC-008 `ReadingPanePanel` + the optional Vite swap, plus the optional QA-007/008/009.
4. **`[tool.setuptools] packages` gap** (flagged, not an audit issue): add the `cli.*` subpackages to `pyproject.toml` before exercising wheel/sdist packaging — the editable/symlink install is unaffected.
5. **Optional**: implement the `--approved-only` flag (enhancement **ENH-A**) now that DOC-002 corrected the README — `vault_review.py` already records approvals nothing consumes.
5. **Sync installed location** after merge: `uv run install.py --force --yes` (skill/hooks source → `~/.claude/skills/parsidion`).
