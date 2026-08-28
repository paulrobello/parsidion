# Vault CLI Reference

Complete command-line reference for the Parsidion vault tooling: search, scaffold, analytics, review, export, merge, conflicts, summarizer, doctor, and the graph/color coverage utilities. Global commands (`vault-search`, `vault-new`, `vault-stats`, `vault-review`, `vault-export`, `vault-merge`, `vault-conflicts`) require `uv run install.py --install-tools` (or `uv tool install --editable ".[tools]"` from the repo root). Without it, invoke the underlying script via `uv run --no-project ~/.claude/skills/parsidion/scripts/<name>.py`.

## Table of Contents

- [Rebuild the vault index](#rebuild-the-vault-index)
- [Build semantic search embeddings](#build-semantic-search-embeddings)
- [Search vault from the CLI](#search-vault-from-the-cli)
- [Scaffold a new vault note](#scaffold-a-new-vault-note)
- [Vault analytics and health](#vault-analytics-and-health)
- [Review pending sessions](#review-pending-sessions)
- [Export vault](#export-vault)
- [Merge near-duplicate notes](#merge-near-duplicate-notes)
- [Detect and resolve conflicting notes](#detect-and-resolve-conflicting-notes)
- [Summarize queued sessions](#summarize-queued-sessions)
- [Run vault doctor](#run-vault-doctor)
- [Run trigger eval](#run-trigger-eval)
- [Preview session start context](#preview-session-start-context)
- [Search vault programmatically](#search-vault-programmatically)
- [Audit graph color group coverage](#audit-graph-color-group-coverage)
- [Reinstall after source changes](#reinstall-after-source-changes)
- [Uninstall](#uninstall)
- [Related Documentation](#related-documentation)

## Rebuild the vault index

```bash
uv run --no-project ~/.claude/skills/parsidion/scripts/update_index.py

# Rebuild the index AND regenerate visualizer graph.json in one pass
uv run --no-project ~/.claude/skills/parsidion/scripts/update_index.py --rebuild-graph

# Also include Daily notes in the graph
uv run --no-project ~/.claude/skills/parsidion/scripts/update_index.py --rebuild-graph --graph-include-daily

# Graph rebuilds are incremental by default (summarizer.graph_incremental config);
# --no-graph-incremental forces a full rebuild.
# Other flags: --vault/-V PATH|NAME
```

## Build semantic search embeddings

```bash
uv run --no-project ~/.claude/skills/parsidion/scripts/build_embeddings.py
```

## Search vault from the CLI

```bash
# Semantic search (natural language) — three output formats
vault-search "sqlite vector search patterns"          # JSON output (default)
vault-search "sqlite vector search patterns" -t       # human-readable text
vault-search "sqlite vector search patterns" -r       # Rich-colorized output
vault-search "hook patterns" -n 5 -r                  # top 5, rich output

# Backend override (semantic mode)
vault-search -B parsight "hook patterns"              # force parsight hybrid backend (no silent fallback)
vault-search -B embeddings "hook patterns"            # force local embeddings pipeline

# Metadata search (filter flags, no query)
vault-search -f Patterns -T python                    # short options
vault-search --folder Patterns --tag python           # long options (also valid)
vault-search -d 7                                     # modified in last 7 days
vault-search -p parsidion -k debugging                # by project and type

# Full-text body search
vault-search --grep "dedup_threshold"                 # case-insensitive body search
vault-search --grep "FLOCK" --grep-case               # case-sensitive body search
vault-search --grep "pattern" -f Patterns             # combine with metadata filters

# Temporal filters (metadata mode; a query cannot be combined with them)
vault-search --changed-since 2026-06-01               # notes modified on/after a date (file mtime)
vault-search --changed-since 2026-06-01 -T python     # combine with other metadata filters
vault-search --as-of 2026-05-15                       # notes whose frontmatter date is on/before a date

# Interactive curses TUI (real-time results, navigation, editor integration)
vault-search --interactive
vault-search -i

# Environment variables (override config.yaml defaults)
VAULT_SEARCH_FORMAT=rich vault-search "query"
VAULT_SEARCH_MIN_SCORE=0.5 VAULT_SEARCH_TOP=5 vault-search "query"
```

**`VAULT_SEARCH_*` environment variables:**

| Variable | Description | Example |
|---|---|---|
| `VAULT_SEARCH_FORMAT` | Default output format: `json`, `text`, or `rich` | `VAULT_SEARCH_FORMAT=rich` |
| `VAULT_SEARCH_MIN_SCORE` | Minimum cosine similarity threshold (0.0–1.0) | `VAULT_SEARCH_MIN_SCORE=0.5` |
| `VAULT_SEARCH_TOP` | Max semantic results | `VAULT_SEARCH_TOP=5` |
| `VAULT_SEARCH_LIMIT` | Max metadata results | `VAULT_SEARCH_LIMIT=20` |
| `VAULT_SEARCH_MODEL` | fastembed model ID | `VAULT_SEARCH_MODEL=BAAI/bge-small-en-v1.5` |

Precedence: **CLI flag > env var > config.yaml > built-in default**

> **Note:** `vault-search` requires `uv run install.py --install-tools` (or `uv tool install --editable ".[tools]"` from the repo root) to register it as a global command. Without this, use `uv run --no-project ~/.claude/skills/parsidion/scripts/vault_search.py` instead.

## Scaffold a new vault note

```bash
# Create a new pattern note and open it in your editor
vault-new --type pattern --title "My Reusable Pattern" --project myproj --tags python,vault --open

# Create a debugging note without opening
vault-new --type debugging --title "Fix SQLite Connection Error" --tags sqlite,python

# See all options
vault-new --help
```

## Vault analytics and health

```bash
vault-stats                        # composite vault health score (default mode, ENH-007)
vault-stats --health --json        # machine-readable health report (consumed by MCP + visualizer)
vault-stats --summary              # note counts, growth, top tags
vault-stats --stale                # notes with no incoming links, older than 30 days
vault-stats --top-linked           # most-referenced notes
vault-stats --by-project           # note counts per project
vault-stats --growth               # notes added per week
vault-stats --tags                 # tag frequency cloud
vault-stats --pending              # pending queue status: count, sources, oldest entry, estimated cost
vault-stats --graph                # knowledge graph metrics: avg degree, hubs, isolated clusters, orphans
vault-stats --hooks 50             # last 50 hook events from hook_events.log
vault-stats --hooks 20 --hooks-window 14  # latency percentiles (p50/p95/max, timeouts) over 14 days, then the raw events
                                  # warns when SessionStart p95 exceeds 70% of its 60s timeout
vault-stats --weekly               # generate weekly rollup note from daily notes
vault-stats --monthly              # generate monthly rollup note from daily notes
vault-stats --timeline 90          # activity bar chart for last 90 days
vault-stats --summarizer-progress  # live feedback from running summarize_sessions.py
vault-stats --dashboard            # full combined dashboard (all modes)
vault-stats --fast                 # skip the metadata-quality scan in --health (faster on large vaults)
vault-stats --weekly --dry-run     # preview rollup output without writing notes (also --monthly)
```

## Review pending sessions

```bash
vault-review                       # interactive TUI: inspect sessions, approve (y) / reject (n) / skip (s)
vault-review --list                # print pending sessions without launching the TUI
vault-review --clear               # remove all entries from the queue (with confirmation)
# Other flags: --vault/-V PATH|NAME
```

> **Approval filtering is not yet implemented.** `vault-review` records per-session approve/reject/skip decisions, but `summarize_sessions.py` does not currently consume them — running it processes the full queue regardless. The `--approved-only` flag referenced in older docs does not exist.

## Export vault

```bash
vault-export --html ~/vault-site   # export to HTML static site
vault-export --zip ~/vault.zip     # export filtered subset as zip
```

## Merge near-duplicate notes

```bash
# Backend-aware: uses the configured prompt AI backend (claude -p, codex exec, or grok).
# NOTE_A survives; NOTE_B is moved to .trash/.
vault-merge --scan                                # list near-duplicate pairs (no merge)
vault-merge NOTE_A NOTE_B --execute               # merge two notes
vault-merge NOTE_A NOTE_B --no-index --execute    # batch mode: skip per-merge index rebuild
vault-merge NOTE_A NOTE_B --execute --from-preview  # reuse cached dry-run merge output
vault-merge NOTE_A NOTE_B --dry-run               # preview merged body without writing
# Other flags: --vault/-V, --output PATH, --threshold SCORE, --top N, --no-ai (naive concat)
```

## Detect and resolve conflicting notes

```bash
vault-conflicts                    # interactive: scan similar pairs, resolve contradictions via prompt AI backend
vault-conflicts --scan-only        # scan + write conflicts/report.json, no TUI (AI still runs unless --no-ai)
vault-conflicts --json             # machine-readable output for scripting
vault-conflicts --no-ai            # list pairs without invoking the AI backend
# Other flags: --vault/-V PATH|NAME, --threshold SCORE (cosine cutoff), --top N (max pairs)
```

## Summarize queued sessions

Generates structured vault notes via the configured prompt AI backend (`ai.backend` in `config.yaml`): `claude-cli` runs `claude -p`, `codex-cli` runs `codex exec`, and `grok-cli` runs `grok --prompt-file` using the CLI's own OAuth login. `auto` (the default) picks the backend from runtime hints — `PARSIDION_RUNTIME=grok` selects grok-cli. No Claude Agent SDK, Codex SDK, or Grok SDK is required.

```bash
# Process all pending sessions (run from a terminal, not inside Claude Code)
uv run --no-project ~/.claude/skills/parsidion/scripts/summarize_sessions.py

# If running from inside a Claude Code session, unset CLAUDECODE to allow nesting:
env -u CLAUDECODE uv run --no-project ~/.claude/skills/parsidion/scripts/summarize_sessions.py

# Preview without writing
uv run --no-project ~/.claude/skills/parsidion/scripts/summarize_sessions.py --dry-run

# Process an explicit file (e.g. to test a single entry)
uv run --no-project ~/.claude/skills/parsidion/scripts/summarize_sessions.py --sessions /path/to/file.jsonl

# Other flags: --model MODEL, --vault/-V PATH|NAME, --retry-dead-letters
#               (pairs with --reason CODE, --min-age-days N, --max-count N),
#               --run-doctor, --rebuild-graph, --graph-include-daily
```

### Dead-letter reason codes

`--retry-dead-letters` re-queues entries from `<vault>/dead_letters.jsonl` whose recorded `last_failure` starts with the `--reason` prefix (default `no_result`, which covers the legacy opaque `no_result` kind and every granular `no_result_*` cause below). Retryable kinds are dead-lettered after 3 failed attempts; non-retryable (deterministic) kinds are dead-lettered on the first attempt, because the same failure re-occurs on every retry (ARC-030). Matching a non-retryable kind with an explicit prefix does re-queue it, but the retry re-bills an AI call to re-derive the same failure — inspect the note by hand instead.

| Code | Meaning | Retryable |
|------|---------|-----------|
| `no_result_timeout` | Backend produced no result within the timeout | Yes |
| `no_result_empty` | Backend returned an empty response | Yes |
| `no_result_backend` | Backend failed to launch, exited nonzero, or is disabled | Yes |
| `transcript_read` | Reading the transcript failed | Yes |
| `ai_backend_error` | Backend raised an unexpected error | Yes |
| `backup_failed` | Pre-mutation backup of the target note failed | Yes |
| `unhandled` | Unexpected exception that may not reproduce | Yes |
| `merge_malformed` | Merge output could not be parsed | No |
| `merge_unresolvable` | Merge decision could not be resolved | No |
| `merge_validation` | Merged note failed frontmatter validation | No |
| `merge_containment` | Merge output violated vault containment | No |
| `note_validation` | Note writer rejected the generated note | No |

Sessions the write-gate skips `_MAX_SKIPS` (2) times are dead-lettered under the literal reason `write-gate skip (transient)`. The catalog lives in `FailureReason` (`skills/parsidion/scripts/summarizer/_state_const.py`).

## Run vault doctor

Scan for issues and repair via the configured prompt AI backend.

```bash
# Scan and report only
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --dry-run

# Repair repairable issues. For Claude CLI backend inside Claude Code, unset CLAUDECODE;
# Codex backend uses codex exec plus an internal recursion guard; grok backend uses
# grok --prompt-file with the CLI's OAuth login (no CLAUDECODE guard applies).
env -u CLAUDECODE uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix --limit 20

# Errors only; skip warnings
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --errors-only --dry-run

# Ignore state file, rescan everything
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --no-state --dry-run

# Detect notes that share the same session_id and suggest consolidation
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-sessions

# Detect 3+ prefix clusters and show candidates for subfolder migration
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --migrate-subfolders

# Preview + execute: move files and update all wikilinks
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --migrate-subfolders --execute

# Run all automated structural maintenance (tags, prefixes, sessions, legacy formats)
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-all
# Other flags: --fix-tags, --fix-frontmatter, --migrate-daily-notes [--daily-username NAME],
#               --list-rules, --only RULE / --skip RULE, --jobs/-j N, --timeout SECS,
#               --model MODEL, --vault/-V PATH|NAME, plus a `notes` positional
```

The doctor is singleton-guarded — it takes an `flock` on `<vault>/.doctor.lock` (released by the kernel when the holder dies) and exits if another instance is already running. Before scanning it auto-commits any uncommitted vault files whose mtime is ≥ 15 minutes old. Notes that time out twice are flagged `needs_review` and skipped on future runs. The vault health summary appears in `CLAUDE.md` after running `update_index.py`.

## Run trigger eval

Run from a separate terminal, not inside Claude Code:

```bash
bash ~/.claude/skills/parsidion/scripts/run_trigger_eval.sh
```

## Preview session start context

```bash
./scripts/show-context
./scripts/show-context /path/to/project
```

## Search vault programmatically

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path.home() / ".claude/skills/parsidion/scripts"))
from vault_common import find_notes_by_tag, find_notes_by_project
```

## Audit graph color group coverage

Find uncovered vault tags and spot stale graph group entries:

```bash
python ~/.claude/skills/parsidion/scripts/check_graph_coverage.py

# Only show tags used 2+ times
python ~/.claude/skills/parsidion/scripts/check_graph_coverage.py --threshold 2

# JSON output for scripting
python ~/.claude/skills/parsidion/scripts/check_graph_coverage.py --json
```

## Reinstall after source changes

```bash
uv run install.py --force --yes
```

## Uninstall

```bash
# Uninstall everything installed by the installer
uv run install.py --uninstall

# Uninstall only hook registrations
uv run install.py --uninstall-hooks
```

## Environment variables

Variables Parsidion reads at runtime. Real environment variables always win over `config.yaml` values (see `anthropic_env` for API-key forwarding).

| Variable | Read by | Effect | Default |
|---|---|---|---|
| `CLAUDE_VAULT` | `core/vault_path.py` (resolver channel 3) | Resolve the vault by path or by name from `vaults.yaml` | unset |
| `VAULT_ROOT` | `core/vault_path.py` (`resolve_vault_server`) | Override the default vault root for server-side resolution (visualizer/MCP path) | unset |
| `CLAUDE_TEMPLATES_DIR` | `core/vault_path.py` | Override the note-templates directory | `~/.claude/skills/parsidion/templates` |
| `XDG_CONFIG_HOME` | `core/vault_path.py` | Base for `~/.config/parsidion/vaults.yaml` lookup | `~/.config` |
| `XDG_CACHE_HOME` | `tools/eval/prompt_eval_run.py` | Cache dir for prompt-eval results | `~/.cache` |
| `USER` / `USERNAME` | `core/vault_fs.py` (`get_vault_username`) | Daily-note filename suffix when `vault.username` is blank | `$USER` (Windows: `$USERNAME`) |
| `CODEX_HOME` | `core/vault_hooks.py` | Codex config/sessions root (transcript allowlist) | `~/.codex` |
| `CODEX_SANDBOX`, `CODEX_SESSION_ID` | `core/ai_backend.py` | Runtime hints: auto-select the `codex-cli` prompt backend | unset |
| `GEMINI_HOME` | `core/vault_hooks.py` | Gemini config root (transcript allowlist) | `~/.gemini` |
| `PARSIDION_RUNTIME` | `core/ai_backend.py` | Runtime hint for `ai.backend: auto` (`grok` selects grok-cli, `codex` codex-cli, `claude` claude-cli) | unset |
| `CLAUDECODE` | `core/ai_backend.py` | Runtime hint (claude-cli); stripped from child environments via `env_without_claudecode()` | unset |
| `CLAUDE_VAULT_STOP_ACTIVE` | `session_stop_hook.py`, `subagent_stop_hook.py` | Recursion guard: set while a Parsidion-launched summarizer runs so nested session-end hooks do not re-queue | unset |
| `PARSIDION_INTERNAL` | hooks, `agent_adapter.py` | Recursion guard for Parsidion-internal CLI invocations | unset |
| `PARSIDION_SCRIPTS_DIR` | pi/omp extension (`scriptRunner.ts`) | Force the hook-scripts directory the extension invokes | installed `~/.claude/skills/parsidion/scripts` |
| `PARSIDION_DIR` | pi/omp extension (`scriptRunner.ts`) | Parsidion checkout root; scripts resolve under `<dir>/skills/parsidion/scripts` | unset |
| `NO_COLOR` | `installer/colors.py` | Disable installer colour output | unset |
| `VAULT_SEARCH_*` | `vault_search.py` | Per-flag defaults: `VAULT_SEARCH_FORMAT`, `VAULT_SEARCH_MIN_SCORE`, `VAULT_SEARCH_TOP`, `VAULT_SEARCH_LIMIT`, `VAULT_SEARCH_MODEL` (CLI flag > env > config) | unset |
| `VISUALIZER_TOKEN` | `visualizer/lib/apiAuth.ts` | When set at server start, every API request requires this bearer token | unset (token auth off) |

## Related Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — Component details, the `vault_common` library surface, and the full configuration reference
- [EMBEDDINGS.md](EMBEDDINGS.md) — Semantic search setup, the embeddings database, and evaluation
- [MULTI_VAULT.md](MULTI_VAULT.md) — Multi-vault setup and `--vault` flag reference
- [README.md](../README.md) — Project overview and installation
