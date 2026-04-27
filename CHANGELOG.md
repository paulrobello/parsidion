# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Default vault path** — new installs now default to `~/ParsidionVault`; existing `~/ClaudeVault` installs are detected and continue to be used unless `~/ParsidionVault` also exists or `--vault` is provided.

### Fixed

- **Summarizer write-gate queue cleanup** — sessions skipped by the write-gate are now treated as processed and removed from `pending_summaries.jsonl` instead of being retried forever.

## [0.6.0] - 2026-04-27

### Added

- **Gemini runtime hooks** — Added installer support for Gemini CLI `SessionStart` and `SessionEnd` hooks, Gemini transcript parsing, and `--runtime gemini` / `--runtime all` runtime selection.
- Added a Codex runtime adapter that registers native `~/.codex/hooks.json` SessionStart and Stop hooks and enables `codex_hooks = true` in `~/.codex/config.toml` when selected. Parsidion does not manage `~/.codex/auth.json`.
- **Codex CLI AI backend for prompt-style scripts** — Parsidion can now route `claude -p`-style helper calls through `codex exec` with `ai.backend: codex-cli` or runtime-aware `auto` detection. Backend-specific model defaults prevent Claude model IDs from being passed to Codex; Codex small and large/synthesis tasks default to `gpt-5.5`.
- **CLI-backed session summarizer** — `summarize_sessions.py` no longer depends on `claude-agent-sdk`; it now uses the configured prompt AI backend, enabling Codex summarization through `codex exec` with backend-aware small/large model defaults.
- **Internal Codex notification suppression** — Parsidion Codex backend calls now pass `--config notify=[]` by default so vault summarization does not trigger user-configured Codex turn-complete notifications.

### Changed

- Added installer runtime selection via `--runtime {claude,codex,gemini,both,all,none}` so users can install Claude hooks, Codex hooks, Gemini hooks, Claude+Codex, all runtimes, or shared vault tooling only.

- Rebranded the project from `parsidion-cc` to `parsidion`. New installs use `~/.claude/skills/parsidion/`, package metadata uses `parsidion`, and docs now describe Parsidion as an agent-agnostic memory/vault layer for coding assistants.
- Renamed the GitHub repository to `paulrobello/parsidion`; GitHub redirects from `paulrobello/parsidion-cc` remain available for legacy links.

### Migration

- The installer now automatically removes managed legacy `parsidion-cc` hook registrations and the old `~/.claude/skills/parsidion-cc/` skill directory or symlink before registering new `parsidion` hooks. User vault contents under `~/ClaudeVault/` are preserved.

## [0.5.6] - 2026-04-19

### Changed

- **Project Explorer Cleanup** — updated `project-explorer` agent to clean up outdated information in the vault for the project being explored (updates or deletes if no longer applicable) instead of strictly appending only.

## [0.5.5] - 2026-04-17

### Added

- **SessionStart AI single-flight and cooldown guards** — `session_start_hook.py` now allows only one nested `claude -p` AI selector per vault at a time and skips repeated AI launches for a short configurable cooldown window via `session_start_hook.ai_single_flight` and `session_start_hook.ai_cooldown_seconds`.

### Fixed

- **Repeated nested SessionStart `claude -p` launches** — AI note selection now degrades cleanly to the standard non-AI path under contention or cooldown instead of spawning overlapping startup selectors.
- **AI SessionStart timeout cleanup** — timed-out nested `claude -p` runs are now launched in their own process group and fully killed on timeout, preventing descendant processes from lingering.

## [0.5.4] - 2026-04-16

### Added

- **pi transcript compatibility across hook pipeline** — `session_stop_hook.py`, `subagent_stop_hook.py`, `summarize_sessions.py`, `pre_compact_hook.py`, and `vault_review.py` now parse both Claude Code JSONL (`type: "assistant" | "user"`) and pi JSONL (`type: "message"` with `message.role`).
- **Config knobs for pi tail parsing** — `session_stop_hook.transcript_tail_lines` (default 200) and `session_stop_hook.pi_transcript_tail_lines` (default 1000) allow deeper fallback parsing for noisy pi tails.
- **`scripts/install-pi-extension` helper** — installs `extensions/pi/parsidion-vault/parsidion-vault.ts` into `~/.pi/agent/extensions` (copy mode by default, `--symlink` for dev mode).
- **Vault `anthropic_env` config support** — Parsidion can now source Anthropic-compatible transport and model env vars from `~/ClaudeVault/config.yaml`, including `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `API_TIMEOUT_MS`, and default model IDs.
- **`/parsidion-vault` Anthropic status reporting** — the pi extension now reports effective Anthropic / GLM config state with per-key source labels (`env`, `vault config`, `unset`) and masked secret previews.

### Changed

- **Transcript path allowlist** — stop hooks now accept transcript paths under `~/.claude/`, `~/.pi/`, and `<cwd>/.pi/`.
- **Subagent minimum-message behavior** — `subagent_stop_hook` uses a pi-friendly default floor of 1 assistant message when `min_messages` is unset for pi transcript paths.
- **Documentation updates** — README, SKILL.md, CONTRIBUTING.md, and ARCHITECTURE.md now document pi transcript support, accepted roots, `anthropic_env`, and pi status reporting.
- **Python runtime env resolution** — `vault_hooks.py`, `vault_common.py`, `summarize_sessions.py`, `vault_doctor.py`, and `vault_merge.py` now apply vault-configured Anthropic-compatible defaults while preserving environment-variable precedence.

## [0.5.3] - 2026-04-09

### Fixed

- **Daily notes missing YAML frontmatter** — `append_session_to_daily()` in `vault_fs.py` created empty daily note files via `daily_path.touch()` when no daily note existed yet, then appended a `## Sessions` section — producing frontmatter-less notes that tripped `vault_doctor` (`MISSING_FRONTMATTER`). The session_stop hook is frequently the first writer of the day, so this was the common path. Fixed by calling `create_daily_note_if_missing(vault=vault_path)` so the `templates/daily.md` template (with proper frontmatter) is always applied. `create_daily_note_if_missing()` also gained an optional `vault` parameter so callers with a custom vault path are respected.

