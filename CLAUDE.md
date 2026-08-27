# CLAUDE.md

This file provides guidance to AI coding assistants when working with code in this repository.

## Project Overview

Parsidion is the source repository for an agent-agnostic markdown knowledge vault: skills, agents, hook scripts, search/index tools, and visualizer/MCP integrations that give coding agents persistent memory. Claude Code remains the primary installed adapter today, but the core vault tooling is runtime-agnostic.

## Installed vs Source Paths

| Component | Source (this repo) | Installed to |
|---|---|---|
| Installer entrypoint | `install.py` | run in-place (`uv run install.py`) |
| Installer package | `installer/` (Python package: `cli`, `plan`, `steps`, `paths`, `hooks`, `skill`, `schedule`, `vault`, `ui`, `uninstall`, `colors`) | imported by `install.py` |
| Parsidion vault skill | `skills/parsidion/` | `~/.claude/skills/parsidion/` |
| Research agent | `agents/research-agent.md` | `~/.claude/agents/` |
| Hook scripts | `skills/parsidion/scripts/` | referenced from `~/.claude/settings.json` |
| parsidion-mcp sub-project | `parsidion-mcp/` (own `Makefile`, `pyproject.toml`, `src`, `tests`) | standalone MCP server |
| Agent extensions | `extensions/<agent>/` (e.g. `extensions/pi/parsidion/`) | agent-specific integrations |
| Vault | (generated) | `~/ParsidionVault/` (legacy `~/ClaudeVault/` fallback; or custom path) |

**Install model (ARC-026):** The skill install differs by platform.

- **macOS / Linux** — `installer/skill.py:install_skill()` symlinks `~/.claude/skills/parsidion` back to this repo (`skills/parsidion/`), so edits to source files are live in the installed location without a reinstall. The same applies to the visualizer's vault hooks, which can import `vault_common` directly from the repo.
- **Windows** — symlinks require elevated privileges or a Developer-mode opt-in, so the installer falls back to `shutil.copytree`. Edits under `skills/` are **not** picked up live; re-run `uv run install.py --force --yes` after every source change.

The same `~/.claude/skills/parsidion` target is used either way, so callers see a single installed location regardless of platform. The symlink-vs-copy split is the source of ARC-021's "two copies of the same codebase" concern: it is real only on Windows.

Use `install.py` to sync changes from this repo to the installed locations. After editing source files, run:

```bash
uv run install.py --force --yes
```

## Running Scripts

Most scripts (hooks, installer, vault management CLIs) use Python stdlib only. The semantic-search pipeline (`vault_search.py`, `build_embeddings.py`, and the eval harness under `tools/eval/`) additionally uses `fastembed` + `sqlite-vec` + `pillow`, available via the `search`/`tools`/`eval` extras; `make docs-api` uses pdoc from the `docs` extra. Run scripts with `uv`:

