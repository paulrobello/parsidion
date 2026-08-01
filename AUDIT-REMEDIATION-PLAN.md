# AUDIT-REMEDIATION-PLAN.md

> Per-issue execution playbook for `/fix-audit`. Every entry is ordered to match the
> `## Remediation Plan` phases in `AUDIT.md` and is executable by an Opus 5 fix agent
> **without re-deriving the analysis**. Run `make checkall` after each phase (the gate
> is currently fully green: 1281 + 30 + 60 tests, 0 lint/type errors).
>
> **Global conventions**
> - This repo's gate is `make checkall` (fmt-check + lint + typecheck + test + test-graph + visualizer-check + checkall-mcp).
> - The hook/CLI scripts are **stdlib-only Python** (enforced by `tests/test_stdlib_only.py`, which poisons `rich`/`fastembed`/`sqlite_vec`/`anyio`/`yaml`/`numpy`/`PIL` in `sys.modules`). Any new stdlib import is fine; any third-party import in a hook/CLI/core module **fails the gate**.
> - par-mem is indexed (`repo_id: parsidion`). For multi-site fixes, the listed `get_impact` / `get_symbol_context` / `find_symbol` queries enumerate all callers — do not grep.
> - **Do not commit** — `/fix-audit` leaves verification to its own wrap-up; this plan describes edits, not commits.

---

## Phase 1 — Critical Security (Sequential, Blocking)

### SEC-P001 — Port the allowlist vault resolver from TypeScript to Python
- **Files**: `skills/parsidion/scripts/core/vault_path.py` (`_resolve_vault_reference` ~L306-337; `_VAULT_FORBIDDEN_PREFIXES` ~L229-259; `resolve_vault` ~L340-375); reference impl `visualizer/lib/vaultResolver.ts:156-200`; tests `tests/test_vault_resolver_parity.py`, `tests/test_vault_common.py`, `tests/test_vault_dirs_sync.py`; fixture `tests/fixtures/parity/vault-resolution.json`
- **Steps**:
  1. Read `visualizer/lib/vaultResolver.ts:156-200` (`resolveVault`) — the hardened allowlist logic to mirror: accept only (a) named vaults resolved from `~/.config/parsidion/vaults.yaml`, (b) the default vault by its own path, and (c) `CLAUDE_VAULT`/`.claude/vault` references **only when the resolved path is itself one of the named/allowlisted vaults**.
  2. In `core/vault_path.py`, rewrite `_resolve_vault_reference` so the `cwd/.claude/vault` and `CLAUDE_VAULT` branches no longer accept "any existing non-forbidden path" — instead they resolve the reference and then require it to match a `vaults.yaml` entry or the default vault root. Drop the `_VAULT_FORBIDDEN_PREFIXES` denylist branch that lets arbitrary non-system paths through (keep the system-path guard as defense-in-depth).
  3. Add a helper `_named_vault_paths() -> set[Path]` that reads `vaults.yaml` (mirror the TS `loadAllowedVaults`), so the allowlist is computed from one source.
  4. Update `tests/fixtures/parity/vault-resolution.json` to lock the new shared behavior: add vectors where an arbitrary `.claude/vault`/`CLAUDE_VAULT` path resolves to **default** (Python) / is **rejected** (the narrow TS behavior we are now matching). Then run `make parity-fixtures` to regenerate and confirm both resolvers agree.
  5. Update any parity-test expectations in `tests/test_vault_resolver_parity.py` / `tests/test_vault_common.py` / `tests/test_vault_dirs_sync.py` that currently assert denylist acceptance.
- **Method**: The TS twin already won this hardening; this is a back-port, not a design. The parity fixture is the contract — both resolvers must agree on every vector, which is exactly what prevents reintroducing the gap. Watch for `update_index.py`'s `--vault-path` flow (see ARC-003) which relies on explicit CLI args — that path uses the `explicit` parameter, not the `.claude/vault` channel, so it is unaffected by tightening the reference resolver. **par-mem**: `get_symbol_context("resolve_vault", repository_id="parsidion")` to confirm every caller; `get_impact("resolve_vault", repository_id="parsidion")` for blast radius before editing.
- **Verify**: `make checkall` (must stay green); then a manual exploit check: create a temp repo with `.claude/vault` pointing at `/tmp/parsidion-evil-test`, run `python -c "from skills.parsidion.scripts.core.vault_path import resolve_vault; print(resolve_vault(cwd='/tmp/<that-repo>'))"` and confirm it resolves to the **default** vault, not the evil path.

---

## Phase 2 — Critical/Blocking Architecture (Sequential, Blocking)