### Changed

- **Project explorer agent** model upgraded from Sonnet to Opus for deeper analysis
- **README** — added Changelog section with link to CHANGELOG.md

## [0.5.2] - 2026-03-30

### Fixed

- **Zombie `session_start_hook.py` processes** on macOS — when the 10 s vault_search semantic-search timeout fired, `subprocess.run` killed the `uv` parent but left the Python grandchild (`vault_search.py`) holding the stdout pipe open. `communicate()` then blocked indefinitely waiting for EOF, causing `session_start_hook.py` to hang past the 30 s hook timeout and accumulate as orphaned processes (reported: 67+ instances on M4 Mac). Fixed by switching to `Popen(..., start_new_session=True)` and killing the entire process group with `os.killpg` on timeout so all descendants are cleaned up immediately.

### Added

- **`knowledge` note type** — new vault category for general knowledge, concepts, and reference material that doesn't fit pattern/research/tool. Added to `vault_path.py` (`VAULT_DIRS`), `install.py`, `vault_new.py` (`_TYPE_TO_FOLDER`), `vault_doctor.py` (`VALID_TYPES`), `SKILL.md` (folder listing and frontmatter docs), visualizer `FrontmatterEditor.tsx`, `NewNoteDialog.tsx`, and `sigma-colors.ts` (pink `#ec4899`).

## [0.5.1] - 2026-03-26

### Added

- **`--enable-embeddings` installer flag** — interactive prompt and CLI flag to enable/disable semantic search embeddings (`embeddings.enabled` in `config.yaml`); defaults to yes in interactive mode
- **`configure_embeddings()` function** in `install.py` — writes `embeddings.enabled` to vault `config.yaml`
- **`summarizer.rebuild_graph` config key** — when `true`, the summarizer automatically rebuilds `graph.json` after indexing (same as `--rebuild-graph` CLI flag, but persisted in `config.yaml`)
- **`summarizer.graph_include_daily` config key** — include Daily notes in graph rebuild (same as `--graph-include-daily` CLI flag)

### Changed

- **AI-powered note selection prompt** now defaults to **yes** (was no) — most users benefit from AI-powered context injection
- **Embeddings status** shown in the installation plan summary
- **`summarize_sessions.py`** — `--rebuild-graph` and `--graph-include-daily` now resolve from config.yaml when CLI flags are not passed (precedence: CLI flag > config > default false)

## [0.5.0] - 2026-03-25

### Added

