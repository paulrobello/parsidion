# Audit Remediation Report

> **Project**: parsidion
> **Audit Date**: 2026-07-28
> **Remediation Date**: 2026-07-29
> **Base commit**: `8e5d549` → branch `fix/audit-remediation` (65 commits)
> **Severity Filter Applied**: all
> **Plan Source**: AUDIT.md `## Remediation Plan` + AUDIT-REMEDIATION-PLAN.md playbook
> **Implementation Model**: Opus 5 (all fix agents), orchestrated across 5 sequential/parallel waves

---

## Execution Summary

| Phase | Status | Agent(s) | Targeted | Resolved | Partial | Manual |
|-------|--------|----------|----------|----------|---------|--------|
| 1 — Critical Security | ✅ | fix-security | 5 | 5 | 0 | 2 (vault-side follow-ups) |
| 2 — Critical Architecture | ✅ | fix-architecture | 5 | 5 | 0 | 0 |
| 3b — Architecture (remaining) | ✅ | fix-architecture ×3 (Python-installer, Python-scripts, Visualizer) | ~38 | 33 | 5 | 0 |
| 3c — Code Quality | ✅ | fix-code-quality | ~18 | 15 | 3 | 0 |
| 3a — Security (remaining) | ✅ | fix-security | ~19 | 18 | 0 | 0 |
| 3d — Documentation | ✅ | fix-documentation | 43 | 40 | 1 | 2 (Python-owned, reported) |
| 4 — Verification | ✅ | orchestrator (make checkall) | — | — | — | — |
| 5 — Deferred Restructures | ⏭️ Optional | — | 4 | 0 | — | needs approval |

**Overall**: ~116 of 143 findings resolved, ~9 partial, ~14 deferred (Phase 5 + large refactors), 4 require human follow-up (none blocking). The full `make checkall` gate is **green and non-mutating**.

> ✅ **Status (updated 2026-07-30): ALL items resolved.** Every Phase-5
> restructure, partial, and manual follow-up listed below has since landed
> (commits through 2026-07-30) or been verified on the live vault. The only
> remaining recommendation is to re-run `/audit` for a fresh baseline.

## 2026-07-30 Reconciliation

Closed since the 2026-07-29 report above:

**Phase 5 restructures (all done):**
- **ARC-004** — flat scripts dir split into the `scripts/core/` package behind flat re-export shims; stdlib-only gate enforced by `tests/test_stdlib_only.py`.
- **ARC-008 / QA-003** — `vault_doctor.py` decomposed 3,127 → 180 LOC into a `doctor/` package.
- **ARC-009** — `summarize_sessions.py` decomposed 2,242 → 1,141 LOC.
- **ARC-017 / QA-002** — `install()`/`uninstall()` rebuilt on the `StepList` transaction primitive (CC 54→7 / 40→22).

**Partials (deferred halves landed):**
- **ARC-037** — `useVisualizerState` God-hook split into focused slices.
- **ARC-020** — installer-side `merge_codex/gemini_hooks` collapse.
- **ARC-023** — `vault_fs`↔`vault_hooks` import cycle broken.
- **ARC-024** — leaf-import migration finished across `parmem_backend`/`ai_backend`/`session_start_hook`.
- **ARC-038** — `build_graph.py` now emits `graph.schema.json` (the Python half).
- **QA-005** — **all 12 installer `subprocess` calls bounded with `timeout=`** (AST-verified 2026-07-30: 0 unbounded across `installer/` + `install.py`). The earlier "installer sites deferred" note is obsolete.
- **QA-007 / QA-013 / QA-014** — observable-state test assertions + GraphCanvas split + CC reductions.

**Manual follow-ups (verified on the live vault, 2026-07-30):**
- **SEC-101** — `~/ParsidionVault/.git/hooks/post-merge` regenerated; carries the current `# parsidion post-merge hook` marker (legacy `parsidion-cc` marker gone).
- **SEC-104** — the four sensitive files are no longer tracked in `~/ParsidionVault` (`git ls-files` returns none).
- **DOC-039 / DOC-040** — installer docstrings + `vault_common` re-export surface completed.