```bash
# Install (or reinstall after source changes)
uv run install.py                    # interactive
uv run install.py --force --yes      # non-interactive reinstall
uv run install.py --dry-run          # preview only
uv run install.py --uninstall        # remove skill, agent, hooks, and launchd plist / cron job
uv run install.py --uninstall-hooks  # remove only managed hook registrations from settings.json

# Schedule nightly auto-summarization (launchd on macOS, cron on Linux)
uv run install.py --schedule-summarizer
uv run install.py --schedule-summarizer --summarizer-hour 3  # run at 3 AM

# Also rebuild visualizer graph.json each night
uv run install.py --schedule-summarizer --rebuild-graph
uv run install.py --schedule-summarizer --rebuild-graph --graph-include-daily

# Install vault CLIs as global commands — cross-platform via uv tool
uv run install.py --install-tools    # runs uv tool install --editable ".[tools]"
# OR manually from the repo root:
uv tool install --editable ".[tools]"

# Connect parsidion to another coding agent (hooks + instructions injection)
uv run install.py connect codex     # wires ~/.codex/AGENTS.md + codex hooks
uv run install.py connect gemini    # wires ~/.gemini/GEMINI.md + gemini hooks
uv run install.py connect pi        # installs the pi TypeScript extension (~/.pi/agent/extensions)
uv run install.py connect omp       # installs the same extension for omp (~/.omp/agent/extensions)
uv run install.py disconnect codex  # remove codex integration only

# Rebuild the vault index (after creating/renaming/deleting notes)
uv run --no-project ~/.claude/skills/parsidion/scripts/update_index.py

# Rebuild index AND regenerate visualizer graph.json in one pass
uv run --no-project ~/.claude/skills/parsidion/scripts/update_index.py --rebuild-graph

# Also include Daily notes in the graph
uv run --no-project ~/.claude/skills/parsidion/scripts/update_index.py --rebuild-graph --graph-include-daily

# Graph rebuilds are incremental by default (summarizer.graph_incremental, ENH-010):
# the previous graph is reused and only changed notes are recomputed, with an
# automatic full-rebuild fallback. Override per run with --graph-incremental /
# --no-graph-incremental.
uv run --no-project ~/.claude/skills/parsidion/scripts/update_index.py --rebuild-graph --no-graph-incremental

# Summarize queued sessions (from a terminal outside Claude Code)
uv run --no-project ~/.claude/skills/parsidion/scripts/summarize_sessions.py
uv run --no-project ~/.claude/skills/parsidion/scripts/summarize_sessions.py --dry-run
# Other flags: --sessions FILE (process explicit JSONL), --model MODEL,
#               --persist (legacy no-op), --run-doctor, --rebuild-graph,
#               --graph-include-daily, --vault/-V PATH|NAME,
#               --retry-dead-letters (re-queue dead-lettered entries; pairs with
#               --reason CODE, --min-age-days N, --max-count N)

# Summarize from inside a Claude Code session (unset CLAUDECODE to allow nesting)
env -u CLAUDECODE uv run --no-project ~/.claude/skills/parsidion/scripts/summarize_sessions.py

# Search vault notes (after uv tool install --editable ".[tools]")
vault-search "hook patterns" -n 5            # semantic, top 5
vault-search -n 5 -r "hook patterns"         # semantic, rich output
vault-search -f Patterns                     # metadata: by folder
vault-search -T python -d 7                  # metadata: by tag + recency
vault-search --folder Patterns --tag python  # metadata: long form still works
vault-search --grep "dedup_threshold"        # full-text body search (case-insensitive)
vault-search --grep "FLOCK" --grep-case      # full-text body search (case-sensitive)
vault-search --changed-since 2026-06-01      # temporal: notes modified on/after a date (mtime)
vault-search --as-of 2026-06-01              # temporal: notes dated on/before a date (frontmatter date)
vault-search --interactive                   # interactive curses TUI
# Per-query overrides (0.13.0 flagship flag is --backend/-B):
vault-search -B parsight "query"              # force parsight hybrid backend (silent fallback off)
vault-search -B embeddings "query"           # force local embeddings pipeline
vault-search -s 0.5 "query"                  # min cosine similarity
vault-search -m BAAI/bge-small-en-v1.5 "q"   # fastembed model override
vault-search -l 50 -f Patterns               # max metadata results
vault-search -V ~/MyVault "query"            # target a specific vault (path or name)
VAULT_SEARCH_FORMAT=rich VAULT_SEARCH_MIN_SCORE=0.5 vault-search "query"  # env vars

# Scaffold a new vault note (after uv tool install --editable ".[tools]")
vault-new --type pattern --title "My Pattern" --project myproj --tags python,vault --open
vault-new --type debugging --title "Fix X Error" --tags sqlite

# Vault analytics (after uv tool install --editable ".[tools]")
vault-stats                        # composite vault health score (default mode, ENH-007)
vault-stats --health --json        # health report as JSON (consumed by MCP + visualizer)
vault-stats --summary              # note counts, growth, top tags
vault-stats --stale                # notes with no incoming links older than 30 days
vault-stats --top-linked           # most-referenced notes
vault-stats --by-project           # note counts per project
vault-stats --growth               # notes added per week
vault-stats --tags                 # tag frequency cloud
vault-stats --pending              # pending queue status (count, sources, oldest entry)
vault-stats --graph                # knowledge graph metrics (avg degree, hubs, orphans)
vault-stats --hooks 50             # last 50 hook events from hook_events.log
vault-stats --hooks 20 --hooks-window 7  # per-hook latency table (count/p50/p95/max/timeouts) over N days + SessionStart budget warning; feeds the hook_latency health component
vault-stats --weekly               # generate weekly rollup note from daily notes
vault-stats --monthly              # generate monthly rollup note from daily notes
vault-stats --timeline 90          # activity bar chart for last 90 days
vault-stats --summarizer-progress  # live feedback from running summarize_sessions.py
vault-stats --dashboard            # full combined dashboard
vault-stats --fast                 # skip the metadata-quality scan in --health (faster on large vaults)
vault-stats --dry-run              # preview --weekly/--monthly rollup output without writing notes

# Review pending sessions before summarization (after uv tool install --editable ".[tools]")
vault-review                       # interactive TUI: approve/reject sessions

# Export vault (after uv tool install --editable ".[tools]")
vault-export --html ~/vault-site   # HTML static site
vault-export --zip ~/vault.zip     # filtered zip

# Merge near-duplicate notes (after uv tool install --editable ".[tools]")
# Backend-aware: uses the configured prompt AI backend (claude -p, codex exec, or grok).
# NOTE_A survives; NOTE_B is moved to .trash/.
vault-merge --scan                                # list near-duplicate pairs (no merge)
vault-merge NOTE_A NOTE_B --execute               # merge two notes
vault-merge NOTE_A NOTE_B --no-index --execute    # batch mode: skip per-merge index rebuild
vault-merge NOTE_A NOTE_B --execute --from-preview  # reuse cached dry-run merge output
vault-merge NOTE_A NOTE_B --dry-run               # preview merged body without writing
# Other flags: --vault/-V, --output PATH, --threshold SCORE, --top N, --no-ai (naive concat)

# Detect contradictory notes (after uv tool install --editable ".[tools]")
vault-conflicts                    # scan for contradictions, then interactive TUI review
vault-conflicts --scan-only        # scan + write conflicts/report.json, no TUI
vault-conflicts --json             # emit JSON report to stdout and exit
vault-conflicts --no-ai            # clustering only, skip the AI backend (dry run)
vault-conflicts --threshold 0.80   # cosine threshold for candidate pairs
vault-conflicts --top 50           # limit to top N candidate pairs

# Vault doctor — individual fix modes
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-tags           # detect duplicate tags (dry-run)
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-tags --execute # apply tag merges
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-sessions       # detect multiple notes from same session
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --migrate-subfolders           # detect prefix clusters (dry-run)
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --migrate-subfolders --execute # apply moves
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-frontmatter    # repair frontmatter via the configured prompt AI backend
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --no-fix-headings --fix-frontmatter  # repair frontmatter without heading promotion
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --list-rules         # print the selectable rule catalog (name, kind, risk, description)
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-all --skip strip-prefixes --skip subfolder-prefix  # safe bulk: fix-all without the risky bulk rules

# Vault doctor — migrate legacy un-namespaced daily notes to DD-{username}.md (team use)
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --migrate-daily-notes                            # dry-run
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --migrate-daily-notes --execute                 # apply (uses vault.username from config, then $USER)
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --migrate-daily-notes --daily-username alice --execute  # explicit username

# Vault doctor — fix everything in one pass (used by nightly cron)
# Note: --fix-headings is enabled by default (promotes ## to # when no # heading exists)
# Note: --fix-all implies --execute and ALL fix flags (frontmatter, tags, subfolder
#       migration, daily-note migration, permissions) AND --strip-prefixes (a bulk file rename that
#       rewrites wikilinks vault-wide). Run on a clean git tree so a rename can be reverted.
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-all
# Other flags: --fix (alias for --fix-frontmatter), --errors-only, --no-state,
#               --jobs/-j N (parallel workers, default 3), --timeout SECS (default 120),
#               --limit N, --model MODEL, --strip-prefixes (off unless --fix-all),
#               --fix-permissions (repair note file permissions; implied by --fix-all),
#               --only RULE / --skip RULE (repeatable, mutually exclusive; select rules by
#               name from --list-rules — gates fix modes, scan checks, and the AI repair
#               stage; every scan ends with a per-rule found/fixed/skipped report),
#               --list-rules (print the rule catalog with its risk column and exit),
#               --vault/-V PATH|NAME, plus a `notes` positional (specific notes to check).

# Run the skill trigger accuracy eval (MUST be from a separate terminal, not inside Claude Code)
bash ~/.claude/skills/parsidion/scripts/run_trigger_eval.sh
```