- **CI pipeline** (`.github/workflows/ci.yml`) — runs `make checkall` on push/PR for both root and `parsidion-mcp/`; build-status badge added to README
- **`vault_config.py`** — new sub-module extracted from `vault_common.py`: config loading, YAML parsing, schema validation
- **`vault_path.py`** — new sub-module: path resolution (`resolve_vault`, `resolve_templates_dir`), vault constants, forbidden-prefix validation, secure log directory helpers
- **`vault_fs.py`** — new sub-module: file locking, pending queue, git commit, daily note lifecycle, vault directory management
- **`vault_index.py`** — new sub-module: frontmatter/body parsing, note search, context building, SQLite index queries
- **`vault_hooks.py`** — new sub-module: hook event logging, env management, transcript helpers, process utilities
- **`vault_adaptive.py`** — new sub-module: adaptive context scoring, last-seen tracking, usefulness scores
- **`vault_tui.py`** — new standalone module: curses-based interactive TUI extracted from `vault_search.py`; lazily imported so metadata/grep modes no longer load curses or fastembed eagerly
- **`embed_eval_common.py`** — shared dataclasses, constants, and utilities for embed eval pipeline
- **`embed_eval_generate.py`** — Phase 1 of embed eval: ground-truth dataset generation
- **`embed_eval_run.py`** — Phase 2 of embed eval: evaluation run against embeddings DB
- **`embed_eval_report.py`** — Phase 3 of embed eval: statistics and HTML/JSON report generation
- **`tests/test_vault_doctor.py`** — 26 unit tests covering vault_doctor validators, state management, link parsing, tag deduplication, and migration logic
- **`tests/test_embed_eval.py`** — 42 unit tests covering embed_eval dataclasses, chunking strategies, CLI parsing, and report generation
- **`resolve_templates_dir()`** in `vault_common.py` — runtime resolution of templates directory (env var `CLAUDE_TEMPLATES_DIR`, sibling `templates/`, or default `~/.claude/skills/parsidion-cc/templates/`)
- **`secure_log_dir()`** in `vault_common.py` — returns `~/.claude/logs/` created with `mode=0o700`
- **`rotate_log_file()`** in `vault_common.py` — log rotation with configurable `max_lines` for hook error logs
- **`is_process_running()`** in `vault_common.py` — canonical implementation (was duplicated in `update_index.py` and `vault_doctor.py`)
- **`append_session_to_daily()`** in `vault_common.py` — moved from `session_stop_hook.py` for reuse
- **`SCRIPTS_DIR`** constant exported from `vault_common.py`
- **`__version__ = "0.5.0"`** exported from `vault_common.py`
- **`VaultToolError`** and **`OpsToolError`** exception classes in `parsidion-mcp` tools
- **`_extract_vault_dirs()`** in `install.py` — reads `VAULT_DIRS` from `vault_common.py` source at runtime, eliminating the duplicate hardcoded list

### Changed

- **`vault_common.py`** is now a thin re-export facade (8 lines) — all public symbols are preserved for full backward compatibility; direct `import vault_common; vault_common.X()` patterns continue to work unchanged
- **`embed_eval.py`** refactored into a thin orchestrator that delegates to the three phase sub-scripts
- **`session_start_hook.py` `_build_candidates()`** — now queries SQLite via `query_note_index()` first; filesystem walk used only as fallback when DB is absent
- **`append_to_pending()` deduplication** — replaced O(n) list scan with O(1) `set[str]` membership test
- **`resolve_vault()` LRU cache** — split into public wrapper (normalizes `Path` → `str`) and private `_resolve_vault_cached()` to fix cache-key inconsistency between `Path("/x")` and `"/x"`
- **`load_config()`** — `@lru_cache` applied directly; `_load_config_cached` indirection removed
- **MCP tools** — all `return "ERROR: ..."` sentinel strings replaced with raised exceptions (`VaultToolError`, `OpsToolError`, `ValueError`)
- **`parsidion-mcp` dev dependencies** — aligned `pytest`, `ruff`, `pyright` versions with root `pyproject.toml`
- **`visualizer/app/api/note/route.ts`** — all synchronous `fs` calls replaced with `fs/promises` + `await`
- **`visualizer/components/GraphCanvas.tsx`** — replaced `useRef<any>` with typed `Sigma` and `AbstractGraph` refs; extracted magic color/numeric constants to `sigma-colors.ts`
- **`visualizer/lib/useVisualizerState.ts`** — betweenness centrality computation now gated behind a 500-node limit
- **`MIN_SCORE` default** corrected to `0.45` in `parsidion-mcp/tools/search.py` (was `0.35`)
- `ideas.md` and `reddit-release.md` moved from repo root to `docs/`

### Fixed

