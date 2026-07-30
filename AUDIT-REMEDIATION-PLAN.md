# Audit Remediation Playbook

> **Consumed by `/fix-audit`.** Entries are ordered to match the `## Remediation Plan` phases in `AUDIT.md`,
> so each phase agent receives its work already sequenced.
>
> **Do not confuse this file with `AUDIT-REMEDIATION.md`** — that is the post-fix *report* `/fix-audit` writes.
> This file is the *input*.
>
> **Standing rules for every entry below:**
> - **Re-read before editing.** Line numbers here were accurate at `8e5d549`. Earlier phases in this same
>   run will have shifted them. Use `Read`/`Grep` to confirm the anchor, never edit blind.
> - **`make checkall` is currently mutating** (ARC-006). Until ARC-006 lands in Phase 2, verify with the
>   individual non-mutating targets: `uv run ruff format --check . && uv run ruff check . && uv run pyright . && uv run pytest tests/`.
> - **Never auto-change credentials or auth configuration.** SEC-103 and anything touching
>   `anthropic_env`, tokens, or API keys is flagged for maintainer decision, not automated edit.
> - **Commit per completed issue** (or per tight cluster), with the issue ID in the subject.

---

# Phase 1 — Critical Security (Sequential, Blocking)

### [SEC-101] Vault post-merge hook RCE — missing `--no-project`, plus unrepairable stale hook
- **Files**:
  - `installer/vault.py:173` (`_POST_MERGE_MARKER`)
  - `installer/vault.py:176-184` (`_POST_MERGE_HOOK_TEMPLATE`) — the defect is on the `build_embeddings.py` line
  - `installer/vault.py:113-126` (`configure_vault_gitignore` entries list)
  - `installer/vault.py:215-223` (the marker containment test and its `_warn` fallthrough)
  - **Manual, outside the repo**: `~/ParsidionVault/.git/hooks/post-merge`
- **Steps**:
  1. In `_POST_MERGE_HOOK_TEMPLATE`, change `uv run {scripts_dir}/build_embeddings.py --incremental` to `uv run --no-project {scripts_dir}/build_embeddings.py --incremental`. This is the whole security fix.
  2. Introduce a legacy-marker tuple next to `_POST_MERGE_MARKER`:
     ```python
     _POST_MERGE_MARKER = "# parsidion post-merge hook"
     _POST_MERGE_LEGACY_MARKERS = ("# parsidion-cc post-merge hook",)
     ```
  3. In `install_vault_post_merge_hook`, replace the single `if _POST_MERGE_MARKER in existing: return` with logic that (a) returns when the current marker is present **and** the body already contains `--no-project` on both `uv run` lines, and (b) **regenerates** the hook when either the current marker is present with a stale body, or any legacy marker is present. Only fall through to the `_warn("not ours")` branch when no known marker matches.
  4. Add these entries to the `configure_vault_gitignore` list (defence in depth against the same class): `"pyproject.toml"`, `"uv.toml"`, `"setup.py"`, `".venv/"`.
  5. Add a test in `tests/` asserting the rendered template contains `--no-project` on **every** `uv run` line — a regex over the template constant, so the class cannot regress:
     `assert all("--no-project" in ln for ln in tmpl.splitlines() if ln.startswith("uv run"))`.
  6. Add a test that a hook file carrying the legacy `parsidion-cc` marker is regenerated rather than skipped.
  7. **Report to the user (do not do it silently):** the live hook at `~/ParsidionVault/.git/hooks/post-merge` still carries the legacy marker and dead `parsidion-cc` script paths. After step 3 ships, running `uv run install.py --force --yes` will regenerate it. State this explicitly in the fix report — it is a required manual follow-up, and touching a file in the user's vault is outside the repo's scope.
- **Method**: Git runs `post-merge` with cwd = the vault worktree. `uv run` without `--no-project` discovers a `pyproject.toml` in cwd and executes its PEP 517 build backend **before** resolving the target script — so a committer to a shared vault remote gets code execution on every machine that pulls. `set -e` does not help: the payload runs at backend-import time. The fix is one flag, but the marker bug is what makes it stick: the live hook's `# parsidion-cc post-merge hook` does not contain `# parsidion post-merge hook`, so the `in` test at `:217` fails, the installer warns *"already exists (not ours) … Skipping"*, and **no future install ever repairs it**. Fixing only the template leaves every pre-rename installation permanently vulnerable. Note the current machine is not exploitable today (no vault remote configured, and `set -e` aborts on the dead first line — `uv run --no-project <missing file>` exits 2), but a fresh install writes resolvable paths and *is*.
- **Verify**:
  ```bash
  grep -n 'uv run' installer/vault.py           # both lines must show --no-project
  uv run pytest tests/ -k "post_merge or gitignore" -v
  uv run ruff check installer/vault.py && uv run pyright installer/vault.py
  ```

### [SEC-104] Vault `.gitignore` needs globs; four sensitive files are already tracked
- **Files**: `installer/vault.py:113-126` (entries list), `installer/vault.py:133` (the `e not in content` membership test)
- **Steps**:
  1. Replace the exact-filename entries with globs so backup variants are covered:
     `embeddings.db*`, `pending_summaries.jsonl*`, `dead_letters.jsonl*`, `hook_events.log*`, `graph.json`, `summarizer_state.json`, `doctor_state.json`, `.obsidian/`, `config.yaml`, `config.local.yaml`, `conflicts/`. Keep the existing explanatory comment about `ANTHROPIC_API_KEY`.
  2. Fix the membership test at `:133`. `if e not in content` is a substring test, so a commented-out `# config.yaml` already present in the file makes the real `config.yaml` entry look present and suppresses it. Change to a line-wise comparison:
     ```python
     existing_lines = {ln.strip() for ln in content.splitlines()}
     missing = [e for e in entries if e not in existing_lines]
     ```
  3. Add a test that a `.gitignore` containing only `# config.yaml` (commented) still gets a real `config.yaml` line appended.
  4. **Report to the user (do not execute):** four files are already tracked in the live vault — `pending_summaries.jsonl.bak`, `pending_summaries.jsonl.bak-20260712-092800`, `dead_letters.jsonl.bak-20260712-092800`, `conflicts/report.json`. They must be untracked with `git -C ~/ParsidionVault rm --cached <file>` **after** this change lands. Do not run it — it mutates the user's vault repo.
- **Method**: Sequenced immediately after SEC-101 because both edit `installer/vault.py` and SEC-101 adds four more entries to the same list — merging them into one edit avoids a conflict. The glob change must land **before** any untracking, or the next migration re-adds the files. The `.bak` files contain session IDs, absolute transcript paths, and project names; because they are already in history, adding a remote leaks them retroactively.
- **Verify**:
  ```bash
  uv run pytest tests/ -k gitignore -v
  git -C ~/ParsidionVault ls-files | grep -E '\.bak|conflicts/'   # report what remains; do not delete
  ```

### [SEC-102] Visualizer: token unchecked on reads, same-origin bypassable, binds all interfaces
- **Files**:
  - `visualizer/lib/apiAuth.ts:13-14` (false docstring), `:37-38`, `:57-80` (`requireSameOrigin`, `requireAuth`)
  - `visualizer/package.json:6-8` (`dev`, `start` scripts)
  - All 12 route files under `visualizer/app/api/`: `note/route.ts`, `note/history/route.ts`, `note/diff/route.ts`, `files/route.ts`, `graph/route.ts`, `graph/rebuild/route.ts`, `search/route.ts`, `vaults/route.ts`, `stats/route.ts`, `summarize/route.ts`, `summarizer/status/route.ts`, `vault/events/route.ts`
- **Steps**:
  1. In `apiAuth.ts`, add an exported `requireToken(req: NextRequest): NextResponse | null` that returns 401 when `process.env.VISUALIZER_TOKEN` is set and the `Authorization: Bearer <token>` header does not match. Use a constant-time comparison (`crypto.timingSafeEqual` on equal-length buffers, length-checked first) rather than `===`.
  2. Make `requireAuth` call `requireToken` so mutation routes keep working unchanged.
  3. Add `requireToken(req)` as the **first** statement of every GET handler across all 12 route files, keeping the existing `requireSameOrigin(req)` call as the CSRF layer directly after it.
  4. Add the missing guards to `app/api/stats/route.ts`, which currently imports neither (this resolves QA-011 and SEC-118 — record that in the fix report so the 3c agent skips them).
  5. Correct the `apiAuth.ts:13-14` docstring, which currently claims every request must carry the header. Either make it true (step 3 does) or state the actual scope.
  6. In `package.json`, change `"dev": "next dev --port 3999"` → `"next dev -H 127.0.0.1 --port 3999"` and the same for `start`. Next.js defaults `--hostname` to `0.0.0.0`.
  7. Add `visualizer/lib/apiAuth.test.ts` covering: token set + correct header → null; token set + missing header → 401; token set + wrong token → 401; token unset → null; `Sec-Fetch-Site: cross-site` → 403; header absent (curl) → passes `requireSameOrigin` but is now caught by `requireToken`.
- **Method**: Three defects compose into one exposure. `requireAuth()` is the only function reading `VISUALIZER_TOKEN` and it is called from mutation handlers only, so the documented hardening step protects nothing on reads. `requireSameOrigin` rejects only the literal string `cross-site`, and `curl` omits `Sec-Fetch-Site` entirely, so `site` is `null` and passes — the function's own comment concedes this. And the server binds `0.0.0.0`. Next.js 16's `blockCrossSiteDEV` does not help; it returns early unless the path matches `/_next` or `/__nextjs`. Binding loopback (step 6) is the single highest-value change and also closes the DNS-rebinding vector (SEC-119). Do this **before** ARC-002 and QA-006, which edit the same route files.
- **Verify**:
  ```bash
  cd visualizer && bunx tsc --noEmit && bun run lint && bun test
  grep -rn 'requireToken\|requireSameOrigin' visualizer/app/api --include=route.ts | wc -l   # expect >= 24 (2 per route)
  grep -n '"dev"\|"start"' visualizer/package.json                                          # both must show -H 127.0.0.1
  ```

### [SEC-105] Malformed `settings.json` is discarded then overwritten, destroying `permissions.deny`
- **Files**: `installer/hooks.py:565-567` (the reset), `installer/hooks.py:621-629` (the write), plus the sibling correct implementations at `:172-188` and `:201-219` to copy from
- **Steps**:
  1. In `merge_hooks`, replace `except (json.JSONDecodeError, OSError) as exc: _warn(...); settings = {}` with a bail-out that returns a failure result and leaves the file untouched — mirroring the Codex/Gemini readers at `:172-188`. A parse failure must never lead to a write.
  2. Add `_atomic_write_json(path: Path, data: dict) -> None` in `installer/hooks.py`: write to `path.with_suffix(path.suffix + f".tmp.{os.getpid()}")`, `os.replace()` into place, and preserve the existing file's mode via `os.stat` when the target exists. This helper is also what ARC-018 will reuse — write it once, here.
  3. Before the first mutation of a **pre-existing** `settings.json`, copy it to `settings.json.bak` (overwrite any prior `.bak`). Print the backup path.
  4. Route the `:621-629` write through `_atomic_write_json`.
  5. Add tests: (a) a `settings.json` with a trailing comma leaves the file byte-identical and returns failure; (b) a valid `settings.json` carrying `permissions.deny` and an unrelated `statusLine` key retains both after `merge_hooks`; (c) a `.bak` is created on first mutation.
- **Method**: `merge_hooks` is the sole exception in a file where every other reader bails out correctly — `remove_installed_hooks`, `remove_legacy_hooks`, and `enable_ai_mode` all do the right thing. One stray comma in the user's hand-edited settings currently destroys `permissions.allow`, `permissions.deny`, `env`, `statusLine`, MCP servers, and every non-parsidion hook behind a single yellow warning, on every `make install`, with no backup anywhere in the installer. Landing the atomic-write helper here means ARC-018 in Phase 3b becomes "apply the existing helper at the other 12 sites" rather than a fresh design.
- **Verify**:
  ```bash
  uv run pytest tests/test_install.py -k "settings or merge_hooks" -v
  uv run ruff check installer/hooks.py && uv run pyright installer/hooks.py
  ```

### [SEC-103] Config template routes all AI traffic to a third-party endpoint by default ⚠️ MAINTAINER DECISION
- **Files**: `skills/parsidion/templates/config.yaml:102-109`
- **Steps**:
  1. **Do not edit this file automatically.** Surface the finding to the user and stop.
  2. Present the facts: the committed template ships `ANTHROPIC_BASE_URL: https://api.z.ai/api/anthropic` with `ANTHROPIC_DEFAULT_HAIKU_MODEL: GLM-5-TURBO` and `ANTHROPIC_DEFAULT_SONNET_MODEL/OPUS_MODEL: GLM-5.1` as **defaults**, committed in `c216e6a`. `ANTHROPIC_BASE_URL` is in `_CONFIGURABLE_ENV_KEYS` (`skills/parsidion/scripts/vault_hooks.py:188-201`) and is merged into the environment of every `claude -p` call, so every user who follows `CLAUDE.md:191`'s instruction to copy the template sends full cleaned transcripts — containing source code and file contents — to `api.z.ai` nightly and unattended. The adjacent `defaults:` block still names genuine Claude models, contradicting it.
  3. Offer the two options: **(a)** set `ANTHROPIC_BASE_URL: null` and restore Anthropic model IDs, moving the Z.ai values into a commented-out "example: routing through a gateway" block; or **(b)** keep it and document it prominently as an intentional default in `CLAUDE.md` and `README.md`. Recommend (a).
  4. Only after the user chooses, apply the edit and unblock DOC-023.
- **Method**: This is credential/endpoint routing configuration. Standing policy is that security-related configuration is never auto-generated or replaced — it must be opt-in and preserve existing configuration. The evidence strongly suggests a personal setting leaked into the shipped template, but "strongly suggests" is not authorization to change where a user's AI traffic goes. **DOC-023 is blocked on this decision, not on code.**
- **Verify**: after the maintainer decides —
  ```bash
  sed -n '100,115p' skills/parsidion/templates/config.yaml
  uv run pytest tests/ -k config -v
  ```

---

# Phase 2 — Critical Architecture (Sequential, Blocking)

### [ARC-006] `make checkall` rewrites source files — fix this first
- **Files**: `parsidion-mcp/Makefile` (`fmt`, `lint`, `checkall` targets), `Makefile:37` (`checkall-mcp`)
- **Steps**:
  1. In `parsidion-mcp/Makefile`, add `fmt-check: uv run ruff format --check .` mirroring the root Makefile.
  2. Change `parsidion-mcp`'s `checkall` from `fmt lint typecheck test` to `fmt-check lint typecheck test`, and change its `lint` target from `ruff check --fix .` to `ruff check .`.
  3. Keep the mutating variants available as an explicit `fix: fmt lint-fix` target so the convenience is not lost.
  4. Confirm the root `Makefile`'s `checkall-mcp` still delegates correctly.
- **Method**: This is the highest-leverage fix in the entire plan and must land before anything else in Phase 2 or 3. The root `checkall` correctly uses non-mutating `fmt-check` and `lint`, but its final dependency `checkall-mcp` delegates to a target that runs `ruff format .` and `ruff check --fix .` — both rewriting. Until this is fixed, **no fix agent can run the project's own gate without dirtying the working tree**, which means no fix can be verified read-only, which means every subsequent phase's verification is compromised. The architecture audit agent could not run the gate at all for this reason.
- **Verify**:
  ```bash
  git status --porcelain > /tmp/before.txt
  make checkall
  git status --porcelain > /tmp/after.txt
  diff /tmp/before.txt /tmp/after.txt   # MUST be empty — this is the whole point
  ```

### [ARC-007] CI covers neither the visualizer nor the pi extension
- **Files**: `.github/workflows/ci.yml`, `Makefile:29-33` (`visualizer-check`), `visualizer/package.json` (scripts)
- **Steps**:
  1. Add a `visualizer` job to `ci.yml`: `oven-sh/setup-bun@v2` (pin the exact tag; verify it resolves before committing), `bun install --frozen-lockfile` in `visualizer/`, then `bunx tsc --noEmit`, `bun run lint`, `bun test`, and `bun run build`.
  2. Add `bun run build` to the `Makefile`'s `visualizer-check` target so the Makefile and CI agree. This is what catches RSC server/client boundary violations (ARC-041), which `tsc --noEmit` alone does not.
  3. Add an `extensions` job running `bunx tsc --noEmit` and `bun test` in `extensions/pi/parsidion/` — it has a `parsidion-status.test.ts` that is currently executed by nothing.
  4. Add `test-graph` to CI (`uv run --with numpy pytest tests/test_build_graph_parmem.py`); it is in `make checkall` but not in CI.
  5. Add `typecheck`/`test` scripts to `visualizer/package.json` so the gate is not Makefile-only and cannot drift (ARC-047).
  6. Per the repo's pinning rule, use exact action tags and confirm each ref resolves before committing.
