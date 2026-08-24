# Audit Remediation Playbook

> **Companion to** `AUDIT.md` (2026-08-23, Fable 5 audit of parsidion @ `5d02483`, v0.20.0).
> **Consumer**: `/fix-audit`. Entries are ordered to match the `## Remediation Plan` phases in
> AUDIT.md. Each entry is meant to be executed without re-deriving the analysis.
> **Not** the post-fix report — `/fix-audit` writes that to `AUDIT-REMEDIATION.md`.

## Conventions for every entry

- Paths are relative to the repo root; `scripts/` means `skills/parsidion/scripts/`.
- **Gate**: `make checkall` (fmt-check + lint + pyright + pytest + graph tests + visualizer + MCP). Run it at the end of every phase; per-entry **Verify** lines are the targeted check that proves the specific fix.
- Work in a git worktree (`main` is live through the `~/.claude/skills/parsidion` symlink). Commit after each verified entry. Never push without the user's confirmation.
- Before editing any file in the **File Conflict Map** (AUDIT.md), re-read it: an earlier phase may have changed it.
- par-mem is indexed for this repo (`repository_id: parsidion`). For multi-site changes, run `get_impact` / `get_symbol_context` on the symbol named in **Method** to enumerate callers; re-index (`index_directory`) after Phase 2 because it moves symbols.
- Hook scripts and `core/` must stay stdlib-only; `tests/test_stdlib_only.py` enforces it.

---

## Phase 1 — Promoted Security (sequential)

### [SEC-005] `atomic_write_text` predictable `.tmp` sibling and symlink follow
- **Files**: `scripts/core/vault_fs.py:285-318` (`atomic_write_text`), `scripts/build_graph.py:762-781` (graph.json atomic write), `installer/vault.py:125-146` (vault `.gitignore` list)
- **Steps**:
  1. In `atomic_write_text`, replace `tmp.write_text(...)` with `fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)`; write via `os.fdopen(fd, "w", encoding="utf-8")`. Keep `os.replace(tmp, path)` and the existing mode-preservation logic.
  2. If `tmp` already exists (stale from a crash), `FileExistsError` is raised by `O_EXCL`: unlink it first only when `not tmp.is_symlink()`, then retry once; otherwise raise.
  3. Apply the same open in `build_graph.py:762-781`.
  4. Add `*.tmp` and `.merge_previews/` (for SEC-013) to the gitignore list in `installer/vault.py:125-146`. Bump the marker the installer uses to decide the vault `.gitignore` is current (see SEC-012 for the same marker on the hook).
  5. Add a test in `tests/test_vault_fs.py` (or the existing atomic-write test file): create `X.md.tmp` as a symlink to a file outside the vault, call `atomic_write_text(X.md)`, assert the symlink target is unchanged and `X.md` has the content.
- **Method**: `O_EXCL|O_NOFOLLOW` refuses to open through a symlink and refuses an existing tmp; this is the same pattern already used in `session_start/context.py:244`. Keep the tmp in the same directory so `os.replace` stays atomic on the same filesystem. Do not switch to `tempfile.NamedTemporaryFile` without `dir=path.parent`, or replace crosses filesystems.
- **Verify**: `uv run pytest tests/ -q -k "atomic_write"`; `uv run pytest tests/test_stdlib_only.py -q`.