The trigger eval and Claude CLI-backed summarizer cannot run nested inside a Claude Code session because they
invoke `claude` internally. Use `env -u CLAUDECODE` as a workaround when the summarizer backend is `claude-cli`;
Codex-backed summarization uses `codex exec` and grok-backed summarization uses `grok --prompt-file` — neither is
affected by the `CLAUDECODE` guard.

## Vault Git Integration

The installer automatically initializes the vault as a git repository (with `.gitignore` and
initial commit) during installation. When `<vault>/.git` exists, the scripts automatically
stage and commit changes after every vault write (default vault is `~/ParsidionVault/`;
legacy `~/ClaudeVault/` is still honored if present):

- `session_stop_wrapper.sh` / `session_stop_hook.py` — updates the pending queue (gitignored, never committed) and commits the daily note after each session end
- `pre_compact_hook.py` — commits daily note after each pre-compact snapshot
- `update_index.py` — commits `CLAUDE.md` + `TAGS.md` + per-folder `MANIFEST.md` files after each index rebuild
- `summarize_sessions.py` — commits new notes + updated index after processing

If no `.git` directory is present, all `git_commit_vault()` calls are silent no-ops. `git_commit_vault` also self-heals a stale `.git/index.lock` (left by a killed git process) before staging — clearing it only when older than 300 s and not held by a live process (best-effort `lsof`), so a stale lock can no longer silently block all commits.

### Multi-Machine Sync

The installer creates a `post-merge` git hook inside the vault (`<vault>/.git/hooks/post-merge`)
that rebuilds the `note_index` and refreshes embeddings after every `git pull`. This allows sharing
the vault via a private git remote — only markdown notes are synced; `embeddings.db`,
`pending_summaries.jsonl`, and `hook_events.log` are gitignored and rebuilt locally.

See [docs/VAULT_SYNC.md](docs/VAULT_SYNC.md) for the full multi-machine setup guide.

## Vault Configuration

All hook and summarizer options can be set in `<vault>/config.yaml` (default vault `~/ParsidionVault/`; legacy `~/ClaudeVault/` honored if present). Precedence:
**defaults → config.yaml → config.local.yaml → CLI args** (last one wins).

`config.local.yaml` is an optional overlay in the same vault directory, always gitignored
by the installer. `load_config()` deep-merges it over `config.yaml` section-by-section
(local values win on key conflict within a section; a section only present in
`config.local.yaml` is added; nested dicts such as `ai_models.codex` merge recursively).
This lets you keep secrets or machine-specific overrides in `config.local.yaml` while
choosing to git-sync a secret-free `config.yaml`, or vice versa.

A template with all options documented is shipped at `skills/parsidion/templates/config.yaml`.
Copy it to the vault root to get started:

```bash
cp ~/.claude/skills/parsidion/templates/config.yaml ~/ParsidionVault/config.yaml
```

Config sections (generated from `core/vault_schema.py` by `scripts/gen_config_docs.py`; the per-key annotated reference lives in `docs/generated/config-reference.md`):

<!-- config-table:start -->
| Section | Keys | Used by |
|---|---|---|
| `ai` | `backend` | ai_backend.py |
| `ai_models` | `claude`, `codex`, `grok` | ai_backend.py |
| `claude_cli` | `minimal_context`, `system_prompt`, `timeout` | ai_backend.py |
| `codex_cli` | `command`, `timeout`, `sandbox`, `ephemeral`, `skip_git_repo_check`, `suppress_notify`, `allow_danger_full_access` | ai_backend.py |
| `grok_cli` | `command`, `timeout`, `minimal_context`, `system_prompt`, `allow_tools` | ai_backend.py |
| `session_start_hook` | `ai_model`, `ai_cooldown_seconds`, `ai_single_flight`, `ai_candidates_max`, `max_chars`, `ai_timeout`, `recent_days`, `debug`, `verbose_mode`, `use_embeddings`, `track_delta`, `show_dead_letter_notice`, `graph_expand`, `graph_expand_max`, `graph_rerank` | session_start_hook.py |
| `session_stop_hook` | `ai_model`, `ai_timeout`, `auto_summarize`, `auto_summarize_after`, `transcript_tail_lines`, `pi_transcript_tail_lines`, `transcript_tail_bytes` | session_stop_hook.py, agent_adapter.py |
| `subagent_stop_hook` | `enabled`, `min_messages`, `excluded_agents`, `transcript_tail_bytes` | subagent_stop_hook.py |
| `pre_compact_hook` | `lines`, `transcript_tail_bytes` | pre_compact_hook.py |
| `summarizer` | `model`, `max_parallel`, `transcript_tail_lines`, `transcript_tail_bytes`, `max_cleaned_chars`, `ai_timeout`, `cluster_model`, `dedup_threshold`, `dead_letter_retention_days`, `rebuild_graph`, `graph_include_daily`, `graph_incremental`, `persist` (reserved) | summarize_sessions.py, summarizer/transcript.py |
| `embeddings` | `enabled`, `model`, `min_score`, `top_k`, `decay_enabled`, `decay_half_life_days`, `decay_min_factor`, `service_enabled`, `service_idle_exit` | build_embeddings.py, vault_search.py, vault_embed_serve.py |
| `parsight` | `enabled`, `binary`, `timeout_s` | parsight_backend.py, vault_search.py |
| `search` | `backend`, `use_note_index` | vault_search.py, vault_index.py |
| `anthropic_env` | `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_CUSTOM_HEADERS`, `ANTHROPIC_DEFAULT_HAIKU_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, `API_TIMEOUT_MS`, `HTTPS_PROXY`, `HTTP_PROXY` | vault_hooks.py |
| `git` | `auto_commit` | vault_common.py (vault_fs.git_commit_vault) |
| `defaults` | `haiku_model` | ai_backend.py |
| `event_log` | `enabled`, `max_lines`, `path` | vault_hooks.py |
| `adaptive_context` | `enabled`, `decay_days` | session_start_hook.py, vault_adaptive.py, session_start/seed_selection.py |
| `vault` | `username` | vault_fs.py, installer |
| `transcripts` | `tail_lines`, `tail_bytes`, `max_line_bytes` | agent_adapter.py, subagent_stop_hook.py, pre_compact_hook.py, summarizer/transcript.py, core/transcript_reader.py |
| `adapters` | `load_external` | agent_adapter.py |
<!-- config-table:end -->

The config is parsed by `vault_common.load_config()` (simple stdlib YAML parser — supports
one level of nesting, inline comments, scalars). Results are cached per process.
Use `vault_common.get_config(section, key, default)` to read values. The single source of
truth for section names, key names, types, and **real code defaults** is the typed schema in
`core/vault_schema.py` (ARC-007: every dataclass field carries the actual default the readers
use). `vault_common.load_typed_config()` returns the schema-shaped `VaultAppConfig` for
attribute access; `get_config()` is a thin adapter over it.

## Making Changes

**After editing any file under `skills/` or `agents/`**, sync to the live location:
```bash
uv run install.py --force --yes
```

For a single-file quick sync (faster than full reinstall) — **Windows only**: on macOS/Linux
`~/.claude/skills/parsidion` is a symlink to this checkout, so edits are already live and the
`cp` would copy the file onto itself. Use it only on Windows, where the installer copies:
```bash
# Example: after editing vault_common.py
cp skills/parsidion/scripts/vault_common.py ~/.claude/skills/parsidion/scripts/vault_common.py

