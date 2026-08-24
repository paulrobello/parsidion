# Audit Remediation Report

> **Project**: parsidion
> **Audit Date**: 2026-08-23 (Fable 5 audit @ `5d02483`, v0.20.0)
> **Remediation Date**: 2026-08-23 – 2026-08-24
> **Severity Filter Applied**: all (no filter argument)
> **Plan Source**: AUDIT.md `## Remediation Plan` + `AUDIT-REMEDIATION-PLAN.md` playbook
> **Implementation Model**: Opus 5 (all fix agents)
> **Integration branch**: `fix/audit-remediation` (8 squash commits off `main` @ `7aef9bf`, merged back to `main` after verification)

---

## Execution Summary

| Phase | Status | Agent | Issues Targeted | Resolved | Partial | Manual |
|-------|--------|-------|-----------------|----------|---------|--------|
| 1 — Promoted Security | ✅ | fix-security | 6 | 6 | 0 | 0 |
| 2 — Blocking Architecture | ✅ | fix-architecture | 3 | 3 | 0 | 0 |
| QA-001 gate fix (Wave 1) | ✅ | fix-code-quality | 1 | 1 | 0 | 0 |
| 3a — Security (remaining) | ✅ | fix-security | 27 | 27 | 0 | 0 |
| 3b — Architecture (remaining) | ✅ | fix-architecture | 9 | 9 | 0 | 0 |
| 3c — Code Quality (all) | ✅ | fix-code-quality | 19 | 19 | 0 | 0 |
| 3d — Documentation (all) | ✅ | fix-documentation | 27 | 27 | 0 | 0 |
| 4 — Verification | ✅ | orchestrator | — | — | — | — |

**Overall**: 92 of 93 issues resolved by agents; ARC-012 (stray untracked `skills/parsidion-cc/` + `.claude/.cc2cc-session-id`) removed directly from the main checkout by the orchestrator — **93 of 93 resolved, 0 partial, 0 blocked**.

Notes on "resolved" semantics:
- SEC-022 was verified already-landed via ARC-002's byte-bounded `_read_transcript_tail` (no further change needed).
- ARC-010 was verified already-fixed by Phase 1's SEC-007 rewrite (`run_ai_prompt` is a thin wrapper over `run_ai_prompt_with_cause`).
- QA-002 was verified largely pre-done by ARC-002 (`_classify_session` / `_persist_and_report` existed); the agent confirmed completeness rather than re-implementing.
- DOC-027: two of the auditor-cited plan files never existed in git history (path confusion); the real link defects were fixed.
- One issue the audit could not have caught: the two Wave-2 agents were killed mid-run by an API usage limit and resumed from their surviving worktrees with no lost work.

---

## Resolved Issues ✅

### Security (33)
- **[SEC-001]** Visualizer Host-header allowlist — `requireLocalHost` first in `runGuards` (`visualizer/lib/apiAuth.ts`).
- **[SEC-002]** Note-only mutation paths — shared `rejectNonNotePath` on POST/PUT/DELETE `/api/note` (.md only, no dot segments, excludes `Templates`/`TagsRoutes`).
- **[SEC-003]** pi/omp extension — cwd-relative script candidates removed; spawned env allowlisted (`buildHookEnv`); resolution extracted to `lib/scriptRunner.ts` (+11 tests).
- **[SEC-004]** Secure scratch cwd under `secure_log_dir()` (0700, uid-owned, non-symlink) with `mkdtemp` fallback.
- **[SEC-005]** `atomic_write_text` uses `O_CREAT|O_EXCL|O_NOFOLLOW`; same in `build_graph.py`; `*.tmp` + `.merge_previews/` gitignored.
- **[SEC-006]** `event_log.path` contained to vault or `~/.claude/logs`, `O_NOFOLLOW` open.
- **[SEC-007]** `is_trusted_executable` gate for configured binaries; `anthropic_env` network keys honored only from `config.local.yaml` or untracked `config.yaml` (`config_key_sources`).
- **[SEC-008]** MCP `vault_read` restricted to `.md` under the vault, 10 MB cap, `UnicodeDecodeError` → `VaultToolError`.
- **[SEC-009]** HTML export allows only `http`/`https`/`mailto`/relative hrefs.
- **[SEC-010]** Daily-note username validated `^[A-Za-z0-9._-]{1,64}$`; same-parent rename guard.
- **[SEC-011]** `vault-merge` notes and `--output` contained to the vault.
- **[SEC-012]** Post-merge hook emits `shlex.quote`d absolute paths; v2 marker regenerates broken hooks on install.
- **[SEC-013..021, 023..033]** All Low hardening items: preview-cache validation, locked queue read-modify-write (`_mutate_entries`), stable daily-note sibling lock, lsof-absence warning, embed-serve read cap + config-only model, rename collision guard, `config.local.yaml` pathspec, `clamp_timeout` (nan/inf/negative), `.bak` mode preservation, cron quoting, `.env` gitignore, 17 Actions `uses:` refs pinned to verified SHAs, generic 404s, API concurrency caps + 10 MiB body limit, audit-driven dep bumps (`bun audit` clean), MCP `VAULT_ROOT` threading, six integrity fixes (SEC-033a–f).