### [SEC-007] Config-sourced binaries and API endpoints trusted without ownership/tracking checks
- **Files**: `scripts/parmem_backend.py:104-108,172,523` (`par_mem.binary`), `scripts/ai_backend.py:393-423` (`codex_cli.command`, `grok_cli.command`), `scripts/core/vault_hooks.py:112-125,145-163,191-194` (`_configured_env_defaults`, `anthropic_env`), `scripts/core/vault_fs.py:698` (`_git_path_ignored`), `scripts/agent_adapter.py:231-239` (existing writable-file refusal to copy)
- **Steps**:
  1. Add `core/vault_fs.is_trusted_executable(path: Path) -> bool`: resolves the path, requires `st_uid == os.getuid()` (skip on Windows) and no group/other write bits, and returns False on any `OSError`.
  2. In `ai_backend.py:393-423`, after the existing `exists()` + `X_OK` check, require `is_trusted_executable`; on failure log one stderr line and fall back to the backend's default command name resolved via `shutil.which`.
  3. Same check in `parmem_backend.py` where `par_mem.binary` is resolved (`:104-108`).
  4. In `vault_hooks._configured_env_defaults`, split the `anthropic_env` keys into network-affecting (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_CUSTOM_HEADERS`, `HTTPS_PROXY`, `HTTP_PROXY`) and benign. Apply network-affecting keys only when they come from `config.local.yaml` or when `config.yaml` is gitignored (`not _git_path_ignored(...)` → refuse and warn once). `vault_config.load_config` already merges the two files; add a `source` map to the merged result (which file each section/key came from) so this decision is possible.
  5. Tests: writable binary refused; tracked `config.yaml` with `ANTHROPIC_BASE_URL` ignored with a warning; `config.local.yaml` honored.
- **Method**: This mirrors the refusal already in `agent_adapter.py:231-239`. `setdefault` semantics stay: a real shell env var still wins over config. Do not change `_SAFE_ENV_KEYS`.
- **Verify**: `uv run pytest tests/ -q -k "ai_backend or vault_hooks or parmem"`.

### [SEC-012] Vault `post-merge` hook is generated with a double-quoted `~`
- **Files**: `installer/vault.py:221-230` (template), `:275` (`scripts_rel = f"~/{rel}"`), `:233-245` (`_is_current_post_merge_hook`)
- **Steps**:
  1. Replace the `~/…` form with the absolute path passed through `shlex.quote(str(scripts_dir / "update_index.py"))`.
  2. Add a version comment line to the template (e.g. `# parsidion post-merge v2`) and make `_is_current_post_merge_hook` compare that marker so existing broken hooks are regenerated on `install.py --force`.
  3. Same edit adds the gitignore entries from SEC-005 step 4 and SEC-013.
  4. Test: render the hook for a home containing a space, run `bash -n` on it, and `grep -q '"~'` must fail.
- **Method**: Tilde does not expand inside double quotes and uv does not expand it either. A `$HOME`-based form is acceptable but the absolute quoted path is deterministic and survives `HOME` edge cases.
- **Verify**: `uv run pytest tests/ -q -k "post_merge or vault_git"`; then `uv run install.py --dry-run` shows the hook as stale.

### [SEC-016] Doctor singleton is an unlocked PID-JSON read-check-write
- **Files**: `scripts/doctor/cli.py:292-305`, `scripts/doctor/_state.py:201-250` (`_acquire_pid`, `_release_pid`), `scripts/core/vault_hooks.py:418-424` (`is_process_running`), `scripts/core/vault_fs.py` (`try_singleton_lock`)
- **Steps**:
  1. Replace the PID-file dance in `doctor/cli.py` with `vault_fs.try_singleton_lock(vault / ".doctor.lock")` (flock-based, already used elsewhere). Keep the lock handle alive for the run; release in `finally`.
  2. Delete `_acquire_pid`/`_release_pid` in `_state.py` if nothing else calls them (par-mem `find_dead_code` already lists `_release_pid` as zero-caller).
  3. In `is_process_running`, return `False` on `PermissionError` for PIDs owned by other users only when `os.kill(pid, 0)` raises `PermissionError` *and* `pid == 1` or the PID's start time cannot be read; otherwise keep `True`. Simplest safe rule: treat `PermissionError` as "unknown" and let the flock decide.
- **Method**: flock is released by the kernel on process death, so the stale-PID class of bug disappears entirely.
- **Verify**: `uv run pytest tests/ -q -k "doctor and (singleton or lock or pid)"`; run two `vault_doctor.py --dry-run` concurrently, second exits with the "already running" message.

### [SEC-020] Paths sourced from `embeddings.db` / `note_index` / par-mem JSON read without containment
- **Files**: `scripts/vault_conflicts.py:234-252`, `scripts/cli/search/metadata.py:305-317`, `scripts/parmem_backend.py:490-499`
- **Steps**: At each site, after building the `Path`, `resolve()` it and skip the row unless `vault_fs.is_path_inside_vault(path, vault)` (or the equivalent already used by `core/vault_index.query_note_index`, the "SEC-005" comment block from the prior cycle). Log skipped rows once at debug level.
- **Method**: Copy the guard from `core/vault_index.py:437-535` rather than re-deriving it; QA-009 later centralizes it, so keep the call shape identical.
- **Verify**: `uv run pytest tests/ -q -k "conflicts or metadata or parmem"`.

### [SEC-021] `--grep` regex and negative `-l`/`-n` limits unbounded
- **Files**: `scripts/cli/search/metadata.py:292-317`, `scripts/vault_search.py` argparse (`-l`, `-n`)
- **Steps**: Clamp `limit`/`top_k` to `1..1000` in the parser (`type=` function raising `ArgumentTypeError`); cap the grep body scan at 1 MiB per note; compile the regex once and reject patterns over 512 chars.
- **Verify**: `uv run pytest tests/ -q -k "vault_search"`; `vault-search -l -5 -f Patterns` exits 2 with a usage error.

---

## Phase 2 — Blocking Architecture (sequential)

### [ARC-002] Unify the Claude session-end path with `agent_adapter.run_session_end`
- **Files**: `scripts/session_stop_hook.py:325-611` (`main`), `scripts/agent_adapter.py:288` (`_register_builtin_adapters` claude entry), `:491-619` (`run_session_end`), `tests/test_session_stop_hook.py`, `tests/test_agent_adapter.py`
- **Steps**:
  1. Read both functions fully. List every step in `session_stop_hook.main` that `run_session_end` lacks: (a) `--ai` classification via `ai_backend`, (b) `_launch_summarizer_if_pending` (auto-summarize), (c) `git_commit_vault` with the sanitized project message, (d) any Claude-specific transcript parsing (pi transcript tail at `:451`).
  2. Add those as optional stages to `run_session_end`, each gated by the same config keys the Claude path reads (`session_stop_hook.ai_model`, `auto_summarize`, `auto_summarize_after`, `git.auto_commit`). Keep the stage order identical to the Claude path.
  3. Give the claude `AgentAdapter` a real `read_transcript_tail` (the byte-bounded `read_last_n_lines(..., max_bytes=tail_bytes)` from `session_stop_hook.py:435`) so SEC-022's byte bound lands here for every adapter.
  4. Rewrite `session_stop_hook.main` as: parse args → build hook input → `run_session_end(get("claude"), payload, args)` → print `{}`. Keep the existing `_should_skip` guards and `_log_hook_error` wrapper.
  5. Retarget `tests/test_session_stop_hook.py` monkeypatches from `session_stop_hook.<name>` to `agent_adapter.<name>`; keep the tests' behavioural assertions unchanged.
  6. Update CLAUDE.md's "All adapters ... produce identical vault notes" sentence to describe the shared pipeline.
- **Method**: `get_symbol_context` on `run_session_end` and `get_impact` on `session_stop_hook.main` (repository_id `parsidion`) enumerate every caller and every test monkeypatch target. Do the QA-002 extraction (single `_classify` + `_persist_and_report`) *inside* this change so the tail is written once. The hook must still never fail closed: wrap the new stages in the same broad `except` the Claude path uses (documented `BLE001` contract).
- **Verify**: `uv run pytest tests/test_session_stop_hook.py tests/test_agent_adapter.py -q`; manual: `bash scripts/session_stop_wrapper.sh <<< '{"cwd":"/tmp","transcript_path":"/nonexistent"}'` prints `{}` and logs to `~/.claude/logs/session_stop_hook.log`; then full `make checkall`.

### [ARC-009] Python version floor decision
- **Files**: `pyproject.toml:10-11` (`requires-python`), `:119` (`target-version`), `parsidion-mcp/pyproject.toml:3-4`, `.github/workflows/ci.yml` (matrix), `README.md:5,43`, `CONTRIBUTING.md:16`, `docs/ARCHITECTURE.md:63`
- **Steps** (recommended option, raise the floor): set `requires-python = ">=3.13"` and `target-version = "py313"` in the root `pyproject.toml`; run `uv lock`; leave the docs as they are (they already say 3.13+). If the user instead wants 3.11 support: add `python-version: ["3.11", "3.13"]` to the CI matrix and change the three doc lines to "3.11+".
- **Method**: Every other manifest, CI, and doc already assumes 3.13; the root floor is the outlier. Raising it is the zero-risk direction because nothing tests 3.11. Decide once here so DOC-001/DOC-007/DOC-014 edit their files once.
- **Verify**: `uv lock --check`; `make lint typecheck`; `grep -n "3.11" README.md CONTRIBUTING.md docs/ARCHITECTURE.md pyproject.toml` returns no support claims.

### [ARC-001] `core/` imports back through the deprecated `vault_common` facade
- **Files**: `scripts/core/vault_health.py:32-33`, `scripts/core/vault_links.py:17`, `scripts/core/vault_metrics.py:23`, `tests/test_stdlib_only.py` (or new `tests/test_core_layering.py`), `scripts/vault_common.py:46` (AUDIT.md comment, see DOC-024)
- **Steps**:
  1. For each of the three modules, list the names used from `vault_common`/`vault_metrics` (grep `vault_common\.` and `vault_metrics\.` in the file) and replace with direct relative imports from the owning `core` module (`from .vault_index import parse_frontmatter`, `from .vault_path import resolve_vault`, ...). `get_symbol_context` on each name tells you the owning module.
  2. If a name is only defined in the root facade (not in `core/`), move the definition into the appropriate `core` module and re-export it from the facade.
  3. Add `tests/test_core_layering.py`: parse every `scripts/core/*.py` with `ast`, assert no `Import`/`ImportFrom` names a root shim (`vault_common`, `vault_metrics`, `vault_index`, `vault_path`, `vault_fs`, `vault_hooks`, `vault_config`, `vault_adaptive`, `vault_links`, `vault_health`, `vault_tui`, `vault_constants`, `subproc_util`).
  4. Update the comment at `vault_common.py:46` that cites AUDIT.md to cite this playbook or drop the citation (DOC-024 dependency).
- **Method**: The cycle only works today because of import-order luck; relative imports inside the package remove it. Do not migrate the ~60 external `import vault_common` callers in this entry (that is an enhancement-scale migration); the facade stays, it just stops being a dependency of `core/`.
- **Verify**: `uv run pytest tests/test_core_layering.py tests/test_stdlib_only.py -q`; `uv run python -c "import sys; sys.path.insert(0,'skills/parsidion/scripts'); import core.vault_health, core.vault_links, core.vault_metrics"` from a fresh interpreter with the facade *not* pre-imported.

---

## Phase 3a — Security (remaining, parallel)

### [SEC-001] Visualizer API has no Host-header check
- **Files**: `visualizer/lib/apiAuth.ts:169-183` (`runGuards`), `visualizer/lib/apiAuth.test.ts`, `visualizer/lib/env.ts` (port source if present)
- **Steps**:
  1. Add `function hostAllowed(req: Request): boolean` that parses `req.headers.get("host")`, splits host/port, and returns true only for hosts `127.0.0.1`, `localhost`, `[::1]`/`::1` and a port equal to the server port (read `process.env.PORT ?? "3999"`; also accept a missing port for the default).
  2. Call it first in `runGuards`; on failure return the same 403 JSON the other guards use.
  3. Tests: `Host: attacker.example:3999` → 403; `Host: localhost:3999` → passes to the next guard; `Host: 127.0.0.1` (no port) → passes.
- **Method**: DNS rebinding makes the request same-origin from the browser's view, so `Sec-Fetch-Site` is `same-origin`; only the `Host` header reveals the rebinding. The bearer token, when set, already defeats this, but the documented default is unset. Land with SEC-002.
- **Verify**: `cd visualizer && bun test lib/apiAuth.test.ts app/api` and `make visualizer-check`.

### [SEC-002] `POST`/`DELETE /api/note` accept any existing vault file
- **Files**: `visualizer/app/api/note/route.ts:96-107` (POST), `:168-172` (existing PUT guard), `:218-229` (DELETE), `visualizer/app/api/note/route.test.ts`
- **Steps**:
  1. Extract the PUT `.md` check into `assertMarkdownNotePath(relPath)` that also rejects any path segment starting with `.` and any first segment in `["Templates", "TagsRoutes"]` (mirror `EXCLUDE_DIRS`).
  2. Call it in POST and DELETE immediately after `guardPath`.
  3. Tests: POST `path: ".git/config"` → 400; POST `path: "config.yaml"` → 400; DELETE `path: "Templates/x.md"` → 400; POST `path: "Patterns/new.md"` → 200.
- **Verify**: `cd visualizer && bun test app/api/note`.

### [SEC-003] pi/omp extension executes hook scripts from a cwd-relative sibling directory
- **Files**: `extensions/pi/parsidion/parsidion.ts:267-278` (`candidateScriptDirs`), `:299`, `:369-396` (`invokeHook`), `:413`; `extensions/pi/parsidion/*.test.ts`; `visualizer/lib/env.ts:72-83` (allowlist to mirror); `docs/PI_EXTENSION.md`
- **Steps**:
  1. Delete the two `path.resolve(cwd, "../parsidion/...")` candidates. Order becomes: `PARSIDION_SCRIPTS_DIR`, `PARSIDION_DIR/skills/parsidion/scripts`, `~/.claude/skills/parsidion/scripts`.
  2. Build the child env from an allowlist (`PATH`, `HOME`, `USER`, `TMPDIR`, `LANG`, `LC_*`, `XDG_*`, `CLAUDE_VAULT`, `PARSIDION_*`, the `ANTHROPIC_*`/proxy keys in `_SAFE_ENV_KEYS`) instead of `...process.env`, and always delete `CLAUDECODE`.
  3. Update the extension test for resolution order and add one asserting a sibling `../parsidion` is ignored.
  4. Document in `docs/PI_EXTENSION.md` that repo-local development uses `PARSIDION_SCRIPTS_DIR`.
- **Verify**: `cd extensions/pi/parsidion && bun test`; `make checkall`.

### [SEC-004] Predictable shared-tmp scratch cwd for `claude -p` / `grok`
- **Files**: `scripts/ai_backend.py:594-596` (`_minimal_context_cwd`), `:316`, `:652-653`; `scripts/core/vault_path.py` (`secure_log_dir`)
- **Steps**: Create the scratch dir under `secure_log_dir() / "clean-cwd"` with mode 0700; after `mkdir`, `lstat` it and refuse (fall back to a fresh `tempfile.mkdtemp(prefix="parsidion-clean-")`) if it is a symlink, not owned by `os.getuid()`, or has group/other bits. Keep it empty (no `.claude/`, no `CLAUDE.md`).
- **Verify**: `uv run pytest tests/ -q -k "minimal_context or ai_backend"`.

### [SEC-006] `event_log.path` override redirects hook-log append and rotation to any file
- **Files**: `scripts/core/vault_fs.py:411-460`
- **Steps**: After `expanduser().resolve()`, require the path to be inside the vault or inside `secure_log_dir()`; otherwise warn once and use the default `<vault>/hook_events.log`. Open with `O_NOFOLLOW`. Rotation reuses the SEC-005 `atomic_write_text`.
- **Verify**: `uv run pytest tests/ -q -k "hook_event"`.

### [SEC-008] MCP `vault_read` reads any vault file
- **Files**: `parsidion-mcp/src/parsidion_mcp/tools/notes.py:57-58` (read), `:99-116` (write rules to mirror), `parsidion-mcp/tests/test_notes.py`, `docs/MCP.md`
- **Steps**: Require `.md` suffix, reject dotfile/dot-dir segments and `EXCLUDE_DIRS`, cap at 10 MB, catch `UnicodeDecodeError` → `VaultToolError("not a text note")`. Add four tests. Note the restriction in `docs/MCP.md`.
- **Verify**: `make checkall-mcp`.

### [SEC-009] HTML export renders `javascript:`/`data:` hrefs
- **Files**: `scripts/vault_export.py:143-147`
- **Steps**: In the `_RE_LINK.sub` replacement function, parse the URL scheme; allow `http`, `https`, `mailto`, and scheme-less relative links; otherwise emit the escaped text without an anchor. Test with `[x](javascript:alert(1))`.
- **Verify**: `uv run pytest tests/ -q -k "export"`.

### [SEC-010] Daily-note migration rename target from unsanitized username
- **Files**: `scripts/core/vault_fs.py:863-866` (`get_vault_username`), `scripts/doctor/daily.py:57-60,94`, `scripts/doctor/cli.py` (`--daily-username`)
- **Steps**: Validate `^[A-Za-z0-9._-]{1,64}$` in `get_vault_username()` (fall back to `"user"` with a warning) and as an argparse `type=` on `--daily-username`; in `daily.py`, assert `new.parent == old.parent` before `rename`.
- **Verify**: `uv run pytest tests/ -q -k "daily"`.

### [SEC-011] `vault-merge` accepts absolute or `..` note paths
- **Files**: `scripts/cli/merge/lookup.py:30-38`, `scripts/vault_merge.py:645-652,667` (`--output`)
- **Steps**: After resolving each candidate, require `is_path_inside_vault(path, vault)`; raise the existing lookup error otherwise. Apply to `--output`.
- **Verify**: `uv run pytest tests/ -q -k "merge"`; `vault-merge /etc/hosts x --dry-run` exits non-zero with "outside vault".

### [SEC-013] `--from-preview` bypasses `_is_valid_merge_body`; `.merge_previews/` staged
- **Files**: `scripts/vault_merge.py:376-395,259-263`; gitignore entry added in SEC-005/SEC-012
- **Steps**: Run `_is_valid_merge_body` on the cached preview before use. Verify `.merge_previews/` is in the installer gitignore list.
- **Verify**: `uv run pytest tests/ -q -k "merge"`.

### [SEC-014] `vault-review` rewrites the queue from an unlocked read
- **Files**: `scripts/vault_review.py:88-101,295,512`; `scripts/core/vault_fs.py:531-539` (`append_to_pending` lock pattern)
- **Steps**: Wrap read → edit → write in the same `LOCK_EX` + inode-recheck loop as `append_to_pending`; create the tmp with `os.open(..., 0o600)`.
- **Verify**: `uv run pytest tests/ -q -k "review"`.

### [SEC-015] `append_session_to_daily` flocks an inode that is then replaced
- **Files**: `scripts/core/vault_fs.py:969-1000`, `:531-539`
- **Steps**: Lock a sibling `<daily>.lock` file (0600) for the duration of read+write instead of the note itself; or reuse the inode-retry loop. Prefer the sibling lock: it survives `os.replace`.
- **Verify**: `uv run pytest tests/ -q -k "daily and (lock or concurrent)"`.

### [SEC-017] Stale `.git/index.lock` self-heal when `lsof` is absent
- **Files**: `scripts/core/vault_fs.py:716-752`
- **Steps**: When `shutil.which("lsof")` is None or the call errors, emit one stderr line ("lsof unavailable; relying on mtime age") before removing.
- **Verify**: `uv run pytest tests/ -q -k "index_lock"`.

### [SEC-018] Embed service request-size cap and client model override
- **Files**: `scripts/vault_embed_serve.py:134-144,156-158`
- **Steps**: `_read_line` stops at 64 KiB and closes the connection; ignore a client-supplied `model` (server model comes from config only).
- **Verify**: `uv run pytest tests/ -q -k "embed_serve"`.

### [SEC-019] Subfolder migration can rename a variant over the base note
- **Files**: `scripts/doctor/subfolder.py:340-346,367,551`
- **Steps**: Before each move, if the target exists or another planned move targets the same path, skip with a reported conflict instead of overwriting. Test with `foo.md` + `foo-foo.md`.
- **Verify**: `uv run pytest tests/ -q -k "subfolder"`.

### [SEC-022] Codex/Gemini adapter path reads transcripts without the byte bound
- **Files**: `scripts/agent_adapter.py:546-549,663`
- **Steps**: Handled by ARC-002 step 3 (shared byte-bounded `read_last_n_lines(..., max_bytes=transcript_tail_bytes)`). If ARC-002 is deferred, replace `fh.readlines()` with `read_last_n_lines(path, n, max_bytes=get_config("subagent_stop_hook","transcript_tail_bytes", ...))`.
- **Verify**: `uv run pytest tests/test_agent_adapter.py -q`.

### [SEC-023] `config.local.yaml` excluded from `git add -A` only by the installer's `.gitignore`
- **Files**: `scripts/core/vault_fs.py:803-807`
- **Steps**: Add `":(exclude)config.local.yaml"` beside the existing `config.yaml` exclusion pathspec.
- **Verify**: `uv run pytest tests/ -q -k "git_commit"`.

### [SEC-024] Config timeouts accept `nan`/negative/`inf`
- **Files**: `scripts/core/vault_config.py:142-152`, `scripts/ai_backend.py:210,382-390`
- **Steps**: Add `vault_config.clamp_timeout(value, default, lo=1, hi=3600)` returning the default for non-finite or non-positive input; use it at every timeout read in `ai_backend.py`.
- **Verify**: `uv run pytest tests/ -q -k "config and timeout"`.

### [SEC-025] `settings.json.bak` written at umask
- **Files**: `installer/hooks.py:830-832`
- **Steps**: `shutil.copy2(src, bak)` (preserves mode) or `os.chmod(bak, src.stat().st_mode & 0o777)` after writing.
- **Verify**: `uv run pytest tests/ -q -k "hooks and bak"`.

### [SEC-026] Cron line wraps paths in double quotes only
- **Files**: `installer/schedule.py:210-213`
- **Steps**: Build the command with `shlex.join([...])`.
- **Verify**: `uv run pytest tests/ -q -k "schedule"`.

### [SEC-027] Root `.env` not gitignored
- **Files**: `.gitignore`
- **Steps**: Add `.env` and `.env.*` (keep `!.env.example` if one exists).
- **Verify**: `git check-ignore .env` exits 0.

### [SEC-028] GitHub Actions pinned to mutable tags
- **Files**: `.github/workflows/ci.yml`, `.github/workflows/pages.yml`
- **Steps**: For each `uses:`, resolve the current tag to its commit SHA (`gh api repos/<owner>/<repo>/git/ref/tags/<tag>`) and pin `uses: owner/repo@<sha> # vX.Y.Z`. Verify every ref resolves before committing (per `~/.claude/guides/git-ci.md`).
- **Verify**: `grep -n "uses:" .github/workflows/*.yml` shows only 40-hex SHAs; CI green after push (user confirms push).

### [SEC-029] Absolute vault path leaked in a 404 body
- **Files**: `visualizer/app/api/graph/delta/route.ts:118-121`
- **Steps**: Return the generic "graph not built" message; log the path server-side only.
- **Verify**: `cd visualizer && bun test app/api/graph`.

### [SEC-030] No concurrency cap on `/api/health` and `/api/graph/rebuild`; sync 47 MB read; unbounded JSON bodies
- **Files**: `visualizer/lib/vaultStatsServer.ts:230`, `visualizer/app/api/graph/rebuild/route.ts:55-58`, `visualizer/app/api/graph/delta/route.ts:56-62`, `visualizer/app/api/note/route.ts` (body size)
- **Steps**: Module-level in-flight promise for rebuild (second caller awaits the first); same for health with a 60 s TTL cache; `await fs.promises.readFile` in delta; reject note bodies over 10 MB (`Content-Length` check in `withApi` or per-route).
- **Verify**: `make visualizer-check`.

### [SEC-031] Transitive advisories pinned by `overrides`
- **Files**: `visualizer/package.json` (`overrides`), `visualizer/bun.lock`
- **Steps**: Bump `brace-expansion` → 5.0.9, `nanoid` → 3.3.18, `postcss` → 8.5.23, `sharp` → latest 0.34.x; `bun install`; `bun audit` clean.
- **Verify**: `cd visualizer && bun audit && bun test && bun run build`.

### [SEC-032] MCP `vault_context` swaps the module-global `VAULT_ROOT`
- **Files**: `parsidion-mcp/src/parsidion_mcp/tools/context.py:44-76`
- **Steps**: Pass `vault_root` explicitly to the helpers it calls (`build_compact_index(vault=...)` etc.) instead of mutating `vault_common.VAULT_ROOT`; delete the swap/restore.
- **Verify**: `make checkall-mcp`.

### [SEC-033] Minor integrity items
- **Files**: `scripts/core/vault_links.py:1011-1014`, `scripts/vault_conflicts.py:301-305`, `scripts/vault_merge.py:323`, `scripts/doctor/frontmatter.py:245-258`, `scripts/core/vault_fs.py:789`, `scripts/show-context:25-27`
- **Steps**: (a) preserve mode via `atomic_write_text`; (b) lock the destination path, not the tmp; (c) escape `"` in YAML list items (superseded by ARC-005's emitter if that lands first); (d) write only the frontmatter block from the AI repair, keep the original body; (e) `git add -- <paths>`; (f) quote `"$FOLDER"` and JSON-escape it in the heredoc.
- **Verify**: `uv run pytest tests/ -q -k "links or conflicts or merge or frontmatter"`; `bash -n scripts/show-context`.

---

## Phase 3b — Architecture (remaining, parallel)

### [ARC-003] Wheel manifest omits modules the shipped console scripts import
- **Files**: `pyproject.toml:52-100`, `.github/workflows/ci.yml:120-125`, new `tests/test_packaging_manifest.py`
- **Steps**:
  1. Replace the hand list with `[tool.setuptools.packages.find] where = ["skills/parsidion/scripts"] include = ["core*", "cli*", "doctor*", "summarizer*", "session_start*"]` and `py-modules` generated by a test: `tests/test_packaging_manifest.py` walks `scripts/*.py` (excluding hooks that are not importable as modules: `html-to-md.py`, `show-context`) and asserts each stem appears in `py-modules` (or switch `py-modules` to a glob if setuptools supports it in the pinned version; otherwise keep the list and let the test enforce it).
  2. Extend the CI smoke step to `python -c "import vault_merge, vault_conflicts, vault_new, prompt_templates, note_schema, vault_health, vault_resolve, agent_adapter, session_start, summarizer.pipeline, doctor.cli, cli.stats.health"`.
  3. `uv build` locally and run the same import line inside a fresh venv with the wheel installed.
- **Method**: The failure only appears in non-editable installs, which is why nothing caught it; the test makes the manifest self-checking.
- **Verify**: `uv run pytest tests/test_packaging_manifest.py -q`; `uv build && (cd /tmp && uv venv .w && .w/bin/python -m pip install <wheel> && .w/bin/python -c "import vault_merge, summarizer.pipeline")`.

### [ARC-004] Three `rebuild_index` subprocess launchers
- **Files**: `scripts/core/vault_index.py` (new `run_index_rebuild`), `installer/skill.py:457-489`, `parsidion-mcp/src/parsidion_mcp/tools/ops.py:37-75`, `scripts/summarizer/queue.py:234-304`, `scripts/core/vault_path.py` (`SCRIPTS_DIR`)
- **Steps**: Implement `run_index_rebuild(vault, *, rebuild_graph=False, include_daily=False, timeout=300) -> subprocess.CompletedProcess` that builds `["uv","run","--no-project", str(SCRIPTS_DIR/"update_index.py"), "--vault", str(vault), ...]`, uses `env_without_claudecode()`, and the shared `subproc_util.run_with_timeout`. Replace the three bodies with a call. Delete the `~/.claude/skills/parsidion/scripts` fallback in `queue.py:263-270`.
- **Method**: `get_impact` on each `rebuild_index` symbol lists the callers/tests to keep green. The installer copy currently omits `--no-project`; the shared function fixes that automatically.
- **Verify**: `uv run pytest tests/ -q -k "rebuild_index or update_index"`; `make checkall-mcp`.

### [ARC-005] Single frontmatter emitter + parity fixture
- **Files**: `scripts/core/vault_index.py:122` (`parse_frontmatter`), `scripts/note_schema.py`, `scripts/vault_new.py:54-97`, `scripts/vault_merge.py:298-331`, `tools/migrate_memory.py:215-236`, `tools/migrate_research.py:238-272,275-311`, `visualizer/lib/frontmatter.ts:29,97`, new `tests/fixtures/parity/frontmatter.json`, `tests/test_frontmatter_parity.py`, `visualizer/lib/frontmatter.parity.test.ts`
- **Steps**:
  1. Add `serialize_frontmatter(fields: dict) -> str` in `core/vault_index.py` beside the parser: emits keys in `note_schema` canonical order, `tags`/`sources` as inline arrays, `related` as an inline array of double-quoted `[[wikilinks]]`, scalars unquoted unless they contain `: ` or start with a special char, and round-trips through `parse_frontmatter`.
  2. Replace the four `_build_frontmatter` bodies with calls (keep their signatures as thin adapters).
  3. Write the fixture: 8-10 vectors of `{fields, expected}` including quotes, colons, empty lists, and unicode; the Python test asserts `serialize_frontmatter(fields) == expected` and `parse_frontmatter(expected) == fields`; the TS test does the same against `frontmatter.ts` serializer/parser.
  4. Adjust `frontmatter.ts` until the TS test passes (formatting only).
- **Method**: Follow the ENH-005 fixture pattern (`tests/fixtures/parity/vault-resolution.json`). `get_impact` on `_build_frontmatter` ×4 for tests that assert exact output; update those expectations to the canonical form.
- **Verify**: `uv run pytest tests/test_frontmatter_parity.py tests/ -q -k "frontmatter or vault_new or merge"`; `cd visualizer && bun test lib/frontmatter`.

### [ARC-006] Test monkeypatch contracts dictate module layout
- **Files**: `scripts/vault_search.py:20-30,113-120,174-181`, `scripts/cli/search/embeddings.py`, `tests/test_vault_search_backend.py` and the five tests reaching `vault_common._private` names, `scripts/ai_backend.py`, `scripts/parmem_backend.py`, `tests/test_stdlib_only.py`, `CLAUDE.md` (architecture note)
- **Steps**: (1) delete `LAST_BACKEND` and the `_search_embeddings` re-export; retarget tests to `monkeypatch.setattr("cli.search.embeddings._search_embeddings", ...)`; (2) move `ai_backend.py` and `parmem_backend.py` to `core/` leaving root shims (`from core.ai_backend import *`), add them to the stdlib gate's module list; retarget monkeypatches to `core.ai_backend.<name>`; (3) update the CLAUDE.md sentence explaining why they were at root.
- **Method**: `get_impact` on `LAST_BACKEND`, `ai_backend`, `parmem_backend` enumerates every monkeypatch string; grep `"vault_search\.` and `"ai_backend\.` in `tests/` for string-form targets par-mem cannot see.
- **Verify**: `uv run pytest tests/ -q`; `uv run pytest tests/test_stdlib_only.py -q`.

### [ARC-007] Typed config access
- **Files**: `scripts/core/vault_schema.py:302` (`VaultAppConfig`), `scripts/core/vault_config.py:394` (`get_config`), `scripts/session_start_hook.py` (16 sites), `scripts/session_stop_hook.py`/`agent_adapter.py:70`, `scripts/summarizer/*`
- **Steps**: (1) give every schema field a real default (move the inline defaults from callers); (2) `load_typed_config(vault=None) -> VaultAppConfig` built from `load_config()` with the same cache; (3) migrate `session_start_hook`, `run_session_end`, `summarizer/pipeline` to attribute access; (4) `get_config` becomes `getattr(getattr(load_typed_config(), section), key, default)`.
- **Method**: Keep `validate_config` warn-only. `get_impact` on `get_config` gives the 47 sites; migrate the three hot modules, leave the rest on the adapter.
- **Verify**: `uv run pytest tests/ -q -k "config or session_start or summarizer"`; `grep -c 'get_config(' skills/parsidion/scripts/session_start_hook.py` is 0.

### [ARC-008] `install.py` → `installer/plan.py` + `installer/cli.py`
- **Files**: `install.py:56-170,288,363-665,977-1338`, new `installer/plan.py`, `installer/cli.py`, `installer/__init__.py:26`, `installer/paths.py:130`, `tests/test_install*.py`
- **Steps**: (1) `InstallPlan` frozen dataclass holding the 16-20 parameters; (2) move `_build_install_steps` and `_print_install_plan` into `installer/plan.py` taking an `InstallPlan`; (3) move `parse_args` into `installer/cli.py` with subparsers (`install` default, `connect`, `disconnect`, `uninstall`, `schedule`) while keeping every existing flag spelling as an alias so `uv run install.py --force --yes` still works; (4) `install.py` becomes `from installer.cli import main; sys.exit(main())`; (5) delete the private re-exports and the duplicate `sys.path` insert in `paths.py:130`.
- **Method**: Tests import `install` for private helpers; `get_impact` on `install` module lists them. Retarget to `installer.plan`/`installer.cli`. Run `uv run install.py --dry-run` before and after and diff the plan text.
- **Verify**: `uv run pytest tests/ -q -k install`; `uv run install.py --dry-run` output identical to pre-change.

### [ARC-010] `run_ai_prompt` vs `run_ai_prompt_with_cause`
- **Files**: `scripts/ai_backend.py:697-783`
- **Steps**: `def run_ai_prompt(*a, **k): return run_ai_prompt_with_cause(*a, **k)[0]` (match the actual tuple shape).
- **Verify**: `uv run pytest tests/ -q -k ai_backend`.

### [ARC-011] `build_graph.py` excluded from pyright
- **Files**: `pyproject.toml:132`, `scripts/build_graph.py`
- **Steps**: Remove the exclude; add `# pyright: basic` at the top of `build_graph.py` and guard numpy imports with `if TYPE_CHECKING` stubs or `from typing import Any` fallbacks until clean.
- **Verify**: `make typecheck`.

### [ARC-012] Stray `skills/parsidion-cc/`
- **Files**: `skills/parsidion-cc/` (untracked)
- **Steps**: Confirm `git status --ignored skills/parsidion-cc` shows only untracked/ignored content; `rm -rf skills/parsidion-cc` (local only, not a repo change); add `skills/parsidion-cc/` to `.parmemignore`.
- **Verify**: `ls skills/` shows only `parsidion`.

### [ARC-013] Interactive TUI loop triplicated
- **Files**: `scripts/vault_tui.py:132-233`, `scripts/vault_review.py:500-601`, `scripts/vault_conflicts.py:352-400`
- **Steps**: In `vault_tui.py`, add `run_list_view(stdscr, rows, render_row, on_key) -> None` owning the curses init, scrolling, and resize; each caller supplies `render_row` and an `on_key(key, idx) -> "quit"|"redraw"|None` callback. Keep key bindings identical.
- **Verify**: `uv run pytest tests/ -q -k "tui or review or conflicts"`; manual smoke of `vault-review` and `vault-conflicts --no-ai`.

---

## Phase 3c — Code Quality (parallel; QA-001 first)

### [QA-001] Order/coverage-dependent test failure
- **Files**: `tests/test_vault_doctor_orchestrator.py:159`, `pyproject.toml:113-114`
- **Steps**: (1) reproduce: `uv run pytest tests/ -q` (no `-x`, coverage on) and read the failure; (2) if it is `Timeout >10s`, add `@pytest.mark.timeout(60)` to the `TestScanExecuteAppliesPythonOnlyFixes` class (and any other test that drives `run_scan_and_repair` end to end); (3) if it is a logic failure, fix the logic and say so in the report.
- **Verify**: `uv run pytest tests/ -q` three consecutive runs green; `make test`.

### [QA-002] Single classify + persist tail
- **Files**: `scripts/agent_adapter.py` (post-ARC-002), `tests/test_session_stop_hook.py`
- **Steps**: Handled inside ARC-002 step 4. If ARC-002 is deferred, apply the same extraction to `session_stop_hook.main`: `_classify(assistant_texts, project, args) -> tuple[list[str], str, bool, str]` and `_persist_and_report(vault, project, categories, summary, queued, mode, start_ts)`.
- **Verify**: `uv run pytest tests/test_session_stop_hook.py -q`; `calculate_cyclomatic_complexity` on `main` ≤ 12.

### [QA-003] Shared `log_hook_error`
- **Files**: `scripts/core/vault_hooks.py` (new), `scripts/session_stop_hook.py:259-279`, `session_start_hook.py:540-560`, `pre_compact_hook.py:308-328`, `post_compact_hook.py:24-44`, `subagent_stop_hook.py:70-90`, `scripts/vault_hooks.py` shim `__all__`
- **Steps**: Copy the most complete variant into `core/vault_hooks.log_hook_error(hook_name: str, exc: BaseException | None = None) -> None`; export via the shim; replace the five bodies with `from vault_hooks import log_hook_error` and `functools.partial`/direct calls. Run after SEC-007/SEC-016 have finished with `vault_hooks.py`.
- **Verify**: `uv run pytest tests/ -q -k hook`; `find_duplicate_code` no longer lists `_log_hook_error`.

### [QA-004] Single `vaults.yaml` reader/writer
- **Files**: `scripts/core/vault_path.py:149-170`, `installer/vault.py:668-760`, `installer/paths.py:243-305`, `tests/fixtures/parity/vault-resolution.json`, `tests/test_vault_resolver_parity.py`
- **Steps**: Add `read_vaults_yaml(path) -> tuple[dict[str,str], str|None]` and `write_vaults_yaml(path, vaults, default, *, preserve=original_text)` to `core/vault_path.py`; `_render_vaults_yaml_for_record` becomes a call; delete the dead `if vault_name not in vaults` branch; `_resolve_vault_root_for_uninstall` uses `read_vaults_yaml`. Run after SEC-012.
- **Method**: `get_impact` on `get_vaults_config_path` and `_render_vaults_yaml_for_record`. The parity fixture pins resolution semantics; the writer must produce a file the reader (and `vaultResolver.ts`, through `vault_resolve.py`) resolves identically.
- **Verify**: `uv run pytest tests/test_vault_resolver_parity.py tests/ -q -k "vaults_yaml or installer"`; `cd visualizer && bun test lib/vaultResolver`.

### [QA-005] Doctor `DoctorOptions` + rule registry
- **Files**: `scripts/doctor/orchestrator.py:77,263,323`, `doctor/worker.py:77`, `doctor/check.py:47,182`, `doctor/tags.py:165,337`, `doctor/protocol.py`, `doctor/cli.py`, `tests/test_vault_doctor*.py`
- **Steps**: (1) `@dataclass(frozen=True) class DoctorOptions` with every flag `run_scan_and_repair` takes; `ScanContext` with `notes`, `note_map`, `state`, `skipped`; (2) thread the two objects instead of 10-13 params (keep a compatibility signature for one release if tests call positionally); (3) in `protocol.py` define `Rule(name, check: Callable[[Note, ScanContext], list[Issue]], fix: Callable | None)`; split `check_note` into one function per existing check (frontmatter syntax, required fields, related links, tags, headings, ...), register in `RULES`, `check_note` iterates; (4) `_apply_prefix_clusters` returns a small dataclass instead of a 5-tuple.
- **Method**: `get_symbol_context` on `check_note` for every call; `find_most_complex_functions` after the change should show none of these above 15. The four historical doctor regressions in memory are the acceptance context: each rule must be independently testable.
- **Verify**: `uv run pytest tests/ -q -k doctor`; `--dry-run` output on a fixture vault identical before/after.

### [QA-006] Visualizer render tests
- **Files**: new `visualizer/components/ReadingPane.test.tsx`, `FrontmatterEditor.test.tsx`, `visualizer/lib/useNoteTabs.test.ts`, `visualizer/package.json` (add `@testing-library/react`, `happy-dom` or `@happy-dom/global-registrator` for bun)
- **Steps**: Install the deps; register happy-dom in a `bunfig.toml` preload; ReadingPane: render with a note, edit, save → `fetch` mocked PUT called with body; conflict (412) shows the conflict banner. FrontmatterEditor: Enter adds a tag, Backspace on empty removes, Escape cancels. `useNoteTabs`: open/close/switch reducer transitions.
- **Verify**: `make visualizer-check`.

### [QA-007] Confirmed dead code
- **Files**: `scripts/session_start_hook.py:531`, `scripts/note_schema.py:101`, `installer/steps.py:131`, `scripts/cli/stats/_common.py:50`
- **Steps**: Delete the four; re-run `find_dead_code` and grep each name in `tests/`.
- **Verify**: `make lint test`.

### [QA-008] Shims still re-export stdlib names
- **Files**: `scripts/subproc_util.py:12-23`, `scripts/vault_constants.py`, `AUDIT-REMEDIATION.md` (QA-005 note, then archived per DOC-024)
- **Steps**: `from core.subproc_util import *` + explicit `__all__` of public names; same for `vault_constants`.
- **Verify**: `uv run python -c "import sys; sys.path.insert(0,'skills/parsidion/scripts'); import subproc_util; assert not hasattr(subproc_util,'os')"`; `make test`.

### [QA-009] Shared `note_index` WHERE builder
- **Files**: `scripts/core/vault_index.py:437-535`, `scripts/cli/search/metadata.py:22-171`, `scripts/core/vault_metrics.py:52-88,235-263`, `scripts/cli/stats/_common.py:50-80`
- **Steps**: `_build_note_index_where(*, tag, folder, note_type, project, recent_days, changed_since=None, as_of=None) -> tuple[str, list]` in `core/vault_index.py`; both callers use it; `cli/stats/_common` imports `open_db`/`fetch_all`/`collect_tags` from `core.vault_metrics` and deletes its copies. Run after SEC-020/021.
- **Verify**: `uv run pytest tests/ -q -k "metadata or note_index or stats"`; `vault-search -f Patterns -T python` output unchanged.

### [QA-010] `run_weekly`/`run_monthly` duplication
- **Files**: `scripts/cli/stats/rollups.py:18-280`
- **Steps**: `_collect_daily_rollup(paths) -> RollupData` (projects, categories, session count) and `_render_rollup(kind, period, data)`; hoist `import re`; drop the unused `conn`; add `tests/test_stats_rollups.py` with a fixture vault (also closes the QA-015 gap for this module).
- **Verify**: `uv run pytest tests/test_stats_rollups.py -q`.

### [QA-011] Edge-build loop copied three times
- **Files**: `visualizer/components/GraphCanvas.tsx:240-258,485-512`, `visualizer/lib/useSigmaInstance.ts:283-310`, new `visualizer/lib/graphEdges.ts`
- **Steps**: `addEdgesForSource(graph, data, {overlay, colors})` with the duplicate-edge swallow inside; three call sites.
- **Verify**: `make visualizer-check`.

### [QA-012] `eslint-disable` volume
- **Files**: `visualizer/components/GraphCanvas.tsx:218-525`, `VaultStats.tsx:161-183`, `app/page.tsx:44,146,199`, `ReadingPane.tsx:73,121`, `FrontmatterEditor.tsx:204,342`, `UnifiedSearch.tsx:74,108`, `HistoryView.tsx:41,70`, `lib/useVaultFiles.ts:161`, new `lib/useLatest.ts`
- **Steps**: `useLatest(value)` returns a ref updated in an effect; replace the per-prop mirroring effects in GraphCanvas with one; for `set-state-in-effect` sites, derive with `useMemo` or reset via `key`. Remove each disable comment once the rule passes.
- **Verify**: `cd visualizer && bun run lint` with `grep -c eslint-disable components/GraphCanvas.tsx` ≤ 2.

### [QA-013] Silent `except OSError: pass` on state writes
- **Files**: `scripts/core/vault_adaptive.py:122,197,232`, `scripts/session_start_hook.py:331`, `scripts/session_start/ai_selector.py:71`
- **Steps**: `print(f"[parsidion] {what} skipped: {exc}", file=sys.stderr)` in each handler (never raise; hooks stay fail-open).
- **Verify**: `uv run pytest tests/ -q -k "adaptive or ai_selector"`.

### [QA-014] `_normalize_underscores_in_frontmatter` double read
- **Files**: `scripts/doctor/tags.py:337-465`
- **Steps**: First pass returns `list[tuple[Path, str, re.Match]]`; second pass rewrites from those; extract `_rewrite_tag_block(match) -> str`.
- **Verify**: `uv run pytest tests/ -q -k "tags"`; `--fix-tags --dry-run` output unchanged on a fixture vault.

### [QA-015] Untested Python modules
- **Files**: `scripts/cli/stats/rollups.py` (see QA-010), `scripts/cli/stats/operations.py`, `scripts/html-to-md.py`, `scripts/check_graph_coverage.py`
- **Steps**: Add `tests/test_stats_operations.py` (`run_hooks` on a fixture log); decide on `html-to-md.py`: if kept, add a 5-case conversion test; if retired, remove the README mention and the file. Same decision for `check_graph_coverage.py` (README/Makefile references).
- **Verify**: `make test`.

### [QA-016] `sys.exit` in library helpers
- **Files**: `scripts/cli/merge/scan.py:41-130`, `scripts/vault_merge.py` (`main`)
- **Steps**: Raise `MergeScanError(msg)`; catch in `main` and exit 1.
- **Verify**: `uv run pytest tests/ -q -k merge`.

### [QA-017] Three `open_db` implementations
- **Files**: `scripts/build_embeddings.py:55`, `scripts/core/vault_metrics.py:52`, `scripts/cli/search/embeddings.py:36`
- **Steps**: One `core/vault_metrics.open_db(path, *, readonly=True, load_vec=False)`; the embeddings variant passes `load_vec=True` (sqlite-vec is optional, import inside the branch).
- **Verify**: `uv run pytest tests/ -q -k "open_db or embeddings or stats"`; `tests/test_stdlib_only.py`.

### [QA-018] `tools/migrate_*.py` near-clones
- **Files**: `tools/migrate_memory.py`, `tools/migrate_research.py`, new `tools/_migrate_common.py`
- **Steps**: After ARC-005, hoist `_print_report`, `_execute_migration`, `_infer_tags`, `_build_frontmatter` (now a call to the emitter) into the shared module.
- **Verify**: `uv run pytest tests/ -q -k migrate`.

### [QA-019] Lint-suppression volume
- **Files**: `tools/eval/*.py` (`sys.path` bootstrap), `tools/eval/__init__.py`, `pyproject.toml` (`[tool.ruff.per-file-ignores]`)
- **Steps**: Make `tools/eval` a package installed via the `eval` extra (or a `conftest`-style `_bootstrap.py` imported first) so the `E402` disables go; move the shim `F401` exemptions to a per-file-ignore for `skills/parsidion/scripts/vault_*.py` instead of per-line.
- **Verify**: `make lint`; `grep -rc "noqa" skills tools | awk -F: '{s+=$2} END {print s}'` below 200.

### [QA-020] Weak test assertions
- **Files**: `tests/test_vault_doctor.py:870-965` and the five assert-less tests
- **Steps**: Replace terminal `assert x is not None` with a value assertion; add an explicit assertion to each assert-less test.
- **Verify**: `make test`.

---

## Phase 3d — Documentation (parallel; after ARC-009)

### [DOC-001] README version 0.20.0
- **Files**: `README.md:11`, `:651`, `CHANGELOG.md:10-28`
- **Steps**: Rewrite the "New in" blurb from the 0.20.0 CHANGELOG entry (grok-cli backend, unified 60 s SessionStart timeout, AI selector Python-side ranking); set "Latest release: 0.20.0"; or replace both with "see CHANGELOG.md" if the user prefers no hard-coded version.
- **Verify**: `grep -n "0.18.0" README.md` returns nothing; `grep -n "Latest release" README.md` shows 0.20.0.

### [DOC-002] grok-cli backend documented
- **Files**: `docs/USAGE.md:138,159,184`, `docs/ARCHITECTURE.md:407,439,1038-1129`, `docs/PROMPTS.md`, `CLAUDE.md:310,360,364`, `README.md:602-606`, `SECURITY.md:17-28,41`; source of truth `scripts/ai_backend.py:19,127-141,393-423,594-660`, `templates/config.yaml`
- **Steps**: Add `ai`, `ai_models`, `claude_cli`, `codex_cli`, `grok_cli`, `adapters` sections to the ARCHITECTURE config block (copy key names and defaults from `vault_schema.py`); change every "claude -p or codex exec" to "the configured prompt backend (`claude -p`, `codex exec`, or `grok`)"; one troubleshooting entry for grok OAuth login. After SEC-008/SEC-032 for the MCP paragraphs.
- **Verify**: `grep -rn "claude -p or codex exec" docs README.md CLAUDE.md SECURITY.md` returns nothing; `grep -c grok docs/ARCHITECTURE.md` > 5.

### [DOC-003] Regenerate `docs/api`, fix drift gate, CI job, parmemignore
- **Files**: `visualizer/typedoc.json` (or the typedoc config in `package.json`), `Makefile:66-79`, `.github/workflows/ci.yml`, `.parmemignore`, `docs/api/`
- **Steps**: (1) set `"gitRevision": "main"` (or `"disableSources": true`) in the typedoc config; (2) `make docs-api`; (3) `make docs-api-check` must exit 0; commit the regenerated tree; (4) add a `docs-api-check` job to `ci.yml` (needs `uv sync --extra docs` and bun); (5) add `docs/api/` to `.parmemignore`.
- **Verify**: `make docs-api-check` exit 0 twice in a row (second run after a no-op commit proves SHA churn is gone).

### [DOC-004] Config table/template/schema sync
- **Files**: `CLAUDE.md:232-234,240,248`, `skills/parsidion/templates/config.yaml:93-94,104-107,120-122,215`, `scripts/core/vault_schema.py:130,163,173,181,279,293`, readers `session_stop_hook.py:429`, `subagent_stop_hook.py:197`, `pre_compact_hook.py:390`, `ai_backend.py:313,324,444,490-494,648`
- **Steps**: Add rows/keys for the three `transcript_tail_bytes`, `codex_cli.allow_danger_full_access`, `adapters.load_external`, `claude_cli.system_prompt`/`timeout`, `grok_cli.system_prompt`; set template `codex_cli.ephemeral/skip_git_repo_check/suppress_notify: true` with a comment that these are the code defaults; delete `summarizer.persist`; mark `adaptive_context.decay_days` "reserved, not yet read by code (see ENH-016)" in table and template (do not delete: ENH-016 implements it).
- **Verify**: a small script comparing `vault_schema` field names against the template keys and the CLAUDE.md table (ENH-017 makes this a permanent gate).

### [DOC-005] 60 s timeout guidance
- **Files**: `README.md:300`, `CLAUDE.md:352`, `docs/ARCHITECTURE.md:287-293,340`, `scripts/session_start_hook.py:103` (comment)
- **Steps**: Replace with "the installer registers a 60 s SessionStart timeout for every runtime; no manual `settings.json` edit is needed".
- **Verify**: `grep -rn "30 s\|30s\|30000" README.md CLAUDE.md docs/ARCHITECTURE.md skills/parsidion/scripts/session_start_hook.py` shows no timeout guidance.

### [DOC-006] SKILL.md `--fix-all` + daily path
- **Files**: `skills/parsidion/SKILL.md:462,86,146,416`; truth `scripts/doctor/cli.py:317-324`, `scripts/core/vault_fs.py:869`
- **Steps**: List all seven flags `--fix-all` implies (frontmatter, tags, subfolder migration, daily-note migration, permissions, strip-prefixes, execute) with the bulk-rename warning already in CLAUDE.md; change the three `Daily/YYYY-MM/DD.md` references to `Daily/YYYY-MM/DD-{username}.md`.
- **Verify**: `grep -n "DD.md" skills/parsidion/SKILL.md` returns nothing; `bash ~/.claude/skills/parsidion/scripts/run_trigger_eval.sh` unchanged (description not edited, so optional).

### [DOC-007] CONTRIBUTING table + resolver
- **Files**: `CONTRIBUTING.md:54-74,124`
- **Steps**: Rebuild the PEP 723 table from `grep -l "# /// script" skills/parsidion/scripts/*.py tools/eval/*.py`; note the `tools/eval/` scripts separately; rewrite line 124: Python `resolve_vault_server()` is canonical and `vaultResolver.ts` delegates via `vault_resolve.py`, pinned by `tests/fixtures/parity/vault-resolution.json`.
- **Verify**: table row count equals the grep count.

### [DOC-008] ARCHITECTURE config reference
- **Files**: `docs/ARCHITECTURE.md:415,1085,1130,1038-1129`
- **Steps**: Remove `sonnet_model`; add the nine missing keys with defaults from `vault_schema.py`. (ENH-017 generates this block permanently.)
- **Verify**: `grep -n sonnet_model docs/ARCHITECTURE.md` returns nothing.

### [DOC-009] omp runtime
- **Files**: `docs/ARCHITECTURE.md:60`, `docs/PI_EXTENSION.md`, `SECURITY.md:17-28`
- **Steps**: Add omp to runtime lists; short "omp" section in PI_EXTENSION.md covering `install.py connect omp`, `--omp-home`, and the shared extension.
- **Verify**: `grep -c omp docs/PI_EXTENSION.md` > 3.

### [DOC-010] USAGE.md flag descriptions
- **Files**: `docs/USAGE.md:61,152`
- **Steps**: `--as-of` applies to metadata filters only; `--scan-only` writes `conflicts/report.json` and still calls the AI backend unless `--no-ai`.
- **Verify**: read.

### [DOC-011] `docs/MCPL.md`
- **Files**: `docs/MCPL.md` → `docs/archive/MCPL.md`, `docs/README.md:31`, `docs/ARCHITECTURE.md:1567`, `agents/research-agent.md:303`
- **Steps**: `git mv`; add a "legacy, not installed" banner; update the three references; make the research-agent step conditional on `which mcpl`.
- **Verify**: `grep -rn "MCPL.md" docs README.md agents` only points at the archive.

### [DOC-012] ENH plan status lines
- **Files**: `docs/opus/ENH-002,004,005,007,008,009,013-*.md:3`, `ENH-009:129`, `ENH-008:153`
- **Steps**: `> Status: shipped in 0.15.0 (<commit>)` on 001-008; `shipped 2026-08-01` on 009-012, 014; `obsolete — all candidates false positives (fa06be8)` on 013; fix the two paths.
- **Verify**: `grep -L "Status:" docs/opus/ENH-*.md` returns nothing.

### [DOC-013] Environment-variable reference
- **Files**: `docs/USAGE.md` (new section); sources listed in AUDIT.md DOC-013
- **Steps**: One table: variable, read by (file), effect, default. Include `CLAUDE_VAULT`, `CLAUDE_TEMPLATES_DIR`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `GEMINI_HOME`, `CODEX_HOME`, `CODEX_SANDBOX`, `CODEX_SESSION_ID`, `USERNAME`/`USER`, `CLAUDE_VAULT_STOP_ACTIVE`, `PARSIDION_INTERNAL`, `PARSIDION_SCRIPTS_DIR`, `PARSIDION_DIR`, `NO_COLOR`, `VAULT_SEARCH_*`, `VISUALIZER_TOKEN`, `VAULT_ROOT`.
- **Verify**: read; link from README "Configuration".

### [DOC-014] SECURITY.md scope
- **Files**: `SECURITY.md:37,41,50,55-57`
- **Steps**: Sync the module list and the 12-module poison list from `tests/test_stdlib_only.py:42-55`; "via the configured CLI backend"; add "Supported versions: latest minor" line (after ARC-009).
- **Verify**: read.

### [DOC-015] CLAUDE.md Makefile table + log path
- **Files**: `CLAUDE.md:289,307,337,341`; truth `Makefile:66-79,100-101,127-128`, `scripts/session_stop_wrapper.sh:24-28`
- **Steps**: Fix the four cells; add `docs-api` and `docs-api-check` rows.
- **Verify**: read against `Makefile`.

### [DOC-016] Tag v0.20.0
- **Files**: git
- **Steps**: `git tag -a v0.20.0 a5036cc -m "v0.20.0"` locally; **ask the user** before `git push --tags`.
- **Verify**: `git tag --list v0.20.0`.

### [DOC-017] Research agent placeholder
- **Files**: `agents/research-agent.md:10`
- **Steps**: Replace both `<vault root>/` with `~/ClaudeVault/`; sync with `uv run install.py --force --yes`.
- **Verify**: `grep -c "<vault root>" agents/research-agent.md` is 0.

### [DOC-018] README installer flags and contradictions
- **Files**: `README.md:99,620`, installer flags section; truth `install.py:1017,1085`
- **Steps**: Add `--omp-home`, `--purge-config`; reconcile line 620 with 102-110 (summarizer runs nightly when scheduled, otherwise on demand); line 99 lists all seven CLIs.
- **Verify**: read.

### [DOC-019] CLAUDE.md prose lag
- **Files**: `CLAUDE.md:14,37,307,311,315,354,362,376` and the flag list
- **Steps**: Apply each correction listed in AUDIT.md DOC-019 (haiku → large tier of configured backend; `find`/`ls` in pre-compact list; `installer/steps.py`, `installer/uninstall.py`; stdlib list additions; `embed_eval_run.py` under `tools/eval/`; `write_hook_event` implemented in `core/vault_fs.py`; pre-commit runs lint + pyright; document the listed flags).
- **Verify**: read against the cited sources.

### [DOC-020] `docs/README.md` index
- **Files**: `docs/README.md:37`
- **Steps**: "ENH-001..014 under `docs/opus/`, ENH-015+ under `docs/fable/`"; list the PNG and five slideshow files.
- **Verify**: read.

### [DOC-021] Archive changelog link
- **Files**: `docs/archive/CHANGELOG-0.11-and-older.md:6`
- **Steps**: `../../CHANGELOG.md`.
- **Verify**: link resolves.

### [DOC-022] pi upstream link
- **Files**: `docs/PI_EXTENSION.md:3`
- **Steps**: Verify the pi repository URL against `~/Repos/pi-mono` remote (`git -C ~/Repos/pi-mono remote -v`) and correct it.
- **Verify**: URL resolves.

### [DOC-023] Minor doc fixes
- **Files**: `docs/USAGE.md:225-229`, `docs/ARCHITECTURE.md:1558-1571`, `docs/AGENT-ADAPTERS.md`, `docs/PAR-MEM.md`
- **Steps**: Add `from pathlib import Path` to the example; extend the Related list; add a short TOC to the two long docs.
- **Verify**: read.

### [DOC-024] Root artifacts
- **Files**: `AUDIT-REMEDIATION.md` (2026-08-02 report), `ENHANCEMENTS.md`, `MEMORY_REPORT.md`, `scripts/vault_common.py:46`, `tools/migrate_research.py:7`, `tools/migrate_memory.py:7`
- **Steps**: After ARC-001: `git mv AUDIT-REMEDIATION.md docs/archive/AUDIT-REMEDIATION-2026-08-02.md` (after QA-008 corrects its QA-005 note and its `:85`/`:139` inconsistencies); `git mv ENHANCEMENTS.md docs/archive/ENHANCEMENTS-2026-08.md` with a header saying the board is the source of truth and ENH-013 is obsolete; delete the local `MEMORY_REPORT.md`; repoint the three code comments to `docs/archive/`. Note: the current-cycle `AUDIT.md`/`AUDIT-REMEDIATION-PLAN.md` are consumed by `/fix-audit`, which archives them itself.
- **Verify**: `ls *.md` at root shows no `ENHANCEMENTS.md`/`MEMORY_REPORT.md`; `grep -rn "AUDIT.md" skills tools` points at the archive.

### [DOC-025] Undocumented public symbols
- **Files**: `scripts/cli/search/_common.py:65,69,73`, `scripts/codex_*_hook.py:14`, `scripts/gemini_*_hook.py:11`, `scripts/vault_embed_serve.py:186`, `scripts/vault_resolve.py:46`, `scripts/doctor/_state.py:100`
- **Steps**: One-line docstrings.
- **Verify**: the docstring-coverage check the documentation agent used (pydocstyle-style scan) reports 100% on these files.

### [DOC-026] Quick-sync `cp` guidance
- **Files**: `CONTRIBUTING.md:97-100`, `CLAUDE.md` "Making Changes"
- **Steps**: State that on macOS/Linux the skill is a symlink so edits are live; the `cp` lines apply to Windows only.
- **Verify**: read.

### [DOC-027] Historical plan/spec links
- **Files**: `docs/superpowers/plans/2026-07-12-par-mem-integration.md:3262-3351`, `plans/2026-07-12-visualizer-parmem-benefits.md:1232,1250`, `specs/2026-03-16-parsidion-mcp-design.md:300-301`, `specs/2026-03-21-visualizer-redesign.md:432`
- **Steps**: Fix the relative links; add a `> Status: shipped` header to the 24 plans/specs lacking one.
- **Verify**: `find_broken_doc_links` no longer lists these files.

---

## Phase 4 — Verification

1. `make checkall` from the repo root (exit code checked directly, not through a pipe).
2. `uv run pytest tests/ -q` three times (QA-001 determinism).
3. `uv run install.py --force --yes` to sync the live skill, then a manual SessionStart hook run: `python skills/parsidion/scripts/session_start_hook.py <<< '{"cwd":"/Users/probello/Repos/parsidion"}'` prints valid JSON.
4. `cd visualizer && bun run build`.
5. Re-run par-mem `index_directory` and `find_duplicate_code` / `find_most_complex_functions`: `_log_hook_error`, `_build_frontmatter`, `rebuild_index` groups gone; no function above complexity 25 in `doctor/` or the hook entrypoints.
6. Close each board card tagged `audit-2026-08-23` only after its **Verify** line passed.