- **Shell injection** (`SEC-001`) — `vault_new.py` `--open` flag: replaced `os.system(f'{editor} "{path}"')` with `subprocess.run([*shlex.split(editor), str(path)], check=False)`
- **World-readable `/tmp` log files** (`SEC-002`) — all `/tmp/parsidion-cc-*` paths redirected to `~/.claude/logs/` (`mode=0o700`)
- **`vault_doctor.py` credential leakage** (`SEC-003`) — replaced 4 `os.environ.copy(); env.pop("CLAUDECODE")` call sites with `vault_common.env_without_claudecode()`
- **Transcript path boundary check** (`SEC-004`) — `session_stop_hook.py` and `subagent_stop_hook.py` now validate transcript path is under `~/.claude/`
- **`vault_write` content size limit** (`SEC-006`) — 10 MB guard added; raises `VaultToolError` on oversized content
- **`vault_write` file extension allow-list** (`SEC-009`) — non-`.md` extensions rejected
- **`cwd` vault path validation** (`SEC-007`) — `resolve_vault()` validates resolved path against `_VAULT_FORBIDDEN_PREFIXES`
- **`VAULT_ROOT` mutation without restore** (`QA-001`) — `try/finally` restore pattern added to `vault_merge.py`, `vault_review.py`, `vault_export.py`, `build_embeddings.py`
- **Installer regex-patching removed** (`ARC-001`) — `install.py` no longer mutates installed `.py` source files; vault path resolved at runtime via `resolve_vault()`
- **`sys.path.insert(0, ...)` removed** (`ARC-002`) — eliminated from all 21 hook scripts and 6 test files
- **`parsidion-mcp` uses `resolve_vault()`** (`ARC-004`) — replaced direct `VAULT_ROOT` references with `resolve_vault()` calls
- **`_CONFIG_SCHEMA`** now includes `"vault": {"username": (str,)}` section (`ARC-010`)
- **`ops.py` `SCRIPTS_DIR`** — now uses `vault_common.SCRIPTS_DIR` instead of fragile path arithmetic (`ARC-007`)
- **`flock_*` type annotations** (`QA-009`) — `f: IO[Any]` added to all 6 flock function signatures
- **`_extract_title` thin wrapper** removed from `update_index.py`; calls `extract_title()` directly (`QA-013`)
- **Stale TODO reference** in `vault_links.py` module docstring updated (`QA-014`)
- **`daily note path format`** in README corrected to `DD-{username}.md` (`DOC-001`)
- **`min_score` default** corrected to `0.45` in README, `docs/EMBEDDINGS.md`, `docs/MCP.md`, `SKILL.md` (`DOC-002`)
- **CONTRIBUTING.md stdlib-only rule** updated to cover all scripts under `skills/parsidion-cc/scripts/` (`DOC-003`, `DOC-004`)
- **`DOCUMENTATION_STYLE_GUIDE.md`** project name corrected to "Parsidion CC" (`DOC-005`)
- **`graph.json`** added to README vault structure block (`DOC-010`)
- **`console.log` → `console.info`** in `visualizer/server.ts` (`DOC-015`)

## [0.4.1] - 2026-03-25

### Added

- **Visualizer: `GET /api/graph` route** — serves `graph.json` from the vault root via API instead of static file, enabling correct per-vault graph data in multi-vault setups
- **Makefile targets documented** in `CLAUDE.md` — full table of all `make` targets including visualizer commands

### Fixed

- **`graph.json` now lives in the vault root** (`{vault}/graph.json`) instead of `visualizer/public/graph.json` — each vault owns its own graph, gitignored and rebuilt locally
- **`build_graph.py` repo-root detection** — replaced broken hardcoded `parent.parent` depth with `.git`-walk so the script works regardless of where it is installed or run from
- **`make graph` / `make graph-with-daily`** — fixed broken Makefile targets pointing at deleted `scripts/build_graph.py` (moved to `skills/parsidion-cc/scripts/`)
- **Visualizer `api/graph/rebuild`** — fixed script path (was `scripts/build_graph.py`, now resolved via `~/.claude/` then repo fallback) and added `--output` flag so rebuild writes to the correct vault
- **Visualizer vault switching** — graph now reloads when the user switches vaults (new `useEffect` on `selectedVault`)
- **`datetime.UTC` alias** in `build_graph.py` (replaced deprecated `timezone.utc` usage)
- **`vault_doctor.py` type annotation** — `seen` set corrected from `set[str]` to `set[tuple[str, str]]`
- **Pyright config** — excluded `.worktrees` and `.venv` to prevent scanning 50k+ spurious errors in third-party packages
- **`TestAppendToPending` tests** — cleared `resolve_vault` lru_cache before each test so `VAULT_ROOT` monkeypatching actually takes effect
- **Windows install** — `install.py` falls back to `shutil.copytree` when symlinks are unavailable

### Changed

- `visualizer/public/graph.json` removed from repo; `public/graph.json` added to `visualizer/.gitignore`
- `graph.json` added to vault `.gitignore` template in `install.py`

## [0.4.0] - 2026-03-24

### Added

#### Multi-Vault Support

Major new feature enabling multiple isolated vaults with per-vault configuration.

- **New `--vault` flag** on all vault tools:
  - `vault-search --vault <name>`
  - `vault-new --vault <name>`
  - `vault-stats --vault <name>`
  - `vault-review --vault <name>`
  - `vault-export --vault <name>`
  - `vault-merge --vault <name>`
  - `vault-doctor --vault <name>`
- **Multi-vault support in build scripts**:
  - `build_embeddings.py --vault <name>`
  - `update_index.py --vault <name>`
- **Multi-vault support in hooks**:
  - `session_start_hook.py`
  - `session_stop_hook.py`
  - `pre_compact_hook.py`
  - `post_compact_hook.py`
  - `subagent_stop_hook.py`
- **New installer option**: `install.py --create-vaults-config` for multi-vault setup
- **Vault resolver**: Centralized vault path resolution with config file support

### Changed

- **Summarizer improvements**:
  - Convert relative dates to absolute dates in generated notes
  - Added `--vault` flag for multi-vault support
- **Archived completed roadmap**: ENHANCE.md moved to archive

### Fixed