### Architecture (13)
- **[ARC-001]** `core/vault_health`, `vault_links`, `vault_metrics` import siblings directly; `tests/test_core_layering.py` AST gate.
- **[ARC-002]** `session_stop_hook.py` is a ~170-line shim over `run_session_end(get("claude"))`; AI classification + summarizer launch moved in as config-gated adapter-neutral stages; every runtime gets the byte-bounded transcript reader.
- **[ARC-003]** Wheel manifest complete (packages.find + full py-modules); CI smoke imports every console-script target and package; `tests/test_packaging_manifest.py`; clean-room wheel verified (122 modules import).
- **[ARC-004]** Single `run_index_rebuild` in `core/vault_index.py` (argv/env/discovery/timeout); installer, MCP, summarizer are thin callers.
- **[ARC-005]** `serialize_frontmatter` single emitter + `tests/fixtures/parity/frontmatter.json` consumed by Python AND visualizer TS suites; closes the SEC-033 quoting hazard.
- **[ARC-006]** `LAST_BACKEND` deleted; `ai_backend`/`parmem_backend` moved to `core/` with shims; tests retargeted.
- **[ARC-007]** Schema dataclasses carry real defaults; `get_config` is an adapter; hot readers migrated to `load_typed_config()`; behavior pinned by `tests/test_typed_config.py` (54 call shapes).
- **[ARC-008]** `install.py` → 25-line shim over `installer/plan.py` + `installer/cli.py`; dry-run output byte-identical.
- **[ARC-009]** `requires-python >= 3.13`, ruff `py313`, lock refreshed.
- **[ARC-010]** Verified already fixed (see above).
- **[ARC-011]** `build_graph.py` under pyright (`# pyright: basic`).
- **[ARC-012]** Stale `skills/parsidion-cc/` (bytecode caches) and `.claude/.cc2cc-session-id` removed from the main checkout.
- **[ARC-013]** Shared curses list-view `vault_tui.run_list_view`; review/conflicts TUIs rewired.

### Code Quality (20)
- **[QA-001]** `@pytest.mark.timeout(60)` on the five doctor e2e test classes; full suite green WITH coverage (the flaky configuration), 3×.
- **[QA-002]** Verified complete post-ARC-002 (single classify + persist tail; `main` complexity 8).
- **[QA-003]** Shared `log_hook_error` in `core/vault_hooks.py`; five copies removed.
- **[QA-004]** `read_vaults_yaml`/`render_vaults_yaml` single-source three parsers; dead branch gone.
- **[QA-005]** Doctor `DoctorOptions`/`ScanContext`/`Rule` registry; `run_scan_and_repair` 31→12, `check_note` 35→6, `_repair_one` 28→15; fixture dry-run byte-identical.
- **[QA-006]** 25 visualizer render/state tests (testing-library + happy-dom; devDeps added).
- **[QA-007..020]** Dead code deleted (4 symbols); shims via `core.__all__`; shared `_build_note_index_where`; rollups deduped (+tests); `graphEdges.ts` helper; eslint-disable 32→15 (GraphCanvas 14→0); stderr diagnostics at 4 silent sites; single-read underscore normalization; tests for operations/graph-coverage/html-to-md; `MergeScanError` replaces library `sys.exit`; single `connect_with_vec`; migrate-tools hoisted onto `_migrate_common` + `serialize_frontmatter`; noqa 260→125; 3 terminal asserts strengthened.
- **Bonus**: `resolve_vault` now coerces `Path` explicit references — `doctor --vault <name>` named-vault lookup was silently failing (latent pre-existing bug).

### Documentation (27)
- **[DOC-001..007]** README 0.20.0; grok-cli documented everywhere (six config sections, zero stale "claude -p or codex exec" pairings); `docs/api` regenerated with `gitRevision: main` + CI `docs-api-checks` job + `.parmemignore`; config table/template/schema synced (schema = typed source of truth); 60 s SessionStart guidance; SKILL.md `--fix-all` seven flags + namespaced daily path; CONTRIBUTING PEP 723 + resolver delegation model.
- **[DOC-008..017]** Nine missing config keys; omp runtime; USAGE flag fixes; MCPL archived; opus plan status lines; env-var reference (18 variables); SECURITY scope synced to the 12-module poison gate; Makefile table; `v0.20.0` tag created locally at `a5036cc`; research-agent placeholders.
- **[DOC-018..027]** Installer flags + FAQ; CLAUDE.md prose sync; docs index; link repairs; audit-artifact citations repointed (**artifacts kept** per user instruction); eleven docstrings; quick-sync marked Windows-only; historical plan headers.