> Note: the 2026-07-29 "Partial / Deferred" and "Requires Manual Intervention"
> sections below are retained as the historical remediation record; treat them
> as resolved per this reconciliation.

> **What "partial" means here**: the high-value, correctness-bearing part of each partial issue landed (e.g. ARC-037 shipped its perf wins + betweenness extraction; only the organizational hook *split* is deferred). Deferred items are listed under Next Steps.

---

## Resolved Issues ✅

### Security (Phase 1 + 3a) — SEC-101 … SEC-132
- **SEC-101** `installer/vault.py` — added `--no-project` to the post-merge `build_embeddings` line; legacy `parsidion-cc` marker now regenerates stale hooks instead of skipping; `pyproject.toml`/`uv.toml`/`setup.py`/`.venv/` added to vault `.gitignore`.
- **SEC-102** visualizer — `requireToken()` (constant-time) on every GET; loopback bind (`-H 127.0.0.1`); guards added to `/api/stats` (closes QA-011/SEC-118/SEC-119); corrected docstring; +14 tests.
- **SEC-103** `templates/config.yaml` — **per maintainer decision**, reverted `anthropic_env` to Anthropic defaults; Z.ai/GLM values preserved as a commented gateway example. Unblocks DOC-023.
- **SEC-104** `installer/vault.py` — `.gitignore` globs + line-wise membership test (no more `# config.yaml` false-suppression).
- **SEC-105** `installer/hooks.py` — `merge_hooks` bails on parse failure (no more reset-to-`{}`); added reusable `_atomic_write_json` + `settings.json.bak`.
- **SEC-106** `vault_index.py`/`vault_metrics.py` — escaping symlinks skipped in vault walks; `Templates/` preserved.
- **SEC-107** `summarize_sessions.py` — merge path now validates frontmatter + containment + backup + atomic write.
- **SEC-108** `session_start_hook`/`post_compact_hook`/`research-agent.md` — untrusted-content framing; removed the comply-instruction in post_compact.
- **SEC-109/110/112/114** — 0600/0700 permission hardening on the queue, logs, configs, and vault root (code halves + a `vault_doctor --fix-permissions` migration).
- **SEC-111** — all transcript readers byte-bounded via shared `vault_fs.read_last_n_lines`.
- **SEC-115** `vault_merge.py` — both note bodies inlined; untrusted preamble; strengthened output guard.
- **SEC-116** `installer/skill.py` — symlink-escape refused; instructions-block + `[features] hooks=true` now reverted on disconnect (closes ARC-022 asymmetry).
- **SEC-117** `ai_backend.py` — `codex_cli.command` gated via `shutil.which`; `danger-full-access` opt-in; **repointed at shared `subproc_util.run_with_pgkill`** (closes SEC-122/ARC-048f drift).
- **SEC-120** — generic client error messages on graph/summarize routes.
- **SEC-121/123/124/126/126b/127b/128/130/131** — low-severity hardening batch (transcript allowlist, prompt-on-stdin, quoted hook/cron/plist, real-queue lock, daily-note flock, conservative crontab handling, `--` separators, consolidated containment helper, `--summarizer-hour` range check).