- Resolved F821 undefined name errors in vault scripts
- Removed invalid `vault_path` args from pure functions
- Lint fixes and formatting for multi-vault support

## [0.3.5] - 2026-03-24

### Added
- **Windows compatibility** — installer now works on Windows without elevated privileges or Developer Mode
  - Raw strings for patched paths prevent backslash escape issues in `vault_common.py`
  - Lambda-based regex replacement avoids `\U` unicode escape errors in Windows paths
  - Graceful symlink fallback to `shutil.copytree` when symlinks require admin rights

### Fixed
- Lint issues in `build_graph.py` (BLE001, UP017) and `vault_doctor.py` (B007)
- Upgraded Pillow to 12.1.1 to fix CVE (out-of-bounds write in PSD image loading)

## [0.3.4] - 2026-03-24

### Added
- **Real-time vault sync in visualizer** — WebSocket-based live updates when vault files change externally
  - `/ws/vault` WebSocket endpoint with automatic reconnection and exponential backoff
  - Heartbeat mechanism (30-second ping/pong) with connection status indicator in toolbar
  - Live file tree updates (new/deleted notes appear instantly without refresh)
  - Auto-refresh for modified notes with scroll position preservation
  - `graph:rebuilt` event handling triggers automatic graph refetch
- **Conflict detection in visualizer** — warns when external modifications conflict with local edits
  - `ConflictDialog` component with three resolution options: Take theirs / Keep mine / Merge
  - Server-side conflict detection via `lastModified` timestamp comparison
- **Graph includes daily notes by default** — `update_index.py --rebuild-graph` now includes daily notes; use `--no-daily` to exclude
- **Note editing in visualizer** — full edit mode with frontmatter editor, keyboard shortcuts (⌘E/⌘S), and auto-save
- **WebSocket status indicator** — green/amber/red dot in toolbar shows connection health with tooltip

### Changed
- Graph tab is now permanent (cannot be closed) with stable layout persistence
- Clicking a graph node switches to read mode and opens the note
- Same-stem collision handling — visualizer now uses full vault-relative paths instead of stems to disambiguate notes with identical filenames in different folders
- FileExplorer renders vault root files inline under "Root" instead of a phantom folder
- Synthetic `NoteNode` objects created on-the-fly for vault-only notes not in graph.json (e.g. daily notes)
- FrontmatterEditor includes 'daily' in note type options

### Fixed
- WebSocket upgrades for non-vault paths now forward to Next.js so HMR works correctly
- Graph node highlighting uses path not stem to avoid wrong highlights when multiple notes share the same stem
- Opening a note from graph context menu switches to read mode first
- WS status tooltip positioned below dot, not above toolbar edge

### Documentation
- Synced all 13 documents in `docs/` with current implementation
- Updated architecture docs to reflect `vault.username` config and per-user daily note paths
- Fixed `min_score` default values (0.35→0.45) in EMBEDDINGS.md and ARCHITECTURE.md
- Documented real-time sync, conflict detection, and edit mode in VISUALIZER.md
- Marked implemented specs: vault-explorer-agent, parsidion-mcp, visualizer-redesign, git-diff-viewer

## [0.3.3] - 2026-03-23

### Added
- **Git diff viewer in visualizer** — browse version history for any vault note and compare any two commits with syntax-highlighted diffs
  - `HistoryView` component — split-screen container with commit list (left) and diff viewer (right)
  - `CommitList` component — scrollable commit list with FROM/TO badge selection; clicking FROM/TO on any commit sets the comparison range; defaults to latest vs previous commit
  - `DiffViewer` component — three render modes: UNIFIED (single column with `+`/`-` prefixes), SPLIT (side-by-side with aligned line pairs), WORDS (inline word-level highlighting using the `diff` package); default is SPLIT
  - `/api/note/history` route — runs `git log --follow` inside `VAULT_ROOT` and returns a commit list
  - `/api/note/diff` route — runs `git diff` between two commits; supports `to=working` sentinel for uncommitted working-tree diffs; truncates at 5000 lines
  - `parseDiff.ts` — client-side unified diff parser producing typed `DiffHunk[]` / `DiffLine[]` models
  - History accessible from three entry points: HISTORY button in ReadingPane toolbar, right-click context menu on FileExplorer file items, right-click context menu on GraphCanvas nodes
  - `historyMode` / `historyNote` / `openHistory` / `closeHistory` state added to `useVisualizerState`; previous view mode is saved and restored on close
  - Path traversal protection (`guardPath`) and SHA validation on all new API routes

## [0.3.2] - 2026-03-23

