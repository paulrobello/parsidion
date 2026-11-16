# Personal Vault Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the approved personal-vault design: per-vault folder taxonomy, safe secondary-vault bootstrap, loud `.claude/vault` failure, and all-vault nightly maintenance.

**Architecture:** A `taxonomy:` config section (scalar-encoded) drives a new `resolve_taxonomy()` resolver consumed by folder creation, note routing, and scaffolding; the installer learns to register a secondary vault without stealing `default:`; the resolver records fall-through warnings that hooks surface; `summarize_sessions.py` learns `--all-vaults`. Isolation itself needs no new code — vaults are already separate indexes/embeddings/git.

**Tech Stack:** Python stdlib only (enforced by `tests/test_stdlib_only.py`), pytest, bun for the visualizer check.

**Spec:** `docs/superpowers/specs/2026-11-16-personal-vault-design.md` (approved 2026-11-16) — the plan argues from it; executors read both.

## Global Constraints

- stdlib-only for everything under `skills/parsidion/scripts/` and `installer/` (the gate poisons `rich`, `yaml`, `requests`, etc.).
- Taxonomy config is scalar-encoded: `folders` is a comma-separated string, routing keys are `type_folder.<type>: <Folder>` — never YAML lists/maps (parser and typed schema are scalar-only).
- Note types stay exactly `parsidion/note_schema.py:VALID_NOTE_TYPES`; no new types.
- `Daily`, `Templates`, `History` are system folders, always created, never pruned, outside taxonomy control.
- Vault with no `taxonomy:` section behaves byte-identically to today (built-in `VAULT_DIRS`/`TYPE_FOLDERS`/`DEFAULT_FOLDER`).
- Existing SEC guards (path containment, allowlist resolution) stay intact.
- Bootstrap targets: vault `~/ParsidionPersonalVault` registered as name `personal`; agent folder `~/Repos/personal` (exists, no `.claude/vault` until Task 11).
- Tests must run numpy-free (`make test`); final gate `make checkall` (done gate `make checkall-ci`).
- Work happens in a worktree (parsidion convention); atomic commit per task.

## Review Focus

1. Unresolvable `.claude/vault` name must warn and still fall through — never silently resolve a wrong vault. Pinned by Task 6 (unit test + parity fixture vector).
2. Secondary-vault install must not touch the existing `default:` line. Pinned by Task 7 test `test_no_set_default_preserves_existing_default`.
3. No-taxonomy vaults keep today's folder set exactly — a regression here breaks every existing install. Pinned by Task 3 test `test_ensure_vault_dirs_without_taxonomy_creates_standard_dirs`.
4. Pruning must never remove a folder containing notes (or any file). Pinned by Task 3 test `test_prune_skips_nonempty_folders`.
5. `--all-vaults` must skip stale/unresolvable named-vault entries, not crash the nightly run. Pinned by Task 8 test `test_all_vaults_skips_unresolvable_named_entry`.

---

### Task 1: Taxonomy model + `resolve_taxonomy()`

**Files:**
- Modify: `skills/parsidion/scripts/parsidion/core/vault_config.py` (new code near the other public helpers)
- Test: `tests/test_vault_taxonomy.py` (new)

**Interfaces:**
- Consumes: `parsidion.note_schema.TYPE_FOLDERS`, `DEFAULT_FOLDER`; `parsidion.core.vault_path.VAULT_DIRS`; existing `load_config()`/`get_config()` (vault_config already imports vault_path — do NOT import vault_config from vault_path).
- Produces:
  - `class Taxonomy` dataclass: `folders: list[str]`, `type_folders: dict[str, str]`, `default_folder: str`
  - `def resolve_taxonomy(vault: Path) -> Taxonomy` — reads the vault's merged config (config.yaml + config.local.yaml); absent/empty section → built-ins exactly (`list(VAULT_DIRS)`, `dict(TYPE_FOLDERS)`, `DEFAULT_FOLDER`). `folders` = comma-split stripped non-empty. `type_folders` starts from built-ins when no taxonomy is set; when a taxonomy IS set it starts empty and merges every `type_folder.<type>` key from the section dict, then for built-in types not overridden keep the built-in folder **iff** that folder is in `folders`, else `default_folder`. `default_folder` defaults to `DEFAULT_FOLDER` ("Research"), or the first entry of `folders` if the config default names a folder not in `folders`.

