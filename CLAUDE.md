# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

parsidion-cc is a Claude Code customization toolkit — the **source repository** for skills, agents, and hook scripts that get installed into `~/.claude/`. It is not a runnable application; it is managed configuration and scripts.

## Installed vs Source Paths

| Component | Source (this repo) | Installed to |
|---|---|---|
| Installer | `install.py` | run in-place (`uv run install.py`) |
| Claude Vault skill | `skills/claude-vault/` | `~/.claude/skills/claude-vault/` |
| Research agent | `agents/research-documentation-agent.md` | `~/.claude/agents/` |
| Hook scripts | `skills/claude-vault/scripts/` | referenced from `~/.claude/settings.json` |
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
uv run install.py --uninstall        # remove skill, agent, hooks

# Rebuild the vault index (after creating/renaming/deleting notes)
uv run --no-project ~/.claude/skills/claude-vault/scripts/update_index.py

# Summarize queued sessions (from a terminal outside Claude Code)
uv run --no-project ~/.claude/skills/claude-vault/scripts/summarize_sessions.py
uv run --no-project ~/.claude/skills/claude-vault/scripts/summarize_sessions.py --dry-run

# Summarize from inside a Claude Code session (unset CLAUDECODE to allow nesting)
env -u CLAUDECODE uv run --no-project ~/.claude/skills/claude-vault/scripts/summarize_sessions.py

# Run the skill trigger accuracy eval (MUST be from a separate terminal, not inside Claude Code)
bash ~/.claude/skills/claude-vault/scripts/run_trigger_eval.sh
```

The trigger eval and summarizer cannot run nested inside a Claude Code session because they
invoke `claude` internally. Use `env -u CLAUDECODE` as a workaround for the summarizer.

## Vault Git Integration

The vault supports optional git version control. When `~/ClaudeVault/.git` exists, the scripts
automatically stage and commit changes after every vault write:

- `session_stop_hook.py` — commits daily note + pending queue after each session end
- `pre_compact_hook.py` — commits daily note after each pre-compact snapshot
- `update_index.py` — commits `CLAUDE.md` after each index rebuild
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

A template with all options and their defaults is at `skills/claude-vault/templates/config.yaml`.
Copy it to the vault root to get started:

```bash
cp ~/.claude/skills/claude-vault/templates/config.yaml ~/ClaudeVault/config.yaml
```

Config sections:

| Section | Keys | Used by |
|---|---|---|
| `session_start_hook` | `ai_model`, `max_chars`, `ai_timeout`, `recent_days`, `debug` | `session_start_hook.py` |
| `session_stop_hook` | `ai_model`, `ai_timeout`, `auto_summarize` | `session_stop_hook.py` |
| `pre_compact_hook` | `lines` | `pre_compact_hook.py` |
| `summarizer` | `model`, `max_parallel`, `transcript_tail_lines`, `max_cleaned_chars`, `persist` | `summarize_sessions.py` |
| `git` | `auto_commit` | `vault_common.git_commit_vault()` |

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
cp skills/claude-vault/scripts/vault_common.py ~/.claude/skills/claude-vault/scripts/vault_common.py

# After editing SKILL.md
cp skills/claude-vault/SKILL.md ~/.claude/skills/claude-vault/SKILL.md

# After editing the research agent
cp agents/research-documentation-agent.md ~/.claude/agents/research-documentation-agent.md
```

**Testing hooks manually** — hooks communicate via JSON on stdin/stdout.
Use heredoc to avoid shell quoting issues with JSON:
```bash
# Test session_start_hook
python skills/claude-vault/scripts/session_start_hook.py <<'EOF'
{"cwd": "/Users/yourname/Repos/myproject"}
EOF

# Test session_stop_hook (requires a real transcript path)
python skills/claude-vault/scripts/session_stop_hook.py <<'EOF'
{"cwd": "/path/to/project", "transcript_path": "/path/to/transcript.jsonl"}
EOF

# Test pre_compact_hook
python skills/claude-vault/scripts/pre_compact_hook.py <<'EOF'
{"cwd": "/path/to/project", "transcript_path": "/path/to/transcript.jsonl"}
EOF
```

**stdlib-only rule**: `install.py` and all hook scripts (`session_start_hook.py`, `session_stop_hook.py`, `pre_compact_hook.py`, `vault_common.py`, `update_index.py`) must use Python stdlib exclusively — no `pip install`, no `uv add`. The `pyproject.toml` intentionally has no dependencies.

**Exception**: `summarize_sessions.py` is a PEP 723 script with inline dependency declarations (`claude-agent-sdk`, `anyio`). Run it with `uv run` — deps are installed automatically into an isolated environment.

## Architecture

The system has four layers:

1. **Hook scripts** — Python scripts fired by Claude Code's lifecycle events, communicating via JSON stdin/stdout:
   - `session_start_hook.py`: Loads relevant vault notes as `additionalContext`; optional `--ai [MODEL]` flag uses `claude -p` (haiku by default, `CLAUDECODE` unset) to intelligently select notes from the full vault — requires bumping the hook timeout to 30 s in `settings.json`
   - `session_stop_hook.py`: Registered under the `SessionEnd` hook (fires once when the session terminates, not on every turn). Detects learnable content; appends session metadata (session_id, transcript_path, categories) to `~/ClaudeVault/pending_summaries.jsonl`. Uses `fcntl.flock` for safe concurrent access across parallel Claude instances.
   - `pre_compact_hook.py`: Snapshots current task state before context compaction. Extracts the current task by scanning backwards through the last 200 transcript lines for the most recent user text message. Extracts recently-touched files by parsing `tool_use` blocks from assistant messages (Read/Write/Edit/Grep/NotebookEdit tools).

2. **`summarize_sessions.py`** — On-demand PEP 723 script (requires `claude-agent-sdk`, `anyio`). Reads `pending_summaries.jsonl`, pre-processes transcripts, and calls Claude via the Agent SDK (up to 5 parallel sessions) to generate structured vault notes. Cleans processed entries from the queue and rebuilds the index when done.

3. **`vault_common.py`** — Shared library imported by all hooks. Contains frontmatter parsing (regex-based, no pyyaml), vault traversal, note search functions, and path utilities. All vault operations go through this module.

4. **`~/ClaudeVault/`** — The Obsidian vault itself. Auto-generated `CLAUDE.md` index at the root. Subfolders: `Daily/`, `Projects/`, `Languages/`, `Frameworks/`, `Patterns/`, `Debugging/`, `Tools/`, `Research/`, `Templates/` (symlink to skill templates).

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
related: []             # must contain at least one [[wikilink]]
session_id: <uuid>      # optional — set by summarize_sessions.py on AI-generated notes
---
```

- Filenames: kebab-case, 3-5 words, no date suffix
- **Daily notes**: stored as `Daily/YYYY-MM/DD.md` (e.g. `Daily/2026-03/13.md`) — the hook writes them there automatically; never create flat `Daily/YYYY-MM-DD.md` files
- No orphan notes — every note must link to at least one other note via `related`
- Search before create — update existing notes rather than creating duplicates
- **Tag brevity**: prefer short single-word or minimal-hyphen tags — e.g. `voxel` not `voxel-engine`, `terminal` not `terminal-emulator`. Use a longer form only when the short form would be genuinely ambiguous.
- `Templates/` is a symlink to `skills/claude-vault/templates/` — never edit template files directly from the vault side
- **Subfolder rule**: when 3 or more notes share a common subject prefix, move them into a subfolder named after that subject. Drop the redundant prefix from filenames inside the subfolder. Only one level of subfolder is allowed — never nest subfolders within subfolders. Update all wikilinks and run `update_index.py` after reorganizing.

## Skill SKILL.md Structure

`skills/claude-vault/SKILL.md` has YAML frontmatter with `name` and `description` fields. The description is what Claude Code uses for automatic skill invocation — it was iteratively optimized using `run_trigger_eval.py`. When modifying the description, run the trigger eval to measure impact on precision/recall.

## Research Agent

`agents/research-documentation-agent.md` defines a Sonnet-powered agent that:
1. Searches `~/ClaudeVault/` first for existing knowledge
2. Uses Brave Search + Web Fetch for external research
3. Saves findings to the appropriate vault subfolder with YAML frontmatter
4. Runs `update_index.py` after saving

## Key File Paths in Code

- `VAULT_ROOT` = `~/ClaudeVault/` (module-level constant in `vault_common.py`, patched by installer for custom vault paths)
- `TEMPLATES_DIR` = `~/.claude/skills/claude-vault/templates/` (module-level constant in `vault_common.py`, patched by installer)
- `pending_summaries.jsonl` = `~/ClaudeVault/pending_summaries.jsonl` — queue of sessions awaiting AI summarization. Each line: `{"session_id": "...", "transcript_path": "...", "project": "...", "categories": [...], "timestamp": "..."}`. Deduplicated by `session_id`.
- `EXCLUDE_DIRS` = set of folder names skipped by the indexer and vault traversal (defined in `vault_common.py`). Currently: `.obsidian`, `Templates`, `.git`, `.trash`, `TagsRoutes`.
- Hook registration: `~/.claude/settings.json`
- Trigger eval results: `~/.claude/skills/claude-vault/eval_results.json`
- Installer: `install.py` (repo root)