### Added
- **Per-user daily notes** — daily notes are now stored as `Daily/YYYY-MM/DD-{username}.md` (e.g. `23-probello.md`) so multiple team members can share a vault via git without merge conflicts
- `get_vault_username()` in `vault_common.py` — resolves username from `vault.username` config key, falling back to `$USER` env var
- `vault.username` config key in `config.yaml` template — new `vault` section
- `vault_doctor --migrate-daily-notes` — renames legacy `DD.md` notes to `DD-{username}.md`, updates wikilinks in weekly/monthly rollup notes, commits, and rebuilds the index
- `--daily-username NAME` flag for `vault_doctor` — explicit override for migration username
- `configure_vault_username()` in `install.py` — writes `vault.username = $USER` to vault `config.yaml` on install if not already set
- `--vault-username NAME` CLI flag for `install.py` — non-interactive username override
- Interactive installer prompt for vault username (shown between summarizer and plan steps)
- Team vault section in `docs/VAULT_SYNC.md` with migration instructions

### Changed
- `vault_doctor --fix-all` now includes `--migrate-daily-notes` (uses auto-detected username)
- Weekly and monthly rollup generators in `vault_stats.py` now handle both `DD.md` (legacy) and `DD-{username}.md` (new), aggregating all users' notes for the same day
- `post_compact_hook.py` falls back to legacy `DD.md` path if the namespaced path does not exist (smooth migration transition)
- Summarizer prompt corrected to use `#` (H1) for the title heading instead of `##`, eliminating recurring heading-promotion noise in `vault_doctor`
- `parse_note_title_slug()` updated to prefer H1 headings when extracting filenames
- `docs/VAULT_SYNC.md` — daily-note conflict section replaced with per-user note explanation; stale conflict troubleshooting entry removed

## [0.3.1] - 2026-03-23

### Added
- Multi-machine vault sync support — installer now initializes the vault as a git repo (`git init` + initial commit) and installs a `post-merge` hook that rebuilds the index and embeddings after every `git pull`
- `install_vault_post_merge_hook()` in `install.py` — creates `.git/hooks/post-merge` with marker-based idempotency; never overwrites user hooks
- `init_vault_git()` in `install.py` — runs `git init`, `git add -A`, and initial commit; silent no-op when `.git` already exists
- `remove_vault_post_merge_hook()` in `install.py` — cleans up the hook on uninstall (only if it was created by the installer)
- `docs/VAULT_SYNC.md` — multi-machine sync guide covering strategies, recommended git setup, what gets synced, conflict handling, and troubleshooting
- FAQ section in README covering token usage, context bloat, and multi-machine sync

### Changed
- `configure_vault_gitignore()` now also adds `pending_summaries.jsonl` and `hook_events.log` to the vault `.gitignore` (previously only `embeddings.db`)
- CLAUDE.md "Vault Git Integration" section updated to describe automatic git initialization and multi-machine sync

## [0.3.0] - 2026-03-18

### Added
- `vault-deduplicator` agent (`agents/vault-deduplicator.md`) — scans for near-duplicate note pairs via embedding similarity, evaluates with parallel Haiku subagents, merges confirmed duplicates with `--no-index`, and rebuilds the index once at the end
- `--no-index` flag for `vault-merge` — skips per-merge index rebuild, enabling efficient batch deduplication workflows; auto-rebuilds by default when omitted
- `vault-merge --scan` — scans all vault notes for near-duplicate pairs using embedding similarity with configurable `--threshold` and `--top` options
- `vault-deduplicator-slideshow.html` — interactive build session slideshow documenting the dedup pipeline creation
- Updated `parsidion-cc-architecture.png` infographic via NotebookLM covering all 5 architectural layers
- `--rich` / `-r` output format for `vault-search` — Rich-colorized one-line-per-note output with score colored green/yellow/red by value, folder in cyan, stem bold, tags dim yellow, and title bright white
- Short options for all `vault-search` flags: `-n`/`--top`, `-s`/`--min-score`, `-m`/`--model`, `-T`/`--tag` (uppercase to avoid conflict with `-t`), `-f`/`--folder`, `-k`/`--type`, `-p`/`--project`, `-d`/`--recent-days`, `-l`/`--limit`, `-j`/`--json`, `-t`/`--text`, `-r`/`--rich`
- `VAULT_SEARCH_*` environment variable support: `VAULT_SEARCH_FORMAT`, `VAULT_SEARCH_MIN_SCORE`, `VAULT_SEARCH_TOP`, `VAULT_SEARCH_LIMIT`, `VAULT_SEARCH_MODEL`; precedence is CLI flag > env var > config.yaml > built-in default
- `rich>=13.0` added to `[tools]` extras in `pyproject.toml` (was only in `[eval]`)
- `vault-review` y/n keyboard support inside transcript popup; auto-chains to next session
- `--run-doctor` flag for `summarize_sessions.py`; cron/launchd always passes it
- `--enable-ai` flag for non-interactive AI mode setup in installer
- Unschedule summarizer on uninstall (launchd/cron)