- **Method**: 66 TypeScript files / ~12.5k LOC — including all 12 API routes and `vaultResolver.ts`'s path-traversal guards — currently merge with zero automated verification. ARC-002 (silent data loss) is exactly the class of defect this gap admits. This job must exist before Phase 3 so that QA-004/ARC-016's new route tests actually gate anything. Note `bun test` runs `visualizer/lib/*.test.ts` today; `tsc --noEmit` across the repo has a known `bun:test` typing gotcha — if it surfaces, scope the typecheck rather than disabling it.
- **Verify**:
  ```bash
  make visualizer-check          # must now include a build step
  gh workflow view ci.yml        # confirm the new jobs are registered
  # then push a branch and confirm all jobs go green before merging
  ```

### [ARC-001] `py-modules` omits seven modules — non-editable install is dead on arrival
- **Files**: `pyproject.toml:41-54` (`[tool.setuptools] py-modules`), `parsidion-mcp/pyproject.toml:6` (the `parsidion[search]` dependency)
- **Steps**:
  1. Add the seven missing module names to `py-modules`: `vault_config`, `vault_path`, `vault_fs`, `vault_index`, `vault_hooks`, `vault_adaptive`, `ai_backend`.
  2. Cross-check the resulting list against every top-level `import` inside the declared modules. The authoritative check is the smoke test in step 3, not a manual scan.
  3. Add a CI job (or extend the one from ARC-007) that builds a wheel and installs it clean:
     ```bash
     uv build
     python -m venv /tmp/wheeltest && /tmp/wheeltest/bin/pip install dist/*.whl
     /tmp/wheeltest/bin/python -c "import vault_common, vault_search, vault_links, ai_backend"
     ```
  4. Delete the stale `build/lib/` directory (it contains a March snapshot with 9 modules and actively misleads).
- **Method**: `vault_common.py` (declared) imports six undeclared modules at `:29,43,65,81,103,128`, and `vault_merge`/`vault_conflicts` import `ai_backend`. The bug is invisible locally because both consumers use editable installs — `__editable__.parsidion-0.13.0.pth` is a bare path line to the scripts dir, so `sys.path` covers everything regardless of the manifest. Only a real wheel install exposes it, which is why the smoke test is the actual deliverable here; the manifest edit without it will silently rot again. `parsidion-mcp` declares `parsidion[search]` as a hard dependency, so publishing the MCP server against a published `parsidion` ships a broken server. **Do not attempt ARC-004 (Phase 5) in the same change** — it replaces `py-modules` with `packages` and both edit this file.
- **Verify**:
  ```bash
  uv build && python -m venv /tmp/wheeltest && /tmp/wheeltest/bin/pip install --force-reinstall dist/*.whl
  /tmp/wheeltest/bin/python -c "import vault_common, vault_search, vault_links, ai_backend; print('ok')"
  rm -rf /tmp/wheeltest
  ```

### [ARC-003] `disconnect codex|gemini` tears down shared global infrastructure
- **Files**: `installer/skill.py:614-630` (the unguarded block inside `uninstall()`), `install.py:810-836` (the `disconnect` routing)
- **Steps**:
  1. In `uninstall()`, add near the top of the teardown section:
     ```python
     is_full_teardown = runtime in {"claude", "all", None}
     ```
     Match the exact sentinel the existing `uninstall_*_runtime` flags are derived from — read the surrounding code to confirm whether `runtime is None` means "all".
  2. Indent `_resolve_vault_root_for_uninstall()` → `remove_vault_post_merge_hook(...)` (`:614-615`) and `unschedule_summarizer(...)` (`:617`) inside `if is_full_teardown:`.
  3. Guard the `vaults.yaml` deletion (`:623-628`) behind **both** `is_full_teardown` **and** a new explicit `--purge-config` flag. Under `--yes` alone it must not delete. Add `--purge-config` to the argparse definition in `install.py` and document it.
  4. Add tests: `uninstall(runtime="codex", yes=True)` must not call `unschedule_summarizer`, must not call `remove_vault_post_merge_hook`, and must not unlink `vaults.yaml`; `uninstall(runtime="claude", yes=True, purge_config=True)` may do all three.
- **Method**: `disconnect <agent>` maps to `uninstall(hooks_only=False, runtime=agent)`, and three teardown actions sit at function-body indentation **outside every** `if uninstall_*_runtime:` guard. So the documented "remove the Codex integration only" command also removes the nightly summarizer job, the vault's `post-merge` git hook (breaking multi-machine sync), and `~/.config/parsidion/vaults.yaml` — with **no prompt at all** under `--yes` — while the still-connected Claude install depends on all three. Keep this fix minimal and standalone: it is three lines of re-indentation plus a flag. Attempting it *inside* the ARC-017 restructure (Phase 5) risks losing it, which is precisely how it hid in the first place.
- **Verify**:
  ```bash
  uv run pytest tests/test_install.py -k "uninstall or disconnect" -v
  uv run install.py disconnect codex --dry-run   # inspect printed plan: no summarizer/plist/vaults.yaml lines
  ```