# After editing SKILL.md
cp skills/parsidion/SKILL.md ~/.claude/skills/parsidion/SKILL.md

# After editing the research agent
cp agents/research-agent.md ~/.claude/agents/research-agent.md

# After editing subagent_stop_hook.py
cp skills/parsidion/scripts/subagent_stop_hook.py ~/.claude/skills/parsidion/scripts/subagent_stop_hook.py
```

**Testing hooks manually** — hooks communicate via JSON on stdin/stdout.
Use heredoc to avoid shell quoting issues with JSON:
```bash
# Test session_start_hook
python skills/parsidion/scripts/session_start_hook.py <<'EOF'
{"cwd": "/Users/yourname/Repos/myproject"}
EOF

# Test session_stop_wrapper (the registered SessionEnd hook)
bash skills/parsidion/scripts/session_stop_wrapper.sh <<'EOF'
{"cwd": "/path/to/project", "transcript_path": "/path/to/transcript.jsonl"}
EOF
# Background work logs to ~/.claude/logs/session_stop_hook.log

# Test session_stop_hook directly (requires a real transcript path)
python skills/parsidion/scripts/session_stop_hook.py <<'EOF'
{"cwd": "/path/to/project", "transcript_path": "/path/to/transcript.jsonl"}
EOF

# Test pre_compact_hook
python skills/parsidion/scripts/pre_compact_hook.py <<'EOF'
{"cwd": "/path/to/project", "transcript_path": "/path/to/transcript.jsonl"}
EOF