### Changed
- `vault-merge` now auto-rebuilds the vault index after a successful `--execute` merge (unless `--no-index` is passed)
- Hooks suppress internal `claude -p` sessions from vault queue
- `vault-doctor` auto-checks and repairs legacy pending paths on every run
- Architecture slideshow embedded image updated to v2 infographic
- README.md slideshow links now include vault-deduplicator
- SKILL.md updated with vault-deduplicator agent and vault-merge batch pattern
- All docs synced to current implementation (ARCHITECTURE, AGENTCHROME, EMBEDDINGS, EMBEDDINGS_EVAL, MCP, MCPL)

### Fixed
- `vault-review`: read subagent transcript content from nested `message.content`
- `vault-review`: enable keypad on popup so arrow keys scroll instead of close
- `vault-review`: split transcript text on newlines to prevent curses row corruption
- `vault-review`: store real transcript path for subagent entries so dump works
- `vault-review`: improve transcript-not-found message with explanation
- `vault_common`: forward additional Anthropic env vars (`ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_CUSTOM_HEADERS`, proxy vars) to child processes
- `vault_stats`: use half-block char for bar charts; parse comma-separated tags column in `_collect_tags`
- `chat-to-slideshow` skill: section-label spacing fix (positive margin) and ASCII box alignment guidance

## [0.2.0] - 2026-03-15

### Added
- `uv tool install` support — `pyproject.toml` now declares a `vault-search` `[project.scripts]` entry point and a `[tools]` optional-dependency group (`fastembed`, `sqlite-vec`); running `uv tool install --editable ".[tools]"` from the repo (or `uv run install.py --install-tools`) makes `vault-search` globally available on all platforms without PATH manipulation
- `install.py --install-tools` flag — calls `uv tool install --editable ".[tools]"` automatically as step 11; without the flag, the next-steps summary prints the manual command
- `note_index` table in `embeddings.db` — populated by `update_index.py` on every index rebuild; stores per-note metadata (stem, path, folder, title, summary, tags, type, project, confidence, mtime, related, is_stale, incoming_links) with 5 secondary indexes for sub-millisecond queries without O(n) file walks
- `vault_search.py` merged with former `vault_query.py` — single unified CLI with two modes: semantic (positional `QUERY` string, uses fastembed + sqlite-vec) and metadata (filter flags `--tag`/`--folder`/`--type`/`--project`/`--recent-days`, queries `note_index` table); both modes share identical JSON output with `score` field (`null` for metadata results); mutually exclusive — error if both a query and filter flags are provided
- `ensure_note_index_schema(conn)` in `vault_common.py` — creates `note_index` table and all 5 indexes; called by `build_embeddings.py` `open_db()` so the schema is guaranteed from first DB creation
- `query_note_index(*, tag, folder, note_type, project, recent_days, limit)` in `vault_common.py` — DB-first query; opens DB read-only; returns `None` (not `[]`) when absent or table missing to signal fallback to file walk; uses 4-pattern LIKE for exact tag-token matching (avoids `python` matching `python-decorator`)
- DB-first pattern for `find_notes_by_tag()`, `find_notes_by_project()`, `find_notes_by_type()`, and `find_recent_notes()` in `vault_common.py` — try `query_note_index()` first; fall back to O(n) file walk transparently when DB absent
- `_write_note_index_to_db()` in `update_index.py` — upserts all note rows into `note_index`, prunes rows for deleted notes, swallows all exceptions (never crashes the indexer)
- `subagent_stop_hook.py` — new `SubagentStop` hook (registered with `async: true`) that captures subagent transcripts and queues them for AI summarization via the same `pending_summaries.jsonl` pipeline as the SessionEnd hook
- `excluded_agents` config key under `subagent_stop_hook` (default: `"vault-explorer,research-documentation-agent"`) to prevent recursive capture of vault system agents
- `TRANSCRIPT_CATEGORIES`, `TRANSCRIPT_CATEGORY_LABELS`, `parse_transcript_lines()`, `detect_categories()`, and `append_to_pending()` moved to `vault_common.py` (shared between `session_stop_hook.py` and `subagent_stop_hook.py`)
- `source` and `agent_type` fields in `pending_summaries.jsonl` entries for traceability (`source: "session"` or `"subagent"`)
- `subagent_stop_hook` config section in `config.yaml` template with `enabled`, `min_messages`, and `excluded_agents` keys
- `_HOOK_OPTIONS` dict in `install.py` for per-event hook handler options (enables `async: true` on SubagentStop)
- `[tool.ty.environment]` `extra-paths` in `pyproject.toml` so `ty` resolves `vault_common` from source
- PID singleton guard in `update_index.py` to prevent concurrent runs (mirrors `vault_doctor.py` pattern)
- CONTRIBUTING.md with development setup and PR guidelines
- CHANGELOG.md following Keep a Changelog format
- Makefile with standard quality targets (`lint`, `fmt`, `typecheck`, `test`, `checkall`)
- Unit test suite with 61 tests covering core vault_common functions
- Troubleshooting section in README
- Quick Start and Prerequisites sections in README
- Table of Contents in README
- `__all__` declaration in vault_common.py
- `env_without_claudecode()` helper in vault_common.py
- `--help` flag for `scripts/show-context`
- `related` field guidance in daily note template
- pyright extraPaths config for test module resolution