### [ARC-002] Visualizer note writes target the wrong vault — silent data loss
- **Files**: `visualizer/app/api/note/route.ts:76` (POST), `:146` (PUT), `:122-132` (mtime conflict check); `visualizer/lib/useVisualizerState.ts:278,324` (client body construction)
- **Steps**:
  1. **Write the test first** (ARC-016's harness should exist by now; if not, create `visualizer/app/api/note/route.test.ts` here). Assert: with `?vault=` absent but `{vault: "<name>"}` in the JSON body, PUT writes into `<name>`'s root, not the default vault's.
  2. In the POST handler at `:76`, read the vault from the body as well as the query string, preferring the query string for backward compatibility:
     ```ts
     const vaultParam = req.nextUrl.searchParams.get('vault') ?? body.vault ?? null
     ```
     Note the body must be parsed before this line — check the current ordering and move the `await req.json()` up if needed.
  3. Apply the identical change to the PUT handler at `:146`.
  4. Confirm `resolveVault(vaultParam)` still rejects unknown names — it is an allowlist (`vaultResolver.ts:166-192`), so accepting the value from the body introduces no traversal risk.
  5. Re-verify the mtime conflict check at `:122-132` now stats the correct file.
- **Method**: The client puts the selected vault in the **JSON body** (`if (selectedVault) body.vault = selectedVault`) while POST and PUT read it only from the **query string**; `body.vault` is destructured nowhere and silently discarded, so `resolveVault(null)` falls through to `getDefaultVault()`. GET and DELETE use query params correctly, which is why reads and deletes appear to work while writes go elsewhere. The consequence is not just a failed write: when a same-relative-path note exists in the default vault, POST **overwrites the wrong file**, and the mtime guard compares the wrong file's mtime so it cannot prevent it. Fixing the server side (rather than the client) also fixes any other client that already sends the body field. Must run **after** SEC-102, which edits the top of the same handlers.
- **Verify**:
  ```bash
  cd visualizer && bun test && bunx tsc --noEmit && bun run lint
  ```

---

# Phase 3a — Security (Remaining)

### [SEC-106] Symlinked `.md` files are indexed and read, bypassing every containment check
- **Files**: `skills/parsidion/scripts/vault_index.py:486-497` (`_walk_vault_notes`), read site at `:519`; `skills/parsidion/scripts/vault_metrics.py:516,589`
- **Steps**:
  1. In `_walk_vault_notes`, before appending a discovered path, skip symlinks that escape the vault:
     ```python
     if p.is_symlink():
         try:
             if not p.resolve().is_relative_to(vault.resolve()):
                 continue
         except OSError:
             continue
     ```
  2. Apply the same guard at the two `vault_metrics.py` sites.
  3. **Preserve the `Templates/` symlink**, which is intentional (`Templates` → `skills/parsidion/templates/`). It is already excluded via `EXCLUDE_DIRS`, so verify the new guard runs *after* the exclude check and does not regress it — add a test asserting `Templates/` handling is unchanged.
  4. Add a test: create a vault with `Patterns/evil.md` symlinked to a file outside the vault; assert `_walk_vault_notes` does not yield it.
- **Method**: `os.walk` does not follow symlinked *directories* but does list symlinked *files*, and `_walk_vault_notes` appends them unconditionally. The existing path guards validate paths arriving *from a caller*; these are *discovered*, so no guard ever sees them. Git preserves symlinks as tree entries, so a shared-vault committer can add `Patterns/onboarding.md → ~/.ssh/id_ed25519`; on pull the index rebuilds, `_extract_summary()` writes the first body line into `CLAUDE.md` and `MANIFEST.md` (both intentionally *not* gitignored, since the index is meant to sync), and `git_commit_vault()` commits them back — a closed exfiltration loop. The visualizer is already immune (Node's `readdir(withFileTypes)` is lstat-based). **Do this before SEC-130** (consolidating the four containment checks) — consolidating first would bake this fix into a shared helper whose call sites have not been proven equivalent.
- **Verify**: `uv run pytest tests/ -k "symlink or walk_vault or vault_index" -v`

### [SEC-107] Summarizer merge path writes model output with no validation, containment, or backup
- **Files**: `skills/parsidion/scripts/summarize_sessions.py:1400-1443` (the dedup-merge branch); reference implementations at `:933` (`_validate_frontmatter`), `:959` (containment), and `vault_doctor.py:1675-1683` (`_backup_note`)
- **Steps**:
  1. In the merge branch, before `target_path.write_text(new_content)`, call `_validate_frontmatter(new_content)` and abort the merge (returning the existing failure sentinel) when it fails.
  2. Add the same containment assertion the create path uses at `:959` — resolve `target_path` and confirm it is inside the vault, raising the same `ValueError` on escape.
  3. Call a `_backup_note(target_path)` equivalent before overwriting, mirroring `vault_doctor.py:1675-1683`.
  4. Replace the direct `write_text` with `vault_fs.atomic_write_text` (this also satisfies part of SEC-127).
  5. Add a test: a merge whose model output has invalid frontmatter leaves the target note byte-identical.
- **Method**: The create path is well defended and the merge path calls none of the same guards. Traversal is not reachable today only because `_resolve_note_stem` returns indexed paths — containment holds by *accident of the resolver*, not by check, and combines with SEC-106 to become reachable. A repo file crafted to steer the model into emitting `{"decision":"merge","target":"[[…]]"}` silently replaces a trusted, frequently-retrieved note; the `dedup_block` at `:463` teaches the model this exact schema. Must land **before** ARC-009's restructure (Phase 5), which will carry this code forward.
- **Verify**: `uv run pytest tests/test_summarize_sessions.py -k "merge or dedup or validate" -v`

### [SEC-108] No untrusted-content framing where content reaches the primary agent
- **Files**: `skills/parsidion/scripts/session_start_hook.py:918-941` (`_assemble_context`), `:316-332` (the `--ai` selector prompt); `skills/parsidion/scripts/post_compact_hook.py:126-131`; `agents/research-agent.md`
- **Steps**:
  1. Copy the framing already used on the three *ingest* prompts (`session_stop_hook.py:92-97`, `summarize_sessions.py:479`, `vault_doctor.py:548-564`): wrap injected note content in `<content>…</content>` and prepend a SYSTEM preamble stating the enclosed text is untrusted vault data, not instructions.
  2. Apply it in `_assemble_context` so every note body injected as `additionalContext` is delimited and labelled with its source path.
  3. In `post_compact_hook.py:126-131`, wrap the restored snapshot the same way and **remove or requalify** the trailing *"(Resume from where you left off above.)"* — that is an instruction to comply, attached to unvalidated content.
  4. In the `--ai` selector at `session_start_hook.py:316-332`, move the instruction block **before** the interpolated note bodies rather than after them.
  5. Add a paragraph to `agents/research-agent.md` (and check the other files in `agents/`) instructing that fetched web content is untrusted and must not be treated as instructions when composing a vault note.
  6. Add a test asserting `_assemble_context` output contains the delimiter for every injected note.
- **Method**: The codebase demonstrably knows this pattern and applies it consistently on ingest — the one place content reaches the agent *with full authority* is bare concatenation with no delimiter, provenance label, or preamble. That inversion is the finding. Daily notes **are** git-synced, so `post_compact_hook.py` restoring an unverified snapshot with a comply-instruction attached is the sharpest edge. Truncation (`max_chars`, default 4000) is already applied correctly and should be left alone. Sequence with SEC-111 and SEC-130, which edit the same hook files; do **not** parallelize.
- **Verify**: `uv run pytest tests/test_session_start_hook.py tests/test_pre_compact_hook.py -v`

### [SEC-109] `pending_summaries.jsonl` permissions silently downgraded 0600 → 0644
- **Files**: `skills/parsidion/scripts/summarize_sessions.py:1812-1816` (`remove_processed`'s tmp+replace); `skills/parsidion/scripts/vault_fs.py:265-280` (creation path); reference at `vault_fs.py:402` (`migrate_pending_paths`)
- **Steps**:
  1. In `remove_processed`, replace `tmp.write_text(...)` with `os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)` + `os.fdopen`, matching `vault_fs.migrate_pending_paths:402`. Alternatively `os.chmod(tmp, 0o600)` before `tmp.replace()` — but prefer `os.open` so there is no window.
  2. Add a **migration** to `vault_doctor.py`'s `--fix-all` path that chmods existing `pending_summaries.jsonl`, `dead_letters.jsonl`, and their `.bak*` variants to 0600. A code fix alone changes nothing for existing installs.
  3. Add a test asserting the mode after `remove_processed` is 0600.
- **Method**: `tmp.write_text(...)` uses the process umask (typically 022), and `tmp.replace()` then makes the queue **inherit** the tmp file's 0644, silently undoing the hardening. The proof is the live divergence between two files with identical stated protection: `-rw------- dead_letters.jsonl` (never rewritten this way) vs `-rw-r--r-- pending_summaries.jsonl` (rewritten by `remove_processed`). `vault_fs.py:265-280`'s own comment concedes *"existing files retain their current mode"* — which is why step 2 is not optional.
- **Verify**:
  ```bash
  uv run pytest tests/ -k "pending or permission or remove_processed" -v
  stat -f '%Sp %N' ~/ParsidionVault/pending_summaries.jsonl ~/ParsidionVault/dead_letters.jsonl
  ```

### [SEC-110] `~/.claude/logs/` is 0755 containing 0644 logs
- **Files**: `skills/parsidion/scripts/vault_path.py:93-101` (`secure_log_dir`), `skills/parsidion/scripts/session_stop_wrapper.sh:26-28`
- **Steps**:
  1. In `secure_log_dir`, after `mkdir(exist_ok=True, mode=0o700)`, add an explicit `os.chmod(log_dir, 0o700)` — `mkdir`'s `mode` is ignored when the directory already exists.
  2. In `session_stop_wrapper.sh`, change the plain `mkdir -p` to `mkdir -p -m 700`.
  3. Open log files with `os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)` wherever the Python side creates them.
  4. Add the same chmod repair to `vault_doctor.py --fix-all` (pair with SEC-109's migration — one permission-repair pass covers both).
- **Method**: `mkdir(exist_ok=True, mode=0o700)` never chmods an existing directory, and the shell wrapper's plain `mkdir -p` usually wins the creation race, so the 0700 intent has effectively never applied. Verified live: `drwxr-xr-x logs/` holding world-readable `parsidion-summarizer.log` (1.8 MB), `session_stop_hook.log` (1.3 MB), `parsidion-parmem.log` (705 KB) — exactly the session metadata that SEC-007 claims to protect by moving off `/tmp`.
- **Verify**: `uv run pytest tests/ -k "log_dir or secure_log" -v` then `stat -f '%Sp %N' ~/.claude/logs`

### [SEC-111] Transcript reads are unbounded or bounded only by lines
- **Files**: `skills/parsidion/scripts/subagent_stop_hook.py:177-181` (`f.readlines()`, no cap); `session_stop_hook.py:391,405`; `pre_compact_hook.py:377`; reference implementation at `summarize_sessions.py:280`
- **Steps**:
  1. Extract the byte-bounded reader from `summarize_sessions.py:280` into `vault_fs.read_last_n_lines(path, max_lines, max_bytes)` if it is not already shared, and re-point `summarize_sessions.py` at it.
  2. Replace `subagent_stop_hook.py:177-181`'s `f.readlines()` with that helper. Delete the comment *"Read ALL lines (subagent sessions are short)"* — the project's own vault note records that premise as false.
  3. Re-point the three lines-only readers at the same helper so a single newline-free multi-GB file cannot defeat them (`deque(f, maxlen=n)` still streams the whole file).
  4. Add `summarizer.transcript_tail_bytes` to `_CONFIG_SCHEMA` in `vault_config.py` (it is read but not declared) and add a `subagent_stop_hook.transcript_tail_bytes` key with a sane default.
  5. Add a test with a single 50 MB newline-free line asserting bounded memory and a bounded return.
- **Method**: The byte-bounding fix from commit `f5b26db` landed only in `summarize_sessions.py`; the other four readers never received it, and one has no cap at all. Rated for reliability on an always-on code path rather than confidentiality. Note the coupling with QA-001: write the hook contract tests **first** so this change is verifiable against pinned behavior.
- **Verify**: `uv run pytest tests/ -k "transcript or tail or subagent" -v`

### [SEC-112] `config.yaml` / `config.local.yaml` are world-readable
- **Files**: no source defect — these are user-authored, so `umask 022` applies. Fix belongs in the permission-repair pass.
- **Steps**:
  1. Extend the `vault_doctor.py --fix-all` permission-repair pass (SEC-109 step 2) to chmod `config.yaml` and `config.local.yaml` to 0600 when present.
  2. Have `install.py` chmod both to 0600 when it writes keys into them (`install.py:532`, `:535`, `installer/skill.py:349`).
  3. Add a `> **Security:**` note to `CLAUDE.md`'s config section and the template stating these files should be 0600 because they may hold `ANTHROPIC_API_KEY`.
- **Method**: Latent rather than live — the current vault's copies were checked and contain no secret values — but live the moment anyone follows the documented workflow of putting `ANTHROPIC_API_KEY` there. The git axis is already handled correctly: both are gitignored, `.gitignore` is written *before* the initial `git add -A`, and `git_commit_vault()` excludes `config.yaml` from auto-staging (`vault_fs.py:453`). Only the filesystem-mode axis is open.
- **Verify**: `uv run pytest tests/test_vault_doctor.py -k permission -v`

### [SEC-115] `vault-merge` gives a tool-enabled child agent filesystem access over untrusted content
- **Files**: `skills/parsidion/scripts/vault_merge.py:127-147` (prompt), `:72-75` (output guard); reference at `vault_conflicts.py` (which inlines correctly)
- **Steps**:
  1. Rewrite the prompt to **inline both note bodies** inside `<note_a>` / `<note_b>` delimiters instead of instructing the model to *"Read both files"*, matching what `vault_conflicts.py` already does.
  2. Add the untrusted-content SYSTEM preamble used elsewhere in the repo.
  3. Strengthen the output guard at `:72-75` beyond "length ≥ 50 and starts with `#`": validate that the result parses as a note with valid frontmatter via the shared validator.
  4. Add a test asserting the constructed prompt contains both note bodies and no "Read" instruction.
- **Method**: This is the only place in the repo handing filesystem access to a child agent, and it does so over content that is itself AI-generated from transcripts. Mitigating: `--dangerously-skip-permissions` appears nowhere in the repo, so Bash/Write remain permission-gated — which is why this is Medium, not High. Inlining removes the dependency on undocumented per-backend default tool permissions (`_run_codex_prompt` passes `--sandbox read-only`; the Claude path relies on defaults).
- **Verify**: `uv run pytest tests/ -k "merge and prompt" -v`

### [SEC-116] `connect codex` follows symlinks and would rewrite the global `CLAUDE.md`
- **Files**: `installer/skill.py:265-278` (`install_codex_md` / `install_gemini_md` write path), `:250-292` (`_END_MARKER`, currently unused)
- **Steps**:
  1. Before writing, check `dest.is_symlink()`. If it is, resolve it and refuse to write when the target is outside the expected agent config directory — print the resolved target and instruct the user to remove the symlink. Do not silently follow it.
  2. Implement `_remove_instructions_block(dest) -> bool` that strips everything between `<!-- BEGIN parsidion -->` and `_END_MARKER`, and call it per-runtime from `uninstall()`. This closes the ARC-022 asymmetry where a "disconnected" Codex still loads the full instruction block every session.
  3. Also revert `[features] hooks = true` in `~/.codex/config.toml` on disconnect.
  4. Add tests: writing to a symlinked `AGENTS.md` pointing outside the config dir is refused; `disconnect codex` removes the injected block idempotently.
- **Method**: Verified live on this machine: `~/.codex/AGENTS.md → ~/.claude/CLAUDE.md`, so `connect codex` today would inject the parsidion block into the user's **global agent instructions** rather than a Codex-specific file. `_END_MARKER` exists solely to support removal and is referenced nowhere — the removal function was never written.
- **Verify**: `uv run pytest tests/test_connect.py -v`

### [SEC-117] Vault `config.yaml` becomes subprocess argv[0]
- **Files**: `skills/parsidion/scripts/ai_backend.py:345` (`_config_str("codex_cli", "command", …)`), `:370` (`cmd = [command, "exec"]`), `:353` (`sandbox`); reference gate on `par_mem.binary`
- **Steps**:
  1. Apply the same gate `par_mem.binary` already gets: resolve via `shutil.which(command)` and refuse to execute when it does not resolve to an executable on `PATH`. Reject values containing a path separator unless they resolve to an existing file.
  2. Add an allowlist for `codex_cli.sandbox`: reject `danger-full-access` unless an explicit opt-in key is also set, and log loudly when it is.
  3. Document `codex_cli.command` and `codex_cli.sandbox` in `templates/config.yaml` and `CLAUDE.md` (this overlaps DOC-009 — coordinate so the Doc agent does not duplicate the edit).
  4. Add a test that an unresolvable `codex_cli.command` raises rather than spawning.
- **Method**: Two config keys turn a config file into arbitrary execution. Held at Medium because `config.yaml` is gitignored **and** excluded from auto-commits, so the documented git-sync path cannot deliver a hostile one — but Dropbox/iCloud/NFS sync, a vault directory writable by another local user, or a deliberate `git add -f` all reach it. The asymmetry with `par_mem.binary`, which does get a `shutil.which` + health-check gate, is the tell that this is an oversight.
- **Verify**: `uv run pytest tests/test_ai_backend.py -k "codex or command or sandbox" -v`

### [SEC-118…SEC-132] Low-severity hardening batch
- **Files / Steps** (each is small and independent; group into one or two commits):
  - **SEC-118** `visualizer/app/api/stats/route.ts:6` — add both guards. *Already done by SEC-102 step 4; verify and skip.*
  - **SEC-119** DNS rebinding — *closed by SEC-102's token guard*; verify and skip.
  - **SEC-120** `visualizer/app/api/graph/route.ts:26`, `summarize/route.ts:38`, `note/diff/route.ts:104` — return a generic message to the client and log the detail server-side, matching the SEC-003 pattern already used in the same files.
  - **SEC-121** `skills/parsidion/scripts/pre_compact_hook.py:376` — replace the bare `is_file()` with `is_allowed_transcript_path()`, as the other four readers do.
  - **SEC-123** `skills/parsidion/scripts/ai_backend.py:262` — pass the prompt on **stdin** instead of `cmd = ["claude", "-p", prompt]`; up to 12 KB of transcript is currently visible via `ps auxww`. Check the CLI supports stdin before switching; if not, use a 0600 temp file and pass its path.
  - **SEC-124** `installer/vault.py:182`, `installer/schedule.py:181`, `:40-81` — quote `{scripts_dir}` / `{uv_path}` in the hook body, cron line, and plist XML. Not attacker-reachable; free to fix.
  - **SEC-125** `skills/parsidion/scripts/summarize_sessions.py:957-961` — TOCTOU: validates `resolved` but writes `target_path`. Assign `target_path = resolved` to close the window.
  - **SEC-126** `skills/parsidion/scripts/vault_review.py:78` — the lock is taken on the private *tmp* file, excluding nobody; take it on the real queue and hold it across the TUI session (or re-read under lock before each mutation).
  - **SEC-126b** `skills/parsidion/scripts/vault_fs.py:608-628` — daily-note append is an unlocked read-modify-write, and the wrapper's detached `nohup` makes concurrent writers routine. Wrap in the existing `flock` helper.
  - **SEC-127** Route the remaining non-atomic writes through `vault_fs.atomic_write_text` (overlaps QA-010, QA-017 — coordinate with the 3c agent so each file is edited once).
  - **SEC-127b** `installer/schedule.py:196` — a non-zero `crontab -l` exit currently means "no crontab" and replaces the user's entire crontab. Copy the correct handling from the uninstall path at `:304`.
  - **SEC-128** Add `--` separators before note-derived positional arguments at `vault_doctor.py:1111-1123`, `:1262`, `summarize_sessions.py:1180`, `vault_links.py:358-368`, `session_start_hook.py:205-218`, so `[[--help]]` cannot parse as a flag. `searchServer.ts:49-55` already does this correctly — copy it.
  - **SEC-130** Consolidate the four hand-rolled containment checks (`vault_index.py:462`, `session_start_hook.py:526`, `summarize_sessions.py:959`, `vault_path.py:245`) into one shared helper. **Must run after SEC-106.** All four are currently correct, so this is de-duplication, not a bug fix.
  - **SEC-131** `install.py:735` — add `choices=range(24)` to `--summarizer-hour`; `99` currently yields a job that silently never fires.
  - **SEC-132** `SECURITY.md` — add the visualizer to the scope table. *Fold into DOC-011's edit; the Doc agent owns this file.*
  - **SEC-114** Vault root is 0755 with 0644 notes and a 0644 `embeddings.db` holding 37 MB of note text. Add `mkdir(mode=0o700)` on the vault root in `vault_fs.py:642-645` plus a chmod in the permission-repair pass — far cheaper than chmod-ing thousands of notes.
- **Verify** (whole batch):
  ```bash
  uv run ruff format --check . && uv run ruff check . && uv run pyright . && uv run pytest tests/
  cd visualizer && bunx tsc --noEmit && bun run lint && bun test
  ```

---

# Phase 3b — Architecture (Remaining)

### [ARC-005] `VAULT_DIRS` single-source-of-truth is silently dead
- **Files**: `installer/paths.py:95-131` (`_extract_vault_dirs` and its fallback), `skills/parsidion/scripts/vault_path.py:72` (the real definition), `tests/test_vault_dirs_sync.py`
- **Steps**:
  1. Preferred fix: delete `_extract_vault_dirs` entirely and have `installer/paths.py` import `VAULT_DIRS` from `vault_path` directly. `vault_path` is stdlib-only, so the installer's stdlib-only constraint permits it. Add the scripts dir to `sys.path` the same way other installer code does, or import via the package once ARC-004 lands.
  2. If a direct import is not viable in the installer's load order, point the regex at `vault_path.py` instead of `vault_common.py` and make the fallback **loud** — `raise` (or at minimum `_warn` unconditionally) rather than returning silently.
  3. Rewrite `tests/test_vault_dirs_sync.py` to assert the *mechanism*, not just the values: monkeypatch `vault_path.VAULT_DIRS` to a sentinel and assert `installer.VAULT_DIRS` reflects it. Under the current code that test fails, which is the point.
  4. Fix the test's failure message, which says "install.py should extract VAULT_DIRS from vault_common.py source" — now false and misdirecting.
- **Method**: `_extract_vault_dirs()` regex-scans `vault_common.py` for `^VAULT_DIRS: list[str] = [...]`. After the ARC-005 module split, `vault_common.py` only *re-exports* the name (line 83); the canonical definition moved to `vault_path.py:72`. The regex therefore never matches, the function silently returns the hardcoded fallback at `:103-115`, and `installer.VAULT_DIRS` **is** that fallback at runtime. The existing test passes only because the fallback still coincidentally matches, so it validates values while the mechanism is inoperative — a guard test that has rotted into a tautology. Step 3 is the part that prevents recurrence.
- **Verify**: `uv run pytest tests/test_vault_dirs_sync.py -v` (must fail before the fix, pass after)

### [ARC-010] The summarizer can never write a `knowledge` note
- **Files**: `skills/parsidion/scripts/summarize_sessions.py:208-217` (`_TYPE_FOLDERS`), `:607-618` (`_VALID_NOTE_TYPES`), `:502` (the prompt's hardcoded type prose)
- **Steps**:
  1. Add `"knowledge": "Knowledge"` to `_TYPE_FOLDERS`.
  2. Add `"knowledge"` to `_VALID_NOTE_TYPES`.
  3. At `:502`, replace the hardcoded prose type list with an interpolation from `_VALID_NOTE_TYPES` — e.g. `", ".join(sorted(_VALID_NOTE_TYPES))` — so the prompt cannot drift from the validator again. `vault_doctor.py:1418-1424` already does this correctly; copy the approach.
  4. Add a test asserting `set(_VALID_NOTE_TYPES) == set(vault_doctor.VALID_TYPES)` — this is the guard that makes the class of bug non-recurring.
  5. Add a test that a model response with `type: knowledge` routes to `Knowledge/`.
- **Method**: `knowledge` is first-class everywhere else — `vault_doctor.py:73-85` `VALID_TYPES`, `vault_new.py:26`, `vault_path.py:81` (`Knowledge` is a vault dir), `templates/knowledge.md`, and root `CLAUDE.md` documents both `type: …|knowledge` and a routing row for it. Only the summarizer's three constants omit it. Two failure modes result: the prompt never offers the type, so no session-derived note reaches `Knowledge/`; and if the model emits it anyway — plausible, since the dedup block shows it similar existing notes — validation rejects it, `write_note` refuses, and the session burns three AI calls before dead-lettering. Step 4 is the structural fix; the shared `note_schema` module is ARC-009's job (Phase 5), but the assertion works today. **Do this before ARC-008 and ARC-009**, both of which move this code.
- **Verify**: `uv run pytest tests/test_summarize_sessions.py -k "type or knowledge or folder" -v`

### [ARC-011] Shipped config template produces six false validation warnings
- **Files**: `skills/parsidion/scripts/vault_config.py:350-455` (`_CONFIG_SCHEMA`), `skills/parsidion/templates/config.yaml`
- **Steps**:
  1. Add these keys to `_CONFIG_SCHEMA` — all are genuinely read by the code: `session_stop_hook.transcript_tail_lines`, `session_stop_hook.pi_transcript_tail_lines`, `summarizer.transcript_tail_bytes`, `summarizer.rebuild_graph`, `summarizer.graph_include_daily`, `event_log.path`.
  2. Add `summarizer.ai_timeout` to the schema **and** the template — it is read at `summarize_sessions.py:1039` and `:1362` but declared nowhere.
  3. Add the `ai`, `ai_models`, and `codex_cli` sections to `templates/config.yaml` (they exist in the schema but not the template). Coordinate with SEC-103, which owns this file in Phase 1, and with DOC-009 in Phase 3d.
  4. Decide `event_log.path` and `adaptive_context.decay_days` and `defaults.sonnet_model` (DOC-010): each is documented but never read. Either implement the read or remove from schema+template+docs. Recommend removing `defaults.sonnet_model` (superseded by `ai_models.<backend>`) and implementing `event_log.path` (cheap, and already in the template).
  5. **Add the regression-proof test**: assert `validate_config()` returns `[]` for the shipped `templates/config.yaml`. One test that kills this entire drift class.
- **Method**: Reproduced by copying the repo's own template into a clean vault and calling `validate_config()` — 6 warnings, every one for a key the code honors. Since `validate_config()` runs at `session_start_hook.py:1131`, every user who follows the documented setup sees six spurious warnings at every session start, which trains them to ignore the validator entirely. Meanwhile the section that selects the AI backend is absent from the template users are told to copy. **Blocks DOC-009/010/015** — land this first so the doc fixes describe the corrected state.
- **Verify**:
  ```bash
  uv run pytest tests/ -k "config and (schema or template or validate)" -v
  # the new test is the real gate
  ```

### [ARC-012] One unguarded write aborts the entire parallel summarization run
- **Files**: `skills/parsidion/scripts/summarize_sessions.py:1555-1600` (`_run_one` body), `:1432` (dedup-merge write), `:960` (`write_note`'s containment raise), `:1602-1604`
- **Steps**:
  1. Wrap the body of `_run_one` (`:1555-1600`) in `try/except Exception as exc:` that calls `_mark_failure(entry, f"unhandled: {exc}")` and returns the existing failure sentinel, so the task group sees a normal return rather than a raised exception.
  2. Keep `anyio.get_cancelled_exc_class()` re-raised explicitly — do **not** swallow cancellation, or Ctrl-C stops working.
  3. Add a test: one entry whose processing raises still leaves the other entries processed, the queue cleaned, and the index rebuilt.
- **Method**: `run_all` fans out via `anyio.create_task_group()`, which cancels all siblings when any child raises. `summarize_one` guards the AI call and the backlink step but leaves two write paths unguarded. So one malformed session kills all in-flight sessions **and** `main` never reaches its cleanup at `:2177` — the queue is not cleaned, the index is not rebuilt, and the git commit never happens — while notes from already-completed tasks remain on disk. The next run re-processes those sessions and, because their slugs now exist, appends `## Session update` blocks (`:972-997`), compounding duplicate content on every crash. Catching at `_run_one` rather than inside `summarize_one` is deliberate: it is the task-group boundary, so it covers every path including the two unguarded writes.
- **Verify**: `uv run pytest tests/test_summarize_sessions.py -k "run_all or task_group or failure" -v`

### [ARC-013 / SEC-129] `_prune_dead_letters` reads outside the lock it writes under
- **Files**: `skills/parsidion/scripts/summarize_sessions.py:1675-1733`; reference implementation at `:1812-1816` (`remove_processed`) and `vault_fs.py:174-206` (`atomic_write_text`)
- **Steps**:
  1. Restructure to: `os.open(path, os.O_RDWR)` → `flock_exclusive(fd)` → **read inside the lock** → filter → write to a tmp file → `os.replace()` while the lock is still held → release. This is exactly the shape `remove_processed` already uses.
  2. Remove the `seek(0)`/`truncate()` in-place rewrite at `:1724-1725`; it is not crash-atomic.
  3. Preserve 0600 on the tmp file (couples with SEC-109 — do both in one edit).
  4. Add a test that a concurrent `_append_dead_letter` during prune is not lost.
- **Method**: The read at `:1695-1698` is unlocked; `LOCK_EX` is not taken until `:1720-1722`, 25 lines later, immediately before the truncate. Any `_append_dead_letter` (which *does* lock) landing in that window is destroyed. This is reachable in normal operation, not a theoretical race: prune runs on **every** invocation (`main:2097`) and `session_stop_hook.py:204-216` spawns a summarizer on every session end. The consequence is worse than lost visibility — `_dead_lettered_ids` (`:1609`) is the guard that stops a re-queued session from re-billing an AI call, so an erased record means paid re-processing.
- **Verify**: `uv run pytest tests/test_dead_letter.py -v`

### [ARC-014] Enforce API guards through a shared `withApi()` wrapper
- **Files**: `visualizer/lib/apiAuth.ts`, all 12 `visualizer/app/api/**/route.ts`
- **Steps**:
  1. Add `withApi(handler, opts?: {mutation?: boolean})` to `apiAuth.ts`: it runs `requireToken` then `requireSameOrigin` (and, for mutations, whatever `requireAuth` adds), returning early on any guard failure, then delegates to `handler`.
  2. Convert each route's exported `GET`/`POST`/`PUT`/`DELETE` to `export const GET = withApi(async (req) => {...})`.
  3. Add a test that iterates every module under `app/api/` and asserts each exported HTTP method is wrapped — so a new route physically cannot forget the guards.
- **Method**: This is the structural follow-up to SEC-102, which applies the guards by hand. Hand-application is what produced the `/api/stats` gap in the first place, so the wrapper is the durable fix. Must run **after** SEC-102 (Phase 1) — the wrapper builds on the `requireToken` that phase introduces. The enumeration test in step 3 is the part that matters; the wrapper alone just relocates the same fragility.
- **Verify**: `cd visualizer && bunx tsc --noEmit && bun run lint && bun test`

### [ARC-015] `graph.json` is read synchronously and in full on every request (47.5 MB)
- **Files**: `visualizer/app/api/graph/route.ts:30`, `visualizer/app/page.tsx:77-84`, existing client-side delta logic at `visualizer/lib/graphDelta.ts` and `GraphCanvas.tsx:542-555`
- **Steps**:
  1. Replace `fs.readFileSync` with a streamed response (`fs.createReadStream` → `Response` body) so the 47.5 MB is never materialized as a JS string on the event loop.
  2. Compute an `ETag` from the file's mtime+size, set `Cache-Control: no-cache`, and return `304` when `If-None-Match` matches.
  3. Add `GET /api/graph/delta?since=<generated>` returning `{addedNodes, removedNodes, addedEdges, removedEdges}`, with a full-document fallback when `since` is unknown or too old.
  4. Change `app/page.tsx:77-84` to answer the `graph:rebuilt` SSE event with a delta fetch rather than an unconditional full refetch.
  5. Include the vault name in the `graph:rebuilt` payload (`graph/rebuild/route.ts:64` currently emits none) and filter client-side, so two tabs on different vaults stop refetching each other's rebuilds (this resolves half of ARC-039).
- **Method**: Measured: 5,563 nodes / 376,060 edges / 47.5 MB, blocking-read per request, with no caching headers at all, refetched on mount, on every vault switch, on every `graph:rebuilt`, and after every note creation. `lib/graphDelta.ts` already exists precisely to avoid this and is used correctly client-side — the server simply never got the matching endpoint. Steps 1-2 are cheap and deliver most of the win; step 3 is the larger piece and can be split into its own commit.
- **Verify**:
  ```bash
  cd visualizer && bunx tsc --noEmit && bun run lint && bun test && bun run build
  # manual: curl -I localhost:3999/api/graph twice; second must 304 with If-None-Match
  ```

### [ARC-016 / QA-004] No tests for any API route, component, or the vault path guards
- **Files**: new `visualizer/lib/vaultResolver.test.ts`; new `visualizer/app/api/**/route.test.ts` (12 files); existing pattern in `visualizer/lib/*.test.ts`
- **Steps**:
  1. Write `vaultResolver.test.ts` first — it is the highest-value file in the repo with no test. Cover: `..` traversal rejected; symlink escaping the vault rejected; `VAULT_FORBIDDEN_PREFIXES` enforced; a sibling directory (`~/ParsidionVault-evil`) rejected by the `startsWith(root + path.sep)` check; unknown vault name rejected by the allowlist; `realpathAllowingMissing` correctly permits a not-yet-created file inside the vault while rejecting one outside it; **and the resolution precedence order** (explicit → `.claude/vault` → env → default), which `tests/test_vault_resolver_parity.py` does not cover.
  2. Add a `makeRequest(url, init)` helper that constructs a `NextRequest` against a temp vault fixture.
  3. Write one `route.test.ts` per route asserting: 403 on `../` traversal; 401 without the token when `VISUALIZER_TOKEN` is set; 403 on `Sec-Fetch-Site: cross-site`; 400 on missing required params; happy path returns expected shape.
  4. Register these in the ARC-007 CI job.
- **Method**: All 60 existing visualizer tests target pure `lib/` helpers; the 12 route files, 20 components, and — critically — the entire path-traversal defense have none. ARC-002 (silent data loss) is precisely the class of bug one route test would have caught, which is why this should land **before** ARC-002's fix so that fix can be written test-first. Component tests are explicitly out of scope here; route + resolver coverage is where the risk is.
- **Verify**: `cd visualizer && bun test` — expect the new files to run and the suite count to rise well above 60.

### [ARC-018] No atomic write, backup, or lock on `~/.claude/settings.json`
- **Files**: `installer/hooks.py` (13 write sites at `:279,324,394,441,541,624,675,725`), `installer/skill.py:384,407`, `installer/vault.py:323,431,475`
- **Steps**:
  1. Reuse the `_atomic_write_json` helper added by SEC-105 — do not write a second one.
  2. Convert all 13 bare `path.write_text(...)` config write sites to it.
  3. Add an `flock` on a sidecar lock file held across each read-modify-write cycle.
  4. Merge `enable_ai_mode`'s `settings.json` edit (`skill.py:388-411`) into `merge_hooks` so the file is written **once** per install instead of twice via two independent read-modify-write cycles.
  5. Add a test that two concurrent `merge_hooks` calls do not lose either side's changes.
- **Method**: Searching `install.py` + `installer/` for `os.replace|NamedTemporaryFile|flock|backup|\.bak` returns zero hits today. A crash, full disk, or Ctrl-C mid-write truncates the user's `settings.json`, destroying all unrelated Claude Code configuration with no recovery path; two concurrent installers, or an installer racing Claude Code's own settings write, silently lose one side. The project already uses `fcntl.flock` for `pending_summaries.jsonl`, so the pattern is established — it was simply never applied to the more valuable file. Must run **after** SEC-105, which introduces the helper and changes the parse-failure control flow this code must preserve.
- **Verify**: `uv run pytest tests/test_install.py -k "atomic or concurrent or settings" -v`

### [ARC-019] A custom `--vault` path is never persisted, so the runtime resolver cannot find it
- **Files**: `install.py:290-303`, `installer/vault.py:441-476` (`create_vaults_config`), `installer/paths.py:170-189` (`_resolve_vault_root_for_uninstall`), `skills/parsidion/scripts/vault_path.py:285-373` (`resolve_vault`)
- **Steps**:
  1. At the end of `install()`, when the resolved vault differs from `default_vault_root()`, write it into `~/.config/parsidion/vaults.yaml` as both a named `vaults:` entry and as `default:`. Create the file if absent — do not require `--create-vaults-config` for this path.
  2. Repoint `_resolve_vault_root_for_uninstall()` at `vaults.yaml`. It currently parses a `vault_root:` key from `config.yaml` that nothing in the repo ever writes, making the branch unreachable and forcing uninstall to always fall back to the default vault.
  3. Add a test: `install(vault=tmp_path, yes=True)` then assert `resolve_vault()` (with no explicit argument and no `CLAUDE_VAULT`) returns `tmp_path`.
- **Method**: `resolve_vault()` reads four channels — explicit arg, `cwd/.claude/vault`, `$CLAUDE_VAULT`, default root — and the install flow writes the chosen path into **none** of them. `create_vaults_config()` only emits a template whose vault entries are all commented out, and it runs solely behind an opt-in flag. So `uv run install.py --yes --vault ~/WorkVault` — the exact invocation documented at `install.py:656-657` — populates `~/WorkVault` while every installed hook keeps reading `~/ParsidionVault`. A custom-vault install's post-merge hook is also never removed on uninstall, because uninstall cannot find the vault either.
- **Verify**: `uv run pytest tests/test_install.py -k "vault_path or custom_vault or vaults_config" -v`

### [ARC-021] `parsidion-mcp` straddles two code copies and has no vault scoping
- **Files**: `parsidion-mcp/src/parsidion_mcp/tools/ops.py:11,17-45,65`, `tools/context.py`, `tools/search.py`, `tools/notes.py`
- **Steps**:
  1. Replace `SCRIPTS_DIR = vault_common.SCRIPTS_DIR` (hardwired to `~/.claude/skills/parsidion/scripts`) with resolution from the imported package's own `__file__`, so the process subprocesses the same code it imports.
  2. Add an optional `vault: str | None = None` parameter to every MCP tool, threaded to `resolve_vault(explicit=vault)` and appended to subprocess argv as `--vault <path>` for `update_index.py` and `vault_doctor.py`.
  3. Have `vault_context` call the same candidate-selection function `session_start_hook` uses instead of its parallel simplified copy (no graph expansion, no adaptive ranking, no semantic search). If that function is not importable, extract it — do not duplicate a third time.
  4. Add tests asserting `--vault` reaches the constructed argv (the existing `test_vault_doctor_scan_only_omits_fix_flag` is the pattern to follow).
- **Method**: The same process *imports* `vault_common`/`vault_search` from the repo via the editable install while *subprocessing* scripts from `~/.claude` — two copies of the same codebase serving one request. This is masked on macOS/Linux because `install_skill` symlinks `~/.claude/skills/parsidion` back to the repo, but is real on Windows where it copies (ARC-026). Separately, multi-vault support is real in the Python layer and in the visualizer but silently absent at the MCP layer, which always operates on the default vault. The `~/.claude` hardcoding also contradicts the stated agent-agnostic goal.
- **Verify**: `make -C parsidion-mcp checkall`

### [ARC-022] Partial install leaves inconsistent state and always reports success
- **Files**: `install.py:459-554` (the 12-step sequence), `installer/vault.py:68,84` (unguarded `unlink`/`rmdir`)
- **Steps**:
  1. Change each of the 12 steps to return `bool` (or raise). Accumulate results.
  2. After the sequence, print a summary of failed steps and `return 1` when any failed, instead of the current unconditional `return 0`.
  3. Move `create_templates_symlink`'s `link.unlink()` (`vault.py:68`) and `link.rmdir()` (`:84`) **inside** the surrounding `try`, so an `OSError` warns rather than killing the process mid-install with a traceback.
  4. Add a test that a failing `merge_hooks` makes `install()` return non-zero.
- **Method**: Only step 1 can abort today (`if not SKILL_SRC.exists(): return 1`); every later step swallows errors into `_warn(...)` and returns `None`, and `install()` inspects no result. So if `merge_hooks` fails to write `settings.json`, the skill symlink and vault dirs already exist, the installer prints "Installation complete!" and exits 0, and `make install`/CI cannot detect the broken install. The uninstall asymmetries in this finding (never removing the injected `AGENTS.md`/`GEMINI.md` blocks, never reverting `[features] hooks = true`, never running `uv tool uninstall`, never removing the `Templates` symlink) are covered by SEC-116 steps 2-3 — coordinate rather than duplicating.
- **Verify**: `uv run pytest tests/test_install.py -k "return_code or failure or partial" -v`

### [ARC-023…ARC-041] Medium architecture batch
Group these into themed commits; each is independent unless noted.

- **ARC-023 (import cycles)** — `vault_hooks.py:19` ↔ `vault_fs.py:585`; `vault_search.py:42` ↔ `parmem_backend.py:399`; `vault_search.py:731` ↔ `vault_tui.py:50`. **Steps**: hoist the shared symbols (`TRANSCRIPT_CATEGORY_LABELS`, the search entry point) into a lower leaf module so the lazy in-function imports can become top-level. **Verify**: `uv run pyright .` plus a test that imports each module in isolation.
- **ARC-024 (facade decoupling)** — migrate consumers from `import vault_common` to leaf imports, starting with the latency-sensitive ones: `session_start_hook.py`, `ai_backend.py:15`, `parmem_backend.py:26` (the last two are layering inversions). Keep `vault_common` as the external back-compat facade. Measured payoff: `import vault_path` loads 1 module in 4.5 ms vs `import vault_common` loading 7 in 19 ms, on a hook that blocks session startup. **Verify**: `uv run pytest tests/` + time `python -c "import session_start_hook"`.
- **ARC-025 (installer layering)** — hoist the 25+ non-cyclic function-local imports in `installer/` to module level (`from installer.colors import bold, dim` appears 9× in `hooks.py`; `colors.py` imports nothing). The one genuine cycle edge is `skill.py:347 → hooks`. Move `enable_ai_mode`'s settings.json mutation into `hooks.py` where every other settings write lives, and move `uninstall()` out of `skill.py` into a new `installer/uninstall.py`. **Note**: overlaps ARC-018 step 4 — do them together.
- **ARC-026 (two deployment models)** — `installer/skill.py:68,106` symlinks on Unix, `copytree`s on Windows. Document both models in `CLAUDE.md`'s "Installed vs Source Paths" table (currently describes only the copy model) and in `README.md`. Decide whether to unify; if kept, add a note that ARC-021's dual-source problem is live only on Windows. **Verify**: doc-only unless unified.
- **ARC-027 (subprocess consistency)** — two concrete bugs plus cleanup. (a) Add `--no-project` at `summarize_sessions.py:1854` — without it `uv` walks up from the inherited cwd, which for the auto-launch path is the *user's project directory*, and syncs an unrelated project's dependencies; the failure is swallowed into a warning at `:1868` so the index silently goes stale while the run reports success. (b) Forward `--vault` at `vault_links.py:363-367` — `find_related_by_semantic` never passes it, so multi-vault users get backlinks computed against the wrong vault, then `strip_unresolved_wikilinks` strips them and masks the bug. (c) Longer term, import `vault_search`'s entry point in-process instead of spawning (this also fixes ARC-028). **Verify**: `uv run pytest tests/test_summarize_sessions.py tests/ -k "no_project or vault_arg or links" -v`.
- **ARC-028 (embedding cold starts)** — each queue entry spawns `vault_search.py` twice and each spawn lazily loads a ~67 MB ONNX model (`vault_search.py:148-150`); at `max_parallel: 5` that is up to 5 concurrent loads. Fix by importing in-process per ARC-027(c). Also: `_dead_lettered_ids` re-parses the whole dead-letter file once per entry (`:1299`) — hoist to one read; and `read_project_names` (`:359-385`, called at `:1537`) reads every note in the vault to collect `project` values already available as a `note_index` column — query the DB.
- **ARC-029 (prompt centralization)** — move the six inline prompt literals into `skills/parsidion/templates/prompts/`. Deduplicate the tag rules restated twice inside one function (`summarize_sessions.py:444-448` and `:451-453`). Make the summarizer interpolate its type enum from the constant as `vault_doctor.py` does — **already covered by ARC-010 step 3**, so verify rather than redo.
- **ARC-030 (failure classification)** — replace `_mark_failure(entry, reason)`'s free-text string with an enum carrying `retryable: bool`. Dead-letter non-retryable classes on attempt 1 instead of burning 3 AI calls, and log validation failures distinctly since that class usually indicates a code defect rather than a bad model response. **Verify**: `uv run pytest tests/test_dead_letter.py -v`.
- **ARC-031 (`LAST_BACKEND` global / `score` ambiguity)** — return the backend and a score-kind discriminator in the result envelope instead of the `global LAST_BACKEND` at `vault_search.py:216,256`. par-mem returns RRF rank-fusion values while the embeddings path returns cosine similarity under the same `score` field, and `min_score` applies only to the latter — yet the `vault-explorer` agent, `parsidion-mcp`, and the visualizer all filter on it. **Verify**: `uv run pytest tests/test_parmem_search.py tests/ -k score -v`.
- **ARC-032 (broken slideshow links)** — move the five `*-slideshow.html` files and `parsidion-architecture.png` under `docs/`, which fixes both the 404s and the Pages artifact scope in one move, then update `README.md:17`. This also removes 2.9 MB from the repo root. Alternatively drop the links. **Verify**: `ls docs/*slideshow*` and confirm the Pages workflow globs them.
- **ARC-033 (`vault-deduplicator` not installed)** — add `agents/vault-deduplicator.md` to the list at `installer/paths.py:29-31`, and add a test asserting the installed-agents list covers every file matching `agents/*.md` so the manifest cannot silently drift again. **Verify**: `uv run pytest tests/test_install.py -k agent -v`.
- **ARC-034 (YAML parser + cache-by-reference)** — make `load_config` return a deep copy (or a frozen mapping) instead of the cached dict by reference at `vault_config.py:313`, so a caller mutating it cannot corrupt config process-wide. Raise `lru_cache(maxsize=1)` to something like 8 so alternating vaults stop thrashing. Add a schema-driven depth check for the 3-level cliff at `:214-219` (`ai_models.<backend>.<tier>` already uses two levels). Update the stderr messages still carrying the pre-split `vault_common:` prefix. **Verify**: `uv run pytest tests/test_config_local_overlay.py -v`.
- **ARC-035 (duplicated visualizer helpers)** — the `findNote` triplication is **QA-006**; do not duplicate that work. This entry covers the second half: delete the duplicate `findParsidionScript` in `lib/vaultStatsServer.ts:47-53`, which omits the `PARSIDION_SCRIPTS_DIR` override honored by the canonical `lib/scriptResolver.ts:13-29` — so setting that env var currently redirects graph rebuild and search but silently not summarization. **Verify**: `cd visualizer && bun test`.
- **ARC-036 (git subprocess hardening)** — extract `lib/searchServer.ts`'s wrapper (which already has `SEARCH_TIMEOUT_MS`, `MAX_STDERR_BYTES`, abort wiring, and a concurrency limiter) into a shared `runScript(cmd, args, {timeoutMs, maxBytes, signal})` and use it at `note/diff/route.ts:75-80`, `note/history/route.ts:68-76`, `graph/rebuild/route.ts:48`. Wire `req.signal` through. **Verify**: `cd visualizer && bun test && bunx tsc --noEmit`.
- **ARC-037 (God hook + perf)** — split `useVisualizerState` into `useVaultSelection` / `useNoteTabs` / `useGraphControls`; extract the embedded Brandes betweenness (`:25-75`) into `lib/`. Debounce the threshold slider (`HUDPanel.tsx:410-412`), which currently runs `graph.clearEdges()` + re-add across 376k edges plus a layout reheat per tick. Snapshot edges into typed arrays in the force-layout attraction pass (`useForceLayout.ts:458-475`), matching the repulsion pass beside it which was already optimized to `Float64Array`. **Blocks QA-013 and touches `app/page.tsx`, `GraphCanvas.tsx`, `HUDPanel.tsx` — do ARC-002 first** (it touches `useVisualizerState.ts` but only two lines). **Verify**: `cd visualizer && bun test && bun run build`.
- **ARC-038 (`graph.json` contract parity)** — emit a JSON Schema from `build_graph.py:376-398` and validate both `visualizer/lib/graph.ts`'s interface and a real `graph.json` fixture against it in CI. Extend `tests/test_vault_resolver_parity.py` beyond `VAULT_FORBIDDEN_PREFIXES` to cover resolution **precedence** (this overlaps ARC-016 step 1 — coordinate). **Verify**: `uv run pytest tests/test_build_graph_parmem.py tests/test_vault_resolver_parity.py -v`.
- **ARC-039 (SSE lifecycle)** — add a `cancel()` handler to the `ReadableStream` at `vault/events/route.ts:135-165` that calls `releaseWatcher` (`:100-109`), so teardown paths that do not abort stop leaking `chokidar` watchers. Add a periodic `: keepalive\n\n` heartbeat. Raise the `vaultBroadcast` `EventEmitter`'s default 10-listener cap. The vault-scoping half of this finding is **ARC-015 step 5** — do not duplicate. **Verify**: `cd visualizer && bun test`.
- **ARC-040 (API error semantics)** — pick one conflict encoding (recommend HTTP 409 + `{error, ...}`) and apply it at `note/route.ts:127`, `:172`, and `summarize/route.ts:32`; **coordinate with DOC-006**, which documents 409, so fixing the code here makes the doc correct rather than the reverse. Wrap `req.json()` at `note/route.ts:77,147` to return 400 on malformed bodies instead of 500, and type-validate `content`. Make `fetchNoteContent`/`saveNote`/`deleteNote`/`createNote` check `res.ok` before `res.json()` (`lib/graph.ts:50` already does — copy it). **Verify**: `cd visualizer && bun test`.
- **ARC-041 (server-only guards)** — add `import 'server-only'` to `lib/vaultResolver.ts`, `lib/vaultStatsServer.ts`, `lib/scriptResolver.ts`, `lib/searchServer.ts`. Move the `CommitEntry` type out of `app/api/note/history/route.ts` into `lib/types.ts` so `components/CommitList.tsx:3` and `components/HistoryView.tsx:8` stop importing types from a module that imports `child_process`. **Requires ARC-007's `next build` in the gate to be meaningfully verified.** **Verify**: `cd visualizer && bun run build`.

### [ARC-042…ARC-048] Low architecture batch
- **ARC-042** — adopt one `_resolve(cli, section, key, default)` helper across `install.py`/`summarize_sessions.py:1976-2076`; make `--rebuild-graph`/`--graph-include-daily` tri-state (`default=None`) so a config `true` **can** be overridden off from the CLI, which it currently cannot.
- **ARC-043** — move `migrate_memory.py`, `migrate_research.py` (1,371 LOC of one-time migrations) and `html-to-md.py` out of the installed runtime scripts directory into `tools/`. Update `installer/paths.py` if it enumerates them.
- **ARC-044** — reduce the three-way duplication across `CLAUDE.md` (33 KB), `README.md` (63 KB), `docs/ARCHITECTURE.md` (95 KB). Confirmed drift to fix: `CLAUDE.md` claims `VAULT_ROOT`/`TEMPLATES_DIR` are "patched by installer" while `vault_path.py:45-46` says the opposite and the installer contains zero references to either name; and the `History/` vault folder is documented in SKILL.md/README/ARCHITECTURE but missing from `CLAUDE.md`'s subfolder list. **Owner: Documentation (3d)** — sequence with DOC-021/DOC-022, adjacent sections.
- **ARC-045** — remove empty `docs/ARCHITECTURE/` and `temp_shaders/`, delete stale `build/lib/` (also ARC-001 step 4), remove the vestigial root `.nojekyll`, and replace `installer/skill.py:40`'s `__import__('os').getpid()` with a normal import.
- **ARC-046** — largely resolved by ARC-017 (Phase 5). Independently: add one non-`--dry-run` install test that asserts the resulting `settings.json`, and one install→uninstall round-trip test asserting the tree returns to its prior state.
- **ARC-047** — remove the unused `graphology-layout-forceatlas2` dependency (`useForceLayout.ts:4-5` states outright "It does NOT use FA2") and fix the stale `HUDPanel.tsx:561` tooltip naming it. Resolve `@types/diff@^8` vs `diff@^9`'s bundled declarations. Pin `typescript`, `eslint`, `@types/node` to majors consistent with `next`/`react`. Add `test`/`typecheck` scripts (also ARC-007 step 5).
- **ARC-048** — (a) resolve the Gemini hook `"timeout": 10000` unit ambiguity — Claude's convention is ms, Codex's is documented as seconds at `hooks.py:260-263`; add a comment stating which applies. (b) `vault_fs.append_to_pending:279-314` silently drops an entry after 5 inode-retry attempts — log it. (c) `summarize_sessions.py:1560-1567` writes the progress `current` field before acquiring the semaphore, so `vault-stats --summarizer-progress` names the last-*queued* session rather than one being processed — move it after acquisition. (d) `--sessions FILE` (`:2188`) skips queue lifecycle but still rebuilds the index and commits, so re-runs re-bill AI calls and append duplicate `## Session update` blocks — either honor the lifecycle or refuse to commit. (e) `ai_backend._run_codex_prompt:390` returns `None` on failure with no logging while `_run_claude_prompt:286-296` logs rc/stdout_len/stderr — add matching logging, which is what makes the known "No result" failure diagnosable on Codex. (f) `parmem_backend._run_parmem:144-206` duplicates `ai_backend._run_prompt_subprocess:151-199`'s process-group-kill logic and the two have already drifted — extract one shared implementation.

---

# Phase 3c — Code Quality

### [QA-001] Lifecycle hooks: 0% coverage and silent failure by design
- **Files**: new `tests/test_subagent_stop_hook.py`, new `tests/test_post_compact_hook.py`; subjects `skills/parsidion/scripts/subagent_stop_hook.py` (lines 25-260), `post_compact_hook.py` (lines 9-140); pattern to copy from `tests/test_hook_integration.py`
- **Steps**:
  1. Build a fixture that writes a synthetic agent transcript JSONL into a temp dir and a temp vault, mirroring `tests/test_hook_integration.py`'s setup.
  2. `test_subagent_stop_hook.py` — assert: a valid payload appends exactly one line to `pending_summaries.jsonl` with `source: "subagent"` and the correct `agent_type`; an `agent_type` in `excluded_agents` (default `vault-explorer`, `research-agent`) writes **nothing**; a duplicate `agent_id` does not double-append; a malformed payload emits `{}` on stdout, exits 0, and writes a `hook_events.log` entry; a missing `agent_transcript_path` does not raise.
  3. `test_post_compact_hook.py` — assert: with a daily note containing a `## Pre-Compact Snapshot` section, the hook returns it as `additionalContext`; with no such section, it returns valid empty JSON; with a malformed daily note, it does not raise; with multiple snapshots, it returns the most recent.
  4. Do **not** remove the `except Exception:  # noqa: BLE001` handlers — silent failure is correct for a hook that must never break the user's session. Instead assert that each swallowed failure produces a `hook_events.log` entry, so the behavior is observable.
- **Method**: Both hooks are registered in `~/.claude/settings.json` and run on every subagent stop and every post-compaction, with not one line exercised by the 840-test suite. Because both deliberately swallow exceptions, a regression produces no error anywhere — and the failure mode of this product is *silent memory loss*: sessions stop being queued, compaction context stops being restored, and the vault looks fine while missing entries. Step 4 is the key design decision: the goal is not to make failures loud, it is to make them *recorded*. **These tests must land before QA-008/ARC-020** collapse the agent hooks, so the refactor is verifiable against pinned behavior.
- **Verify**:
  ```bash
  uv run pytest tests/test_subagent_stop_hook.py tests/test_post_compact_hook.py -v
  uv run pytest tests/ --cov=skills/parsidion/scripts --cov-report=term-missing \
    | grep -E 'subagent_stop_hook|post_compact_hook'      # both must be well above 0%
  ```

### [QA-005] Thirteen subprocess call sites have no timeout
- **Files**: `update_index.py:764`; `vault_merge.py:851`; `vault_doctor.py:2166`; `summarize_sessions.py:1860`; `installer/vault.py:162-164`; `installer/schedule.py:130,134,191,200,288,299,314`; `installer/skill.py:179`. Reference: `ai_backend.py:158-191`
- **Steps**:
  1. Extract `ai_backend.py:158-191`'s pattern — `subprocess.run(..., timeout=N)` wrapped with `TimeoutExpired` handling that escalates to a process-group kill — into a shared helper. `parmem_backend._run_parmem:144-206` already duplicates it (ARC-048f), so this extraction serves both.
  2. Apply it at all 13 sites with sensible per-call timeouts: `update_index.py:764` (graph rebuild) ~300 s; the installer's interactive calls ~30 s; `vault_doctor.py:2166` ~600 s given it runs unattended nightly.
  3. Make each site handle `TimeoutExpired` explicitly — log and return a failure result rather than propagating an exception into a hook.
  4. Add a test using a `sleep 60` stub asserting the call returns within its timeout.
- **Method**: Only 7 of ~36 subprocess sites pass `timeout=` today. `update_index.py:764` is the worst because it runs `build_graph.py` synchronously with no bound and is itself invoked from the summarizer and from post-write hook paths — a hung child stalls the summarizer mid-run and leaves the index stale with no error. The installer cases can hang a first-time install at a blank prompt with no indication why. Reusing the existing correct implementation (rather than adding bare `timeout=`) matters because a plain timeout leaves orphaned grandchildren; the process-group escalation is the part that actually reclaims them.
- **Verify**: `uv run pytest tests/ -k "timeout or subprocess" -v` then `grep -c 'subprocess.run' -r skills installer install.py` vs `grep -c 'timeout=' -r skills installer install.py`

### [QA-006] `findNote` is triplicated and the copies have diverged
- **Files**: `visualizer/app/api/note/route.ts:11` (async, correct), `note/history/route.ts:8` (sync), `note/diff/route.ts:8` (sync), `files/route.ts:15-41` (fourth variant); new `visualizer/lib/findNote.ts`
- **Steps**:
  1. Create `visualizer/lib/findNote.ts` exporting the **async** implementation currently in `note/route.ts:11-27`, using `fs/promises`.
  2. Replace all three route-local copies with an import. Delete the sync versions.
  3. Assess whether `files/route.ts:15-41`'s variant can share the same helper; if its return shape differs, have it call the shared walker and adapt, rather than keeping a fourth traversal.
  4. Keep the `QA-006` explanatory comment from `note/route.ts:7-9`, moved to the new shared module.
  5. Add `visualizer/lib/findNote.test.ts`: finds a nested note; returns null for a missing one; does not escape the vault root.
  6. **Record in the fix report that this also resolves QA-012 for `history` and `diff`**, leaving only `graph`, `vault/events`, `summarize`, and `graph/rebuild` for that entry.
- **Method**: A prior fix converted only the first copy to async, and the source comment at `note/route.ts:7-9` documents that intent — but the other two still call `fs.readdirSync` and recurse over the entire vault tree on every history/diff request, blocking the Node event loop. Textbook shotgun surgery where two of three sites were already missed once. Extracting to `lib/` next to `guardPath` (already correctly deduplicated per the `SEC-012` comment at `vaultResolver.ts:232-235`) follows the pattern the codebase already established for exactly this problem. Must run **after** SEC-102 and ARC-002, which edit the same handlers.
- **Verify**:
  ```bash
  cd visualizer && bun test && bunx tsc --noEmit && bun run lint
  grep -rn 'function findNote\|const findNote' visualizer/app visualizer/lib   # expect exactly one definition
  ```

### [QA-007] Twelve tests assert nothing; forty-eight assert only triviality
- **Files**: `tests/test_vault_stats.py:160,164,186,191`; `tests/test_vault_common.py:498,506,514`; `tests/test_atomic_write_fixes.py:79`; `tests/test_embed_eval.py:265,269`; `tests/test_merge_preview.py:303`; `tests/test_session_start_hook.py:426`
- **Steps**:
  1. For the four `test_vault_stats.py` cases, add `capsys` and assert on rendered content. `test_reads_pending_entries` (`:164`) should assert the printed output contains both entries' source labels and the correct count; add a case with a malformed JSONL line asserting it is skipped rather than crashing.
  2. For the three `test_vault_common.py` flock cases (`:498,506,514`), assert observable lock state rather than absence of exception: a second exclusive acquire fails; a second shared acquire succeeds.
  3. For `test_atomic_write_fixes.py:79`, assert the target file's content **and mode** after the write, and that no `.tmp` residue remains.
  4. For the remaining cases, either add a real assertion or rename to `test_*_does_not_raise` so the weakness is visible in the test name rather than hidden behind a misleading one.
  5. Sweep the 48 `assert X is not None` / `assert isinstance(...)` / `assert True` sole-assertions and strengthen the ones covering behavior with a defined contract; leave genuine smoke tests renamed.
- **Method**: `test_reads_pending_entries` writes two pending entries, calls `vault_stats.run_pending(vault)`, and verifies nothing about what was read — its own comment says *"just confirm no exception"*. All four `test_vault_stats.py` cases would pass against `def run_pending(v): pass`, while `vault_stats.py` sits at **12%** coverage. The name is the real damage: it tells a future maintainer the behavior is pinned when it is not. Renaming the irreducible smoke tests is as valuable as strengthening the fixable ones — it makes the coverage number honest.
- **Verify**:
  ```bash
  uv run pytest tests/test_vault_stats.py tests/test_vault_common.py -v
  # sanity: stub out run_pending's body locally and confirm the tests now FAIL, then revert
  ```

### [QA-008 / ARC-020] Five agent-extension hooks are ~90% copy-paste, all at 0% coverage
- **Files**: `skills/parsidion/scripts/codex_session_start_hook.py` (77), `gemini_session_start_hook.py` (78), `codex_stop_hook.py` (107), `gemini_session_end_hook.py` (107), `codex_subagent_stop_hook.py` (100); plus `installer/hooks.py:57-78,227-331` and `installer/paths.py:42-87`
- **Steps**:
  1. **Confirm QA-001's hook contract tests are green first.** Do not start this refactor without them.
  2. Create `skills/parsidion/scripts/agent_adapter.py` defining a stdlib-only `AgentAdapter` record: `{name, hooks_file, hook_scripts, entry_builder, transcript_validator, transcript_parser, runtime_env_value}`, plus a registry keyed by runtime name.
  3. Write two generic entrypoints — `run_session_start(adapter)` and `run_session_end(adapter)` — carrying the shared logic. Reduce the five scripts to three-line shims that import the adapter and call the entrypoint.
  4. **Fold `write_hook_event` and `git_commit_vault` into the shared entrypoint.** The Codex/Gemini wrappers currently call each **zero** times (vs 2× each in `session_stop_hook.py`), so `vault-stats --hooks` is blind to every Codex/Gemini session and a Codex-only user's vault silently accumulates uncommitted daily-note changes. Centralizing makes that gap unrepeatable.
  5. Collapse `merge_codex_hooks`/`merge_gemini_hooks` and `remove_codex_hooks`/`remove_gemini_hooks` in `installer/hooks.py` into two generic functions driven by the same registry — the hook-entry shapes differ only in `matcher`, an optional `name`, and the timeout unit.
  6. Add one **parameterized** test covering all runtimes, replacing what would have been five copies.
  7. Bring the pi extension into the registry: `install.py connect` currently accepts only `claude|codex|gemini`, while `extensions/pi/parsidion/` is installed by a standalone bash script and invokes scripts via bare `python3`/`python` (`parsidion.ts:370,399`) rather than the `uv run --no-project` every other caller uses.
- **Method**: 469 lines across five files whose only real variation is three symbols — `is_codex_transcript_path`/`is_gemini_transcript_path`, `parse_codex_transcript_lines`/`parse_gemini_transcript_lines`, and `PARSIDION_RUNTIME = "codex"`/`"gemini"`. The SessionStart wrappers already delegate correctly to `session_start_hook.build_session_context`; the stop wrappers delegate to nothing and reimplement the entire queueing pipeline, which is where the observability gap crept in. Adding a fourth agent today requires 2-3 near-identical scripts plus four installer copies — the "agent-agnostic" goal is asserted but not architected. Everything here must stay stdlib-only.
- **Verify**:
  ```bash
  uv run pytest tests/ -k "adapter or codex or gemini or runtime" -v
  uv run pytest tests/ --cov=skills/parsidion/scripts --cov-report=term-missing | grep -E 'codex_|gemini_'
  uv run ruff check skills/parsidion/scripts && uv run pyright skills/parsidion/scripts
  ```

### [QA-009] Unguarded third-party imports contradict the documented graceful-degradation contract
- **Files**: `skills/parsidion/scripts/build_embeddings.py:27-28`, `skills/parsidion/scripts/embed_eval_run.py:26-34`, `skills/parsidion/scripts/update_index.py:880-899`; reference guard at `vault_search.py:64-71`
- **Steps**:
  1. **Decide the intended behavior first** — this determines the doc fix and must be settled before DOC/QA edits diverge. Recommended: make both degrade gracefully, matching the other three consumers and the existing documentation.
  2. Apply the `vault_search.py:64-71` pattern to both files: wrap the imports in `try/except ImportError` and exit with an actionable message naming the extra (`uv tool install --editable ".[search]"`) rather than a raw traceback.
  3. In `update_index.py:880-899`, verify the spawned child actually started — check `Popen.poll()` shortly after launch, or have the child write a sentinel — and only then print "Embeddings: full rebuild launched in background". Otherwise print a warning naming the log path.
  4. Add a test that simulates a missing `fastembed` (poison `sys.modules`) and asserts a clean error rather than a traceback.
  5. Hand the resulting behavior to the Doc agent for DOC/`CLAUDE.md` reconciliation — **do not edit `CLAUDE.md` from this phase**; the Doc agent owns that file.
- **Method**: `CLAUDE.md` states these files "degrade gracefully when absent," and three of the four optional-dependency consumers genuinely do (`vault_search.py:64-71` and `:147-154`, `vault_merge.py:710-717`, `vault_conflicts.py:169-173`). These two import at module top level and raise a raw `ImportError`. The user-visible consequence is the worst part: because `update_index.py` spawns `build_embeddings.py` **detached into a log file**, a user without the `search` extra sees a cheerful success message on every index rebuild while a traceback accumulates in `~/.claude/logs/parsidion-embed.log` and embeddings silently never build. Step 3 is what makes the failure visible; step 2 alone would still be silent.
- **Verify**: `uv run pytest tests/ -k "embed and (import or degrade or missing)" -v`

### [QA-010] Backlink rewrite writes note bodies non-atomically
- **Files**: `skills/parsidion/scripts/vault_merge.py:603`
- **Steps**:
  1. Replace `path.write_text(new_content, encoding="utf-8")` with `vault_fs.atomic_write_text(path, new_content)`.
  2. Confirm `vault_merge.py` already imports `vault_fs` (or the facade); add the import if not.
  3. Add a test asserting the target note's mode is preserved after the backlink rewrite (`atomic_write_text` preserves mode — `vault_fs.py:189-199`).
- **Method**: A one-line fix, but it is the highest-value atomic-write gap because of *which* file it truncates. The loop rewrites notes that are merely **link targets** of the merge, not the notes the user asked to merge — so an interrupt during `vault-merge` causes silent collateral data loss in a file the user never mentioned. The repo's atomic-write discipline is otherwise deliberate and consistent (11 sites in `vault_doctor.py`, PID-suffixed temps in `vault_links.py:472`, a crash-atomic queue rewrite in `summarize_sessions.py:1808-1812`), so this is an exception to an established rule, not a missing convention.
- **Verify**: `uv run pytest tests/test_vault_merge.py tests/test_atomic_write_fixes.py -v`

### [QA-011] `/api/stats` is the only route with no guard
- **Files**: `visualizer/app/api/stats/route.ts:6-18`
- **Steps**:
  1. **Check whether SEC-102 (Phase 1) already fixed this** — its step 4 explicitly adds both guards to this file. If so, mark no-change-needed and move on.
  2. If not, add `const originError = requireSameOrigin(req); if (originError) return originError` plus `requireToken(req)` as the first statements, matching `summarizer/status/route.ts:8-9`.
- **Method**: Listed separately because it was found independently by two domains; the security pass owns the actual edit. Recording it here prevents a second agent from re-editing the file and producing a conflict.
- **Verify**: `cd visualizer && bun test && grep -n 'require' visualizer/app/api/stats/route.ts`

### [QA-012] Synchronous `fs` calls in five route handlers
- **Files**: `visualizer/app/api/graph/route.ts:30`, `vault/events/route.ts:18`, `summarize/route.ts:25`, `graph/rebuild/route.ts:32` (plus `note/diff/route.ts:10` and `note/history/route.ts:10`, **already resolved by QA-006**)
- **Steps**:
  1. Confirm QA-006 has landed; skip `note/diff` and `note/history`.
  2. Convert the remaining four to `fs/promises`.
  3. `graph/route.ts:30` is the highest-value one and is **owned by ARC-015** (which replaces the read entirely with a stream + ETag). Coordinate: if ARC-015 has landed, this is a no-op for that file.
- **Method**: `note/route.ts:7-9` documents the intent to convert all sync fs to async to avoid blocking the event loop; the change was applied to one file. This entry finishes the propagation. Prioritize by blocking cost: `graph/route.ts` reads 47.5 MB synchronously and is by far the worst.
- **Verify**: `cd visualizer && bunx tsc --noEmit && bun test` then `grep -rn 'readFileSync\|readdirSync\|existsSync' visualizer/app/api`

### [QA-013] `GraphCanvas.tsx` God component — 1,055 lines, 26 `useEffect` hooks
- **Files**: `visualizer/components/GraphCanvas.tsx` (ref mirrors at `:261-263`, `:338-343`; sigma teardown at `:868`)
- **Steps**:
  1. Add a `useLatest<T>(value: T): RefObject<T>` helper in `lib/`.
  2. Replace the ~12 single-line ref-mirror effects with **one** `useLatest(allRenderOptions)` holding a single options object; update the sigma event handlers to read `optsRef.current.<field>`.
  3. Extract sigma lifecycle — instance creation, event binding, teardown — into a `useSigmaInstance` hook, preserving the correct teardown already at `:868`.
  4. Do **not** change rendering behavior; this is a structural refactor. Verify visually with the visualizer running before/after if practical.
- **Method**: The ref-mirror pattern is a legitimate workaround for reading fresh props inside sigma event handlers, but at a dozen instances it becomes invisible coupling: adding a new prop and forgetting its mirror yields a stale value inside a callback with **no compile-time or lint signal**. Collapsing to one options object makes the coupling structural instead of by-convention. Teardown is already correct, so this is not a leak fix. **Blocked by ARC-037**, which splits `useVisualizerState` and changes this component's props — do ARC-037 first or accept rework.
- **Verify**: `cd visualizer && bunx tsc --noEmit && bun run lint && bun test && bun run build`

### [QA-014] Ten functions at cyclomatic complexity ≥ 27
- **Files**: `installer/skill.py:464` (51), `visualizer/components/ReadingPane.tsx:37` (40), `vault_stats.py:981` (37), `update_index.py:269` (36), `summarize_sessions.py:1971` (35), `:254` (32), `:1236` (32), `session_stop_hook.py:276` (34), `session_start_hook.py:704` (33), `installer/skill.py:49` (27)
- **Steps**:
  1. Convert the `main` dispatchers (`vault_stats.py:981`, `summarize_sessions.py:1971`, `session_stop_hook.py:276`) to a `{flag: handler}` table. These are argparse dispatch, so the metric is inflated — the win is readability, not risk reduction. Low priority.
  2. Prioritize the four with genuine algorithmic complexity: `update_index.py:269 build_index`, `summarize_sessions.py:254 preprocess_transcript`, `:1236 summarize_one`, `session_start_hook.py:704 build_session_context`.
  3. For `build_session_context` specifically, extract three phases into named functions: note selection, graph expansion (Tier 1/Tier 2), and rendering. They are already conceptually separate.
  4. `installer/skill.py:464 uninstall` (51) and `:49 install_skill` (27) are **owned by ARC-017 in Phase 5** — skip them here.
  5. `ReadingPane.tsx:37` (CC 40, 606 lines) — extract the render branches into sub-components.
- **Method**: Do not chase the metric uniformly. Argparse dispatch at CC 35 is not the same risk as `summarize_one` at CC 32, where every branch mutates vault state. Steps 2-3 are the real work; step 1 is cosmetic and can be deferred. Note `preprocess_transcript` and `summarize_one` are inside `summarize_sessions.py`, which ARC-009 restructures in Phase 5 — coordinate so this work is not redone.
- **Verify**: `uv run pytest tests/ -v` after each extraction; complexity re-measured via par-mem `find_most_complex_functions` (parameter is `top_n`, not `limit`).

### [QA-016] CLI-facing modules are effectively untested
- **Files**: `vault_stats.py` (12%), `vault_review.py` (11%), `vault_tui.py` (22%), plus `check_graph_coverage.py`, `html-to-md.py`, `migrate_memory.py`, `migrate_research.py`, `embed_eval*.py`
- **Steps**:
  1. Start with `vault_review.py`'s queue-mutating paths — `--clear` empties the pending queue and is completely unverified. Assert: `--clear` with confirmation empties it; without confirmation leaves it intact; `--list` does not mutate.
  2. Then `vault_stats.py`'s rollup generators `run_weekly:625` (CC 24) and `run_monthly:756` (CC 22) — both **write notes into the vault**. Assert the generated note's frontmatter and that re-running is idempotent.
  3. Then the `--pending`, `--graph`, `--hooks` render paths using `capsys` (this overlaps QA-007 — do them in the same pass).
  4. Leave `migrate_*.py` and `html-to-md.py` untested if ARC-043 moves them out of the runtime scripts directory; note that decision in the fix report.
- **Method**: Every one of these is installed as a user-facing global command via `pyproject.toml [project.scripts]` or invoked by the nightly job. Prioritize strictly by destructiveness: `vault-review --clear` destroys queue state, and the rollup generators write files. Pure display paths matter less and are partly covered by QA-007's work.
- **Verify**: `uv run pytest tests/ --cov=skills/parsidion/scripts --cov-report=term-missing | grep -E 'vault_review|vault_stats|vault_tui'`

### [QA-017] Non-atomic writes of generated index files
- **Files**: `update_index.py:643` (MANIFEST.md), `:826` (CLAUDE.md), `:829` (TAGS.md); `vault_doctor.py:2011` (graph.json), with an existing atomic JSON writer at `vault_doctor.py:196`
- **Steps**:
  1. Route the three `update_index.py` writes through `vault_fs.atomic_write_text`.
  2. Route `vault_doctor.py:2011` through the atomic JSON writer already defined at `vault_doctor.py:196` in the same file.
  3. Add a test asserting no partial file is observable mid-write (write a large payload and assert the destination is either absent or complete).
- **Method**: Lower severity than QA-010 because all four are regenerable, but the vault's `CLAUDE.md` is read by `session_start_hook` at session start — a half-written file injects truncated context into a live agent session, which is a subtle and hard-to-diagnose failure. The `graph.json` case is notable because the file is 47.5 MB, so the write window is wide, and an atomic writer already exists 1,800 lines up in the same file.
- **Verify**: `uv run pytest tests/test_atomic_write_fixes.py tests/test_index_enhancements.py -v`

### [QA-018…QA-022] Low code-quality batch
- **QA-018** — `visualizer/app/api/note/history/route.ts:36`: rename `notPathParam` → `notePathParam` (5 occurrences in the handler; the sibling `diff/route.ts` has it right). **Verify**: `cd visualizer && bunx tsc --noEmit`.
- **QA-019** — `visualizer/lib/__fixtures__/search/slow/vault_search.py:7`: replace `time.sleep(1.5)` with an injectable delay or reduce to ~0.1 s. It is over 20% of the 6.63 s bun test wall time and timing-dependent on loaded CI. Confirm the test asserting timeout behavior still exercises the path. **Verify**: `cd visualizer && bun test` (wall time should drop noticeably).
- **QA-020** — `tests/test_vault_merge.py:370`: replace the hardcoded `/Users/probello/ParsidionVault/Daily/2026-06/15-probello.md` with `/tmp/vault/Daily/2026-06/15-user.md`. The function under test is pure string logic so it passes anywhere; the point is not encoding one developer's home directory into an assertion. **Verify**: `uv run pytest tests/test_vault_merge.py -v`.
- **QA-021** — `skills/parsidion/scripts/update_index.py:890`: close the parent's copy of `_embed_log` after `Popen` returns. Keeping it open is intentional so the detached child inherits the fd, but the parent's handle leaks; `os.close()` after spawn is safe because the child holds its own. **Verify**: `uv run pytest tests/test_index_enhancements.py -v`.
- **QA-022** — `skills/parsidion/scripts/vault_merge.py:671`: `_is_excluded_from_scan` carries `# noqa: ARG001` for an unused `folder` parameter callers still pass. Either use it in the exclusion logic or remove it from the signature and update all callers (grep first — R10 applies). **Verify**: `uv run ruff check skills/parsidion/scripts/vault_merge.py`.

---

# Phase 3d — Documentation

> **Ownership note for this phase.** The Documentation agent owns `CLAUDE.md`, `README.md`, `docs/ARCHITECTURE.md`, and `SECURITY.md` outright. Architecture items ARC-044 (stale `VAULT_ROOT`/`TEMPLATES_DIR` claim, missing `History/` folder) and Security item SEC-132 (visualizer absent from the security scope table) must be folded into this agent's single pass over those files rather than edited by their originating domain.

### [DOC-001] README's vault git setup leaks credentials and destroys installer protection
- **Files**: `README.md:733-737`; reference `installer/vault.py:113-126`, `docs/VAULT_SYNC.md:75-76,120-124`
- **Steps**:
  1. Replace the `echo ".obsidian/" > .gitignore` line. Preferred: delete the manual `git init` block entirely and point at the installer, which already does this correctly — "The installer initializes the vault as a git repository with a protective `.gitignore` during installation; see `docs/VAULT_SYNC.md`."
  2. If a manual block is kept, reproduce **all ten** installer entries and use `>>` (append), never `>` (truncate).
  3. Add a `> **Security:**` callout matching `docs/VAULT_SYNC.md:120-124` warning that `config.yaml` may hold `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` and must never reach a remote.
  4. Cross-check `README.md:606-611` (which shows the keys) still reads consistently with the corrected guidance.
- **Method**: Verified independently by the orchestrator. The installer writes ten `.gitignore` entries with an explicit code comment forbidding sync of `config.yaml`; the README's `>` truncates that file down to one line and then instructs `git add -A && git commit`, with `docs/VAULT_SYNC.md` completing the path via `git remote add` + `git push`. The two documents currently contradict each other on a credential-handling instruction. Pointing at the installer is better than duplicating the list, because a duplicated list is exactly what drifted.
- **Verify**:
  ```bash
  grep -n 'gitignore' README.md          # no bare `>` redirect into .gitignore
  grep -n 'Security' README.md | head    # callout present near the git section
  ```

### [DOC-002] `vault-export --pdf` is a phantom feature documented in four places
- **Files**: `CLAUDE.md:113`, `CLAUDE.md:322`, `README.md:219`, `README.md:861`; source of truth `skills/parsidion/scripts/vault_export.py`
- **Steps**:
  1. Delete the `--pdf` line from all four locations.
  2. Document the four real-but-undocumented flags: `--list`, `--project`, `--folder`, `--tag` (alongside the already-documented `--vault/-V`, `--html`, `--zip`).
  3. Cross-check `docs/ARCHITECTURE.md:827-829`, which is already correct — use it as the reference wording.
  4. **Sequence with DOC-018**: both edit the same command blocks at `CLAUDE.md:110-124` and `README.md:855-870`. Do them in one pass.
- **Method**: `vault_export.py` defines exactly seven arguments and contains zero occurrences of `pdf` or `pandoc`, verified by AST extraction and `grep -c`. The documented command fails outright with `unrecognized arguments`.
- **Verify**:
  ```bash
  uv run skills/parsidion/scripts/vault_export.py --help    # compare against the docs
  grep -rn 'pdf' CLAUDE.md README.md                        # no vault-export --pdf claims remain
  ```

### [DOC-003] `make graph` Daily-note behavior is documented backwards — code fix first
- **Files**: **code** `skills/parsidion/scripts/update_index.py:757-763`; **docs** `CLAUDE.md:291`, `README.md:475`; reference `build_graph.py:44`, `Makefile:53-54`, `docs/VISUALIZER.md:551` (already correct)
- **Steps**:
  1. **Code half — assign to the 3c agent that owns `update_index.py`, not to this agent.** At `:757-759`, pass `--no-daily` when Daily notes should be excluded (currently it only ever appends `--include-daily`). Fix the message at `:761-763`, which prints "without Daily notes" while doing the opposite.
  2. Decide `make graph`'s intended default. `build_graph.py:44` sets `include_daily=True` and `Makefile:53-54` passes no flags, so today both `graph` and `graph-with-daily` are identical. Recommended: make `make graph` pass `--no-daily` so the two targets actually differ as documented.
  3. **Doc half** — only after the code lands, correct `CLAUDE.md:291` and `README.md:475` to describe the now-true behavior.
  4. Add a test asserting `make graph` and `make graph-with-daily` produce different node counts on a vault containing Daily notes.
- **Method**: Correcting the docs alone would enshrine a code bug — the documented feature (controlling whether Daily notes enter the graph) genuinely does not work, in either direction. `docs/VISUALIZER.md:551` already describes the intended behavior correctly, which is the tell that the intent is the documented one and the code drifted.
- **Verify**:
  ```bash
  make graph && python -c "import json; print(len(json.load(open('$HOME/ParsidionVault/graph.json'))['nodes']))"
  make graph-with-daily && python -c "import json; print(len(json.load(open('$HOME/ParsidionVault/graph.json'))['nodes']))"
  # the two counts MUST differ
  ```

### [DOC-004] `docs/README.md` index links a file excluded from the repository
- **Files**: `docs/README.md:32`, `docs/ARCHITECTURE.md:1261`, `.gitignore:20`
- **Steps**:
  1. Decide: is `docs/ideas.md` meant to be shared? If yes, narrow `.gitignore:20` from the bare `ideas.md` (which matches at any depth) to `/ideas.md` (root only), then `git add docs/ideas.md`. If no, remove the index row at `docs/README.md:32` and the tree entry at `docs/ARCHITECTURE.md:1261`.
  2. Recommended: narrow the gitignore pattern regardless — a bare `ideas.md` matching at any depth is a trap for any future `docs/` or `visualizer/` file with that name.
- **Method**: `git check-ignore -v docs/ideas.md` → `.gitignore:20:ideas.md	docs/ideas.md`, and `git ls-files docs/` does not list it. Every reader who clones the repo gets a dead link from the documentation index.
- **Verify**: `git check-ignore -v docs/ideas.md ; git ls-files docs/ | grep ideas`

### [DOC-005] `bun` is required by the quality gate but absent from every prerequisites list
- **Files**: `CONTRIBUTING.md:14-18`, `README.md:41-48`
- **Steps**:
  1. Add `bun` to both prerequisite lists with a one-line reason: "required for the visualizer and for `make checkall`".
  2. In `CONTRIBUTING.md`, add a setup step for the visualizer (`make visualizer-setup`, i.e. `cd visualizer && bun install`) **before** step 5's `make checkall`.
  3. Note that `make checkall` also requires `parsidion-mcp` dependencies synced.
  4. Cross-check that the `Makefile` targets named in `CONTRIBUTING.md` all still exist.
- **Method**: `Makefile:33`'s `checkall` depends on `visualizer-check`, which is `cd visualizer && bunx tsc --noEmit && bun run lint && bun test`. Grepping `CONTRIBUTING.md` for `bun|visualizer|node|npm` exits 1 — the word appears nowhere, yet the file instructs the reader to run `make checkall`. This blocks first-time onboarding at the first verification step.
- **Verify**: `grep -n 'bun' CONTRIBUTING.md README.md`

### [DOC-006] `docs/VISUALIZER.md` documents an unimplemented API contract
- **Files**: `docs/VISUALIZER.md:674-676`; subject `visualizer/app/api/note/route.ts:78,132`
- **Steps**:
  1. **Coordinate with ARC-040** (Phase 3b), which may standardize conflict responses on HTTP 409. Check whether it has landed.
  2. If ARC-040 landed: the doc's `409` is now correct; fix only the field name — `lastModified` → `baseMtimeMs`.
  3. If ARC-040 did not land: correct **both** — field name to `baseMtimeMs`, and status to 200 with a `{conflict: true, serverContent, mtimeMs}` body, matching `route.ts:132`.
  4. Document the actual request body: `{ stem, path, content, baseMtimeMs? }`.
- **Method**: The route destructures `baseMtimeMs` and gates on `if (baseMtimeMs !== undefined)`; the conflict response passes no status argument, so Next.js returns 200. A client written from the current docs sends the wrong field name — optimistic locking silently never engages — and branches on `res.status === 409`, which never fires, so concurrent edits overwrite each other silently. Preferring the code-fix direction (ARC-040) is better here because 409 is the correct semantic; the doc was arguably right and the code drifted.
- **Verify**: `grep -n 'baseMtimeMs\|409\|lastModified' docs/VISUALIZER.md visualizer/app/api/note/route.ts`

### [DOC-007] `CLAUDE.md:207` names a function that does not exist
- **Files**: `CLAUDE.md:207`
- **Steps**: Replace `` `vault_common._safe_env()` `` with the real readers — `vault_hooks.env_without_claudecode()` and `vault_hooks._configured_env_defaults()` (`vault_hooks.py:252` and `:221`). Also check `CLAUDE.md:312`'s neighbouring claims in the same table while editing (DOC-022).
- **Method**: `grep -rn "def _safe_env"` returns nothing and `hasattr(vault_common, "_safe_env")` is `False` at runtime, while `write_hook_event`, `git_commit_vault`, and `get_config` are all `True`. `CLAUDE.md` is loaded into every AI session in this repo, so a dead symbol name costs every future session a wasted search and may lead one to conclude the feature is missing.
- **Verify**: `python -c "import sys; sys.path.insert(0,'skills/parsidion/scripts'); import vault_hooks; print(hasattr(vault_hooks,'env_without_claudecode'))"`

### [DOC-008] `CLAUDE.md:369` documents a vault-override env var that is never read
- **Files**: `CLAUDE.md:369`
- **Steps**: Replace `VAULT_PATH` with `CLAUDE_VAULT`, and add the missing project-local step so the documented precedence matches `vault_path.py:296-300`: explicit `--vault` flag → `cwd/.claude/vault` → `CLAUDE_VAULT` → default root. Use `README.md:725` and `docs/EMBEDDINGS.md:160` as the reference — both are already correct.
- **Method**: `VAULT_PATH` appears **nowhere** in `skills/`, `installer/`, `install.py`, or `parsidion-mcp/src`. A user who sets it observes no effect and cannot distinguish a broken feature from a misdocumented one. `CLAUDE.md` is the sole outlier among three documents describing the same thing.
- **Verify**: `grep -rn 'VAULT_PATH' --include='*.py' --include='*.md' . | grep -v node_modules` — only intentional historical references should remain

### [DOC-009 / DOC-010 / DOC-015] Config documentation reconciliation (do as one pass)
- **Files**: `CLAUDE.md:187,196-211` (the config table), `skills/parsidion/templates/config.yaml` (lines 3, 102-109, 122)
- **Steps**:
  1. **Confirm ARC-011 (Phase 3b) has landed first** — it adds the six missing keys to `_CONFIG_SCHEMA` and resolves the three inert keys. Document the corrected state, not the current one.
  2. **Confirm SEC-103's maintainer decision has been made** — it owns `templates/config.yaml:102-109`. Do not edit that block until it is resolved (this is DOC-023's blocker too).
  3. **DOC-009** — add the three missing sections to `CLAUDE.md`'s table: `ai` (`backend`), `ai_models` (`claude`, `codex`), `codex_cli` (`command`, `timeout`, `sandbox`, `ephemeral`, `skip_git_repo_check`, `suppress_notify`). These drive the entire multi-backend feature (`ai_backend.py:84`, `:121`). Add them to the template too. Then either substantiate or soften `CLAUDE.md:187`'s claim that the template holds "all options and their defaults".
  4. **DOC-010** — remove `defaults.sonnet_model` from the docs (superseded by `ai_models.<backend>`; it appears only in a stale comment at `ai_backend.py:27` and the schema). For `event_log.path` and `adaptive_context.decay_days`, follow whatever ARC-011 step 4 decided — remove from docs if removed from code, document if implemented.
  5. **DOC-015** — fix `templates/config.yaml:3` (`~/ClaudeVault` → `~/ParsidionVault`) and `:122`'s parenthetical default path.
  6. Add `summarizer.ai_timeout` to the table (read at `summarize_sessions.py:1039`, `:1362`, documented nowhere).
- **Method**: All three findings touch the same table and the same template file, so splitting them across agents guarantees conflicting edits. `_CONFIG_SCHEMA` declares 17 sections while `CLAUDE.md` lists 14 — and the three omitted are precisely the ones controlling which AI backend runs, making the central switch undiscoverable from the config reference. The `~/ClaudeVault` path in the template is worse than cosmetic: a new user following the template's own instruction places `config.yaml` in a directory that does not exist on a fresh install, so the config is silently never loaded.
- **Verify**:
  ```bash
  uv run pytest tests/ -k "config and (schema or template)" -v
  grep -n 'ClaudeVault' skills/parsidion/templates/config.yaml   # expect none
  ```

### [DOC-011 / SEC-132] `SECURITY.md` omits the Gemini adapter and the visualizer
- **Files**: `SECURITY.md:17-24` (Overview), `:28-38` (Scope table), `:40-49` (stdlib-only list)
- **Steps**:
  1. Add the Gemini adapter to the Overview alongside Claude and Codex.
  2. Add `gemini_session_start_hook.py` and `gemini_session_end_hook.py` to the Scope table and the stdlib-only guarantee list. Both were verified stdlib-only, so the existing guarantee already holds — no code change is required.
  3. **Fold in SEC-132**: add the visualizer to the Scope table. It is the only network-facing component and is currently outside the declared scope entirely.
  4. Confirm the "Out of Scope" section does not accidentally exclude either.
- **Method**: `SECURITY.md` mentions Gemini zero times while the adapter is fully implemented (`installer/skill.py:288 install_gemini_md()`, `remove_gemini_hooks`, `_wants_gemini_runtime`, two executable lifecycle hooks) and `README.md:44` lists Gemini CLI as supported. An entire code-execution surface sits outside the declared scope of a security policy, and a researcher would reasonably not report issues in it. Documentation-only.
- **Verify**: `grep -in 'gemini\|visualizer' SECURITY.md`

### [DOC-012] `CONTRIBUTING.md` misstates the PEP 723 exception list
- **Files**: `CONTRIBUTING.md:53,57`; cross-reference `CLAUDE.md:269-273`
- **Steps**:
  1. Regenerate the list from ground truth: `grep -rl '# /// script' skills/parsidion/scripts/` — eleven files, not four (`build_embeddings.py`, `build_graph.py`, `embed_eval.py`, `embed_eval_common.py`, `embed_eval_generate.py`, `embed_eval_report.py`, `embed_eval_run.py`, `html-to-md.py`, `summarize_sessions.py`, `vault_search.py`, `vault_stats.py`).
  2. Remove `vault_new.py` — it has no PEP 723 block and imports only stdlib plus `vault_common`.
  3. Reconcile with `CLAUDE.md:269-273`, which lists `vault_new.py` as stdlib-only (correct) and also lists `vault_merge.py` and `vault_conflicts.py` as stdlib-only though both **lazily** import third-party `sqlite_vec` (`vault_merge.py:711`, `vault_conflicts.py:172`). State the distinction explicitly: "stdlib-only at import time; optional third-party imported lazily behind a guard."
  4. Refresh the rest of `CONTRIBUTING.md` for post-ARC-005 accuracy — it is the stalest root doc (2026-04-27, predating the module split).
- **Method**: This is the governing document for the project's central architectural constraint, and a contributor currently cannot tell which rule applies to which file, with two root docs contradicting each other. The lazy-import nuance in step 3 is the substantive part: those two files genuinely satisfy the spirit of the rule and the docs should say so precisely rather than by omission.
- **Verify**: `grep -rl '# /// script' skills/parsidion/scripts/ | wc -l` matches the documented count

### [DOC-013] `visualizer/README.md` documents the deleted WebSocket server
- **Files**: `visualizer/README.md:3,59,68` and the Architecture section
- **Steps**:
  1. Line 3: replace "live file updates over WebSocket" with Server-Sent Events, referencing `/api/vault/events`.
  2. Line 59: delete the `server.ts` entry from the file listing — the file does not exist.
  3. Line 68: correct `bun run build  # Build Next.js and the custom server` to plain `next build`.
  4. Sweep the Architecture section for remaining `server.ts` / `ws` references.
  5. Use `docs/VISUALIZER.md` (26 SSE mentions, zero WebSocket) and `visualizer/CLAUDE.md` as the reference — both were correctly updated during the migration.
- **Method**: The SSE migration updated the two deeper docs and missed the visualizer's own front-door README, which now describes an architecture removed two releases ago and names a file readers will look for and not find.
- **Verify**: `grep -in 'websocket\|server.ts\|ws' visualizer/README.md` — expect no live references

### [DOC-014] `docs/ARCHITECTURE.md` states three of four `vault_links` signatures incorrectly
- **Files**: `docs/ARCHITECTURE.md:775-778`; source of truth `skills/parsidion/scripts/vault_links.py`
- **Steps**:
  1. Regenerate all four signatures from the source. Correct forms: `find_related_by_tags(new_note_path, new_tags, max_links=5, vault_notes=None, vault=None)` and `add_backlinks_to_existing(new_note_path, related_notes, vault_notes=None, vault=None)`; verify `find_related_by_semantic` and `inject_related_links` the same way.
  2. Fix the prose at `:778`, which wrongly claims `add_backlinks_to_existing` "scans existing vault notes for tag overlap" — it consumes an explicit `related_notes` list.
  3. Consider replacing the hand-written signatures with a pointer to the docstrings, which are complete and correct — only the prose drifted.
- **Method**: Two documented signatures **omit a required positional parameter**, so anyone coding from this doc writes a `TypeError`. `vault_links` is the shared backlink API used by both the summarizer and `parsidion-mcp`, so it has real external consumers.
- **Verify**:
  ```bash
  python -c "import sys,inspect; sys.path.insert(0,'skills/parsidion/scripts'); import vault_links; \
    print(inspect.signature(vault_links.find_related_by_tags)); \
    print(inspect.signature(vault_links.add_backlinks_to_existing))"
  ```

### [DOC-016] `parsidion-mcp/` ships with no README
- **Files**: new `parsidion-mcp/README.md`
- **Steps**: Create a short stub — one-paragraph description, the `make checkall` command, the `[project.scripts]` entry point name, and a prominent link to `../docs/MCP.md` for full documentation. Do **not** duplicate `docs/MCP.md`'s content; it was verified 100% accurate and a second copy will drift.
- **Method**: `parsidion-mcp` is a standalone artifact with its own `[project]` name, version, entry point, and quality gate, and nothing inside the directory points to its documentation two levels up. Anyone landing there directly — the normal path for a standalone MCP server — has no entry point.
- **Verify**: `test -f parsidion-mcp/README.md && grep -c 'docs/MCP.md' parsidion-mcp/README.md`

### [DOC-017] `docs/ARCHITECTURE.md` claims the installer copies `config.yaml`
- **Files**: `docs/ARCHITECTURE.md:1014,1396`
- **Steps**: Replace the "copied to the vault during installation" claim with the actual behavior — the installer writes **individual keys** into an existing file (`install.py:532` sets `vault.username`, `:535` sets `embeddings.enabled`, `installer/skill.py:349` sets `ai_model`) and never copies the template. Point at the manual `cp` documented at `CLAUDE.md:191`.
- **Method**: No copy operation exists anywhere in `install.py` or `installer/`. A reader expects a fully-populated commented `config.yaml` after install, does not find one, and cannot tell whether the install failed.
- **Verify**: `grep -rn 'config.yaml' installer/ install.py | grep -i 'copy\|copyfile\|shutil'` — expect no results

### [DOC-018…DOC-028] Medium documentation batch
- **DOC-018** — `CLAUDE.md:116,324`, `README.md:866`: document `vault-merge`'s real interface (`note_a`/`note_b` positionals plus `--scan`, `--dry-run`, `--execute`, `--from-preview`, `--no-index`, `--no-ai`, `--threshold`, `--top`, `--output`, `--vault/-V`) and correct "merges their content via Claude haiku" — `vault_merge.py:35` imports `ai_backend`, so the model is backend-configurable. Port `skills/parsidion/SKILL.md:328-340`, which is already correct. **Sequence with DOC-002** — same command blocks.
- **DOC-019** — `docs/ARCHITECTURE.md:12,22`: fix two broken TOC anchors. `#subagent-stop-hook` → `#subagentstop-hook` (heading at `:374` is `#### SubagentStop Hook`); `#metadata-query-cli` → the actual slug of `### Metadata Query (vault-search filter mode)` at `:699`. These are the only two genuine broken links repo-wide.
- **DOC-020** — `CLAUDE.md:286-287`: after ARC-006 and ARC-007 land, update the `make checkall` description to reflect that it is non-mutating and that CI now runs the same targets. Remove any claim of CI/local parity that is still false.
- **DOC-021** — `CLAUDE.md`: extend the eleven-component architecture list to cover `ai_backend.py` (currently described behaviorally at `:272` but never named), `build_graph.py`, `check_graph_coverage.py`, and the five Codex/Gemini hook scripts. Use `docs/ARCHITECTURE.md` as the reference — it is the accurate inventory. **Run after any module reorganization** (ARC-004, Phase 5) or re-derive.
- **DOC-022** — `CLAUDE.md:312`: `vault_fs.py` is described as "filesystem traversal, note search"; all five note-search functions live in `vault_index.py` (`find_notes_by_project:534`, `find_notes_by_tag:542`, `find_notes_by_type:550`, `find_recent_notes:558`, `all_vault_notes:620`). Use `vault_common.py:11`'s own docstring, which is correct: "`vault_fs` -- file locking, pending queue, git, daily notes."
- **DOC-023** — `docs/ARCHITECTURE.md:1092-1096`: **BLOCKED on SEC-103's maintainer decision.** Once decided, align the doc's `anthropic_env` example with whatever the template ships. Do not edit before then.
- **DOC-024** — `CLAUDE.md:75-85`: document `vault-search`'s `--backend/-B` (the flagship 0.13.0 par-mem vs embeddings switch, announced in `CHANGELOG.md` but absent from the command reference), plus `--min-score/-s`, `--model/-m`, `--limit/-l`, `--vault/-V`.
- **DOC-025** — `CLAUDE.md:68-69` and `:127-143`: document the missing flags for `summarize_sessions.py` (`--sessions`, `--model`, `--persist`, `--run-doctor`, `--rebuild-graph`, `--graph-include-daily`, `--vault/-V`) and `vault_doctor.py` (`--fix`, `--errors-only`, `--no-state`, `--jobs/-j`, `--timeout`, `--limit`, `--model`, `--strip-prefixes`, and the `notes` positional). **Most important**: add to `docs/ARCHITECTURE.md:481` that `--fix-all` also sets `args.strip_prefixes = True` (`vault_doctor.py:3071-3076`) — an undocumented bulk file rename running in the nightly cron path.
- **DOC-026** — `docs/EMBEDDINGS.md`: fix `:247`'s non-runnable `sys.path.insert(0, '~/.claude/...')` (Python never expands `~` in `sys.path` — use `os.path.expanduser`); correct `:624`'s claim that auto-rebuild is skipped without an existing DB (`update_index.py:884-887` spawns unconditionally, choosing full vs incremental); correct `:256`'s "four `find_notes_by_*` functions" to three, and drop the DB-first claim since all three delegate to `os.walk`.
- **DOC-027** — `docs/VISUALIZER.md`: document `VISUALIZER_TOKEN` (`visualizer/lib/apiAuth.ts:70`), including the corrected post-SEC-102 semantics that it now gates reads as well as writes, and the loopback binding. Fix `visualizer/.env.local.example:1` (`~/ClaudeVault` → `~/ParsidionVault`) and reference the example file from the docs — nothing currently does.
- **DOC-028** — `extensions/pi/parsidion/parsidion.md:19-23`: add the missing `lib/parsidion-status.ts` to the install instructions; `parsidion.ts:22-26` imports it, so following this doc produces a broken extension. `README.md:378-381` and `scripts/install-pi-extension:74-77` both have the correct three-file version.

### [DOC-029…DOC-040] Low documentation batch
- **DOC-029** — `CHANGELOG.md:8`: populate `## [Unreleased]` with the two commits post-dating `v0.13.0`, notably `8e5d549 fix(visualizer): bump Next.js 16.2.10 → 16.2.11 (security)`. A security-relevant change is unrecorded in a changelog that declares Keep a Changelog adherence.
- **DOC-030** — `memory/async-orchestration-test-stub-pattern.md` is a vault note committed into the source repo root. Either document `memory/` in `CLAUDE.md`'s path table and `docs/README.md`, or move it to the vault. Its `related:` wikilinks resolve to nothing here because the targets live in the vault.
- **DOC-031** — remove the empty, untracked, unreferenced `docs/ARCHITECTURE/` directory (also ARC-045).
- **DOC-032** — `docs/CLAUDE.md` (202 bytes): add an H1 and a summary per `DOCUMENTATION_STYLE_GUIDE.md:91-95`, and add it to `docs/README.md`'s index.
- **DOC-033** — `docs/EMBEDDINGS_EVAL.md`: replace 39 per-node `style` lines with `classDef` per `DOCUMENTATION_STYLE_GUIDE.md:355`. `EMBEDDINGS.md`, `ARCHITECTURE.md`, and `PAR-MEM.md` all comply — copy their approach.
- **DOC-034** — replace literal `\n` with `<br/>` in Mermaid labels: 9 in `docs/MCP.md`, 7 in `docs/EMBEDDINGS.md`, 7 in `docs/EMBEDDINGS_EVAL.md`. `ARCHITECTURE.md` already does this correctly.
- **DOC-035** — tag four untagged code fences as `text` (directory trees): `EMBEDDINGS.md:615`, `MCP.md:476`, `VISUALIZER.md:234`, `VISUALIZER.md:928`.
- **DOC-036** — `docs/MCP.md:145-152`: `parsidion-mcp --help` cannot work — `server.py:24` calls `mcp.run()`, which never reads `sys.argv`, so the flag is ignored and the stdio server blocks on stdin. Either remove the verification step or add real argv handling to `server.py` (code change; note it as such).
- **DOC-037** — `visualizer/docs/server-evaluation.md`: retitle from "Custom **Express** Server Evaluation" (the removed `server.ts` used `ws` + Next.js `noServer: true`, never Express) and move the present-tense sections at `:12,14,29,36` under a "Historical Context" heading. **Keep the file** — its Implementation Notes capture five migration deviations documented nowhere else.
- **DOC-038** — `CLAUDE.md`: document the pre-commit setup (gitleaks + `detect-private-key`, described in `CONTRIBUTING.md:33-36`) so AI sessions know it exists and that committing triggers `make fmt`. Add a `pre-commit` Makefile target running `pre-commit run --all-files`.
- **DOC-039** — add `Args:` blocks to the highest-traffic undocumented functions in `installer/` (24% coverage vs 51% overall): `installer/skill.py:464 uninstall()` (8 params), `:49 install_skill()` (6), `installer/hooks.py:227 merge_codex_hooks()` (4). Optionally enable ruff's `D` rules for `installer/` to enforce `CONTRIBUTING.md:64`.
- **DOC-040** — `skills/parsidion/scripts/vault_common.py:16-17`: either re-export `vault_fs.atomic_write_text` (callers currently reach for `vault_fs.` directly) or soften the "All public symbols are re-exported here" claim. Add the 5 missing names to `__all__`, at minimum `get_vault_username` and `migrate_pending_paths`, which are already called through the facade in practice. All 72 existing `__all__` names resolve — there are no phantom entries.

---

# Phase 4 — Verification

Run after every Phase 3 agent reports complete.

```bash
# The full gate — non-mutating by now thanks to ARC-006
git status --porcelain > /tmp/pre.txt
make checkall
git status --porcelain > /tmp/post.txt
diff /tmp/pre.txt /tmp/post.txt          # MUST be empty

# Expected baselines to meet or exceed (measured at 8e5d549):
#   ruff format --check .  → clean
#   ruff check .           → clean
#   pyright .              → 0 errors, 0 warnings, 0 informations
#   pytest tests/          → 840 passed, 2 skipped  (should now be HIGHER)
#   make test-graph        → 6 passed
#   visualizer-check       → tsc clean, eslint clean, 60 pass (should now be HIGHER)
#   checkall-mcp           → ruff clean, pyright 0 errors, 43 passed

# New coverage that must exist
uv run pytest tests/ --cov=skills/parsidion/scripts --cov-report=term-missing \
  | grep -E 'subagent_stop_hook|post_compact_hook|codex_|gemini_'   # all well above 0%

# Wheel install smoke test (ARC-001)
uv build && python -m venv /tmp/wheeltest && /tmp/wheeltest/bin/pip install dist/*.whl \
  && /tmp/wheeltest/bin/python -c "import vault_common, vault_search, vault_links, ai_backend; print('ok')" \
  && rm -rf /tmp/wheeltest

# CI parity (ARC-007)
gh run list --limit 1 --json conclusion,name    # all jobs green, including visualizer + extensions
```

**Manual follow-ups to report to the user — do not perform these automatically:**
1. Replace `~/ParsidionVault/.git/hooks/post-merge` (SEC-101) — the installer skips it because of the legacy marker; after the fix ships, `uv run install.py --force --yes` regenerates it.
2. `git -C ~/ParsidionVault rm --cached` the four tracked sensitive files (SEC-104).
3. Resolve the SEC-103 `ANTHROPIC_BASE_URL` decision, which unblocks DOC-023.
4. Run the permission-repair pass over the existing vault (SEC-109, SEC-110, SEC-112, SEC-114).

---

# Phase 5 — Deferred Restructures (Optional, Separately Approved)

> These four are large, file-moving refactors. Each blocks every other fix in its target file, which is why they run last. **`/fix-audit` should confirm with the user before starting this phase.** Each should be its own branch and its own review.

### [ARC-004] Split the 49-file flat scripts directory into a package
- **Files**: all of `skills/parsidion/scripts/` → `parsidion/core/`, `parsidion/hooks/`, `parsidion/cli/`, plus `tools/` outside the installed tree; `pyproject.toml` (`py-modules` → `packages`); `installer/paths.py`; `parsidion-mcp/src/parsidion_mcp/tools/search.py:8`
- **Steps**: (1) `parsidion/core/` — the 7 stdlib-only library modules (`vault_config`, `vault_path`, `vault_fs`, `vault_index`, `vault_hooks`, `vault_adaptive`, `vault_links`). (2) `parsidion/hooks/` — the 10 hook scripts plus `session_stop_wrapper.sh`. (3) `parsidion/cli/` — the 8 user-facing CLIs (extras permitted). (4) `tools/eval/` and `tools/migrations/` — the 5 `embed_eval_*` scripts, 2 migrations, and `html-to-md.py`, outside the installed tree. (5) Add `__init__.py` throughout and delete every `sys.path` insertion, including the one `parsidion-mcp/.../search.py:8` documents relying on. (6) Replace `py-modules` with `packages` in `pyproject.toml`, superseding ARC-001's manifest fix. (7) **Add the enforcement test**: import every `core/` and `hooks/` module with `sys.modules` poisoned against `rich`, `fastembed`, and `sqlite_vec`, converting the documented stdlib-only rule into an executable one. (8) Update `installer/paths.py`, all `[project.scripts]` entry points, `CLAUDE.md`'s path table, and `docs/ARCHITECTURE.md`.
- **Method**: Use par-mem for the rename sweep — `get_impact` and `get_symbol_context` on each moved symbol, then `analyze_relationships` to enumerate callers. Per R10, also grep separately for string literals containing module names, dynamic imports, re-exports, test files, and the `[project.scripts]` entries; a single grep will not catch them. Step 7 is the actual deliverable: without it, this is a reorganization with no durable benefit, since nothing today prevents a hook from importing `rich`.
- **Verify**: `make checkall` plus the wheel smoke test from ARC-001, plus the new stdlib-enforcement test.

### [ARC-008 / QA-003] Decompose `vault_doctor.py` (3,127 LOC)
- **Files**: `skills/parsidion/scripts/vault_doctor.py` → `vault_doctor/` package (`frontmatter.py`, `tags.py`, `links.py`, `migrations.py`, `sessions.py`, `graph.py`, `__init__.py` dispatcher)
- **Steps**: (1) Define a `Fixer` protocol: `detect(notes) -> list[Issue]` and `apply(issue, dry_run) -> bool`. (2) Move each of the nine fix modes into its own module implementing it. (3) Reduce `run_scan_and_repair:2546` (309 lines, CC 58, nesting 6) to a loop over a `(flag, fixer)` registry — the mode list becomes data, so `--fix-all` is a loop rather than a 58-branch function. (4) Split the four Critical-complexity helpers (`_repair_one` CC 38, `check_note` CC 35, `_normalize_underscores_in_frontmatter` CC 31, `_replace_tag_in_note` CC 26) into their owning modules. (5) **Add a test per destructive mode**: dry-run makes no filesystem change; `--execute` produces exact expected note content. Coverage of lines 2587-3109 (currently zero) is the acceptance criterion. (6) Document that `--fix-all` sets `strip_prefixes` (DOC-025).
- **Method**: Must follow ARC-004's layout decision — a `vault_doctor/` package either extends or breaks the flat-module convention, so confirm the shape before moving 3,127 lines. Also confirm ARC-010's shared-enum work has landed, since it touches both this file and `summarize_sessions.py`. This is the tool that rewrites, moves, and renames the user's notes in bulk, unattended, nightly — the tests in step 5 are not optional polish, they are the reason for the refactor.
- **Verify**: `uv run pytest tests/test_vault_doctor.py -v` with coverage on the previously-unexercised range; `make checkall`.

### [ARC-009] Decompose `summarize_sessions.py` (2,242 LOC)
- **Files**: `skills/parsidion/scripts/summarize_sessions.py` → `summarizer/` package (`queue.py`, `transcript.py`, `prompts.py`, `note_repair.py`, `note_schema.py`, `writer.py`, `dedup.py`, `progress.py`, `singleton.py`, `pipeline.py`) plus a ~150-line PEP 723 entrypoint
- **Steps**: (1) **Confirm all small `summarize_sessions.py` fixes have landed first** — ARC-010, ARC-012, ARC-013/SEC-129, SEC-107, SEC-125, ARC-027(a), ARC-030, ARC-048(c)(d). (2) Extract the ten modules. (3) **Replace the six sentinel return values** (`_STALE`/`_SKIPPED`/`_DEAD`/`_DEFERRED`/`None`/`Path`, `:82-97`) with one `Outcome` enum. This removes the duplicated classification at `_run_one:1586-1593` and `main:2156-2171` — **which currently disagree**, `_run_one` counting `_DEAD` as "skipped" while `main` counts it as "stale", so reported statistics are wrong depending on which counter is read. (4) Make `note_schema.py` the single source shared with `vault_doctor.py`, so ARC-010's class of bug becomes structurally impossible. (5) Keep the PEP 723 inline `anyio` dependency on the entrypoint.
- **Method**: Twelve responsibilities in one module, with `summarize_one` at 265 lines and `main` at 268. The sentinel-disagreement in step 3 is the highest-value single change here — it is a live correctness bug, not just structure. Preserve the carefully-engineered queue discipline exactly (`remove_processed`'s lock-across-RMW, `append_to_pending`'s inode re-check, the singleton PID reclaim); those are strengths, not debt.
- **Verify**: `uv run pytest tests/test_summarize_sessions.py tests/test_dead_letter.py -v`; `make checkall`; then a real summarizer run against a temp vault comparing note output before/after.

### [ARC-017 / QA-002] Rebuild `install()` / `uninstall()` on a shared step list
- **Files**: `install.py:277-591`, `installer/skill.py:464-634`, new `installer/plan.py` and `installer/uninstall.py`
- **Steps**: (1) Define `Step(name, predicate, action, undo)`. (2) Write a **pure** `build_plan(args) -> InstallPlan` — the only place `_ask`/`_confirm` are called — with a `render()` method replacing the 45-line plan printer. (3) Reduce `install()` to `plan = build_plan(args); plan.render(); if confirmed: return execute(plan)`. (4) Drive `uninstall()` from the **same** step list in reverse via each step's `undo()`, so the two cannot drift. (5) Snapshot `settings.json` before mutation and restore on any step failure (builds on SEC-105 and ARC-018 — reuse their helpers, do not re-implement). (6) Accumulate step results and return non-zero on any failure (ARC-022). (7) Move `uninstall()` out of `skill.py` into `installer/uninstall.py` (ARC-025). (8) Add an install→uninstall round-trip test asserting the tree returns to its prior state (ARC-046).
- **Method**: Nearly all of `install()`'s CC 68 is one cross-cutting concern repeated ~20 times: the 3-way runtime matrix crossed with `skip_hooks` and `dry_run`. `if install_claude_runtime and not args.skip_hooks:` appears at `:490` and again at `:507`, and the same predicates are re-evaluated a third time in the plan block at `:407-438` purely to decide what to print. This structure is what let ARC-003's unguarded teardown hide in plain sight, which is why ARC-003 must ship as a standalone minimal fix **before** this refactor rather than inside it. The refactor also largely resolves ARC-022, ARC-025, and ARC-046, and removes the need for most of `tests/test_install.py`'s 19-global stubbing (done twice, at `:505-526` and `:1233-1254`).
- **Verify**: `uv run pytest tests/test_install.py -v`; `make checkall`; a real install into a temp `HOME` followed by uninstall, asserting the tree is clean.