### Architecture (Phase 2 + 3b) — ARC-001 … ARC-048
- **ARC-006** `parsidion-mcp/Makefile` — `checkall` is now **non-mutating** (the keystone fix; verified `git status` unchanged across `make checkall`).
- **ARC-007** `.github/workflows/ci.yml` — added visualizer, pi-extension, graph, and wheel-install CI jobs; `bun run build` added to `visualizer-check`.
- **ARC-001** `pyproject.toml` — 7 missing modules added to `py-modules`; clean-room wheel import verified; stale `build/lib/` removed.
- **ARC-002** `note/route.ts` — POST/PUT read `vault` from the body (no more silent cross-vault overwrites); test-first.
- **ARC-003** `installer/skill.py` — `disconnect` no longer tears down shared infrastructure; new `--purge-config` gates `vaults.yaml` deletion.
- **ARC-005/010/011/012/013/014/015/018/019/021/022/023/025/027/028/029/030/031/033/034/035/036/038(viz)/039/040/041/042/043/045/046/047/048** — see commit log; highlights: ARC-015 streams the 47.5 MB `graph.json` with ETag/304 + a delta endpoint; ARC-018 atomic+flock+backup on `settings.json` (13 sites); ARC-019 persists custom `--vault` to `vaults.yaml`; ARC-021 MCP resolves scripts from the package + accepts `vault`; ARC-030 `FailureReason` enum (non-retryable dead-letters on attempt 1); ARC-034 deep-copy config + lru_cache(8).

### Code Quality (Phase 3c) — QA-001 … QA-022
- **QA-001** — stdin/stdout contract tests for `subagent_stop_hook` + `post_compact_hook` (both were 0% coverage).
- **QA-004/ARC-016** — `vaultResolver.test.ts` (29 cases incl. precedence) + per-route auth/traversal tests for every route.
- **QA-006** — async `findNote` extracted to `lib/` (de-triplicated; closes QA-012 for history/diff).
- **QA-008/ARC-020** — 5 codex/gemini hooks collapsed into a shared `agent_adapter` registry (+ observability: they now emit hook events).
- **QA-009/010/012/016/017/018/019/020/021/022** — guarded optional imports, atomic writes, async fs, vault_review tests, etc.
- **DOC-003 (code half)** — `update_index.py` passes `--no-daily` so Daily exclusion actually works.

### Documentation (Phase 3d) — DOC-001 … DOC-040
- All 40 DOC issues addressed (37 fully resolved). Highlights: DOC-001 (README no longer truncates the protective `.gitignore`), DOC-002 (`--pdf` phantom removed), DOC-003 (Daily behavior corrected), DOC-007/008 (dead symbol + wrong env-var fixed in CLAUDE.md), DOC-014 (`vault_links` signatures), DOC-016 (parsidion-mcp README created). Plus SEC-132 (visualizer in SECURITY.md scope) and ARC-026/032/044.

---

## Partial / Deferred ⏭️

> ✅ **Resolved 2026-07-30** — see the reconciliation section above. Retained
> below as the historical record of what the 2026-07-29 waves deferred.

**Phase 5 restructures (optional, separately approved — not attempted):**
- **ARC-004** split the 49-file flat scripts dir into a real package.
- **ARC-008 / QA-003** decompose `vault_doctor.py` (3,127 LOC).
- **ARC-009** decompose `summarize_sessions.py` (2,242 LOC).
- **ARC-017 / QA-002** rebuild `install()`/`uninstall()` on a shared step list with `undo()`.

**Partial (high-value half landed; organizational remainder deferred):**
- **ARC-037** — perf wins (slider debounce, typed-array edges, betweenness extraction) done; full `useVisualizerState` hook split deferred.
- **ARC-020** — adapter registry done; installer-side `merge_codex/gemini_hooks` collapse + pi unification deferred.
- **ARC-023** — cycle 2 resolved; cycles 1 & 3 are already lazy-both-ways (working solution, no actionable top-level cycle).
- **ARC-024** — `parmem_backend` migrated to leaf import; `ai_backend`/`session_start_hook` leaf-import migration deferred (pure ~15 ms perf win, 80+ references).
- **ARC-038** — visualizer-side contract fixture + test done; Python JSON-Schema emission from `build_graph.py` deferred.
- **QA-005** — 4 of ~7 subprocess sites bounded; installer sites (`vault.py`, `schedule.py`, `skill.py`) deferred.
- **QA-007** — worst offender strengthened; remaining triviality cases deferred.
- **QA-013 / QA-014** — GraphCanvas split + CC-reduction (large refactors) deferred.

