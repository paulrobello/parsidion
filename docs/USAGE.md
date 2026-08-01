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

# Metadata search (filter flags, no query)
vault-search -f Patterns -T python                    # short options
vault-search --folder Patterns --tag python           # long options (also valid)
vault-search -d 7                                     # modified in last 7 days
vault-search -p parsidion -k debugging                # by project and type

# Full-text body search
vault-search --grep "dedup_threshold"                 # case-insensitive body search
vault-search --grep "FLOCK" --grep-case               # case-sensitive body search
vault-search --grep "pattern" -f Patterns             # combine with metadata filters

# Temporal filters (metadata mode) — narrow by note mtime
vault-search --changed-since 2026-06-01               # notes modified on/after a date
vault-search --changed-since 2026-06-01 -T python     # combine with other metadata filters
vault-search --as-of 2026-05-15 "fastapi middleware"  # semantic search against note state at a past date

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
vault-stats --weekly               # generate weekly rollup note from daily notes
vault-stats --monthly              # generate monthly rollup note from daily notes
vault-stats --timeline 90          # activity bar chart for last 90 days
vault-stats --summarizer-progress  # live feedback from running summarize_sessions.py
vault-stats --dashboard            # full combined dashboard (all modes)
```

## Review pending sessions

```bash
vault-review                       # interactive TUI: inspect sessions, approve (y) / reject (n) / skip (s)
```

> **Approval filtering is not yet implemented.** `vault-review` records per-session approve/reject/skip decisions, but `summarize_sessions.py` does not currently consume them — running it processes the full queue regardless. The `--approved-only` flag referenced in older docs does not exist.

## Export vault

```bash
vault-export --html ~/vault-site   # export to HTML static site
vault-export --zip ~/vault.zip     # export filtered subset as zip
```

## Merge near-duplicate notes

```bash
# Backend-aware: uses the configured prompt AI backend (claude -p or codex exec).
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
vault-conflicts --scan-only        # list candidate conflict pairs only (no AI, no writes)
vault-conflicts --json             # machine-readable output for scripting
vault-conflicts --no-ai            # list pairs without invoking the AI backend
```

## Summarize queued sessions

Generates structured vault notes via the configured prompt AI backend: Claude uses `claude -p`, Codex uses `codex exec`; no Claude Agent SDK or Codex SDK required.

```bash
# Process all pending sessions (run from a terminal, not inside Claude Code)
uv run --no-project ~/.claude/skills/parsidion/scripts/summarize_sessions.py

# If running from inside a Claude Code session, unset CLAUDECODE to allow nesting:
env -u CLAUDECODE uv run --no-project ~/.claude/skills/parsidion/scripts/summarize_sessions.py

# Preview without writing
uv run --no-project ~/.claude/skills/parsidion/scripts/summarize_sessions.py --dry-run

# Process an explicit file (e.g. to test a single entry)
uv run --no-project ~/.claude/skills/parsidion/scripts/summarize_sessions.py --sessions /path/to/file.jsonl
```

## Run vault doctor

Scan for issues and repair via the configured prompt AI backend.

```bash
# Scan and report only
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --dry-run

# Repair repairable issues. For Claude CLI backend inside Claude Code, unset CLAUDECODE;
# Codex backend uses codex exec plus an internal recursion guard.
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
```

The doctor is singleton-guarded — it stores its PID in `doctor_state.json` and exits if another instance is already running. Before scanning it auto-commits any uncommitted vault files whose mtime is ≥ 15 minutes old. Notes that time out twice are flagged `needs_review` and skipped on future runs. The vault health summary appears in `CLAUDE.md` after running `update_index.py`.

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

## Related Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — Component details, the `vault_common` library surface, and the full configuration reference
- [EMBEDDINGS.md](EMBEDDINGS.md) — Semantic search setup, the embeddings database, and evaluation
- [MULTI_VAULT.md](MULTI_VAULT.md) — Multi-vault setup and `--vault` flag reference
- [README.md](../README.md) — Project overview and installation
