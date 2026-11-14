# Audit Remediation Report

> **Project**: parsidion (`~/Repos/parsidion`)
> **Audit Date**: 2026-11-13
> **Remediation Date**: 2026-11-14
> **Severity Filter Applied**: all
> **Plan Source**: AUDIT.md `## Remediation Plan` + AUDIT-REMEDIATION-PLAN.md playbook
> **Implementation Model**: Sonnet (per standing fix-audit policy); orchestrator on Fable 5.2
> **Effort**: High

---

## Execution Summary

| Phase | Status | Agent | Issues Targeted | Resolved | Partial | Manual |
|-------|--------|-------|----------------|----------|---------|--------|
| 1 — Critical Security | ✅ | fix-security | 1 | 1 | 0 | 0 |
| 2 — Sequential Architecture | ✅ | fix-architecture | 4 (ARC-202, 200.1, 201, 200.2) | 4 | 0 | 0 |
| 3a — Security (remaining) | ✅ | fix-security | 2 | 2 | 0 | 0 |
| 3b — Architecture (remaining) | ✅ | fix-architecture | 9 | 8 | 1 (ARC-204) | 0 |
| 3c — Code Quality | ✅ | fix-code-quality | 6 | 5 + 1 verified-alive | 0 | 0 |
| 3d — Documentation | ✅ | fix-documentation | 3 | 3 | 0 | 0 |
| 4 — Verification | ✅ | — | — | — | — | — |

**Overall**: 24 issues resolved (incl. one verified-alive with no change needed), 1 partial (ARC-204, playbook-sanctioned effort cap), 0 require manual intervention. One issue (ARC-206) has its CI/Pages leg verified only after the approved push.