# Test subagent_stop_hook (provide a real agent_transcript_path)
python skills/parsidion/scripts/subagent_stop_hook.py <<'EOF'
{"cwd": "/path/to/project", "agent_transcript_path": "/path/to/agent.jsonl", "agent_id": "abc-123", "agent_type": "Explore"}
EOF
```

**stdlib-only rule**: `install.py`, the `installer/` package, and the hook layer — the hook scripts (`session_start_hook.py`, `session_stop_hook.py`, `subagent_stop_hook.py`, `pre_compact_hook.py`, `post_compact_hook.py`, the codex/gemini adapter hooks, `session_stop_wrapper.sh`), the `scripts/core/` subpackage (including `core/ai_backend.py` and `core/parsight_backend.py` since ARC-006), the flat re-export shims (`vault_common.py`, `vault_path.py`, `vault_config.py`, `vault_fs.py`, `vault_hooks.py`, `vault_index.py`, `vault_metrics.py`, `vault_adaptive.py`, `vault_tui.py`, `vault_links.py`, `vault_constants.py`, `vault_health.py`, `subproc_util.py`, `ai_backend.py`, `parsight_backend.py`), the subpackages `session_start/`, `summarizer/`, `doctor/`, and `cli/`, plus `agent_adapter.py`, `prompt_templates.py`, and `note_schema.py` — must use Python stdlib exclusively (or POSIX shell builtins) — no `pip install`, no `uv add`. `pyproject.toml` declares **no required runtime dependencies** (`dependencies = []`); optional-dependency extras (`search`, `tools`, `eval`, `docs`) are reserved for the search/embeddings/CLI/API-doc tools that genuinely need them. ARC-004 makes this constraint executable: `tests/test_stdlib_only.py` imports every in-scope module in a fresh interpreter with 12 third-party modules (`rich`, `fastembed`, `sqlite_vec`/`sqlitevec`, `anyio`, `yaml`/`pyyaml`, `numpy`, `PIL`/`pillow`, `requests`, `aiohttp`) poisoned in `sys.modules`, so a forbidden import — even a transitive one — fails the gate. The CLI/build tools (`vault_stats.py`, `vault_search.py`, `build_graph.py`, `build_embeddings.py`, `vault_embed_serve.py`, `update_index.py`, `html-to-md.py`, `run_trigger_eval.py`) are outside the enforced scope but remain stdlib-first with guarded optional extras.

**Exceptions**:
- `summarize_sessions.py` is a PEP 723 script with an inline `anyio` dependency. Run it with `uv run` — deps are installed automatically into an isolated environment. It uses Parsidion's configured prompt AI backend (`claude -p`, `codex exec`, or `grok --prompt-file`), not the Claude Agent SDK.
- `build_embeddings.py`, `vault_search.py`, and the eval harness scripts under `tools/eval/` (`embed_eval_run.py`, `prompt_eval_run.py`) import `fastembed` (and `sqlite-vec`) — these run under the `search`/`tools`/`eval` extras and degrade gracefully when the libs are absent.

## Pre-Commit Hooks

The repo ships a `.pre-commit-config.yaml` that runs `gitleaks` (secret scanning), `detect-private-key`, file-hygiene hooks, `ruff format --check`, and `ruff check` on every commit, so a commit triggers the same gates as `make fmt-check` + `make lint`; `pyright` runs at the pre-push stage (ARC-014 — full-repo type-checking is too slow for per-commit). Install once with `uv run pre-commit install` (see `CONTRIBUTING.md`). Run all hooks manually:

```bash
uv run pre-commit run --all-files
```

A failed gitleaks/detect-private-key hook is a hard block — never bypass it. If a secret has already been committed, rotate it; do not simply remove the line.

## Makefile Targets
| Target | Command | Notes |
|---|---|---|
| `make install` | `uv run install.py --force --yes` | Sync source → `~/.claude/` |
| `make fmt` | `uv run ruff format .` | Format Python |
| `make fmt-check` | `uv run ruff format --check .` | Verify formatting without rewriting (CI gate) |
| `make lint` | `uv run ruff check .` | Lint Python |
| `make typecheck` | `uv run pyright .` | Type-check Python |
| `make test` | `uv run pytest tests/` | Run unit test suite (numpy-free) |
| `make test-graph` | `uv run --with numpy python -c "import numpy" && uv run --with numpy pytest tests/test_build_graph_parsight.py` | parsight body-link enrichment tests (numpy-gated; the import probe fails fast when numpy cannot load) |
| `make test-search` | `uv run --with sqlite-vec --with fastembed pytest tests/test_search_decay_ordering.py tests/test_vec0_ann_search.py` | sqlite_vec-gated search suites (decay contract + vec0 ANN parity/fallback); skipped by `make test`, which runs without the search extra — both deps are needed because `build_embeddings` exits at import when fastembed is absent |
| `make parity-fixtures` | `uv run python scripts/gen_parity_fixtures.py` | ENH-005: regenerate `tests/fixtures/graph.schema.json` from `build_graph.py:GRAPH_JSON_SCHEMA` and structurally validate the vault-resolution vectors. Run after editing either. |
| `make parity-fixtures-check` | `uv run python scripts/gen_parity_fixtures.py --check` | ENH-005 CI gate — regenerate to temp and diff against the committed fixtures; exits 1 on drift. |
| `make config-docs` | `uv run python scripts/gen_config_docs.py` | ENH-017: regenerate the CLAUDE.md config table, the `docs/ARCHITECTURE.md` reference block, `skills/parsidion/templates/config.yaml`, and the standalone copies under `docs/generated/` from `core/vault_schema.py`. |
| `make config-docs-check` | `uv run python scripts/gen_config_docs.py --check` | ENH-017 drift gate — regenerate in-memory and diff against the committed files; exits 1 on drift. Part of `make checkall` and CI. |
| `make docs-api` | regenerate `docs/api/` (pdoc for Python docstrings, typedoc for visualizer TS) | Rebuild the committed generated API reference; needs the `docs` extra (`uv run --extra docs`) and visualizer devDeps. Commit the result. |
| `make docs-api-check` | regenerate to a temp dir and diff against `docs/api/` | DOC-003 drift gate — exits 1 when the committed reference is stale; wired as its own CI job. |
| `make checkall` | fmt-check + lint + typecheck + test + test-graph + test-search + visualizer-check + checkall-mcp + config-docs-check | Full quality gate. Non-mutating (uses `fmt-check`, not `fmt`); CI runs the same targets via separate jobs (see `.github/workflows/ci.yml`). |
| `make checkall-mcp` | `$(MAKE) -C parsidion-mcp checkall` | parsidion-mcp sub-project gate |
| `make visualizer-check` | `cd visualizer && bunx tsc --noEmit && bun run lint && bun test && bun run build` | Visualizer typecheck + lint + unit tests + production build (the build catches RSC server/client boundary violations tsc alone misses) |
| `make build` | no-op | Managed configuration — no compile step |
| `make clean` | removes `__pycache__` and `*.pyc` | Clean generated artifacts |
| `make graph` | `uv run skills/parsidion/scripts/build_graph.py` | Rebuild `graph.json` (Daily notes **included** by default; `--max-neighbors N` caps semantic edges per note, default 15, `0` emits all pairs) |
| `make graph-with-daily` | `uv run skills/parsidion/scripts/build_graph.py --include-daily` | Historical target kept for backwards compatibility; Daily inclusion is already the default, so this is equivalent to `make graph` |
| `make visualizer` | `cd visualizer && bun dev` | Start visualizer dev server on port 3999 |
| `make build-visualizer` | `cd visualizer && bun run build` | Build visualizer for production |
| `make stop-visualizer` | kills port 3999 | Stop dev server |
| `make visualizer-setup` | `cd visualizer && bun install` | Install visualizer dependencies |
| `make bench-hooks` | `uv run --no-project tools/bench/bench_session_start.py` | ENH-023: SessionStart latency bench + budget gate at N=500/5000 (on-demand, NOT in checkall — machine-dependent budgets; pass extra args via `BENCH_ARGS="--sizes 500 --reps 3"`) |

## Architecture

The system has eleven components:

1. **Hook scripts** — Python scripts fired by Claude Code's lifecycle events, communicating via JSON stdin/stdout:
   - `session_start_hook.py`: Loads relevant vault notes as `additionalContext`. Default mode injects a **compact one-line-per-note index** (title + tags) to minimize token usage; `--verbose` flag or `verbose_mode: true` config switches to full summaries. Optional `--ai [MODEL]` flag uses the configured prompt AI backend (small tier by default, `CLAUDECODE` unset for the claude-cli path) to intelligently select notes — the installer registers a 60 s SessionStart timeout for every runtime, so no manual `settings.json` timeout edit is needed. Also shows a **pending queue warning** when `pending_summaries.jsonl` has entries and prepends a **"Since last time" delta** of new/modified notes per project (controlled by `track_delta` config key). When `adaptive_context.enabled: true`, notes are ranked by historical usefulness and unused notes are deranked over time. **Graph retrieval** (`graph_expand`/`graph_rerank`, both default on) turns the wikilink graph maintained by `vault_links.py` into a retrieval signal: it adds 1-hop neighbours of the selected notes (Tier 1, capped by `graph_expand_max`) and re-ranks by seed-cluster tag overlap + hubness (Tier 2), so the bidirectional backlink graph written at note-creation time is finally traversed at retrieval time. In `--ai` mode the Tier-1 neighbours are instead spliced into the selector's candidate pool (after the project-notes prefix), so the AI sees graph-related prior art; Tier 2 rerank does not apply there because the selector ranks the pool itself.
   - `session_stop_wrapper.sh` + `session_stop_hook.py`: Registered under the `SessionEnd` hook. The shell wrapper reads stdin, outputs `{}` immediately (so Claude Code doesn't cancel it during fast exits), then spawns `session_stop_hook.py` detached via `nohup`. Since ARC-002 the Python script is a thin shim: it keeps Claude's invocation guards (recursion flag, parsight watch release, `_should_skip` input/SEC-004 checks, hook-error logging) and delegates to `agent_adapter.run_session_end` — the single session-end pipeline shared by every runtime (byte-bounded transcript tail with the pi deeper-tail fallback, optional AI classification via `--ai`/`session_stop_hook.ai_model`, keyword-heuristics fallback, daily-note update, queue append to `<vault>/pending_summaries.jsonl`, `git_commit_vault`, auto-summarizer launch, hook-event entry). Uses `fcntl.flock` for safe concurrent access across parallel Claude instances.
   - `pre_compact_hook.py`: Snapshots current task state before context compaction. Extracts the current task by scanning backwards through the last 200 transcript lines for the most recent user text message. Extracts recently-touched files by parsing `tool_use` blocks from assistant messages (Read/Write/Edit/Grep/NotebookEdit plus the pi `find`/`ls` tools). Also captures **git branch** (`git branch --show-current`) and **uncommitted files** (`git status --short`) so Claude knows the exact working tree state after compaction.
   - `post_compact_hook.py`: Restores working context after compaction. Reads today's daily note, finds the most recent `## Pre-Compact Snapshot` section written by `pre_compact_hook.py`, and returns it as `additionalContext` so Claude can resume the session without re-reading files.
   - `subagent_stop_hook.py`: Registered under the `SubagentStop` hook with `async: true` (non-blocking). Reads the subagent's own `agent_transcript_path`, skips agents listed in `excluded_agents` (default: `vault-explorer`, `research-agent`), and queues the transcript to `pending_summaries.jsonl` with `source: "subagent"` and `agent_type` metadata. Uses `agent_id` as the dedup key. Configurable via `subagent_stop_hook` section in `config.yaml`.
   - **Runtime adapter hooks** (registered by `install.py connect <agent>`): `codex_session_start_hook.py` / `codex_stop_hook.py` / `codex_subagent_stop_hook.py` wrap the same context-builder and queue path for Codex CLI (registered in `~/.codex/hooks.json`, native Codex hook lifecycle); `gemini_session_start_hook.py` / `gemini_session_end_hook.py` do the same for Gemini CLI (registered in `~/.gemini/settings.json`). pi uses a TypeScript extension (`extensions/pi/parsidion/`) that shells out to the same Python scripts. Since ARC-002 every runtime — Claude's `session_stop_hook.py` included — runs the same session-end pipeline in `agent_adapter.run_session_end`, so all adapters classify, queue to the same `pending_summaries.jsonl`, update daily notes, commit the vault, and trigger the auto-summarizer identically (each stage config-gated by the same `session_stop_hook.*` / `git.auto_commit` keys).
   - All hooks append a structured JSON line to `<vault>/hook_events.log` via `vault_common.write_hook_event()`. The log is rotated when it exceeds `event_log.max_lines` (default 10,000). Viewable with `vault-stats --hooks N`.