- [ ] **Step 1: Write failing tests** — `test_taxonomy_absent_returns_builtins`, `test_folders_comma_list_parsed`, `test_type_folder_dotted_keys_override`, `test_unlisted_type_falls_back_to_default_folder_when_builtin_folder_not_in_list`, `test_default_folder_invalid_falls_back_to_first_folder`. Use the existing `tmp_vault` fixture (tests/conftest.py, sets `CLAUDE_VAULT`) plus a written `config.yaml`.
- [ ] **Step 2: Run** `uv run pytest tests/test_vault_taxonomy.py -v` — expect import error (`Taxonomy` undefined).
- [ ] **Step 3: Implement** `Taxonomy` + `resolve_taxonomy()` per Interfaces. Read the section dict via `load_config(vault).get("taxonomy", {})` so dotted keys are enumerable.
- [ ] **Step 4: Run** tests — PASS.
- [ ] **Step 5: Commit** `feat(config): per-vault taxonomy resolution (taxonomy.folders / type_folder.* / default_folder)`.

### Task 2: Typed schema fields + config docs

**Files:**
- Modify: `skills/parsidion/scripts/parsidion/core/vault_schema.py` (new `TaxonomyConfig` dataclass, wired into `VaultAppConfig` following the pattern of e.g. `GitConfig`)
- Modify (regenerated): `skills/parsidion/templates/config.yaml`, `CLAUDE.md` config table, `docs/ARCHITECTURE.md` reference block, `docs/generated/config-reference.md` — all via `make config-docs`
- Test: `tests/test_config_schema.py` (add)

**Interfaces:**
- Consumes: Task 1's config keys.
- Produces: `TaxonomyConfig` with `folders: str = ""` and `default_folder: str = ""` (empty string = unset), field metadata documenting the scalar encoding and the `type_folder.<type>` dotted-key convention (dotted keys are open-ended, NOT schema fields; validate must not reject them).

