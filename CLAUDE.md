# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

parsidion-cc is a Claude Code customization toolkit — the **source repository** for skills, agents, and hook scripts that get installed into `~/.claude/`. It is not a runnable application; it is managed configuration and scripts.

## Installed vs Source Paths

| Component | Source (this repo) | Installed to |
|---|---|---|
| Installer | `install.py` | run in-place (`uv run install.py`) |
| Claude Vault skill | `skills/parsidion-cc/` | `~/.claude/skills/parsidion-cc/` |
| Research agent | `agents/research-agent.md` | `~/.claude/agents/` |
| Hook scripts | `skills/parsidion-cc/scripts/` | referenced from `~/.claude/settings.json` |
| Vault | (generated) | `~/ClaudeVault/` (or custom path) |

Use `install.py` to sync changes from this repo to the installed locations. After editing source files, run:

```bash
uv run install.py --force --yes
```

## Running Scripts

All scripts use Python stdlib only (no third-party dependencies). Run them with `uv`:

```bash
# Install (or reinstall after source changes)
uv run install.py                    # interactive
uv run install.py --force --yes      # non-interactive reinstall
uv run install.py --dry-run          # preview only
uv run install.py --uninstall        # remove skill, agent, hooks, and launchd plist / cron job

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

# Rebuild the vault index (after creating/renaming/deleting notes)
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/update_index.py

# Rebuild index AND regenerate visualizer graph.json in one pass
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/update_index.py --rebuild-graph

# Also include Daily notes in the graph
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/update_index.py --rebuild-graph --graph-include-daily

# Summarize queued sessions (from a terminal outside Claude Code)
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/summarize_sessions.py
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/summarize_sessions.py --dry-run

# Summarize from inside a Claude Code session (unset CLAUDECODE to allow nesting)
env -u CLAUDECODE uv run --no-project ~/.claude/skills/parsidion-cc/scripts/summarize_sessions.py

# Search vault notes (after uv tool install --editable ".[tools]")
vault-search "hook patterns" -n 5            # semantic, top 5
vault-search -n 5 -r "hook patterns"         # semantic, rich output
vault-search -f Patterns                     # metadata: by folder
vault-search -T python -d 7                  # metadata: by tag + recency
vault-search --folder Patterns --tag python  # metadata: long form still works
vault-search --grep "dedup_threshold"        # full-text body search (case-insensitive)
vault-search --grep "FLOCK" --grep-case      # full-text body search (case-sensitive)
vault-search --interactive                   # interactive curses TUI
VAULT_SEARCH_FORMAT=rich VAULT_SEARCH_MIN_SCORE=0.5 vault-search "query"  # env vars

# Scaffold a new vault note (after uv tool install --editable ".[tools]")
vault-new --type pattern --title "My Pattern" --project myproj --tags python,vault --open
vault-new --type debugging --title "Fix X Error" --tags sqlite

# Vault analytics (after uv tool install --editable ".[tools]")
vault-stats --summary              # note counts, growth, top tags
vault-stats --stale                # notes with no incoming links older than 30 days
vault-stats --top-linked           # most-referenced notes
vault-stats --by-project           # note counts per project
vault-stats --growth               # notes added per week
vault-stats --tags                 # tag frequency cloud
vault-stats --pending              # pending queue status (count, sources, oldest entry)
vault-stats --graph                # knowledge graph metrics (avg degree, hubs, orphans)
vault-stats --hooks 50             # last 50 hook events from hook_events.log
vault-stats --weekly               # generate weekly rollup note from daily notes
vault-stats --monthly              # generate monthly rollup note from daily notes
vault-stats --timeline 90          # activity bar chart for last 90 days
vault-stats --summarizer-progress  # live feedback from running summarize_sessions.py
vault-stats --dashboard            # full combined dashboard

# Review pending sessions before summarization (after uv tool install --editable ".[tools]")
vault-review                       # interactive TUI: approve/reject sessions

# Export vault (after uv tool install --editable ".[tools]")
vault-export --html ~/vault-site   # HTML static site
vault-export --zip ~/vault.zip     # filtered zip
vault-export --pdf ~/vault.pdf     # PDF via pandoc

# Merge near-duplicate notes (after uv tool install --editable ".[tools]")
vault-merge                        # AI-assisted note merging with backlink updates

# Vault doctor — individual fix modes
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/vault_doctor.py --fix-tags           # detect duplicate tags (dry-run)
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/vault_doctor.py --fix-tags --execute # apply tag merges
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/vault_doctor.py --migrate-subfolders           # detect prefix clusters (dry-run)
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/vault_doctor.py --migrate-subfolders --execute # apply moves
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/vault_doctor.py --fix-frontmatter    # repair frontmatter via Claude
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/vault_doctor.py --no-fix-headings --fix-frontmatter  # repair frontmatter without heading promotion

# Vault doctor — fix everything in one pass (used by nightly cron)
# Note: --fix-headings is enabled by default (promotes ## to # when no # heading exists)
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/vault_doctor.py --fix-all

# Run the skill trigger accuracy eval (MUST be from a separate terminal, not inside Claude Code)
bash ~/.claude/skills/parsidion-cc/scripts/run_trigger_eval.sh
```