2. **`summarize_sessions.py`** — On-demand PEP 723 script (requires `anyio`). Reads `pending_summaries.jsonl`, pre-processes transcripts, and calls the configured prompt AI backend (`claude -p`, `codex exec`, or `grok --prompt-file`; up to 5 parallel sessions) to generate structured vault notes. Features: **write-gate filter** (the backend decides per-session if insights are reusable before generating a note; a skip is retried up to `_MAX_SKIPS` (2) times before being sticky dead-lettered, since the decision is stochastic on borderline sessions), **hierarchical summarization** (transcripts exceeding `max_cleaned_chars` are chunked and summarized by the backend small model first), **semantic dedup** (before writing a note, checks for near-duplicates using `vault_search.py`; controlled by `summarizer.dedup_threshold` in config, default `0.80`), **automated backlinks** (via `vault_links.py` — injects bidirectional wikilinks after each note write). Cleans processed entries from the queue and rebuilds the index when done.

3. **`vault_common.py`** — Shared library imported by all hooks. The implementation has been split (ARC-005) into focused modules — `vault_config.py` (config loading/merge), `vault_path.py` (paths, `VAULT_ROOT`, `EXCLUDE_DIRS`, log rotation), `vault_fs.py` (file locking, pending queue, git commit, daily notes), `vault_index.py` (`note_index` schema, `query_note_index()`, `build_compact_index()`, the `find_notes_by_*` family), `vault_fs.py` (also home to hook-event logging: `write_hook_event()`), `vault_adaptive.py` (adaptive-context decay), plus `vault_metrics.py` and `vault_tui.py` for stats/TUI helpers. `vault_common.py` re-exports the public API, so existing `import vault_common` callers keep working. ARC-004 moved these implementations into the `scripts/core/` subpackage (`core/vault_config.py`, `core/vault_path.py`, …); the flat `vault_config.py`/`vault_path.py`/… names remain as thin re-export shims, so `import vault_config` and `from vault_config import X` keep working unchanged across hooks, CLIs, tests, `parsidion-mcp`, and the installer. `ai_backend.py` and `parsight_backend.py` live in `core/` too (ARC-006 moved them under the stdlib gate); the flat root names remain as re-export shims — tests that monkeypatch module internals (`_run_prompt_subprocess`, `_run_parsight`, `_LOG_DIR`, `ai_backend.shutil`) target `core.ai_backend` / `core.parsight_backend` directly, while patches of public names via a carrier module's `X.ai_backend` reference keep working through the shim. `_SAFE_ENV_KEYS` (in `vault_hooks.py`) controls which env vars are forwarded to `claude -p` subprocess calls; includes `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_CUSTOM_HEADERS`, `ANTHROPIC_DEFAULT_{HAIKU,SONNET,OPUS}_MODEL`, `API_TIMEOUT_MS`, and `HTTPS_PROXY`/`HTTP_PROXY` so non-default API configurations (proxy, org key, Bedrock, corporate network) work correctly. `build_compact_index()` was moved here from `session_start_hook.py` and is also used by `parsidion-mcp`. All vault operations go through this module surface.

   **Related helper modules** (used by the components below): `ai_backend.py` — backend-neutral prompt AI helpers used by `session_start_hook.py`, `summarize_sessions.py`, `vault_doctor.py`, `vault_merge.py`, and `embed_eval*.py`; selects between `claude-cli`, `codex-cli`, and `grok-cli` based on the `ai.backend` config and runtime hints (e.g. `PARSIDION_RUNTIME=grok`), with per-backend model tiers from `ai_models`. `build_graph.py` — rebuilds `<vault>/graph.json` from `embeddings.db` (Daily notes included by default; `--no-daily` excludes them; semantic edges capped to the top `--max-neighbors` per node, default 15 — `0` restores the all-pairs behaviour). `check_graph_coverage.py` — audits vault tags vs Obsidian `graph.json` color groups. `parsight_backend.py` — optional parsight code-memory daemon bridge (subprocess transport + availability probe). `vault_embed_serve.py` — optional persistent embedding service (ENH-003; AF_UNIX socket, opt-in via `embeddings.service_enabled`, never used while parsight serves retrieval) that lets short-lived `vault_search` callers share one warm model instead of each cold-loading ~67 MB ONNX. `agent_adapter.py` — the runtime-adapter registry: one `AgentAdapter` per coding-agent runtime (claude/codex/gemini/pi + opt-in external drop-ins) drives the hook shims and the installer's generic merge/remove — see [docs/AGENT-ADAPTERS.md](docs/AGENT-ADAPTERS.md) for the contract and how to add a runtime.