### ARC-001 — Narrow the `vault_common` re-export facade + begin caller migration
- **Files**: `skills/parsidion/scripts/vault_common.py:1-248`; flat shims `vault_config.py`/`vault_path.py`/`vault_fs.py`/`vault_hooks.py`/`vault_index.py`/`vault_links.py`/`vault_metrics.py`/`vault_adaptive.py`/`vault_health.py`
- **Steps** (this Phase-2 deliverable is the **foundation**, not the full 80-caller migration — that continues incrementally):
  1. In `vault_common.py`, add a `# deprecated: import directly from core.<module>` comment above each re-export line and above the `__all__` entries. Do **not** remove any re-export yet (preserves the 80 callers).
  2. Establish the policy in code: add a module docstring note that **new** symbols must not be added to the re-export surface — new code imports from `core.X` directly.
  3. Migrate a tractable first batch of callers to import from `core.*` directly (pick the smallest, most self-contained consumers first). Use the par-mem work-list below to enumerate them.
  4. Leave `vault_common` re-exporting for the external contract (`parsidion-mcp` and any out-of-tree callers) — it becomes a compatibility shim over time.
- **Method**: The decomposition's payoff depends on the import surface narrowing, but ripping out 80 callers at once is high-risk. The incremental approach (deprecate → migrate batch → repeat) keeps the gate green at every step. **par-mem work-list**: `analyze_relationships("vault_common", query_type="imports", repository_id="parsidion")` lists every importer; `find_symbol("vault_common", repository_id="parsidion")` confirms the file. Do **not** collapse the shims to `import *` yet — that is QA-005, which depends on this landing.
- **Verify**: `make checkall` after the docstring/policy change, and again after each migrated batch. The stdlib-only gate must stay green (no new third-party imports).

---

## Phase 3a — Security (remaining)