### Changed
- `vault-explorer.md` agent now has a 7-step workflow with a new Tier 2 metadata search step (step 2) using `vault-search` filter flags between semantic search and the CLAUDE.md+grep fallback; existing steps 2–6 renumbered to 3–7
- Consolidated `extract_text_from_content()` and `read_last_n_lines()` into vault_common.py (was duplicated across hooks)
- Consolidated file locking functions (`flock_exclusive`, `flock_shared`, `funlock`) into vault_common.py
- Replaced `asyncio.gather()` with `anyio.create_task_group()` in summarize_sessions.py
- Replaced `f.readlines()[-n:]` with `collections.deque(f, maxlen=n)` in `read_last_n_lines`
- Extracted `_find_notes_by_field()` generic helper from near-duplicate find_notes functions
- Enhanced `parse_frontmatter()` with multi-line scalar block support; `run_trigger_eval.py` now reuses it
- Reconciled VAULT_DIRS lists between install.py and vault_common.py
- Renamed `_c` to `_colorize` in install.py
- Filter subprocess environment to safe vars only (PATH, HOME, etc.) instead of full passthrough
- Expanded AGENTS.md and GEMINI.md stubs with redirect pattern explanation
- Added upper bounds on PEP 723 dependencies (`claude-agent-sdk<1.0`, `anyio<5.0`)
- `migrate_research.py` accepts research path as CLI argument instead of hardcoding
- `check_graph_coverage.py` imports VAULT_ROOT from vault_common instead of hardcoding
- `migrate_memory.py` computes TODAY at point of use instead of module level

### Fixed
- Added `session_stop_wrapper.sh` shell wrapper for SessionEnd hook; outputs `{}` immediately then runs `session_stop_hook.py` detached via `nohup`, preventing "Hook cancelled" errors when Claude Code exits before `uv run` starts up
- `install.py` now registers `session_stop_wrapper.sh` for SessionEnd (not the Python script directly) and makes `.sh` files executable during install
- Changed `permission_mode` from `bypassPermissions` to `default` in summarize_sessions.py
- Replaced MD5 with SHA-256 for content hashing in migrate_research.py
- Set 0o600 permissions on debug log file in session_start_hook.py
- Added `traceback.print_exc()` to all hook exception handlers (errors no longer silently swallowed)
- Added stderr warnings for unparsable YAML config lines
- Fixed pyproject.toml project name typo (`parsidian-cc` → `parsidion-cc`)
- Fixed type narrowing for `dict[str, object].get()` calls in session_stop_hook.py and summarize_sessions.py
- CLAUDE.md architecture section correctly describes four layers instead of three
- Documentation style guide references Parsidion CC instead of wrong project name
- Line number references removed from CLAUDE.md Key File Paths section
- History folder added to SKILL.md vault structure and update_index.py FOLDER_ORDER
- Added missing .gitignore entries (`*-mcp.json`, `.gemini-clipboard`, `claude_scratch/`, etc.)
- Documented show-context script in ARCHITECTURE.md
- Moved `html-to-md` from `scripts/` to `skills/parsidion-cc/scripts/html-to-md.py`; added `.py` extension (it is a PEP 723 Python script); updated research agent, ARCHITECTURE.md, and slideshows to reference the new path (`~/.claude/skills/parsidion-cc/scripts/html-to-md.py`)

## [0.1.0] - 2026-03-10

### Added
- Claude Vault skill (`skills/parsidion-cc/`) with Obsidian-backed knowledge management
- Session lifecycle hooks: SessionStart, SessionEnd, PreCompact
- AI-powered note selection via `--ai [MODEL]` flag on session start hook
- AI-powered session classification via `--ai [MODEL]` flag on session stop hook
- Session summarizer (`summarize_sessions.py`) using Claude Agent SDK for structured note generation
- Shared library (`vault_common.py`) with frontmatter parsing, vault traversal, config loader, and git integration
- Vault index generator (`update_index.py`) with tag cloud, recent activity, and per-folder listings
- Graph coverage checker (`check_graph_coverage.py`) for auditing Obsidian color groups
- Trigger evaluation harness (`run_trigger_eval.py`) for measuring skill invocation accuracy
- Research documentation agent (`agents/research-documentation-agent.md`)
- Installer (`install.py`) with vault path validation, dry-run mode, and uninstall support
- Centralized configuration via `~/ClaudeVault/config.yaml` with three-tier precedence
- Optional vault git integration with auto-commit support
- 8 note templates (daily, project, language, framework, pattern, debugging, tool, research)
- Architecture documentation with Mermaid diagrams (`docs/ARCHITECTURE.md`)