The trigger eval and summarizer cannot run nested inside a Claude Code session because they
invoke `claude` internally. Use `env -u CLAUDECODE` as a workaround for the summarizer.

## Vault Git Integration

The vault supports optional git version control. When `~/ClaudeVault/.git` exists, the scripts
automatically stage and commit changes after every vault write:

- `session_stop_wrapper.sh` / `session_stop_hook.py` — commits daily note + pending queue after each session end
- `pre_compact_hook.py` — commits daily note after each pre-compact snapshot
- `update_index.py` — commits `CLAUDE.md` + per-folder `MANIFEST.md` files after each index rebuild
- `summarize_sessions.py` — commits new notes + updated index after processing

To enable, initialize the vault as a git repo:

```bash
cd ~/ClaudeVault
git init
# Optionally add a .gitignore (e.g. exclude .obsidian/)
echo ".obsidian/" > .gitignore
git add -A
git commit -m "chore(vault): initial commit"
```

If no `.git` directory is present, all `git_commit_vault()` calls are silent no-ops.

## Vault Configuration

All hook and summarizer options can be set in `~/ClaudeVault/config.yaml`. Precedence:
**defaults → config.yaml → CLI args** (last one wins).

A template with all options and their defaults is at `skills/parsidion-cc/templates/config.yaml`.
Copy it to the vault root to get started:

```bash
cp ~/.claude/skills/parsidion-cc/templates/config.yaml ~/ClaudeVault/config.yaml
```

Config sections:

| Section | Keys | Used by |
|---|---|---|
| `session_start_hook` | `ai_model`, `max_chars`, `ai_timeout`, `recent_days`, `debug`, `verbose_mode`, `use_embeddings`, `track_delta` | `session_start_hook.py` |
| `session_stop_hook` | `ai_model`, `ai_timeout`, `auto_summarize`, `auto_summarize_after` | `session_stop_hook.py` |
| `subagent_stop_hook` | `enabled`, `min_messages`, `excluded_agents` | `subagent_stop_hook.py` |
| `pre_compact_hook` | `lines` | `pre_compact_hook.py` |
| `summarizer` | `model`, `max_parallel`, `transcript_tail_lines`, `max_cleaned_chars`, `persist`, `cluster_model`, `dedup_threshold` | `summarize_sessions.py` |
| `defaults` | `haiku_model`, `sonnet_model` | all scripts that call Claude |
| `embeddings` | `model`, `min_score`, `top_k` | `build_embeddings.py`, `vault_search.py` |
| `git` | `auto_commit` | `vault_common.git_commit_vault()` |
| `event_log` | `enabled`, `max_lines` | `vault_common.write_hook_event()` (all hooks) |
| `adaptive_context` | `enabled`, `decay_days` | `session_start_hook.py`, `vault_common.py` |

The config is parsed by `vault_common.load_config()` (simple stdlib YAML parser — supports
one level of nesting, inline comments, scalars). Results are cached per process.
Use `vault_common.get_config(section, key, default)` to read values.

## Making Changes

**After editing any file under `skills/` or `agents/`**, sync to the live location:
```bash
uv run install.py --force --yes
```

For a single-file quick sync (faster than full reinstall):
```bash
# Example: after editing vault_common.py
cp skills/parsidion-cc/scripts/vault_common.py ~/.claude/skills/parsidion-cc/scripts/vault_common.py

# After editing SKILL.md
cp skills/parsidion-cc/SKILL.md ~/.claude/skills/parsidion-cc/SKILL.md

# After editing the research agent
cp agents/research-agent.md ~/.claude/agents/research-agent.md

# After editing subagent_stop_hook.py
cp skills/parsidion-cc/scripts/subagent_stop_hook.py ~/.claude/skills/parsidion-cc/scripts/subagent_stop_hook.py
```