- [ ] **Step 1: Add failing tests** — `test_taxonomy_section_parses` (load a config with the taxonomy section; `load_typed_config().taxonomy.folders == "Home, Family"`), `test_dotted_type_folder_keys_accepted` (a config with `type_folder.knowledge: Reference` loads and validates without unknown-key rejection; if `validate_config` currently rejects unknown keys, relax it for the taxonomy section's `type_folder.` prefix — that relaxation is part of this task).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** `TaxonomyConfig`; regenerate docs: `make config-docs`.
- [ ] **Step 4: Run** `uv run pytest tests/test_config_schema.py tests/test_config_schema_coverage.py -v` and `make config-docs-check` — PASS.
- [ ] **Step 5: Commit** `feat(config): taxonomy section in typed schema + regenerated config docs`.

### Task 3: `ensure_vault_dirs` honors taxonomy + empty-folder pruning

**Files:**
- Modify: `skills/parsidion/scripts/parsidion/core/vault_fs.py` (`ensure_vault_dirs` at ~line 1294)
- Test: `tests/test_vault_taxonomy.py` (extend)

**Interfaces:**
- Consumes: `resolve_taxonomy(vault)` (Task 1). vault_fs already imports from `parsidion.core` siblings — add vault_config import (cycle-safe: vault_config imports only vault_path + note_schema).
- Produces: unchanged signature `ensure_vault_dirs(vault: Path | None = None) -> None`.

- [ ] **Step 1: Add failing tests** — `test_ensure_vault_dirs_without_taxonomy_creates_standard_dirs` (exact set equality with `VAULT_DIRS` + Templates symlink behavior unchanged), `test_ensure_vault_dirs_with_taxonomy_creates_taxonomy_folders`, `test_prune_removes_empty_standard_folders_when_taxonomy_set`, `test_prune_skips_nonempty_folders` (a folder containing one `.md` survives), `test_prune_never_touches_system_folders` (`Daily`, `Templates`, `History`, and `EXCLUDE_DIRS` members survive even when empty).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement**: create `taxonomy.folders` + system folders; when taxonomy is set, remove empty top-level dirs not in `folders` ∪ system ∪ `EXCLUDE_DIRS` (guard: only `dir.iterdir()` empty check; wrap removal in try/except OSError).
- [ ] **Step 4: Run** `uv run pytest tests/test_vault_taxonomy.py -v` — PASS.
- [ ] **Step 5: Commit** `feat(vault): ensure_vault_dirs follows per-vault taxonomy with empty-folder pruning`.

### Task 4: `vault-new` routes by taxonomy

**Files:**
- Modify: `skills/parsidion/scripts/parsidion/vault_new.py` (folder selection at ~line 203)
- Test: `tests/test_vault_new.py` (new or extend if exists)

**Interfaces:**
- Consumes: `resolve_taxonomy(args.vault)` (Task 1); `--type` choices unchanged.
- Produces: `folder_name = taxonomy.type_folders.get(args.type, taxonomy.default_folder)`.

- [ ] **Step 1: Failing test** — `test_vault_new_routes_knowledge_to_reference_in_personal_taxonomy` (tmp vault with taxonomy `type_folder.knowledge: Reference`; assert created note path starts `Reference/`). Also `test_vault_new_default_route_without_taxonomy` (unchanged behavior).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** the one-line routing swap (drop the module-level `_TYPE_TO_FOLDER` use for folder selection; keep `--type` choices sourced from `TYPE_FOLDERS` keys).
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(cli): vault-new scaffolds into taxonomy folders`.

### Task 5: Summarizer routing + explicit vault on auto-spawn

**Files:**
- Modify: `skills/parsidion/scripts/parsidion/summarizer/notes.py` (`folder_name = _TYPE_FOLDERS.get(note_type, _DEFAULT_FOLDER)` inside `write_note_with_reason`)
- Modify: `skills/parsidion/scripts/parsidion/agent_pipeline.py` (`_launch_summarizer_if_pending`, command list built ~lines 282-300)
- Test: `tests/test_summarizer_notes_taxonomy.py` (new); extend existing agent_pipeline test if present

**Interfaces:**
- Consumes: `resolve_taxonomy(vault)` (Task 1); existing `_launch_summarizer_if_pending(vault_path: Path) -> None` already receives the resolved vault.
- Produces: spawned command gains `["-V", str(vault_path)]`; note routing uses `taxonomy.type_folders.get(note_type, taxonomy.default_folder)`.

- [ ] **Step 1: Failing tests** — `test_write_note_uses_taxonomy_folder` (note with `type: knowledge` lands in `Reference/` for a taxonomy vault, `Knowledge/` without taxonomy); `test_launch_summarizer_passes_vault_flag` (monkeypatch subprocess spawn capture; assert `-V <vault>` present).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** both swaps.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(summarizer): taxonomy note routing + explicit -V on background spawn`.

### Task 6: Loud `.claude/vault` failure + hook surfacing

**Files:**
- Modify: `skills/parsidion/scripts/parsidion/core/vault_path.py` (branch 2 of `_resolve_vault_cached`, ~lines 787-802; also branch 3 `CLAUDE_VAULT` except at ~810)
- Modify: `skills/parsidion/scripts/session_start_hook.py`, `skills/parsidion/scripts/user_prompt_submit_hook.py` (after their `resolve_vault()` call), and the session-end path in `skills/parsidion/scripts/parsidion/agent_pipeline.py`
- Modify: `tests/fixtures/parity/vault-resolution.json` (+ regenerate nothing — this fixture is hand-authored; validate via `make parity-fixtures-check` which structurally validates it)
- Test: `tests/test_resolution_warnings.py` (new); `tests/test_vault_resolver_parity.py` already iterates all fixture vectors

**Interfaces:**
- Produces in `vault_path.py`:
  - `_resolution_warnings: ContextVar[list[str]]`
  - `def drain_resolution_warnings() -> list[str]` (returns and clears)
  - Behavior: when branch 2/3 catches `VaultConfigError` (or OSError) with a non-empty reference, append `"vault reference '<ref>' in <where> did not resolve; falling through to <next>"` and CONTINUE fall-through (resolution result unchanged — compatibility preserved).
  - Note the `lru_cache`: warnings only record on cache miss; tests call `resolve_vault.cache_clear()` (existing convention).
- Produces in hooks: after resolving, `for w in drain_resolution_warnings():` prepend a visible line to the hook's injected context (session-start/prompt-submit) and append to the existing `write_hook_event` payload (session-end path).

- [ ] **Step 1: Failing tests** — `test_unresolvable_project_vault_reference_warns_and_falls_through` (tmp HOME, vaults.yaml without the name, `.claude/vault` containing `no-such-name`; assert resolved == default vault AND drain returned one warning naming the ref), `test_drain_clears`, `test_resolvable_reference_no_warning`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** contextvar + drain + the two except blocks; wire the three hook sites.
- [ ] **Step 4: Add the parity fixture vector** — mirror an existing vector that sets a project-local `.claude/vault` (find one by grepping the fixture for the field that plants the file; copy its shape): name `project-vault-unresolvable-name-falls-through`, expect the default vault. Run `uv run pytest tests/test_vault_resolver_parity.py -v` and `make parity-fixtures-check` — PASS (TS parity runs under `make visualizer-check`).
- [ ] **Step 5: Run** `uv run pytest tests/test_resolution_warnings.py tests/test_vault_resolver_parity.py -v` — PASS.
- [ ] **Step 6: Commit** `feat(resolver): visible warning on unresolvable .claude/vault / CLAUDE_VAULT reference`.

### Task 7: Installer `--vault-name` / `--no-set-default`

**Files:**
- Modify: `installer/vault.py` (`record_installed_vault` at line 607)
- Modify: `installer/cli.py` (flags near `--vault` at line 378 and `--vault-username` at line 565)
- Test: `tests/test_installer_secondary_vault.py` (new)

**Interfaces:**
- Produces: `record_installed_vault(vault_root: Path, dry_run: bool = False, name: str | None = None, set_default: bool = True) -> None`. `name=None` → directory stem (today's behavior). `set_default=False` → named entry written, existing `default:` line preserved verbatim (check `render_vaults_yaml` semantics; if it always writes default, thread `set_default` through or post-preserve the previous default line). CLI: `--vault-name NAME` (default None), `--no-set-default` (store_true), passed through the install plan to `record_installed_vault`.

- [ ] **Step 1: Failing tests** — `test_record_secondary_vault_without_set_default` (pre-existing vaults.yaml with `default: /main`; record secondary with `set_default=False, name="personal"`; assert named entry added AND default line still `/main`), `test_record_vault_name_override` (stem `ParsidionPersonalVault` recorded as `personal`), `test_set_default_true_preserves_current_behavior` (default rewritten — today's ARC-019 behavior).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** signature change + flags + plan plumbing.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(installer): --vault-name and --no-set-default for secondary vaults`.

### Task 8: `--all-vaults` maintenance

**Files:**
- Modify: `skills/parsidion/scripts/summarize_sessions.py` (argparse ~line 429 `--vault`; main resolution at line 903)
- Modify: `installer/schedule.py` (command composition, `--run-doctor` extra-args region ~lines 34-107)
- Test: `tests/test_summarizer_all_vaults.py` (new)

**Interfaces:**
- Consumes: `vault_path.list_named_vaults() -> dict[str, Path]`, `resolve_vault()`.
- Produces: `--all-vaults` flag (store_true; mutually exclusive with `--sessions` and `--vault` — argparse group). New `def _iter_registered_vaults() -> list[Path]` — default vault first, then named vaults deduped by resolved path, **skipping entries whose path does not exist** (Review Focus 5). Main loop: extract the per-vault body into `def _process_vault(vault_path: Path, args: argparse.Namespace) -> int` and iterate when `--all-vaults`. `schedule.py`: newly installed launchd/cron commands include `--all-vaults`.

- [ ] **Step 1: Failing tests** — `test_iter_registered_vaults_dedupes_and_skips_missing` (default + two named, one pointing at a nonexistent path → two entries), `test_all_vaults_processes_each_vault` (two tmp vaults each with one queued entry; monkeypatch the AI backend to a canned note; assert both vaults gained a note), `test_all_vaults_conflicts_with_sessions`.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** flag + helpers + loop refactor + schedule.py command change.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(summarizer): --all-vaults nightly maintenance across registered vaults`.

### Task 9: Two-vault integration test

**Files:**
- Test: `tests/test_personal_vault_integration.py` (new)

**Interfaces:**
- Consumes: Tasks 1-8 outputs. Drives `installer.vault` functions directly with monkeypatched HOME (no subprocess install), then exercises resolution, scaffolding, routing, and warnings.

- [ ] **Step 1: Write the test** — one scenario, linear: bootstrap main vault + secondary vault (`record_installed_vault(..., name="personal", set_default=False)` with taxonomy config) in tmp HOME; assert (a) default still resolves to main, (b) cwd with `.claude/vault` containing `personal` resolves secondary, (c) `ensure_vault_dirs` pruned engineering folders, (d) `vault-new`-style routing via `resolve_taxonomy` puts `knowledge` in `Reference`, (e) no warning drained in the happy path.
- [ ] **Step 2: Run** `uv run pytest tests/test_personal_vault_integration.py -v` — PASS (this is a capstone; failures point back at Tasks 1-8).
- [ ] **Step 3: Run the full fast gate** `make checkall` — PASS.
- [ ] **Step 4: Commit** `test: two-vault personal taxonomy integration scenario`.

### Task 10: Visualizer verification + docs

**Files:**
- Verify (modify only if broken): `visualizer/lib/vaultResolver.ts`, `visualizer/app/api/vaults/route.ts`
- Modify: `docs/MULTI_VAULT.md` (personal-vault recipe + secrets warning)

**Interfaces:** none new — `/api/vaults` already lists named vaults.

- [ ] **Step 1:** `make visualizer-check` (includes `vaultResolver.parity.test.ts`, which consumes the Task 6 fixture vector) — PASS.
- [ ] **Step 2:** Read `docs/MULTI_VAULT.md`; add a "Personal vault" section: the bootstrap recipe from the spec (with `--vault-name personal --no-set-default`), the taxonomy config block, `~/Repos/personal` wiring, and the secrets warning (parvault, never vault notes).
- [ ] **Step 3: Run** `make checkall` (doc-links if wired) — PASS.
- [ ] **Step 4: Commit** `docs(multi-vault): personal vault bootstrap recipe and taxonomy guide`.

### Task 11: Real bootstrap + done gate (main checkout, post-merge)

**Files:** none in the repo — operational task.

- [ ] **Step 1:** After the branch merges to main: `uv run install.py --vault ~/ParsidionPersonalVault --vault-name personal --no-set-default --force --yes` (verify vaults.yaml: `personal` named, default untouched).
- [ ] **Step 2:** Write `taxonomy:` block into `~/ParsidionPersonalVault/config.yaml` (folders/type_folder keys from the spec), run any vault-touching script once to trigger `ensure_vault_dirs` pruning; confirm folder set.
- [ ] **Step 3:** `printf 'personal\n' > ~/Repos/personal/.claude/vault` (mkdir -p first).
- [ ] **Step 4:** Verify from `~/Repos/personal`: a `vault-search -V personal` query returns; `vault-new --type knowledge` scaffolds into `Reference/`; a session-start hook test heredoc resolves the personal vault and injects zero main-vault notes.
- [ ] **Step 5:** `make checkall-ci` — PASS — then check the card's five criteria one at a time and close per board rules.