---

## Requires Manual Intervention 🔧

None blocking. Three follow-ups for the user:

1. **Push `v0.20.0` and the branch** — the annotated tag sits locally at `a5036cc` and the remediation commits are on local `main`. Pushing to origin is outward-facing and was deliberately not done.
2. **Run `uv run install.py --force --yes` on the live machine** — SEC-012's post-merge hook fix and the new gitignore entries only take effect for the live vault after a reinstall (the current installed hook still has the broken `"~/..."` form; the installer now detects it as stale and repairs it).
3. **html-to-md.py retirement decision** — QA-015 kept it and added contract tests. The agent recommends confirming whether it is still a supported research-agent tool; retirement would be a two-line removal plus a README edit.

Documented deviations (accepted, not manual): SEC-028 keeps `id-token: write` on the pages job (`actions/deploy-pages` requires it; SHA pinning closes the mutable-ref risk); SEC-031 pins sharp to 0.35.3 (the libvips advisory covers all of 0.34.x, so the playbook's "latest 0.34.x" would not have fixed it).

---

## Verification Results

- Format (`ruff format --check`): ✅ Pass (259 files)
- Lint (`ruff check`): ✅ Pass
- Type check (`pyright`): ✅ Pass (0 errors)
- Tests (`pytest tests/`): ✅ Pass — 1599 passed, 6 skipped (3 pre-existing; 3 optional-dep)
- with coverage (the QA-001 flaky configuration): ✅ Pass
- Visualizer (`tsc` + `eslint` + `bun test` + `bun run build`): ✅ Pass (321 tests)
- parsidion-mcp (`make checkall-mcp`): ✅ Pass (70 tests, 92% coverage)
- `make docs-api-check`: ✅ Pass (twice consecutively; generation made checkout-path-invariant — see below)
- `make parity-fixtures-check`: ✅ Pass
- **`make checkall`: ✅ exit 0**
- Per-issue validation: all 19 High issues individually re-verified on the final tree (structure greps + targeted suites + playbook Verify commands); kanban cards closed only for validated issues.

**Orchestrator fixes during integration** (not attributable to any agent):
1. Three merge conflicts resolving Phase 3a security edits onto Phase 3b architecture moves (`ai_backend.py` core/ port, `vault_merge.py` emitter + containment, `vault_review.py` TUI + locked mutation adapted to `run_list_view`'s mutate-in-place contract).
2. Two composition regressions caught by the gate (SEC-025 test on the removed `install` facade; SEC-033 test pinned to the old escaping form).
3. `docs-api` generation was checkout-path-dependent (pdoc's view-value toggle keys on pre-scrubbed default-value length) — fixed with a fixed-length symlink import path so the new CI gate cannot flake by runner path.

---

## Files Changed

436 files changed, +44,477 / −33,117 versus `main` @ `7aef9bf` (the bulk of the churn is the regenerated `docs/api/` snapshot, 200 files). Eight atomic squash commits on `fix/audit-remediation`, each preserving the per-issue commit history in its message:

```
d78281e refactor: resolve all code quality issues from audit (Phase 3c: QA-002..020)
e7a110b fix(docs): make docs-api generation checkout-path-invariant (DOC-003 follow-up)
c09f089 docs: resolve all documentation issues from audit (Phase 3d: DOC-001..027)
6b54255 fix(architecture): resolve remaining architecture issues from audit (Phase 3b)
b7af5e6 fix(security): resolve remaining security issues from audit (Phase 3a)
a1203a2 test: deterministic e2e timeout ceiling for doctor orchestrator suite (QA-001)
3ff0481 fix(architecture): resolve blocking architecture issues from audit (ARC-002/009/001)
c13cf99 fix(security): resolve promoted security issues from audit (SEC-005/007/012/016/020/021)
```

---

## Next Steps

1. Follow-ups 1–3 under *Requires Manual Intervention* (push, live reinstall, html-to-md decision).
2. The five pre-existing backlog cards excluded from this audit (par-mem background index, dead-letter transcript check, two vault-stats bugs, migrate-subfolders body refs) remain open on the board.
3. Enhancement backlog ENH-015..019 (plans in `docs/fable/`) is untouched and tracked by `/enhancement-all`.
4. Re-run `/audit` after the next release cycle; the audit itself suggested re-checking `find_hotspots` then (a `replay_history` backfill was started during the audit).