### SEC-P002 — TS `envWithoutClaudecode()` for `spawnSummarizer`
- **Files**: `visualizer/lib/vaultStatsServer.ts:276-287`; reference `skills/parsidion/scripts/vault_hooks.py` (`_SAFE_ENV_KEYS` / `env_without_claudecode`)
- **Steps**:
  1. In `visualizer/lib/` add a small `env.ts` exporting `envWithoutClaudecode()` that builds an env object from an allowlist mirroring `_SAFE_ENV_KEYS` (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_CUSTOM_HEADERS`, `ANTHROPIC_DEFAULT_*_MODEL`, `API_TIMEOUT_MS`, `HTTPS_PROXY`, `HTTP_PROXY`, `PATH`, `HOME`, plus the few the summarizer subprocess needs). Drop `CLAUDECODE`.
  2. In `vaultStatsServer.ts:276-287` replace `{ ...process.env }; delete env.CLAUDECODE` with `envWithoutClaudecode()`.
  3. Add a `visualizer/lib/env.test.ts` asserting `CLAUDECODE` is absent and a known secret-shaped var is dropped.
- **Method**: Every Python spawn already allowlists; this closes the one TS path that inherits the full parent env. Keep the allowlist small to avoid leaking whatever the dev server carries.
- **Verify**: `cd visualizer && bun test && bun run lint && bunx tsc --noEmit` (or `make visualizer-check`).

### SEC-P003 — Close TOCTOU window in MCP `vault_write`
- **Files**: `parsidion-mcp/src/parsidion_mcp/tools/notes.py:14-37,72-102`
- **Steps**:
  1. Replace the `Path(path).resolve()` + `is_relative_to(vault_root)` then `write_text` sequence with a check-and-open in one step: after resolving, open the target via `os.open(..., O_CREAT|O_WRONLY|O_NOFOLLOW, 0o644)` and `os.fdopen`/write, **or** reuse `vault_common.is_path_inside_vault` and write through `atomic_write_text` after re-validating inside the lock.
  2. If reusing the Python helper, import `is_path_inside_vault` from the installed `parsidion[search]` editable dep (the MCP already depends on it).
- **Method**: Exploitation requires an attacker already inside the vault (authenticated to MCP), so blast radius is small — but the fix is cheap and removes the class. `O_NOFOLLOW` blocks the symlink-swap on the leaf.
- **Verify**: `cd parsidion-mcp && make checkall` (60 tests, 92% coverage). Add a test that writes through a path whose parent is swapped to a symlink and asserts the write is rejected.

### SEC-P004 / SEC-P005 — No action (documented threat-model notes)
- **Files**: `skills/parsidion/scripts/vault_embed_serve.py` (SEC-P004), `skills/parsidion/scripts/vault_new.py:246-248` (SEC-P005)
- **Steps**: None required for the current single-user threat model. Optional: add a one-line code comment at each site pointing to this AUDIT note so a future shared-machine or config-sourced-editor change re-triggers review.
- **Verify**: N/A (no behavior change); `make checkall` to confirm comments don't break anything.

---

## Phase 3b — Architecture (remaining)

### ARC-002 — Decouple `install.py` tests from private-name patches
- **Files**: `install.py:43-101,172-193`; tests that `monkeypatch.setattr(install, "_ask", …)` / `monkeypatch.setattr(install, "_FORBIDDEN_PREFIXES", …)` (find via `grep -rn 'monkeypatch.setattr(install' tests/`)
- **Steps**:
  1. Find the patching tests: `grep -rn "setattr(install" tests/` and `grep -rn "install\._ask\|install\._FORBIDDEN_PREFIXES" tests/`.
  2. Rewrite each to patch at the source: `monkeypatch.setattr("installer.ui._ask", …)` (or inject via a fixture/parameter).
  3. Remove the `_ask`/`_FORBIDDEN_PREFIXES`/etc. namespace imports from `install.py:43-101` and the explanatory comment block. Move `validate_vault_path`/`prompt_vault_path` into `installer/` if a natural home exists; otherwise leave in place but no longer re-imported for test convenience.
- **Method**: Python resolves bare names at call time, so patching `installer.ui._ask` affects `install.py` functions that call `_ask` *only if they reference it as a module attribute of `installer.ui`*. Verify each rewritten patch actually changes behavior (the test must still fail when the patch is removed). **par-mem**: `get_symbol_context("_ask", repository_id="parsidion")`.
- **Verify**: `make checkall`. Specifically confirm the install tests still exercise the patched behavior (flip a patch off and see a test fail, then restore).

### ARC-003 — Remove the `VAULT_ROOT` global + `lru_cache` side-channel *(depends on SEC-P001)*
- **Files**: `skills/parsidion/scripts/core/vault_path.py:384-428` (`_resolve_vault_cached`, the `VAULT_ROOT` mutation branch ~L410-428); `skills/parsidion/scripts/update_index.py` (the `vault_common.VAULT_ROOT = vault_path` mutation + `resolve_vault.cache_clear()` call)
- **Steps**:
  1. In `update_index.py`, replace the `VAULT_ROOT = vault_path` global mutation for `--vault-path` with passing the path explicitly: thread it as the `explicit` argument to `resolve_vault()` (the resolver already accepts `explicit`).
  2. In `core/vault_path.py`, deprecate the branch 4 `VAULT_ROOT` read (emit a `DeprecationWarning`), and stop requiring callers to call `resolve_vault.cache_clear()`. Remove the `# type: ignore[attr-defined]` `cache_clear` re-exposure shim once no caller uses it.
  3. After a release cycle, remove branch 4 entirely.
- **Method**: The cache key is `(explicit, normalized_cwd)`; threading `explicit` through `update_index.py` removes the need for the global mutation entirely, so the cache and global can no longer disagree. Do this **after** SEC-P001 so the resolver is already allowlisted. **par-mem**: `get_impact("resolve_vault", repository_id="parsidion")`; `analyze_relationships("VAULT_ROOT", query_type="type_usages", repository_id="parsidion")`.
- **Verify**: `make checkall`; then `python skills/parsidion/scripts/update_index.py --vault-path /tmp/test-vault --dry-run` (or equivalent) and confirm the explicit path is honored without any global mutation.

### ARC-004 — Consolidate the duplicated hook-script maps
- **Files**: `skills/parsidion/scripts/agent_adapter.py:228-250` (`_CLAUDE_HOOK_SCRIPTS`, `_CODEX_HOOK_SCRIPTS`, `_GEMINI_HOOK_SCRIPTS`); `installer/paths.py:44-65` (same three dicts)
- **Steps**:
  1. Decide the dependency direction. Recommended: make `agent_adapter` the single source (it is the documented registry) and have `installer/paths.py` read the hook-script data **off the adapter descriptors** (the comment at `agent_adapter.py:228-230` already says this is the plan). If the `installer → skills/parsidion/scripts` import is undesirable, move the three dicts to a neutral module both import (e.g. `installer/hooks.py` or a shared constants module) and have `agent_adapter` re-export.
  2. Delete the duplicate copy. Add a test asserting the dicts are defined in exactly one place (`grep -rn "_CLAUDE_HOOK_SCRIPTS" --include=*.py` returns one definition).
- **Method**: The acknowledged footgun is silent drift; a test that asserts single-definition closes it permanently. Pick the option that does not create a circular import (`installer` and `skills/parsidion/scripts` already have a relationship via the install flow — verify with `analyze_relationships` before choosing).
- **Verify**: `make checkall`; then `uv run install.py --dry-run` for each runtime (`connect codex`, `connect gemini`) to confirm hook registration still resolves the correct script filenames.

### ARC-005 — Decompose CLI God-files into `cli/<tool>/<mode>.py` packages *(depends on QA-002)*
- **Files**: `skills/parsidion/scripts/vault_stats.py` (1244 LOC), `vault_search.py` (1228), `vault_merge.py` (1179), `update_index.py` (1081)
- **Steps**:
  1. **Do QA-002 first** (vault_stats dispatch table) — it is the foundation.
  2. For each CLI, extract each `run_*`/mode (and its helpers) into `cli/<tool>/<mode>.py`, mirroring the `doctor/` package layout. Leave the top-level `vault_<tool>.py` as a thin dispatcher that imports and registers subcommands.
  3. Keep the public CLI entrypoints (`vault-stats`, `vault-search`, etc., installed via `uv tool`) unchanged — only internal structure moves.
  4. Preserve the flat-shim convention if external code imports from these modules (check `analyze_relationships` first).
- **Method**: The `doctor/` (16 modules) and `summarizer/` (11 modules) packages are the proven template — copy that layout. The risk is breaking the installed entry points; keep `pyproject.toml` `[project.scripts]` / `[tool.uv]` entry points pointing at the same `main`. **par-mem**: `list_symbols(file_path="vault_stats.py", repository_id="parsidion")` to scope each `run_*`.
- **Verify**: `make checkall`; then run a few CLIs end-to-end: `vault-stats --summary`, `vault-stats --dashboard`, `vault-search "test" -n 3`, `vault-merge --scan` (all via the installed command after `uv tool install --editable ".[tools]"`).

### ARC-006 — Decompose `session_start_hook.py` into a `session_start/` subpackage
- **Files**: `skills/parsidion/scripts/session_start_hook.py` (1253 LOC, 25 functions)
- **Steps**:
  1. Group functions into cohesive modules under `session_start/`: `ai_selector.py` (AI mode + lock/cooldown/stamp — ~6 functions), `graph_retrieval.py` (`_enrich_with_graph`, `_graph_neighbors`, `_rank_by_graph`, `_apply_graph_retrieval`), `seed_selection.py` (`_build_candidates`, `_select_seed_notes`, `_rank_by_usefulness`), `context.py` (`_assemble_context`, `build_session_context`).
  2. Leave `session_start_hook.py` as the orchestrator (~300 LOC) that reads stdin and calls `build_session_context`.
  3. Maintain the flat re-export convention **only if** tests/external code import these names directly — check first; if they import via `session_start_hook.X`, keep a shim.
- **Method**: This is the most-edited hook (high churn); decomposition reduces per-edit context load. The concerns are already coherent, so extraction is mechanical. **par-mem**: `get_symbol_context("build_session_context", repository_id="parsidion")` for the call graph.
- **Verify**: `make checkall`; then test the hook manually per CLAUDE.md: `python skills/parsidion/scripts/session_start_hook.py <<'EOF' {"cwd": "/Users/probello/Repos/parsidion"} EOF`.

### ARC-007 — Reconcile cross-language vault-resolution parity *(depends on SEC-P001)*
- **Files**: `skills/parsidion/scripts/core/vault_path.py:340-375`; `visualizer/lib/vaultResolver.ts:174+`; `tests/fixtures/parity/vault-resolution.json`
- **Steps**:
  1. After SEC-P001, decide: either (a) add the missing channels (`cwd/.claude/vault`, `CLAUDE_VAULT`) to the TS resolver and a parity vector for each, or (b) document the TS resolver as a deliberately-narrower allowlist view and defer full unification to **ENH-009** (serve resolution through parsidion-mcp).
  2. If (a): extend `visualizer/lib/vaultResolver.ts` and regenerate the fixture (`make parity-fixtures`).
  3. If (b): add a code comment at both resolvers and a `docs/` note pointing to ENH-009; add parity vectors that assert the *current* (narrower TS) behavior so drift is still caught.
- **Method**: The parity fixture is the single source of truth both tests consume; whatever you decide, encode it there. Prefer (b) unless the visualizer actually needs the extra channels today.
- **Verify**: `make checkall` + `make parity-fixtures-check`.

### ARC-008 — Right-size visualizer stack + decompose `Home`
- **Files**: `visualizer/package.json`; `visualizer/app/page.tsx` (554 LOC, 34 hooks)
- **Steps**:
  1. Decide Next.js-vs-Vite: confirm whether any SSR/SEO is actually used (the graph is read from a local file via API routes — likely no SSR benefit). If not justified, migrating to Vite+React SPA removes RSC-boundary complexity and the `bun run build` server/client-violation gate. (This is a large change — consider scoping to a follow-up.)
  2. Regardless of (1), decompose `Home`: extract `<GraphPanel>`, `<SidebarPanel>`, `<ReadingPanePanel>` containers and/or move shared state into a small store (Zustand/Jotai).
  3. Pin exact versions (drop `^`) for rendering-critical deps (`sigma`, `graphology`, `next`, `react`) in `package.json` to prevent auto-advancing majors.
- **Method**: `app/page.tsx::Home` is a top-10 bridge symbol (betweenness 0.021); the 34 hooks indicate one component absorbing state-management complexity. Extraction is the lower-risk half; the framework swap is optional and large.
- **Verify**: `make visualizer-check` (tsc + lint + bun test + build).

### ARC-009 — Broad-exception auditability sweep
- **Files**: ~30 files under `skills/parsidion/scripts/` (65 `except Exception`, 19 `except Exception: pass`)
- **Steps**:
  1. Enumerate: `grep -rn "except Exception" skills/parsidion/scripts/ | grep -c pass` and `grep -rln "noqa: BLE001" skills/parsidion/scripts/`.
  2. For each `except Exception: pass` where the swallow is best-effort (not where logging itself could fail), add a one-line `print(f"<context>: {exc}", file=sys.stderr)` before `pass`. Reserve bare `pass` for cases where stderr writing is unsafe.
  3. Do not narrow exception types in this pass unless the case is obvious — that is a separate review. Keep all `# noqa: BLE001` markers with their reasons.
- **Method**: The goal is auditability, not correctness — making diagnostics reachable when a hook silently misbehaves. Hooks must still never fail closed, so the `print` must be best-effort.
- **Verify**: `make checkall`. Spot-check: temporarily raise inside a best-effort path and confirm the stderr line appears.

### ARC-010 — Lazy adapter registration
- **Files**: `skills/parsidion/scripts/agent_adapter.py:323` (`_register_builtin_adapters()`)
- **Steps**: Move registration from import-time to first-access (lazy), mirroring `_load_external_adapters`; or add a `configure()` entry point tests call explicitly. Ensure tests that depend on a clean registry still call `reset_external_adapters()`.
- **Verify**: `make checkall`.

### ARC-011 — Migrate `vault_tui.py` to `core/` (or document the deviation)
- **Files**: `skills/parsidion/scripts/vault_tui.py` (266 LOC, at scripts root, no `core/vault_tui.py`)
- **Steps**: Either move it to `core/vault_tui.py` with a flat shim (curses is stdlib, so the constraint is satisfiable) **or** add a one-line code comment explaining why it deliberately stays at the scripts root. Update `tests/test_stdlib_only.py`'s module list if moved.
- **Verify**: `make checkall` (the stdlib-only gate covers the moved module).

### ARC-012 — Restrict shim re-exports to defined symbols
- **Files**: `skills/parsidion/scripts/vault_config.py` (flat shim; re-exports `Any`, `Path`, `math`, `re`, `sys`, `functools`, `annotations`)
- **Steps**: In each flat shim, drop re-exports of transitively-imported stdlib names; keep only symbols *defined* in the corresponding `core/` module. Update any caller that relied on `from vault_config import math` to `import math` itself (find via `grep -rn "from vault_config import" .`).
- **Verify**: `make checkall`.

### ARC-013 — Lower `requires-python` floor
- **Files**: `pyproject.toml` (`requires-python = ">=3.13"`)
- **Steps**: First confirm no load-bearing 3.13-only syntax is used (`grep` for recent features; the stdlib-only scripts are conservative). If safe, lower to `>=3.11`. Run the gate on 3.11/3.12 if available.
- **Verify**: `make checkall`; optionally `uv run --python 3.12 pytest tests/` if a 3.12 interpreter is installed.

### ARC-014 — Move pyright to pre-push stage
- **Files**: `.pre-commit-config.yaml`
- **Steps**: Move the pyright hook's `stages` to `pre-push` (keep `ruff format`/`ruff check`/gitleaks at commit). Ensure `default_stages` is consistent.
- **Verify**: `uv run pre-commit run --all-files` still passes; `git commit` is faster.

### ARC-015 — Name `_first_summary` magic numbers
- **Files**: `skills/parsidion/scripts/agent_adapter.py:348-353`
- **Steps**: Replace `len(text.strip()) > 50` and `text[:500]` with named constants (`_MIN_SUMMARY_LEN = 50`, `_MAX_SUMMARY_CHARS = 500`) with a comment stating the rationale (token budget).
- **Verify**: `make checkall`.

### ARC-016 — Split README/CHANGELOG *(after DOC-001/002/003)*
- **Files**: `README.md` (67KB), `CHANGELOG.md` (93KB)
- **Steps**: Move deep reference material out of the README into `docs/` sections (the README keeps quickstart + overview + pointers). Archive old CHANGELOG entries per major version (e.g. `CHANGELOG-0.x.md`). Do this **after** the DOC content fixes so they land on the pre-split file first.
- **Verify**: `make checkall`; manual read of the slimmed README.

### ARC-017 / DOC-012 — Archive `MEMORY_REPORT.md` (merged finding)
- **Files**: `MEMORY_REPORT.md` (gitignored, stale March 2026)
- **Steps**: Optional — `git mv` is unnecessary (it is gitignored). Either move to `docs/archive/MEMORY_REPORT.md` with a "Historical, March 2026" header and un-gitignore that path, or leave as-is. No published impact today.
- **Verify**: N/A; confirm still gitignored after any move (`git check-ignore`).

---

## Phase 3c — Code Quality (all)

### QA-001 — Consolidate duplicated `_backup_note`
- **Files**: `skills/parsidion/scripts/doctor/_state.py:249` (`_backup_note(vault, note_path)`); `skills/parsidion/scripts/summarizer/notes.py:397` (`_backup_note(note_path, vault)`); new shared helper in `skills/parsidion/scripts/core/vault_fs.py`
- **Steps**:
  1. Add `backup_note(vault, note_path)` to `core/vault_fs.py` (next to `atomic_write_text`) with a single canonical parameter order — pick `(note_path, vault)` or `(vault, note_path)` and document it. Implement the "first version of the day wins" `.trash/backup/<date>/<rel>` logic with explicit `raise OSError` on failure.
  2. In `doctor/_state.py`, replace the local `_backup_note` with a call to the shared helper wrapped in try/except (doctor's contract is "never raises" + per-run dedup set — keep the dedup set at the call site).
  3. In `summarizer/notes.py`, replace the local `_backup_note` with the shared helper (summarizer lets `OSError` propagate).
  4. Add a regression test asserting the shared helper's parameter order (call with swapped args and assert it raises/behaves as documented).
  5. Add `backup_note` to the relevant `core/vault_fs.py` `__all__` and the `vault_fs.py` flat shim's re-export (so `from vault_fs import backup_note` works).
- **Method**: This is the one place the `summarizer/*` split left a genuine inconsistency. The reversed parameter order is invisible to the type checker (both `Path`), so the regression test is the real guard. **par-mem**: `get_symbol_context("_backup_note", repository_id="parsidion")`.
- **Verify**: `make checkall`.

### QA-002 — `vault_stats:main` dispatch table *(foundational to ARC-005)*
- **Files**: `skills/parsidion/scripts/vault_stats.py:1014` (`main`)
- **Steps**:
  1. Extract the 15-mode argparse block into `_build_parser() -> argparse.ArgumentParser`.
  2. Build a dispatch table `_MODES = {"summary": (run_summary, needs_db=True), "timeline": (run_timeline, needs_db=False), ...}` covering all 18 modes.
  3. Replace the two parallel if/elif chains with a single loop over the table: for each requested mode, look up `(fn, needs_db)`, skip if `needs_db and conn is None`, else call `fn(...)`. Collapse the duplicated `no_mode` 13-flag enumeration into one computed expression.
- **Method**: This removes the "edit 3+ places to add a mode" hazard and is exactly the refactor ARC-005's package split will build on. Behavior must be identical — the table is a pure reorganization. The existing `tests/test_vault_stats.py` pins behavior.
- **Verify**: `make checkall`; then `vault-stats --summary`, `vault-stats --dashboard`, `vault-stats --timeline 30`, `vault-stats` (bare → default mode) all behave as before.

### QA-003 — Finish `summarize_sessions` split (state machine + lifted `main`)
- **Files**: `skills/parsidion/scripts/summarize_sessions.py` (`summarize_one` L? complexity 37; `main` L? complexity 34); `summarizer/` package
- **Steps**:
  1. Move `summarize_one`'s decision dispatch (write-gate → merge/skip/write → dead-letter classification → dedup → backlinks → progress) into a small state machine in a new `summarizer/pipeline.py` (or extend `summarizer/notes.py`), keyed off the existing `FailureReason` enum.
  2. Lift the argparse + config-resolution block out of `main` into `_build_parser()` / a config helper.
  3. Keep `summarize_one` and `main` as thin orchestrators calling the extracted pieces. Preserve the `summarizer/*` re-export shim in `summarize_sessions.py` for back-compat.
- **Method**: This is the repo's #1 hotspot (score 740/680); high churn means do it in small, gate-green commits. `tests/test_summarizer_queue_fixes.py` and `tests/test_dead_letter.py` pin the dispatch/dead-letter behavior. **par-mem**: `get_symbol_context("summarize_one", repository_id="parsidion")`.
- **Verify**: `make checkall`; then run the summarizer on a queued session: `env -u CLAUDECODE uv run --no-project ~/.claude/skills/parsidion/scripts/summarize_sessions.py --dry-run`.

### QA-004 — De-cluster Critical-band entrypoints (watch-list)
- **Files**: `doctor/worker.py:38` (`_repair_one` 38), `doctor/check.py:26` (`check_note` 35), `session_stop_hook.py:280` (`main` 34), `doctor/tags.py:327` (31), `installer/vault.py:668` (31), `vault_tui.py:113` (30), `html-to-md.py:153` (28), `doctor/orchestrator.py:264` (28), `install.py:54` (27), `pre_compact_hook.py:101` (26)
- **Steps** (pick the highest-value two; the rest are inherent):
  1. `session_stop_hook.main`: lift the guard sequence into `_should_skip(input_data) -> str | None` returning a skip-reason, so the validation chain reads as a list.
  2. `doctor/worker._repair_one`: wrap the status-icon/`repair_status` bookkeeping in a `_classify_repair_outcome(...)` helper (the 4 repair stages are already separate functions).
- **Method**: These are largely inherent complexity (security-guard chains, explicit pipelines). Treat as a watch-list, not a rewrite queue — do not force-complexity-reduce the security guards in `session_stop_hook.main`.
- **Verify**: `make checkall`.

### QA-005 — Tighten the shim re-export pattern *(depends on ARC-001)*
- **Files**: `skills/parsidion/scripts/vault_doctor.py` (16 re-exports), `summarize_sessions.py` (9), the 9 flat shims
- **Steps** (after ARC-001's surface-narrowing):
  1. Switch each flat shim from explicit per-name re-export to `from core.X import *  # noqa: F401,F403` plus an explicit `__all__` defined in the `core/` module (add `__all__` to each `core/X.py` if absent).
  2. Drop stdlib modules (`re`, `sys`, `math`, `functools`) from the re-export surface — callers should `import re` themselves.
  3. Stop re-exporting private helpers (`_CONFIG_SCHEMA`, `_clear_config_cache`) unless a real external caller needs them.
- **Method**: ARC-001 narrows the surface; this collapses what remains. Do **not** do this before ARC-001 or the two will collide on the same shim files.
- **Verify**: `make checkall` (the stdlib-only gate must stay green).

### QA-006 — Replace `!` non-null assertions with explicit guards
- **Files**: `visualizer/app/api/note/route.ts:39,103,223`; `note/diff/route.ts:42`; `note/history/route.ts:37` (`stem!`); `visualizer/components/GraphCanvas.tsx:231-232,367`; `lib/useForceLayout.ts:64-65`; `lib/graphDelta.ts:100-101`; `lib/useVaultFiles.ts:33`
- **Steps**:
  1. API routes: replace `stem!` with `if (!stem) return new Response('missing stem', { status: 400 })` before the `findNote(vaultRoot, stem)` call.
  2. `Map.get()!` cases: replace with `const x = map.get(k); if (!x) return;` where scope allows; add a brief comment where the assertion is provably safe (key just initialized in scope).
- **Method**: The `stem!` cases are a real (small) bug class — a missing query param dereferences `undefined`. The `Map.get()!` cases are safe today but fragile under refactor.
- **Verify**: `make visualizer-check`; add a route test that GETs `/api/note?vault=...` with no `stem` and asserts a 400.

### QA-007 — (Defer) structured logger for the visualizer
- **Files**: `visualizer/app/api/**/route.ts`, `lib/vaultStatsServer.ts`, `lib/searchServer.ts`, et al.
- **Steps**: Defer unless the visualizer grows a multi-user deployment. If/when: adopt `pino` (or a 20-line wrapper) and route all `console.error('[tag]', err)` through it with request IDs.
- **Verify**: N/A (deferred).

### QA-008 — (Optional) extract `GraphCanvas` interactions hook
- **Files**: `visualizer/components/GraphCanvas.tsx` (699 LOC, 24 hooks)
- **Steps**: Lift the context-menu and edge-pruning logic into `useGraphCanvasInteractions(data, sigma)`. The hook extractions (`useForceLayout`, `useSigmaInstance`, `useGraphControls`, `useGraphReducers`) already absorb most noise.
- **Verify**: `make visualizer-check`.

### QA-009 — (Tracked as ENH-013) eval dead-code sweep
- **Files**: `tools/eval/**` (`embed_eval_report.display_results`, `save_json_results`; per-evaluator `_load_inputs`; `evaluators/_base.version_stamp`)
- **Steps**: See ENH-013 plan (`docs/opus/ENH-013-eval-dead-code-sweep.md`). Wire-or-delete each genuinely-dead helper. The MCP-tool/JSX/Next-config/SSE-controller false positives are **not** dead — leave them.
- **Verify**: `cd tools/eval && python embed_eval_run.py --help` (or the eval entrypoint) still works.

### QA-010 — (Positive; no action) PEP 723 deps
- **Files**: `skills/parsidion/scripts/summarize_sessions.py:2-12`
- **Steps**: None. Optional: move the in-process-dedup comment out of the dependency list into the module docstring.
- **Verify**: N/A.

---

## Phase 3d — Documentation (all)

> The `fix-documentation` agent only modifies documentation files, README, and docstrings — **never core logic**. So DOC-002's remedy here is the doc-side correction; the optional flag *implementation* is a separate code task (ENH-A below), not a documentation fix.

### DOC-001 — Fix README "Latest release" version
- **Files**: `README.md:1084`
- **Steps**: Change "Latest release: **0.14.0**" → "Latest release: **0.15.0**". Replace the parenthetical summary with the 0.15.0 entry from `CHANGELOG.md` (or replace the whole line with a pointer: "See [CHANGELOG.md](CHANGELOG.md) for the latest release."). Confirm against `pyproject.toml` (`version = "0.15.0"`) and `git tag`.
- **Verify**: `grep -n "Latest release" README.md` shows 0.15.0; `make checkall` (docs changes don't break the gate, but run it).

### DOC-002 — Correct README `--approved-only` documentation
- **Files**: `README.md:884`
- **Steps**: The flag does not exist. Correct the README: either remove the `--approved-only` line, or replace it with a note that approval-filtering is **not yet implemented** (`vault-review` records approvals; the summarizer does not yet consume them). Cross-reference the enhancement (ENH-A) if you want to record the intent.
- **Method**: Do **not** claim the flag works. The code-side implementation is tracked separately (ENH-A) because it is a feature addition, not a documentation fix, and the `fix-documentation` agent cannot modify `summarize_sessions.py`.
- **Verify**: `grep -n "approved-only" README.md` shows corrected text; running the documented command (minus the removed flag) succeeds.

### DOC-003 — Fix MCP tool counts (7 → 8)
- **Files**: `README.md:497-507`; `parsidion-mcp/README.md:42-50`
- **Steps**: In both files, change "Seven tools" → "Eight tools" and add a `vault_health` row: "`vault_health` — Composite 0–100 vault-health score (subprocess wrapper around `vault-stats --health --json`); seven per-dimension grades and next actions." Source of truth: `docs/MCP.md` and `server.py:13-20`.
- **Verify**: `grep -n "tools" README.md parsidion-mcp/README.md` shows Eight.

### DOC-004 — Fix `docs/MCP.md` "other six → seven"
- **Files**: `docs/MCP.md:46`
- **Steps**: Change "the other six tools work fully today" → "the other seven tools work fully today" (eight total, `code_search` being the eighth). Confirm against the file's own TOC (14-22) and diagram (88-97).
- **Verify**: `grep -n "other .* tools" docs/MCP.md`.

### DOC-005 — Delete stale `hackernews-release.md`
- **Files**: `hackernews-release.md`
- **Steps**: `git rm hackernews-release.md` (or `gitignore` it). The historical context is preserved in git history; the file is an orphan not linked from any index and contradicts current docs (`~/ClaudeVault/`, "five hooks", "agent SDK"). **Confirm with the user before `git rm`** (destructive/outward-facing-adjacent) — but since `/fix-audit` does not commit, just remove the file from the working tree.
- **Verify**: `ls hackernews-release.md` → not found.

### DOC-006 — Add missing `config.yaml` sections to README
- **Files**: `README.md:558-656`
- **Steps**: Add a `vault:` block (`username: $USER  # daily-note suffix, see DD-{username}.md`), the `embeddings.service_enabled: false` / `embeddings.service_idle_exit: 600` pair (ENH-003 note), and an `adapters: { load_external: false }` entry with a security pointer to `docs/AGENT-ADAPTERS.md`. Source of truth: `skills/parsidion/templates/config.yaml`. (Or add a sentence pointing at that template.)
- **Verify**: manual read of the config block.

### DOC-007 — Expand `SECURITY.md` scope table
- **Files**: `SECURITY.md:36-43,46-74`
- **Steps**: Add rows (or expand) to name the split modules and CLI tools: "Shared library (`vault_*.py` flat shims + `core/` implementations)" and "Vault CLI tools (`vault_new.py`, `vault_review.py`, `vault_export.py`, `vault_merge.py`, `vault_conflicts.py`, `vault_doctor.py`, `build_graph.py`, `vault_embed_serve.py`, `ai_backend.py`, `parmem_backend.py`, `agent_adapter.py`)". Update the Stdlib-Only Constraint section to say "every module under `skills/parsidion/scripts/` (`core/` + flat shims + CLI tools) uses only the Python standard library" — and verify that claim is still true (CLI tools that are PEP 723 are excepted).
- **Verify**: `make checkall`; manual read.

### DOC-008 — Add 7 missing scripts to README Components table
- **Files**: `README.md:194-234`
- **Steps**: Add rows for `vault_tui.py`, `vault_metrics.py`, `ai_backend.py`, `parmem_backend.py`, `build_graph.py`, `vault_embed_serve.py`, `agent_adapter.py` with one-line purposes (copy from CLAUDE.md sections 2-3).
- **Verify**: `grep -n "build_graph\|agent_adapter\|vault_embed_serve" README.md` shows rows.

### DOC-009 — Add Quick Start verification step
- **Files**: `README.md:53-69`
- **Steps**: Add a Step 4 "Verify": `ls ~/.claude/skills/parsidion/SKILL.md && ls ~/ParsidionVault`, plus "Start a runtime session and run `vault-stats --summary`; expect non-zero note counts after your first session ends." Mirrors CONTRIBUTING.md's dev-setup verify step.
- **Verify**: manual read.

### DOC-010 — Exempt/expand `AGENTS.md` shim
- **Files**: `AGENTS.md`; `docs/DOCUMENTATION_STYLE_GUIDE.md`
- **Steps**: Either add a one-line exemption to the style guide ("Adapter instruction shims such as `AGENTS.md` and the installer-generated `GEMINI.md` are redirect stubs, exempt from H1/Summary"), or expand `AGENTS.md` to a brief H1 + summary redirect.
- **Verify**: manual read.

### DOC-011 — Version-pin external tool docs
- **Files**: `docs/MCPL.md`; `docs/AGENTCHROME.md`
- **Steps**: Add a one-line "Verified against `<tool>` vX.Y.Z (date)" to each doc's Overview (check the installed `mcpl` / `agentchrome` versions).
- **Verify**: manual read.

### DOC-012 / ARC-017 — Archive `MEMORY_REPORT.md` (merged)
- **Files**: `MEMORY_REPORT.md` (gitignored)
- **Steps**: Optional — move to `docs/archive/MEMORY_REPORT.md` with a "Historical, March 2026" header and un-gitignore that path; or leave as-is (already correctly gitignored, no published impact).
- **Verify**: `git check-ignore MEMORY_REPORT.md` (confirm still ignored unless deliberately archived).

---

## Cross-cutting follow-ups (not /fix-audit items)

### ENH-A — Implement `summarize_sessions.py --approved-only` (code feature)
- **Files**: `skills/parsidion/scripts/summarize_sessions.py` (argparse ~L850-910 + entry loop); `vault_review.py` (already writes `"status": "approved"` at L559, 580)
- **Steps**: Add `--approved-only` to argparse; in the entry loop, when set, skip entries whose `status != "approved"`. Add a test feeding a mixed-status queue and asserting only approved entries are processed.
- **Note**: This is a **feature** (the workflow is half-built), not a documentation fix, so it is out of `/fix-audit`'s documentation scope. File it as an enhancement if desired. (See ENHANCEMENTS.md.)
- **Verify**: `make checkall`; manual run against a queue with approved + pending entries.