Phases ran as five waves of isolated worktree agents (the plan's four Phase-3 domains split into three waves around intra-Phase-3 file conflicts: QA-002 before the ARC-203 split; ARC-204 before QA-008/009; ARC-203 before QA-010).

---

## Resolved Issues ✅

### Security
- **[SEC-003]** Unquoted hook command paths — `installer/hooks.py` — `shlex.quote` on the path tail in both `_managed_hook_command` and the independently-discovered second copy `_build_managed_command`; leading `~/` left unquoted so tilde expansion survives; unspaced homes produce byte-identical strings so existing registrations still match merge/remove. (`e8552fd`)
- **[SEC-001]** `summarize-chunk.md` untrusted-data framing — SEC-108-style SYSTEM preamble, `$chunk_text` wrapped in `---BEGIN TRANSCRIPT CHUNK---`/`---END---`, version 1.1.0, byte-identical test baseline + framing-marker assertions. (`9ff2c9e`)
- **[SEC-002]** `repair-frontmatter.md` framing — one-line SYSTEM preamble, version 1.1.1-era convention bump; no loader change. (`2afbd37`)

### Architecture
- **[ARC-202]** Dedup consolidation — new stdlib `recall_context.py` (excerpt/context/settings/stamp) consumed by both retrieval hooks; summarizer pass-through `_backup_note` deleted; `rebuild_index`×3 and `_run_tui`×3 and `_build_frontmatter` pair confirmed already single-homed by earlier landed work (ARC-004/013/005); `_to_shared_payload` single-homed on `agent_adapter`; `_resolved_inside` atom shared behind the `*_inside_vault` checks. (`df002a3`…`6e89320`)
- **[ARC-200 step 1]** parsidion-mcp imports → `core.*` canonical surface, 14 files incl. test patch targets; MCP gate caught a real `SCRIPTS_DIR` depth break. (`49f52d7`)
- **[ARC-201]** Wheel re-namespaced to `parsidion.*` — 360 files; physical move under `skills/parsidion/scripts/parsidion/` (package-dir mapping rejected because three execution contexts root import at the scripts dir and pyright cannot see a dynamic `__path__` shim); 21 by-path entry scripts stayed at the root so installed hook command strings keep working; wheel ships zero generic top-level names; clean-room proof `ok: imported 117 shipped modules/packages`. (`50ce87b`)
- **[ARC-200 step 2]** 15 flat shims → raising deprecation stubs (release 0.29.0 named in each message; stub form chosen over deletion since the repo is at 0.x). Agent correctly discovered ARC-201 had left 214 in-repo import lines on the shim surface and migrated them in-card first. Stub-deprecation assertion now enforced in the CI wheel job (`scripts/check_wheel_imports.py`). (`22ebd58`)
- **[ARC-203]** God files split — `core/frontmatter.py` (374 ln), `core/note_index.py` (799 ln), `core/vault_index.py` (815 ln walk+re-export surface), `agent_pipeline.py` (610 ln; one-way dependency via `TYPE_CHECKING`); deviation accepted: `find_notes_by_*` stayed in `vault_index.py` (DB-first policy layer; moving would create an import cycle). (`f3a0d76`)
- **[ARC-205]** Installer↔core default-vault-path parity fixture — 8 matrix cases + one-time mutation sanity. (`5e5b328`)
- **[ARC-206]** `docs/api` snapshot moved to CI artifact — drift gate + normalization machinery deleted; `docs/api` gitignored and untracked (254 files); new SHA-pinned CI `docs-api` upload job; Pages deploy generates API docs under `/api/`. **CI-leg verification pending the approved push.** (`77e9234`)
- **[ARC-207]** `gen_config_docs.py` imports `yaml_lite.dump_scalar` — swap exposed a real divergence (44 schema defaults rendered string `"None"` instead of `null`); fixed in `yaml_lite`; 41-case emit→parse round-trip suite added; `config-docs-check` byte-identical. (`e1ca46b`)
- **[ARC-208]** Scratch hygiene — `.superpowers/` untracked + gitignored, `.claude/commmands/` typo dir removed (real command `git mv`'d to `.claude/commands/`), screenshots relocated to `docs/` with URLs updated. (`d9f3096`)
- **[ARC-209]** Resolver memoization — already existed (config-signature invalidation, stronger than the playbook's TTL); added the missing proof: 4 spawn-count cache tests. (`ca72ec3`)
- **[ARC-210]** `useConfirmDelete` + `useKeyboardShortcuts` hooks extracted; `FrontmatterEditor` split 520→199+313 lines; key protocol byte-identical; two deliberate safe deltas documented (delete-target captured at request time; `isDeleting` resets in `finally`). HUDPanel sub-item deferred by playbook gate (see Follow-ups). (`1461984`)
- **[ARC-211]** `search` → `search_with_meta` delegation — verified already present at wave base (thin wrapper, ARC-031/ARC-006 docstring); no change needed.

### Code Quality
- **[QA-002]** CLI metadata `query()` → 10-line delegate over core `_query_note_index_results`; `vault-search` output byte-identical (59,528 bytes, empty diff). (`464f47e`)
- **[QA-008]** Typed hook config — `load_hook_settings` coerces each value to its schema-declared default type; 18 `# type: ignore[arg-type]` deleted; pyright clean. Sanctioned behavior delta: malformed values degrade to typed defaults. (`e376eb6`)
- **[QA-009]** Debug-gated logging inside broad excepts — stage + exception class only (never note content), gated on the existing `debug` key which also sets `PARSIDION_DEBUG=1`; stdout JSON contract unchanged. (`88978ee`)
- **[QA-010]** Stdlib gate extended (+71 modules to `summarizer/`, `doctor/`, `cli/`) — the unmodified probe caught **three real anyio violations**, fixed (guarded import + `TYPE_CHECKING`); gate grew 28→99 cases. (`97ded41`)
- **[QA-011]** Dead-code candidates — all five verified **alive** (parsight `no_callers` wrong on every one); nothing removed. (`d905f55` batch card)
- **[QA-015]** `tests/test_installer_hooks.py` — 11 tests: SEC-003 quoting assertions, merge idempotency, foreign-hook preservation, managed-only removal. (`cb9e370`)
- **[QA-016]** ruff B904 enabled repo-wide (both pyprojects); exactly four findings, all `from err`. (`d905f55`)

### Documentation
- **[DOC-001]** All six stale three-name installer sites → eight-name list. (`2789b2d`)
- **[DOC-002]** `--enable-ai` help reworded to backend-small-model + 60s-timeout phrasing. (`1e9eb6e`)
- **[DOC-003]** 17 waiver markers (`<!-- doc-path: example -->`) on illustrative citations + **14 real stale-path fixes** from the namespace moves + one dead-guidance removal; parsight `doc_links` 51 → 12 with every remaining row deliberately out of lane (frozen audit artifacts, style-guide byte-copy rule, golden fixture). (`5e02f5a`)

---

## Requires Manual Intervention 🔧

None. One item needs a post-push step, not a decision:

### [ARC-206] CI/Pages verification
- **Why**: the new `docs-api` CI job and the Pages `/api/` deploy only run after a push, which awaits Paul's approval per standing Git Workflow rules.
- **Local evidence**: `make checkall` green under the new (drift-gate-free) definition; simplified `docs-api` target runs end-to-end locally; CI workflow YAML uses SHA-pinned actions.
- **Estimated effort**: verify the CI job green + Pages `/api/` live after the next approved push (small).

---

## Verification Results

- Build/wheel: ✅ (clean-room import smoke, `make wheel-install` green; stub-deprecation assertions in the CI wheel job)
- Tests: ✅ (final `make checkall-ci` run on the integration tip; every wave also gated before merge)
- Lint: ✅ (`ruff check` clean, B904 active in both pyprojects)
- Type Check: ✅ (pyright 0 errors)

Per-issue playbook Verify commands were run at each wave's merge on the merged tree (byte-identical output diffs for QA-002 and ARC-211, `doc_links` counts for DOC-003, shlex/tilde real-shell probes for SEC-003, complexity re-measure for ARC-204, output-shape diff for the `_get_all_notes_as_results` follow-up).

**In-run regression found and fixed**: the ARC-201 physical move broke `resolve_templates_dir()`'s sibling computation (two parents up, now needs three) — repo-source runs silently read stale templates from the installed symlink. Caught by the 3a merge (template byte-baseline tests), fixed forward (`a918cac`), 15/15 template tests green.

---

## Follow-up Cards Filed (kanban, tag `audit-2026-11-13`)

Completed in-run (closed):
- Route `_get_all_notes_as_results` through core note-index helpers — done (`f552ae9`, grep output byte-identical via before/after diff)
- Rewrite SECURITY.md shared-library prose for `parsidion.core.*` — done (`7b0e11b`)

Left as deliberate backlog:
- ARC-204 residual: extract helpers from remaining complexity hotspots (`vault_merge`/installer/CLI mains, `parsight_search`, `summarize_sessions`, ReadingPane) — playbook effort cap ("top 3-4")
- Split HUDPanel.tsx into smaller components — playbook "only if trivial" gate
- Wire doc-links check into `make checkall` — decision item for Paul

---

## Next Steps

1. Review `ARC-206` post-push verification when the push is approved.
2. Re-run `/audit` for an updated AUDIT.md reflecting current state (the parsight complexity numbers update after the merge-to-main reindex).
3. The three deliberate backlog cards above are the record of capped/deferred scope; the enhancement backlog (ENH-067, ENH-068) is untouched and tracked by `/enhancement-all`.