---

## Requires Manual Intervention 🔧

> ✅ **Resolved 2026-07-30** — SEC-101 and SEC-104 verified done on the live
> vault; DOC-039/DOC-040 landed in code. Retained below as the historical
> record of the original follow-ups.

These are **not code defects** — the fixes are committed. They are follow-up actions on your live vault/machine that are outside the repo's scope (and outside automated change per security policy).

### SEC-101 — regenerate the live vault post-merge hook
The installed hook at `~/ParsidionVault/.git/hooks/post-merge` still carries the legacy `# parsidion-cc post-merge hook` marker and dead script paths. After merging this branch, run:
```bash
uv run install.py --force --yes
```
The installer will now regenerate it (the legacy-marker check was fixed). Until then, post-merge index rebuild on this machine is silently dead.

### SEC-104 — untrack four already-committed sensitive files
These are in `~/ParsidionVault` git history (session IDs, absolute transcript paths, project names):
```bash
git -C ~/ParsidionVault rm --cached pending_summaries.jsonl.bak \
  pending_summaries.jsonl.bak-20260712-092800 \
  dead_letters.jsonl.bak-20260712-092800 conflicts/report.json
```
(The new `.gitignore` globs prevent re-adding; this removes them from the index. History scrubbing is separate if a remote is ever added.)

### Reported for a Python follow-up (not docs)
- **DOC-039** — `installer/` docstring coverage at 24% vs 51% average (optionally enable ruff `D` rules).
- **DOC-040** — `vault_common.py` claims "all public symbols re-exported" but omits 5 (e.g. `atomic_write_text`); soften the claim or add the re-exports.

---

## Verification Results

| Check | Result |
|-------|--------|
| `ruff format --check .` | ✅ 108 files formatted |
| `ruff check .` | ✅ All checks passed |
| `pyright .` | ✅ 0 errors, 0 warnings, 0 informations |
| `pytest tests/` | ✅ **1010 passed**, 3 skipped (was 840 at audit → +170) |
| `make test-graph` | ✅ 6 passed |
| `visualizer-check` (tsc + lint + test + build) | ✅ **226 tests** pass (was 60 → +166), build clean |
| `checkall-mcp` | ✅ 53 passed |
| **Non-mutation** | ✅ `git status` identical before/after `make checkall` (ARC-006 proof) |

**No regressions.** The gate is strictly greener than the audit baseline. (Two waves committed code the per-agent verifier didn't catch was slightly off — unformatted files, an `importlib.util`/`anyio` pyright error, a `tools/` extraPath gap, a str/Path test typing — each caught and fixed by the orchestrator's authoritative `make checkall` before proceeding.)

---

## Files Changed

~110 files across `installer/`, `install.py`, `skills/parsidion/scripts/`, `skills/parsidion/templates/`, `visualizer/**`, `parsidion-mcp/src/`, `tests/`, `docs/`, `README.md`, `CLAUDE.md`, `SECURITY.md`, `CONTRIBUTING.md`, `pyproject.toml`, `Makefile`, `.github/workflows/ci.yml`. 65 atomic commits on `fix/audit-remediation` (each with its issue ID in the subject). Full list: `git diff --stat 8e5d549..HEAD`.

---

## Next Steps

1. **Re-run `/audit`** to regenerate `AUDIT.md` against current state — the 2026-07-29 report predates all the 2026-07-30 work, so a fresh baseline is the only way to confirm the finding count has dropped to near-zero (expected: only items introduced or surfaced since).
2. The Phase-5 restructures, partials, and manual follow-ups (Next Steps #1–4 from the 2026-07-29 report) are all closed per the reconciliation section above — no further action.
3. Optional housekeeping: the stale par-mem worktree references (`fix/embed-singleton`, `fix/qa-007-013`) shown at session start are gone from `git worktree list`; their commits are merged to `main`.