**Testing hooks manually** — hooks communicate via JSON on stdin/stdout.
Use heredoc to avoid shell quoting issues with JSON:
```bash
# Test session_start_hook
python skills/parsidion-cc/scripts/session_start_hook.py <<'EOF'
{"cwd": "/Users/yourname/Repos/myproject"}
EOF

# Test session_stop_wrapper (the registered SessionEnd hook)
bash skills/parsidion-cc/scripts/session_stop_wrapper.sh <<'EOF'
{"cwd": "/path/to/project", "transcript_path": "/path/to/transcript.jsonl"}
EOF
# Background work logs to /tmp/session_stop_hook.log

# Test session_stop_hook directly (requires a real transcript path)
python skills/parsidion-cc/scripts/session_stop_hook.py <<'EOF'
{"cwd": "/path/to/project", "transcript_path": "/path/to/transcript.jsonl"}
EOF

# Test pre_compact_hook
python skills/parsidion-cc/scripts/pre_compact_hook.py <<'EOF'
{"cwd": "/path/to/project", "transcript_path": "/path/to/transcript.jsonl"}
EOF

# Test subagent_stop_hook (provide a real agent_transcript_path)
python skills/parsidion-cc/scripts/subagent_stop_hook.py <<'EOF'
{"cwd": "/path/to/project", "agent_transcript_path": "/path/to/agent.jsonl", "agent_id": "abc-123", "agent_type": "Explore"}
EOF
```

**stdlib-only rule**: `install.py` and all hook scripts (`session_start_hook.py`, `session_stop_hook.py`, `subagent_stop_hook.py`, `pre_compact_hook.py`, `post_compact_hook.py`, `vault_common.py`, `vault_links.py`, `vault_new.py`, `vault_stats.py`, `vault_review.py`, `vault_export.py`, `vault_merge.py`, `update_index.py`, `session_stop_wrapper.sh`) must use Python stdlib exclusively (or POSIX shell builtins) — no `pip install`, no `uv add`. The `pyproject.toml` intentionally has no dependencies.

**Exception**: `summarize_sessions.py` is a PEP 723 script with inline dependency declarations (`claude-agent-sdk`, `anyio`). Run it with `uv run` — deps are installed automatically into an isolated environment.

## Architecture

The system has four layers:

1. **Hook scripts** — Python scripts fired by Claude Code's lifecycle events, communicating via JSON stdin/stdout:
   - `session_start_hook.py`: Loads relevant vault notes as `additionalContext`. Default mode injects a **compact one-line-per-note index** (title + tags) to minimize token usage; `--verbose` flag or `verbose_mode: true` config switches to full summaries. Optional `--ai [MODEL]` flag uses `claude -p` (haiku by default, `CLAUDECODE` unset) to intelligently select notes — requires bumping hook timeout to 30 s in `settings.json`. Also shows a **pending queue warning** when `pending_summaries.jsonl` has entries and prepends a **"Since last time" delta** of new/modified notes per project (controlled by `track_delta` config key). When `adaptive_context.enabled: true`, notes are ranked by historical usefulness and unused notes are deranked over time.
   - `session_stop_wrapper.sh` + `session_stop_hook.py`: Registered under the `SessionEnd` hook. The shell wrapper reads stdin, outputs `{}` immediately (so Claude Code doesn't cancel it during fast exits), then spawns `session_stop_hook.py` detached via `nohup`. The Python script detects learnable content and appends session metadata (session_id, transcript_path, categories) to `~/ClaudeVault/pending_summaries.jsonl`. Uses `fcntl.flock` for safe concurrent access across parallel Claude instances.
   - `pre_compact_hook.py`: Snapshots current task state before context compaction. Extracts the current task by scanning backwards through the last 200 transcript lines for the most recent user text message. Extracts recently-touched files by parsing `tool_use` blocks from assistant messages (Read/Write/Edit/Grep/NotebookEdit tools). Also captures **git branch** (`git branch --show-current`) and **uncommitted files** (`git status --short`) so Claude knows the exact working tree state after compaction.
   - `post_compact_hook.py`: Restores working context after compaction. Reads today's daily note, finds the most recent `## Pre-Compact Snapshot` section written by `pre_compact_hook.py`, and returns it as `additionalContext` so Claude can resume the session without re-reading files.
   - `subagent_stop_hook.py`: Registered under the `SubagentStop` hook with `async: true` (non-blocking). Reads the subagent's own `agent_transcript_path`, skips agents listed in `excluded_agents` (default: `vault-explorer`, `research-agent`), and queues the transcript to `pending_summaries.jsonl` with `source: "subagent"` and `agent_type` metadata. Uses `agent_id` as the dedup key. Configurable via `subagent_stop_hook` section in `config.yaml`.
   - All hooks append a structured JSON line to `~/ClaudeVault/hook_events.log` via `vault_common.write_hook_event()`. The log is rotated when it exceeds `event_log.max_lines` (default 10,000). Viewable with `vault-stats --hooks N`.

2. **`summarize_sessions.py`** — On-demand PEP 723 script (requires `claude-agent-sdk`, `anyio`). Reads `pending_summaries.jsonl`, pre-processes transcripts, and calls Claude via the Agent SDK (up to 5 parallel sessions) to generate structured vault notes. Features: **write-gate filter** (Claude decides per-session if insights are reusable before generating a note), **hierarchical summarization** (transcripts exceeding `max_cleaned_chars` are chunked and summarized by haiku first), **semantic dedup** (before writing a note, checks for near-duplicates using `vault_search.py`; controlled by `summarizer.dedup_threshold` in config, default `0.80`), **automated backlinks** (via `vault_links.py` — injects bidirectional wikilinks after each note write). Cleans processed entries from the queue and rebuilds the index when done.

3. **`vault_common.py`** — Shared library imported by all hooks. Contains frontmatter parsing (regex-based, no pyyaml), vault traversal, note search functions (`find_notes_by_tag` etc. — DB-first, file-walk fallback), `ensure_note_index_schema()`, `query_note_index()`, `build_compact_index()` (moved here from `session_start_hook.py`; also used by `parsidion-mcp`), and path utilities. `_SAFE_ENV_KEYS` controls which env vars are forwarded to `claude -p` subprocess calls; includes `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_CUSTOM_HEADERS`, `ANTHROPIC_DEFAULT_{HAIKU,SONNET,OPUS}_MODEL`, `API_TIMEOUT_MS`, and `HTTPS_PROXY`/`HTTP_PROXY` so non-default API configurations (proxy, org key, Bedrock, corporate network) work correctly. All vault operations go through this module.

4. **`vault_search.py`** — Unified search CLI with four modes. **Semantic mode** (positional `QUERY`): fastembed + sqlite-vec cosine similarity search. **Metadata mode** (filter flags `--tag`/`-T`, `--folder`/`-f`, `--type`/`-k`, `--project`/`-p`, `--recent-days`/`-d`, no query): SQL query against `note_index` table. **Full-text body search** (`--grep`/`-G` flag): scans note bodies for a regex pattern; `--grep-case` enables case-sensitive matching. **Interactive TUI** (`--interactive`/`-i`): curses-based interface with real-time results, navigation, and editor integration. All modes output identical JSON with a `score` field (`null` for metadata/grep). Three output formats: `--json`/`-j` (default), `--text`/`-t` (human-readable), `--rich`/`-r` (Rich-colorized one-line-per-note). All flags have short options; defaults configurable via `VAULT_SEARCH_*` environment variables. Installed globally as `vault-search` via `uv tool install`. Used by `vault-explorer` agent for both Tier 1 and Tier 2 search.

5. **`vault_links.py`** — Shared stdlib-only module for backlink operations. Extracted from `summarize_sessions.py` and used by both the summarizer and `parsidion-mcp`. Key functions: `find_related_by_tags()` (tag-overlap candidates), `find_related_by_semantic()` (embedding-based candidates), `inject_related_links()` (add wikilinks to a note's `related` field), `add_backlinks_to_existing()` (bidirectional backlink injection after a new note is written).

6. **`vault_stats.py`** — Analytics CLI installed globally as `vault-stats`. Original modes: `--summary`, `--stale`, `--top-linked`, `--by-project`, `--growth`, `--tags`, `--dashboard`. New modes: `--pending` (pending queue status with source breakdown and estimated token cost), `--graph` (knowledge graph metrics: average degree, hub notes, isolated clusters, orphans, citation chains), `--hooks N` (last N events from `hook_events.log`), `--weekly` (generate weekly rollup note from daily notes), `--monthly` (monthly rollup), `--timeline N` (activity bar chart for last N days), `--summarizer-progress` (read `/tmp/parsidion-cc-summarizer-progress.json` for live feedback from a running `summarize_sessions.py`).

7. **`vault_review.py`** — Interactive TUI (`vault-review` global command) for inspecting and approving/rejecting pending sessions before AI summarization. Sessions approved here are processed first when `summarize_sessions.py --approved-only` is used.

8. **`vault_export.py`** — Export tool (`vault-export` global command). Supports HTML static site, filtered zip, and PDF via pandoc.

9. **`vault_merge.py`** — AI-assisted note merging tool (`vault-merge` global command). Detects near-duplicate notes, merges their content via Claude haiku, and updates all bidirectional backlinks.

10. **`~/ClaudeVault/`** — The Obsidian vault itself. Auto-generated `CLAUDE.md` index at the root. Subfolders: `Daily/`, `Projects/`, `Languages/`, `Frameworks/`, `Patterns/`, `Debugging/`, `Tools/`, `Research/`, `Templates/` (symlink to skill templates). `embeddings.db` contains `note_embeddings` (vectors) and `note_index` (metadata). `hook_events.log` records structured JSON hook execution events.

## Vault Note Conventions

Every note **must** have YAML frontmatter:
```yaml
---
date: YYYY-MM-DD
type: pattern|debugging|research|project|daily|tool|language|framework
tags: [tag1, tag2]
project: project-name   # optional
confidence: high|medium|low
sources: []
related: ["[[note-one]]", "[[note-two]]"]  # inline quoted array; must contain at least one [[wikilink]]
session_id: <uuid>      # optional — set by summarize_sessions.py on AI-generated notes
---
```

- Filenames: kebab-case, 3-5 words, no date suffix
- **Daily notes**: stored as `Daily/YYYY-MM/DD.md` (e.g. `Daily/2026-03/13.md`) — the hook writes them there automatically; never create flat `Daily/YYYY-MM-DD.md` files
- No orphan notes — every note must link to at least one other note via `related`
- Search before create — update existing notes rather than creating duplicates
- **Tag brevity**: prefer short singular kebab-case tags — e.g. `voxel` not `voxel-engine`, `hook` not `hooks`, `fractal` not `fractals`. **Never use underscores** in tags or the `project` field — convert repo names like `par_ai_core` to `par-ai-core`. Use a longer form only when the short form would be genuinely ambiguous.
- `Templates/` is a symlink to `skills/parsidion-cc/templates/` — never edit template files directly from the vault side
- **Subfolder rule**: when 3 or more notes share a common subject prefix, move them into a subfolder named after that subject. Drop the redundant prefix from filenames inside the subfolder. Only one level of subfolder is allowed — never nest subfolders within subfolders. Update all wikilinks and run `update_index.py` after reorganizing.

## Skill SKILL.md Structure

`skills/parsidion-cc/SKILL.md` has YAML frontmatter with `name` and `description` fields. The description is what Claude Code uses for automatic skill invocation — it was iteratively optimized using `run_trigger_eval.py`. When modifying the description, run the trigger eval to measure impact on precision/recall.

## Research Agent

`agents/research-agent.md` defines a Sonnet-powered agent that:
1. Searches `~/ClaudeVault/` first for existing knowledge
2. Uses Brave Search + Web Fetch for external research
3. Saves findings to the appropriate vault subfolder with YAML frontmatter
4. Runs `update_index.py` after saving

## Key File Paths in Code

- `VAULT_ROOT` = `~/ClaudeVault/` (module-level constant in `vault_common.py`, patched by installer for custom vault paths)
- `TEMPLATES_DIR` = `~/.claude/skills/parsidion-cc/templates/` (module-level constant in `vault_common.py`, patched by installer)
- `pending_summaries.jsonl` = `~/ClaudeVault/pending_summaries.jsonl` — queue of sessions awaiting AI summarization. Each line: `{"session_id": "...", "transcript_path": "...", "project": "...", "categories": [...], "timestamp": "..."}`. Deduplicated by `session_id`.
- `embeddings.db` = `~/ClaudeVault/embeddings.db` — SQLite database with two tables: `note_embeddings` (384-dim float32 vectors built by `build_embeddings.py`) and `note_index` (per-note metadata built by `update_index.py`). Queried by `vault_search.py` (both modes) and `vault_common.query_note_index()`. All callers fall back gracefully when absent.
- `EXCLUDE_DIRS` = set of folder names skipped by the indexer and vault traversal (defined in `vault_common.py`). Currently: `.obsidian`, `Templates`, `.git`, `.trash`, `TagsRoutes`.
- `hook_events.log` = `~/ClaudeVault/hook_events.log` — structured JSON log of hook executions. Each line: `{"hook": "SessionStart", "ts": "...", "project": "...", "notes_injected": 5, "chars": 2800, "duration_ms": 320}`. Rotated at `event_log.max_lines` (default 10,000). Written by `vault_common.write_hook_event()`. Read by `vault-stats --hooks N`.
- Summarizer progress file: `/tmp/parsidion-cc-summarizer-progress.json` — written by `summarize_sessions.py` during a run; read by `vault-stats --summarizer-progress`.
- Hook registration: `~/.claude/settings.json`
- Trigger eval results: `~/.claude/skills/parsidion-cc/eval_results.json`
- Installer: `install.py` (repo root)