4. **`vault_search.py`** — Unified search CLI with four modes. **Semantic mode** (positional `QUERY`): fastembed + sqlite-vec cosine similarity search. **Metadata mode** (filter flags `--tag`/`-T`, `--folder`/`-f`, `--type`/`-k`, `--project`/`-p`, `--recent-days`/`-d`, no query): SQL query against `note_index` table. **Full-text body search** (`--grep`/`-G` flag): scans note bodies for a regex pattern; `--grep-case` enables case-sensitive matching. **Interactive TUI** (`--interactive`/`-i`): curses-based interface with real-time results, navigation, and editor integration. All modes output identical JSON with a `score` field (`null` for metadata/grep). Three output formats: `--json`/`-j` (default), `--text`/`-t` (human-readable), `--rich`/`-r` (Rich-colorized one-line-per-note). All flags have short options; defaults configurable via `VAULT_SEARCH_*` environment variables. Installed globally as `vault-search` via `uv tool install`. Used by `vault-explorer` agent for both Tier 1 and Tier 2 search.

5. **`vault_links.py`** — Shared stdlib-only module for backlink operations. Extracted from `summarize_sessions.py` and used by both the summarizer and `parsidion-mcp`. Key functions: `find_related_by_tags()` (tag-overlap candidates), `find_related_by_semantic()` (embedding-based candidates), `inject_related_links()` (add wikilinks to a note's `related` field), `add_backlinks_to_existing()` (bidirectional backlink injection after a new note is written).

6. **`vault_stats.py`** — Analytics CLI installed globally as `vault-stats`. Original modes: `--summary`, `--stale`, `--top-linked`, `--by-project`, `--growth`, `--tags`, `--dashboard`. New modes: `--pending` (pending queue status with source breakdown and estimated token cost), `--graph` (knowledge graph metrics: average degree, hub notes, isolated clusters, orphans, citation chains), `--hooks N` (last N events from `hook_events.log`), `--weekly` (generate weekly rollup note from daily notes), `--monthly` (monthly rollup), `--timeline N` (activity bar chart for last N days), `--summarizer-progress` (read `~/.claude/logs/parsidion-summarizer-progress.json` for live feedback from a running `summarize_sessions.py`).

7. **`vault_review.py`** — Interactive TUI (`vault-review` global command) for inspecting and cleaning the pending-sessions queue. Flags: `--vault`/`-V`, `--list` (print sessions without launching the TUI), `--clear` (empty the queue with confirmation).

8. **`vault_export.py`** — Export tool (`vault-export` global command). Supports HTML static site and filtered zip.

9. **`vault_merge.py`** — AI-assisted note merging tool (`vault-merge` global command). Detects near-duplicate notes, merges their content via the configured prompt AI backend, and updates all bidirectional backlinks.

10. **`vault_conflicts.py`** — Contradiction detector (`vault-conflicts` global command, companion to `vault-merge`). Where `vault-merge` collapses near-duplicate notes saying the *same* thing, `vault-conflicts` surfaces semantically-similar pairs saying *opposite* things. Default behavior: scan the vault (embedding-similarity candidate clustering) then drop into an interactive TUI for review. Flags: `--threshold` (cosine cutoff), `--top` (max pairs), `--vault`/`-V`, `--scan-only` (write `conflicts/report.json` and exit, no TUI), `--json` (print report and exit), `--no-ai` (cluster only, skip the AI backend — dry run). Read-only: it detects and reports; it does not mutate notes.

11. **`~/ParsidionVault/`** (legacy `~/ClaudeVault/`) — The Obsidian vault itself. Auto-generated lean `CLAUDE.md` index (stats, conventions, recent activity, folder pointers) and `TAGS.md` (full tag cloud for summarizer tag reuse) at the root. Subfolders: `Daily/`, `Projects/`, `Languages/`, `Frameworks/`, `Patterns/`, `Debugging/`, `Tools/`, `Research/`, `Knowledge/`, `History/`, `Templates/` (symlink to skill templates). Per-folder `MANIFEST.md` files contain detailed note listings (table format). `embeddings.db` contains `note_embeddings` (vectors) and `note_index` (metadata). `hook_events.log` records structured JSON hook execution events.

## Vault Note Conventions

Every note **must** have YAML frontmatter:
```yaml
---
date: YYYY-MM-DD
type: pattern|debugging|research|project|daily|tool|language|framework|knowledge
tags: [tag1, tag2]
project: project-name   # optional
confidence: high|medium|low
sources: []
related: ["[[note-one]]", "[[note-two]]"]  # inline quoted array; must contain at least one [[wikilink]]
provenance: explicit|inferred|corrected|observed|imported   # optional — how the knowledge was obtained
session_id: <uuid>      # optional — set by summarize_sessions.py on AI-generated notes
---
```

- Filenames: kebab-case, 3-5 words, no date suffix
- **Daily notes**: stored as `Daily/YYYY-MM/DD-{username}.md` (e.g. `Daily/2026-03/23-probello.md`) — the hook writes them there automatically using the `vault.username` from `config.yaml` (defaults to `$USER`). Never create flat `Daily/YYYY-MM-DD.md` files. Legacy un-namespaced `DD.md` files can be migrated with `vault_doctor.py --migrate-daily-notes`.
- No orphan notes — every note must link to at least one other note via `related`
- Search before create — update existing notes rather than creating duplicates
- **Tag brevity**: prefer short singular kebab-case tags — e.g. `voxel` not `voxel-engine`, `hook` not `hooks`, `fractal` not `fractals`. **Never use underscores** in tags or the `project` field — convert repo names like `par_ai_core` to `par-ai-core`. Use a longer form only when the short form would be genuinely ambiguous.
- `Templates/` is a symlink to `skills/parsidion/templates/` — never edit template files directly from the vault side
- **Subfolder rule**: when 3 or more notes share a common subject prefix, move them into a subfolder named after that subject. Drop the redundant prefix from filenames inside the subfolder. Only one level of subfolder is allowed — never nest subfolders within subfolders. Update all wikilinks and run `update_index.py` after reorganizing.

## Skill SKILL.md Structure

`skills/parsidion/SKILL.md` has YAML frontmatter with `name` and `description` fields. The description is what Claude Code uses for automatic skill invocation — it was iteratively optimized using `run_trigger_eval.py`. When modifying the description, run the trigger eval to measure impact on precision/recall.

## Research Agent

`agents/research-agent.md` defines a Sonnet-powered agent that:
1. Searches the vault (`~/ParsidionVault/`, legacy `~/ClaudeVault/`) first for existing knowledge
2. Uses Brave Search + Web Fetch for external research
3. Saves findings to the appropriate vault subfolder with YAML frontmatter
4. Runs `update_index.py` after saving

## Key File Paths in Code

Throughout this section `<vault>` is the resolved vault root (`~/ParsidionVault/`, or legacy `~/ClaudeVault/` if that is what exists). Vault resolution order (see `vault_path.py:resolve_vault()`):

1. `--vault PATH_OR_NAME` CLI flag (path or name from `~/.config/parsidion/vaults.yaml`)
2. `<cwd>/.claude/vault` file (path or configured name)
3. `CLAUDE_VAULT` environment variable (path or configured name)
4. Top-level `default:` key in `~/.config/parsidion/vaults.yaml` (vault name or registered path; unset/unresolvable values are ignored)
5. `~/ParsidionVault` (or legacy `~/ClaudeVault` if it exists)

> **ENH-005 / ENH-009 — vault-resolution contract.** Since ENH-009 the resolution algorithm is single-sourced in Python: `visualizer/lib/vaultResolver.ts:resolveVault()` delegates to `core/vault_path.py:resolve_vault_server()` (the deliberately narrower server contract — named vaults + default + `VAULT_ROOT`; no `cwd/.claude/vault` or `CLAUDE_VAULT` channel, since the visualizer is a long-lived server with no current project) via the stdlib `vault_resolve.py` CLI. There is no longer a second implementation to drift. The shared observable behaviour is still pinned by the fixture at `tests/fixtures/parity/vault-resolution.json`, consumed by `tests/test_vault_resolver_parity.py` (the authoritative resolver-semantics suite) and `visualizer/lib/vaultResolver.parity.test.ts` (end-to-end through the delegated path, `uv`-gated). **Changing the contract requires updating that fixture** (add a vector, then run both test files). The `tests/fixtures/graph.schema.json` contract is likewise generated — run `make parity-fixtures` after editing `GRAPH_JSON_SCHEMA` in `build_graph.py`; CI fails on drift via `make parity-fixtures-check`.

- `VAULT_ROOT` = module-level constant in `vault_path.py` holding the *default* vault path (used as a fallback by `resolve_vault()`). Re-exported from `vault_common.py` for backwards compatibility with external callers (e.g. parsidion-mcp, tests). **Not patched by the installer** (ARC-001) — code should call `resolve_vault()` / `resolve_templates_dir()` rather than reading the constant directly.
- `TEMPLATES_DIR` = module-level constant in `vault_path.py` defaulting to `~/.claude/skills/parsidion/templates/`. Re-exported from `vault_common.py`. **Not patched by the installer** (ARC-001) — code should call `resolve_templates_dir()`.
- `pending_summaries.jsonl` = `<vault>/pending_summaries.jsonl` — queue of sessions awaiting AI summarization. Each line: `{"session_id": "...", "transcript_path": "...", "project": "...", "categories": [...], "timestamp": "..."}`. Deduplicated by `session_id`.
- `embeddings.db` = `<vault>/embeddings.db` — SQLite database with two tables: `note_embeddings` (384-dim float32 vectors built by `build_embeddings.py`) and `note_index` (per-note metadata built by `update_index.py`: outgoing `related` links, the `incoming_links` count, and `incoming_stems` — a JSON array of source stems holding the reverse-link adjacency inverted at index time, ENH-021; empty on pre-ENH-021 rows, where consumers fall back to deriving the inversion from `related`). Queried by `vault_search.py` (both modes) and `vault_common.query_note_index()`. All callers fall back gracefully when absent.
- `EXCLUDE_DIRS` = set of folder names skipped by the indexer and vault traversal (defined in `vault_path.py`, re-exported from `vault_common.py`). Currently: `.obsidian`, `Templates`, `.git`, `.trash`, `TagsRoutes`.
- `hook_events.log` = `<vault>/hook_events.log` — structured JSON log of hook executions. Each line: `{"hook": "SessionStart", "ts": "...", "project": "...", "notes_injected": 5, "chars": 2800, "duration_ms": 320}`. Rotated at `event_log.max_lines` (default 10,000). Written by `vault_common.write_hook_event()`. Read by `vault-stats --hooks N`.
- `conflicts/report.json` = `<vault>/conflicts/report.json` — output of `vault-conflicts --scan-only` (and any `vault-conflicts` run). Lists the candidate contradiction pairs detected by embedding-similarity clustering, with scores and the conflicting note paths. Written atomically by `vault_conflicts.py`.
- Summarizer progress file: `~/.claude/logs/parsidion-summarizer-progress.json` — written by `summarize_sessions.py` during a run; read by `vault-stats --summarizer-progress`.
- Hook registration: `~/.claude/settings.json`
- Trigger eval results: `~/.claude/skills/parsidion/eval_results.json`
- Installer: `install.py` (repo root) backed by the `installer/` Python package
