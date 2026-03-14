# Parsidion CC Architecture

A Claude Code customization toolkit that replaces built-in auto memory with an Obsidian vault-based knowledge management system, augmented by lifecycle hooks, a research agent, and a graph-colorized vault explorer.

## Table of Contents
- [Overview](#overview)
- [System Architecture](#system-architecture)
- [Component Details](#component-details)
  - [CLAUDE-VAULT.md — Always-On Guidance](#claude-vaultmd--always-on-guidance)
  - [Claude Vault Skill](#claude-vault-skill)
  - [Hook Scripts](#hook-scripts)
  - [Session Summarizer](#session-summarizer)
  - [Vault Doctor](#vault-doctor)
  - [Graph Coverage Checker](#graph-coverage-checker)
  - [Research Agent](#research-agent)
  - [Vault Explorer Agent](#vault-explorer-agent)
  - [Vault Common Library](#vault-common-library)
  - [Index Generator](#index-generator)
  - [Trigger Evaluation](#trigger-evaluation)
  - [Context Preview Script](#context-preview-script)
  - [Obsidian Integration](#obsidian-integration)
- [Configuration](#configuration)
- [Data Flow](#data-flow)
- [File Layout](#file-layout)
- [Vault Note Lifecycle](#vault-note-lifecycle)
- [Obsidian Graph View](#obsidian-graph-view)
- [Related Documentation](#related-documentation)

## Overview

**Purpose:** Provide a structured, searchable, cross-linked knowledge base that persists across Claude Code sessions, replacing the flat auto memory with richly organized Obsidian notes.

**Key capabilities:**
- Always-on vault-first rule: check the vault before debugging or implementing (via `CLAUDE-VAULT.md`)
- Automatic context loading at session start based on project and recency
- Automatic learning capture at session stop via transcript analysis
- Working state snapshots before context compaction
- A dedicated research agent that saves findings to the vault
- An auto-generated index with tag cloud and per-folder listings
- Obsidian graph view with domain-based color grouping

**Runtime requirements:**
- Python 3.13+ (stdlib only -- no third-party packages)
- `uv` for script execution
- Obsidian for vault browsing and graph view (optional but recommended)

**Configuration:** All hooks and the summarizer read `~/ClaudeVault/config.yaml` for tuneable settings. A reference config with all defaults is shipped as `templates/config.yaml`. Precedence: script defaults → config.yaml → CLI arguments.

## System Architecture

```mermaid
graph TB
    subgraph "Claude Code Runtime"
        CC[Claude Code Session]
        Hooks[Hook System]
    end

    subgraph "Parsidion CC"
        CVG[CLAUDE-VAULT.md]
        Skill[Claude Vault Skill]
        Agent[Research Agent]
        VE[Vault Explorer Agent]
        VC[vault_common.py]
        SSH[session_start_hook.py]
        STH[session_stop_hook.py]
        PCH[pre_compact_hook.py]
        SUM[summarize_sessions.py]
        IDX[update_index.py]
        VD[vault_doctor.py]
        CGC[check_graph_coverage.py]
        EVAL[run_trigger_eval.py]
    end

    subgraph "Obsidian Vault ~/ClaudeVault/"
        Config[config.yaml]
        Index[CLAUDE.md Index]
        Pending[pending_summaries.jsonl]
        Daily[Daily/]
        Projects[Projects/]
        Languages[Languages/]
        Frameworks[Frameworks/]
        Patterns[Patterns/]
        Debugging[Debugging/]
        Tools[Tools/]
        Research[Research/]
        Graph[.obsidian/graph.json]
    end

    CC --> Hooks
    Hooks -->|SessionStart| SSH
    Hooks -->|SessionEnd| STH
    Hooks -->|PreCompact| PCH
    CC -->|invokes| Agent
    CC -->|invokes| VE

    SSH -->|reads| VC
    STH -->|reads| VC
    PCH -->|reads| VC
    IDX -->|reads| VC
    SUM -->|reads| VC
    VC -->|loads| Config
    Agent -->|saves to| Research
    VE -->|searches| Daily
    VE -->|searches| Projects
    VE -->|searches| Debugging
    VE -->|searches| Patterns
    VE -->|searches| Research

    SSH -->|loads context from| Daily
    SSH -->|loads context from| Projects
    STH -->|writes to| Daily
    STH -->|queues to| Pending
    SUM -->|reads| Pending
    SUM -->|creates notes in| Debugging
    SUM -->|creates notes in| Patterns
    SUM -->|creates notes in| Research
    PCH -->|writes to| Daily
    IDX -->|generates| Index
    CGC -->|reads| Graph

    VD -->|reads| VC
    VD -->|reads/writes| Daily

    CVG -->|always-on vault-first rule| CC
    Skill -->|defines conventions| CC
    EVAL -->|reads description| Skill

    style CVG fill:#004d40,stroke:#00bcd4,stroke-width:2px,color:#ffffff
    style CC fill:#e65100,stroke:#ff9800,stroke-width:3px,color:#ffffff
    style Hooks fill:#e65100,stroke:#ff9800,stroke-width:2px,color:#ffffff
    style Skill fill:#4a148c,stroke:#9c27b0,stroke-width:2px,color:#ffffff
    style Agent fill:#4a148c,stroke:#9c27b0,stroke-width:2px,color:#ffffff
    style VE fill:#4a148c,stroke:#9c27b0,stroke-width:2px,color:#ffffff
    style VC fill:#0d47a1,stroke:#2196f3,stroke-width:2px,color:#ffffff
    style SSH fill:#1b5e20,stroke:#4caf50,stroke-width:2px,color:#ffffff
    style STH fill:#880e4f,stroke:#c2185b,stroke-width:2px,color:#ffffff
    style PCH fill:#ff6f00,stroke:#ffa726,stroke-width:2px,color:#ffffff
    style SUM fill:#4a148c,stroke:#9c27b0,stroke-width:2px,color:#ffffff
    style IDX fill:#0d47a1,stroke:#2196f3,stroke-width:2px,color:#ffffff
    style VD fill:#1b5e20,stroke:#4caf50,stroke-width:2px,color:#ffffff
    style CGC fill:#0d47a1,stroke:#2196f3,stroke-width:2px,color:#ffffff
    style EVAL fill:#ff6f00,stroke:#ffa726,stroke-width:2px,color:#ffffff
    style Config fill:#1a237e,stroke:#3f51b5,stroke-width:2px,color:#ffffff
    style Index fill:#1a237e,stroke:#3f51b5,stroke-width:2px,color:#ffffff
    style Pending fill:#1a237e,stroke:#3f51b5,stroke-width:2px,color:#ffffff
    style Daily fill:#37474f,stroke:#78909c,stroke-width:2px,color:#ffffff
    style Projects fill:#37474f,stroke:#78909c,stroke-width:2px,color:#ffffff
    style Languages fill:#37474f,stroke:#78909c,stroke-width:2px,color:#ffffff
    style Frameworks fill:#37474f,stroke:#78909c,stroke-width:2px,color:#ffffff
    style Patterns fill:#37474f,stroke:#78909c,stroke-width:2px,color:#ffffff
    style Debugging fill:#37474f,stroke:#78909c,stroke-width:2px,color:#ffffff
    style Tools fill:#37474f,stroke:#78909c,stroke-width:2px,color:#ffffff
    style Research fill:#37474f,stroke:#78909c,stroke-width:2px,color:#ffffff
    style Graph fill:#37474f,stroke:#78909c,stroke-width:1px,color:#ffffff
```

## Component Details

### CLAUDE-VAULT.md — Always-On Guidance

**Location:** `CLAUDE-VAULT.md` (repo root) → installed to `~/.claude/CLAUDE-VAULT.md`

An unconditional guidance file loaded every Claude Code session via an `@CLAUDE-VAULT.md` import appended to `~/.claude/CLAUDE.md` by the installer. Unlike the claude-vault skill (which requires explicit invocation), this file fires on every session with no trigger needed.

**What it enforces:**

- **Vault-first debugging:** Before diagnosing any error, extract the key signal (exception class, package name, distinctive message phrase) and search `~/ClaudeVault/Debugging/` via Grep. Widen to `Frameworks/`, `Languages/`, `Projects/` if not found. Apply documented fixes when matched; save new solutions when not.
- **Prior-art check:** Before writing non-trivial code, search `Patterns/`, `Frameworks/`, `Languages/`, and `Projects/` for existing implementations. Reuse and adapt proven code from other projects rather than writing from scratch.
- **Vault organization:** Enforces the subfolder rule (3+ notes sharing a prefix → move to a named subfolder; one level only) and reminds when to rebuild the index.
- **Saving solutions:** Specifies target folders by solution type (bug fix → `Debugging/`, reusable pattern → `Patterns/`, framework fix → `Frameworks/`, architectural decision → `Projects/<project>/`).

**Install behavior:** `install.py` copies `CLAUDE-VAULT.md` to `~/.claude/` and idempotently appends `@CLAUDE-VAULT.md` to `~/.claude/CLAUDE.md` if the line is not already present. Uninstall removes the file and strips the import line.

### Claude Vault Skill

**Location:** `skills/claude-vault/SKILL.md`

The skill definition loaded into Claude Code's context. Establishes the philosophy, conventions, and anti-patterns for vault usage. It is not executable code -- it is a prompt artifact that shapes how Claude interacts with the vault.

**Auto-triggering:** The SKILL.md includes YAML frontmatter with a `name` and `description` field that enables Claude Code to automatically invoke the skill when users mention saving knowledge, checking vault notes, or persisting findings across sessions. The description was iteratively optimized using the trigger eval harness (see [Trigger Evaluation](#trigger-evaluation)).

**Key conventions enforced:**
- Search before create (no duplicates)
- Atomic notes (one concept per note)
- Mandatory YAML frontmatter on every note
- Wikilinks for cross-referencing
- Kebab-case filenames

### Hook Scripts

Three Python scripts execute at different points in the Claude Code session lifecycle. All hooks read JSON from stdin, interact with the vault via `vault_common`, and write JSON to stdout. Each hook supports tuneable options via `~/ClaudeVault/config.yaml` and/or CLI arguments (precedence: script defaults → config.yaml → CLI args).

#### SessionStart Hook

**Script:** `skills/claude-vault/scripts/session_start_hook.py`

Fires when a Claude Code session begins. Loads relevant vault context into the conversation so Claude has prior knowledge available immediately.

**CLI flags:** `--ai [MODEL]`, `--max-chars N`, `--debug`

**Configurable options** (section `session_start_hook` in `config.yaml`):

| Key | Default | Description |
|-----|---------|-------------|
| `ai_model` | `null` (disabled) | Model for AI note selection |
| `max_chars` | `4000` | Maximum characters for injected context |
| `ai_timeout` | `25` | AI call timeout in seconds |
| `recent_days` | `3` | Days to look back for recent notes |
| `debug` | `false` | Append injected context + metadata to debug log in `$TMPDIR` |

**Standard behaviour:**
1. Determines the current project from the working directory
2. Ensures vault directories and today's daily note exist
3. Gathers project-specific notes (by `project` frontmatter field)
4. Gathers recent notes (modified within last `recent_days` days, configurable)
5. Deduplicates and builds a context block (max `max_chars`, default ~4000 chars)
6. Returns the context as `additionalContext` in the hook output
7. When `debug` is enabled, appends the full context plus quality metadata (project, mode, char count, budget %, note count, elapsed time) to `$TMPDIR/claude-vault-session-start-debug.log`

**AI-powered mode (`--ai [MODEL]`):**

Pass `--ai` (or `--ai <model-id>`) to the hook command, or set `session_start_hook.ai_model` in config.yaml, to enable intelligent note selection via `claude -p`. When enabled:

1. Collects **all** vault notes as candidates — project-tagged notes first, then the rest sorted by mtime descending
2. Builds a summarised candidate block (up to 8000 chars) from note titles and first 6 body lines
3. Runs `claude -p <prompt> --model <model> --no-session-persistence` with `CLAUDECODE` unset so it can be called from within an active session; timeout is controlled by `session_start_hook.ai_timeout` (default 25 s)
4. Claude selects and formats the most relevant notes as context (target ≤ `max_chars - 500` chars)
5. Falls back silently to standard behaviour on timeout, missing binary, or non-zero exit

Default model: `claude-haiku-4-5-20251001`. Override with `--ai claude-sonnet-4-6`, any valid model ID, or `session_start_hook.ai_model` in config.yaml.

**Hook timeout:** The default 10 s hook timeout must be increased to at least `30000` ms when using `--ai`.

```json
{
  "command": "uv run --no-project ~/.claude/skills/claude-vault/scripts/session_start_hook.py --ai",
  "timeout": 30000
}
```

#### SessionEnd Hook

**Script:** `skills/claude-vault/scripts/session_stop_hook.py`

Registered under the `SessionEnd` hook event — fires once when the session terminates (unlike `Stop`, which fires after every agent turn). Analyzes the session transcript to detect learnable content and persists it to the vault.

**Configurable options** (section `session_stop_hook` in `config.yaml`):

| Key | Default | Description |
|-----|---------|-------------|
| `ai_model` | `null` (disabled) | Model for AI classification |
| `ai_timeout` | `25` | AI call timeout in seconds |
| `auto_summarize` | `true` | Auto-launch summarizer when pending entries exist |

**Behavior:**
1. Reads the last 200 lines of the JSONL transcript
2. Extracts assistant message text
3. Resolves AI model: CLI `--ai` → `session_stop_hook.ai_model` config → `null` (disabled)
4. Runs keyword-based heuristics to detect four categories:
   - **Error fixes** (keywords: "fixed", "root cause", "the fix", etc.)
   - **Research findings** (keywords: "found that", "documentation says", etc.)
   - **Patterns** (keywords: "pattern", "best practice", "architecture", etc.)
   - **Config/setup** (keywords: "configured", "installed", "set up", etc.)
5. Appends a session summary to today's daily note under `## Sessions`
6. Queues sessions with significant learnings (error_fix, research, or pattern categories) to `pending_summaries.jsonl` for AI-powered summarization. Uses `fcntl.flock` on macOS/Linux (with a `try/except ImportError` fallback for Windows) for safe concurrent access; deduplicates by `session_id`.
7. Calls `git_commit_vault` to commit the updated daily note to the vault git repository (respects `git.auto_commit` config)
8. Auto-launches `summarize_sessions.py` as a detached background process if there are pending entries in the queue and `session_stop_hook.auto_summarize` is `true` (default)
9. Uses an environment variable guard (`CLAUDE_VAULT_STOP_ACTIVE`) to prevent recursive invocation

**AI-powered mode (`--ai [MODEL]`):**

Pass `--ai` (or `--ai <model-id>`) to the hook command, or set `session_stop_hook.ai_model` in config.yaml, to enable semantic classification via `claude -p`. When enabled:

1. Samples the first 10 assistant messages (up to 1500 chars total)
2. Asks Claude to determine `should_queue`, `categories`, and a one-sentence `summary`; timeout is controlled by `session_stop_hook.ai_timeout` (default 25 s)
3. Skips queuing if `should_queue` is false (avoids storing routine sessions)
4. Falls back silently to keyword heuristics on timeout, missing binary, or non-zero exit

Default model: `claude-haiku-4-5-20251001`. Override with `--ai <model-id>` or `session_stop_hook.ai_model` in config.yaml. Requires increasing the hook timeout in `settings.json` to at least `30000` ms.

#### PreCompact Hook

**Script:** `skills/claude-vault/scripts/pre_compact_hook.py`

Fires before Claude Code compacts the conversation context. Snapshots the current working state so it survives compaction.

**CLI flags:** `--lines N`

**Configurable options** (section `pre_compact_hook` in `config.yaml`):

| Key | Default | Description |
|-----|---------|-------------|
| `lines` | `200` | Number of transcript lines to analyse |

**Behavior:**
1. Reads the last N lines of the JSONL transcript (default 200, configurable via `--lines` or `pre_compact_hook.lines`)
2. Extracts the most recent user message as a task summary
3. Extracts file paths mentioned in the transcript (up to 15)
4. Appends a `## Pre-Compact Snapshot` section to today's daily note
5. Calls `git_commit_vault` to commit the snapshot (respects `git.auto_commit` config)

### Session Summarizer

**Location:** `skills/claude-vault/scripts/summarize_sessions.py`

An on-demand PEP 723 script (requires `claude-agent-sdk`, `anyio`) that processes the `pending_summaries.jsonl` queue and generates structured vault notes using Claude AI.

**CLI flags:** `--sessions FILE`, `--dry-run`, `--model MODEL`, `--persist`

**Configurable options** (section `summarizer` in `config.yaml`):

| Key | Default | Description |
|-----|---------|-------------|
| `model` | `claude-sonnet-4-6` | Model for note generation |
| `max_parallel` | `5` | Concurrent summarization tasks |
| `transcript_tail_lines` | `400` | Transcript lines to read per entry |
| `max_cleaned_chars` | `12000` | Maximum characters after cleaning |
| `persist` | `false` | SDK session persistence (for debugging) |

**Behavior:**
1. Reads entries from `pending_summaries.jsonl`
2. Pre-processes each transcript via `preprocess_transcript(tail_lines, max_chars)` to extract a cleaned human/assistant dialogue
3. Calls Claude via the Agent SDK (up to `max_parallel` parallel sessions, default 5) to generate structured notes. Default model is `claude-sonnet-4-6`.
4. Saves notes to the appropriate vault subfolder (`Debugging/`, `Patterns/`, `Research/`, etc.) with YAML frontmatter
5. Removes processed entries from the queue, rebuilds the vault index, and commits via `git_commit_vault`

**Must be run from a separate terminal** (or with `env -u CLAUDECODE`) because the Agent SDK cannot be nested inside an active Claude Code session.

### Vault Doctor

**Location:** `skills/claude-vault/scripts/vault_doctor.py`

An on-demand diagnostic and repair tool that scans vault notes for structural issues and fixes them via `claude -p` (haiku model by default).

**Issue codes detected:**

| Code | Severity | Description |
|------|----------|-------------|
| `MISSING_FRONTMATTER` | error | No YAML frontmatter block |
| `MISSING_FIELD` | error | `date`/`type` missing (all notes); `confidence`/`related` missing (non-daily) |
| `INVALID_TYPE` | error | `type` not in allowed set |
| `INVALID_DATE` | warning | `date` not in YYYY-MM-DD format |
| `ORPHAN_NOTE` | warning | No `[[wikilinks]]` in `related` field |
| `BROKEN_WIKILINK` | warning | Link target not found in vault |
| `FLAT_DAILY` | warning | `Daily/YYYY-MM-DD.md` instead of `Daily/YYYY-MM/DD.md` |

Daily notes are exempt from `confidence`, `related`, and orphan checks.

**State file:** `~/ClaudeVault/doctor_state.json` tracks per-note status across runs:
- `ok` — no issues; skipped for 7 days before re-checking
- `fixed` — Claude repaired it; re-checked on next run
- `failed` — Claude returned no output; retried next run
- `timeout` — `claude -p` timed out once; retried one more time
- `needs_review` — timed out on retry; skipped indefinitely, flagged for user
- `skipped` — only non-auto-repairable issues (`BROKEN_WIKILINK`, `FLAT_DAILY`); skipped indefinitely

**Behavior:**
1. Loads `doctor_state.json` and skips notes with `ok`/`skipped`/`needs_review` status
2. Scans remaining notes for issues using stdlib-only checks
3. Records clean notes as `ok` in state (skipped for 7 days)
4. In `--fix` mode: calls `claude -p` per note with haiku to repair repairable issues
5. Saves state after each run; escalates double-timeout to `needs_review`
6. `--no-state` rescans all notes regardless of prior results

The vault health summary (clean count, pending repair, needs review, manual fix) is included in `CLAUDE.md` by `update_index.py` after each index rebuild.

### Graph Coverage Checker

**Location:** `skills/claude-vault/scripts/check_graph_coverage.py`

A utility script that audits vault tags against the Obsidian graph color groups in `.obsidian/graph.json`.

**Behavior:**
1. Collects all tags used across vault notes
2. Compares against tags defined in each graph color group
3. Reports uncovered tags (used in vault but not in any color group)
4. Reports stale entries (in color groups but not used in any note)
5. Supports `--threshold N` to filter by minimum tag usage count and `--json` for scripting

### Research Agent

**Location:** `agents/research-documentation-agent.md`

A Claude Code agent definition (runs on Sonnet) that conducts technical research and saves structured findings to the vault.

**Workflow:**
1. Dispatches `vault-explorer` agent with the research topic to check for existing knowledge — proceeds to web research only for gaps not covered by existing notes
2. Uses NotebookLM (if available) for deep synthesis of source material
3. Uses Brave Search for web research; falls back to `mcpl search "search"` to find alternative search tools when Brave hits rate limits
4. Fetches raw HTML via `agentchrome page html`, pipes through `~/.claude/scripts/html-to-md` to get clean noise-free markdown (curl + html-to-md as fallback if agentchrome fails)
5. **Always** saves a vault note to the appropriate subfolder with YAML frontmatter — regardless of whether a project-specific destination (e.g. `docs/MCPL.md`) was also requested
6. If a project-specific doc was requested, also saves there (following the project style guide, no frontmatter)
7. Runs `update_index.py` after saving vault notes
8. Provides a summary report of findings and gaps

### Vault Explorer Agent

**Location:** `agents/vault-explorer.md`

A read-only Claude Code agent (runs on Haiku) that searches the vault for relevant notes and returns a synthesized answer. It is the preferred way to query the vault programmatically — callers dispatch it with a natural language query and receive a structured response with a prose answer and source file paths.

**Trigger phrases:** "search the vault for X", "check the vault", "have we seen this before", "find vault notes about X", "check for prior art on X", "what do we know about X".

**Scope:** Read-only. Does not write files, create notes, or run `update_index.py`.

**Workflow:**
1. Reads `~/ClaudeVault/CLAUDE.md` (the vault index) to orient on available content
2. Extracts key signals from the query (exception class, package name, feature keyword)
3. Searches folders in priority order by query type:

   | Query type | Folders, in priority order |
   |---|---|
   | Error / exception / bug | `Debugging/` → `Frameworks/` → `Languages/` |
   | Feature / pattern / integration | `Patterns/` → `Frameworks/` → `Projects/` |
   | Cross-project / prior art | `Projects/` → `Patterns/` |
   | Library / tool / CLI | `Tools/` → `Frameworks/` |
   | Research / concepts | `Research/` → all folders |

4. Ranks candidate files by folder priority, then by signal frequency within each file
5. Reads the top 5 ranked files and synthesizes a direct answer
6. Returns exactly two sections: `## Answer` (3–7 sentences) and `## Sources` (absolute paths with one-line relevance notes)

**Relationship to other agents:** When the vault has no relevant information, the agent recommends dispatching `research-documentation-agent` to research the topic externally and save findings to the vault.

### Vault Common Library

**Location:** `skills/claude-vault/scripts/vault_common.py`

The shared utility library used by all hook scripts and the index generator. Uses only Python stdlib (no third-party dependencies).

**Key functions:**

| Function | Purpose |
|----------|---------|
| `parse_frontmatter()` | Regex-based YAML frontmatter parser |
| `get_body()` | Returns markdown content after frontmatter |
| `find_notes_by_project()` | Search by `project` frontmatter field |
| `find_notes_by_tag()` | Search by tag in `tags` list |
| `find_notes_by_type()` | Search by `type` frontmatter field |
| `find_recent_notes()` | Find notes modified within N days |
| `read_note_summary()` | Extract title + first few body lines |
| `build_context_block()` | Assemble notes into a character-budgeted context string |
| `get_project_name()` | Derive project name from cwd or git root |
| `ensure_vault_dirs()` | Create missing vault directories and Templates symlink |
| `create_daily_note_if_missing()` | Create today's daily note from template |
| `slugify()` | Convert text to kebab-case filename |
| `all_vault_notes()` | Return all `.md` files in the vault (excluding `EXCLUDE_DIRS`) |
| `git_commit_vault()` | Stage and commit vault changes; respects `git.auto_commit` config |
| `load_config()` | Load and cache `config.yaml` from `VAULT_ROOT` |
| `get_config()` | Look up a config value by section/key with fallback default |

**Configuration system:** `load_config()` reads `~/ClaudeVault/config.yaml` on first call and caches the result for the process lifetime. The file is parsed by `_parse_config_yaml()`, a stdlib-only YAML parser that handles one level of nesting (section headers with nested key-value pairs). `_strip_inline_comment()` handles trailing `# comment` syntax. `get_config(section, key, default)` provides the lookup API used by all hooks and the summarizer.

**Design decisions:**
- No external dependencies (stdlib only) for maximum portability in hook contexts
- Custom YAML parser via regex rather than importing `pyyaml`; the config parser (`_parse_config_yaml`) is similarly stdlib-only
- File walking excludes `.obsidian/`, `Templates/`, `.git/`, `.trash/`, `TagsRoutes/`

### Index Generator

**Location:** `skills/claude-vault/scripts/update_index.py`

Rebuilds `~/ClaudeVault/CLAUDE.md` by scanning all vault notes.

**Output sections:**
1. **Quick Stats** -- total note count, last updated timestamp, and vault health summary (read from `doctor_state.json` if present)
2. **Tag Cloud** -- all tags sorted by frequency
3. **Recent Activity** -- notes modified within the last 7 days (max 20)
4. **Folders** -- per-folder listings with wikilinks and summaries

### Trigger Evaluation

**Location:** `skills/claude-vault/scripts/run_trigger_eval.py`, `skills/claude-vault/scripts/run_trigger_eval.sh` (macOS/Linux), `skills/claude-vault/scripts/run_trigger_eval.bat` (Windows)

A standalone eval harness that measures how accurately Claude invokes the skill based on its SKILL.md description. Uses a "skill-selection simulation" approach: presents Claude with the skill description alongside distractor skills and asks whether it would invoke `claude-vault` for each test query.

**How it works:**
1. Parses the `name` and `description` from SKILL.md frontmatter
2. Presents 20 test queries (10 should-trigger, 10 should-not-trigger) to Claude via `claude -p`
3. Each query runs 3 times for statistical reliability (60 total API calls)
4. Uses 6 parallel workers via `ProcessPoolExecutor`
5. Computes precision, recall, accuracy, and per-query pass rates
6. Writes results to `~/.claude/skills/claude-vault/eval_results.json`

**Important:** Must be run from a **separate terminal** (not inside Claude Code) because `claude -p` cannot be nested inside an active session. The shell wrappers `run_trigger_eval.sh` (macOS/Linux) and `run_trigger_eval.bat` (Windows) handle unsetting the `CLAUDECODE` environment variable.

**Distractor skills** (5 real skills from the user's setup) are included in the prompt to simulate realistic skill selection. Without distractors, results would be unrealistically optimistic.

### Context Preview Script

**Location:** `scripts/show-context`

A shell script that previews what vault context would be injected at session start for a given project directory. Useful for debugging the SessionStart hook without launching a full Claude Code session.

**Usage:**
```bash
# Preview context for the current directory
./scripts/show-context

# Preview context for a specific project
./scripts/show-context /path/to/project
```

Requires `jq` to be installed. The script invokes `session_start_hook.py` with a synthetic JSON input and extracts the `additionalContext` field from the hook output.

### Obsidian Integration

The vault is a standard Obsidian vault at `~/ClaudeVault/`. Obsidian provides the graph view, search, and wikilink navigation.

**Templates:** The `Templates/` directory is a symlink to the skill's `templates/` folder, making 8 note templates and the reference `config.yaml` available in Obsidian:

| Template | Note Type |
|----------|-----------|
| `daily.md` | Session summaries |
| `project.md` | Per-project context |
| `language.md` | Language-specific knowledge |
| `framework.md` | Framework knowledge |
| `pattern.md` | Design patterns |
| `debugging.md` | Error patterns and fixes |
| `tool.md` | CLI tools and packages |
| `research.md` | Deep-dive research |
| `config.yaml` | Reference config with all defaults |

## Configuration

All hooks and the summarizer support a centralized configuration file at `~/ClaudeVault/config.yaml`. A reference template with all defaults documented is shipped at `templates/config.yaml` and copied to the vault during installation.

**Precedence:** script defaults → `config.yaml` → CLI arguments.

**Sections:**

```yaml
session_start_hook:  # session_start_hook.py
  ai_model: null     # Model for AI note selection (null = disabled)
  max_chars: 4000    # Max context injection characters
  ai_timeout: 25     # AI call timeout in seconds
  recent_days: 3     # Days to look back for recent notes
  debug: false       # Append injected context + metadata to debug log in $TMPDIR

session_stop_hook:   # session_stop_hook.py
  ai_model: null     # Model for AI classification (null = disabled)
  ai_timeout: 25     # AI call timeout in seconds
  auto_summarize: true  # Auto-launch summarizer when pending entries exist

pre_compact_hook:    # pre_compact_hook.py
  lines: 200         # Transcript lines to analyse

summarizer:          # summarize_sessions.py
  model: claude-sonnet-4-6
  max_parallel: 5
  transcript_tail_lines: 400
  max_cleaned_chars: 12000
  persist: false     # SDK session persistence (for debugging)

git:
  auto_commit: true  # Auto-commit vault changes after writes
```

**Model defaults:** Hook scripts (`session_start_hook.py`, `session_stop_hook.py`) default to `claude-haiku-4-5-20251001` when AI mode is enabled. The summarizer defaults to `claude-sonnet-4-6`. Override any model via the corresponding config key or CLI flag. Setting `ai_model` to a model ID in config enables AI mode without needing the `--ai` CLI flag.

**`git.auto_commit`:** When `false`, `git_commit_vault()` returns immediately without staging or committing. This disables all automatic vault git commits across hooks and the summarizer.

## Data Flow

### Session Lifecycle

```mermaid
sequenceDiagram
    participant CC as Claude Code
    participant SSH as SessionStart Hook
    participant V as Vault (~/ClaudeVault/)
    participant STH as SessionEnd Hook
    participant PCH as PreCompact Hook

    CC->>SSH: SessionStart (cwd, session info)
    SSH->>V: Read project notes + recent notes
    V-->>SSH: Note summaries
    SSH-->>CC: additionalContext (vault context)

    Note over CC: Session in progress...

    CC->>PCH: PreCompact (transcript path)
    PCH->>V: Read last 200 transcript lines
    PCH->>V: Write snapshot to Daily/

    Note over CC: Context compacted, session continues...

    CC->>STH: SessionEnd (transcript path)
    STH->>V: Read last 200 transcript lines
    STH->>V: Append session summary to Daily/
    STH->>V: Queue to pending_summaries.jsonl (if significant learnings detected)
    STH->>V: git commit vault changes
    Note over STH: Auto-launches summarize_sessions.py<br/>in background if queue is non-empty
```

### Research Flow

```mermaid
sequenceDiagram
    participant U as User
    participant CC as Claude Code
    participant RA as Research Agent
    participant VE as vault-explorer agent
    participant WS as Web Sources
    participant V as Vault
    participant IDX as update_index.py

    U->>CC: Research request
    CC->>RA: Delegate to research agent
    RA->>VE: Dispatch with topic query
    VE->>V: Search existing vault notes
    V-->>VE: Ranked matches
    VE-->>RA: Answer + Sources (or "no notes found")
    RA->>WS: Brave Search (+ mcpl fallback if rate-limited)
    RA->>WS: agentchrome fetch (Web Fetch fallback)
    WS-->>RA: Source content
    RA->>V: Always save vault note with frontmatter
    RA->>IDX: Rebuild vault index
    IDX->>V: Write updated CLAUDE.md
    Note over RA: If project doc requested,<br/>also save there (no frontmatter)
    RA-->>CC: Summary report
```

## File Layout

### Source Repository (parsidion-cc)

```
parsidion-cc/
├── README.md
├── install.py                       # Installer: syncs skills/agents/hooks to ~/.claude/
├── CLAUDE-VAULT.md                  # Always-on vault-first guidance (installed to ~/.claude/)
├── pyproject.toml
├── Makefile
├── scripts/
│   ├── show-context                 # CLI: preview session start context for any project
│   └── html-to-md                   # CLI: convert HTML to clean markdown (file/stdin/URL)
├── docs/
│   ├── ARCHITECTURE.md              # This document
│   └── DOCUMENTATION_STYLE_GUIDE.md
├── agents/
│   ├── research-documentation-agent.md
│   └── vault-explorer.md                # Read-only vault search agent (Haiku)
├── tests/
│   └── test_vault_common.py
└── skills/claude-vault/
    ├── SKILL.md                     # Skill definition
    ├── scripts/
    │   ├── vault_common.py          # Shared library
    │   ├── session_start_hook.py    # SessionStart hook
    │   ├── session_stop_hook.py     # SessionEnd hook (queues to pending_summaries.jsonl)
    │   ├── pre_compact_hook.py      # PreCompact hook
    │   ├── summarize_sessions.py    # On-demand AI summarizer (PEP 723)
    │   ├── update_index.py          # Index generator
    │   ├── vault_doctor.py          # Vault note issue scanner and repair tool
    │   ├── check_graph_coverage.py  # Graph color group coverage audit
    │   ├── run_trigger_eval.py      # Trigger accuracy eval
    │   ├── run_trigger_eval.sh      # Shell wrapper for eval (macOS/Linux)
    │   ├── run_trigger_eval.bat     # Batch wrapper for eval (Windows)
    │   ├── migrate_research.py      # One-time migration
    │   └── migrate_memory.py        # One-time migration
    └── templates/
        ├── config.yaml              # Reference config with all defaults
        ├── daily.md
        ├── project.md
        ├── language.md
        ├── framework.md
        ├── pattern.md
        ├── debugging.md
        ├── tool.md
        └── research.md
```

### Installed Locations

```
~/.claude/
├── CLAUDE.md                        # Global instructions (@imports CLAUDE-VAULT.md)
├── CLAUDE-VAULT.md                  # Always-on vault-first guidance
├── settings.json                    # Hook registrations
├── agents/
│   ├── research-documentation-agent.md
│   └── vault-explorer.md                # Read-only vault search agent (Haiku)
├── scripts/
│   └── html-to-md                       # HTML → clean markdown (PEP 723)
└── skills/claude-vault/
    ├── SKILL.md
    ├── eval_results.json            # Trigger eval results
    ├── scripts/
    └── templates/

~/ClaudeVault/                       # Obsidian vault
├── .obsidian/
│   └── graph.json                   # Graph view color config
├── config.yaml                      # User config (copied from templates/config.yaml)
├── CLAUDE.md                        # Auto-generated index
├── Daily/YYYY-MM/DD.md      # e.g. Daily/2026-03/13.md
├── Projects/
├── Languages/
├── Frameworks/
├── Patterns/
├── Debugging/
├── Tools/
├── Research/
├── History/
└── Templates/ -> ~/.claude/skills/claude-vault/templates/
```

## Vault Note Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Detected: SessionEnd hook detects learning
    [*] --> Manual: User/agent creates note
    Detected --> Created: Write to vault folder
    Manual --> Created: Write with frontmatter
    Created --> Indexed: update_index.py
    Indexed --> Loaded: SessionStart hook
    Loaded --> Referenced: Used in session
    Referenced --> Updated: New info added
    Updated --> Indexed: Reindex

    note right of Created
        Must have:
        - YAML frontmatter
        - Kebab-case filename
        - At least one wikilink
    end note

    note right of Loaded
        Context budget: ~4000 chars
        Priority: project notes first,
        then recent notes
    end note
```

## Obsidian Graph View

The graph view uses domain-based color groups configured in `.obsidian/graph.json`. Since Obsidian applies **first-match-wins** coloring and 57% of vault notes have multiple tags, colors represent semantic categories rather than individual tags.

### Color Groups (Priority Order)

| Priority | Category | Color | Hex | Tags |
|----------|----------|-------|-----|------|
| 1 | Projects | Cyan | `#00BCD4` | synknot, fractal-flythroughs, parvitar, parsistant, termflix, parvault, cctmux, parsidion-cc |
| 2 | Debugging | Red/Orange | `#FF5722` | debugging |
| 3 | Patterns | Green | `#4CAF50` | memory, migration, sync |
| 4 | Research | Purple | `#9C27B0` | research, e2b, qdrant, pkm-apps-comparison |
| 5 | Tools & SDKs | Blue | `#2196F3` | claude-code, claude-agent-sdk, claude, rich, mcp, ollama, maturin, redis, websockets, sentry, mermaid-cli, custom-tools, acp-protocol, tool, api, encryption |
| 6 | Languages | Amber | `#FFC107` | rust, python, swift, swiftui, typescript, nextjs, react, macos, macos-26, rust-packages |
| 7 | Terminal | Teal | `#009688` | terminal, par-term, par-term-emu-core-rust |
| 8 | Graphics / 3D | Pink | `#E91E63` | wgpu, sdf, sdf-terrain, voxel, fractals, mandel, vrm, avatar, face-tracking |

Nodes with no matching tags remain the default gray. The priority order means a debugging note tagged with a project name appears as Cyan (project), since project membership is the highest-priority grouping. RGB colors are stored as decimal integers in `graph.json` (e.g., `int("FF5722", 16)` → `16733986`).

## Related Documentation

- [README.md](../README.md) - Project overview and quick reference
- [DOCUMENTATION_STYLE_GUIDE.md](DOCUMENTATION_STYLE_GUIDE.md) - Documentation formatting standards
- [SKILL.md](../skills/claude-vault/SKILL.md) - Vault philosophy, conventions, and anti-patterns
