---
name: parsidion
description: >
  ALWAYS invoke first — before any coding or debugging action — when the query
  references accumulated past knowledge or future session persistence.
  Trigger signals: "have we hit/seen this before", "what do we know about X",
  "check our notes", "check if [project] has this", "prior art", "save this to
  the vault", "don't forget", "remember this", "capture this", or any mention of
  "the vault" or "ClaudeVault".

  This skill owns ~/ClaudeVault/ — a persistent Obsidian knowledge base surviving
  across all coding sessions, storing debugging solutions, reusable patterns, and
  cross-project context.

  Core use cases: (1) retrieving whether a problem or pattern was solved before,
  (2) checking other projects for existing implementations, (3) saving new
  discoveries for future sessions, (4) vault admin tasks like rebuilding the index,
  running the session summarizer, or configuring vault hooks and settings.

  Do NOT invoke for pure coding/debugging queries with zero reference to past
  sessions or future memory.
---

# Parsidion vault - Knowledge Management System

> A richly organized Obsidian vault at `~/ClaudeVault/` that replaces built-in auto memory with structured, searchable, cross-linked knowledge.

## Philosophy

- **Search before create.** Always search the vault before creating a new note. Duplicate knowledge fragments rot.
- **Atomic notes.** One concept per note. If a note covers two distinct ideas, split it.
- **Frontmatter is mandatory.** Every note must have a valid YAML frontmatter block. No exceptions.
- **Knowledge compounds.** Individual notes gain value through links. An unlinked note is a dead note.
- **Confidence matters.** Tag what you know vs. what you suspect. Update confidence as understanding deepens.

## Vault-First Consultation

The vault is your **first stop** — not your last resort. Before web search, before documentation, before experimentation, check whether the vault already holds a solution.

### Debugging: Search Before You Diagnose

When you encounter an error, exception, or unexpected behavior:

1. **Extract the key signal**: the exception type, package name, or the most distinctive phrase from the error message
2. **Dispatch the `vault-explorer` agent** with the signal as the query.
3. **Answer returned?** Apply the documented solution; update the note if you learn something new about the problem. `Read` specific files from the Sources section only if you need more depth.
4. **"No relevant vault notes found"?** Diagnose, solve, then save the solution so future sessions benefit.

### Implementation: Check for Prior Art

When implementing any feature, pattern, or integration:

1. **Dispatch the `vault-explorer` agent** with the feature keyword as the query.
2. **Answer returned?** Reuse and adapt — a proven implementation beats a fresh one every time. `Read` specific files from the Sources section for details; check the `sources` frontmatter field for code references.
3. **"No relevant vault notes found"?** Implement it, then save the pattern so future sessions benefit.

### Efficient Vault Search

Dispatch the **`vault-explorer` agent** with a natural language query. The agent
searches all relevant vault folders using the appropriate Grep strategy, ranks
matches, reads the top files, and returns:

- **`## Answer`** — synthesized answer ready to use
- **`## Sources`** — absolute paths for targeted `Read` calls if deeper context is needed

**No results?** Dispatch the `research-agent` to research externally
and save findings to the vault.

### The Vault-First Loop

```
Error or implementation question
  → Dispatch vault-explorer agent
    → Found? Apply / adapt solution → Update note with new learnings
    → Not found? Solve it → Dispatch research-agent to save
```

Saving after a successful solve is as important as searching before. Every unsaved solution is a missed opportunity for every future session.

---

## Vault Structure

```
~/ClaudeVault/
├── CLAUDE.md            # Auto-generated index (rebuilt by update_index.py)
├── config.yaml          # Optional — hook/summarizer settings (see Configuration)
├── Daily/               # Session summaries (Daily/YYYY-MM/DD.md)
├── Projects/            # Per-project context and decisions
├── Languages/           # Python, Rust, TypeScript, Swift, etc.
├── Frameworks/          # Next.js, FastAPI, Textual, Rich, etc.
├── Patterns/            # Design patterns, architectural solutions
├── Debugging/           # Error patterns, diagnostic steps, fixes
├── Tools/               # CLI tools, libraries, packages
├── Research/            # Deep-dive research documents
├── Knowledge/           # General knowledge, concepts, reference material
├── History/             # Historical notes
└── Templates/           # Symlinked to skill templates (read-only)
```

## Conventions

### Filenames
- **Kebab-case only**: `python-async-patterns.md`, `nextjs-app-router-caching.md`
- Descriptive but concise -- aim for 3-5 words

### Subfolders for Related Note Clusters

When **3 or more notes share a common subject**, group them into a subfolder named after that subject:

```
# Before (scattered)
Research/
  fastapi-middleware-basics.md
  fastapi-middleware-auth.md
  fastapi-middleware-cors.md

# After (grouped)
Research/
  fastapi-middleware/
    basics.md
    auth.md
    cors.md
```

**Rules:**
- The subfolder name is the shared subject prefix in kebab-case (e.g., `fastapi-middleware/`)
- Note filenames inside the subfolder drop the redundant prefix (e.g., `auth.md` not `fastapi-middleware-auth.md`)
- This applies both **proactively** (when creating a 3rd note on a subject) and **retroactively** (reorganize existing notes when a 3rd sibling appears)
- Update all `[[wikilinks]]` in `related` fields after moving notes
- Run `update_index.py` after any reorganization

### Cross-References
- Use **wikilinks** for all internal references: `[[python-async-patterns]]`
- Place wikilinks in the `related` frontmatter field AND inline in the note body where contextually relevant

### Folder Placement
| Content Type | Folder |
|---|---|
| Language-specific knowledge (syntax, idioms, stdlib) | `Languages/` |
| Framework-specific knowledge (config, APIs, gotchas) | `Frameworks/` |
| Reusable design patterns and architectural solutions | `Patterns/` |
| Error patterns, diagnostic steps, bug fixes | `Debugging/` |
| CLI tools, libraries, package notes | `Tools/` |
| Long-form research and analysis | `Research/` |
| General knowledge, concepts, reference material | `Knowledge/` |
| Per-project decisions, architecture, key paths | `Projects/` |
| Daily session summaries | `Daily/YYYY-MM/DD.md` (e.g. `Daily/2026-03/13.md`) |

### Frontmatter Standard

Every note must begin with this YAML block:

```yaml
---
date: YYYY-MM-DD
type: pattern|debugging|research|project|daily|tool|language|framework|knowledge
tags: [tag1, tag2]
project: project-name        # optional - omit if not project-specific
confidence: high|medium|low
sources: []                  # URLs, file paths, or references
related: []                  # [[wikilinks]] to other vault notes
---
```

**Field rules:**
- `date`: The date the note was created or last substantially updated.
- `type`: Must be one of the enumerated values. Drives folder placement.
- `tags`: Freeform but prefer existing tags. Check the vault index first. **NEVER use underscores — always kebab-case (hyphens).** Convert repo names like `par_ai_core` to `par-ai-core`. Prefer short singular tags — e.g. `voxel` not `voxel-engine`, `hook` not `hooks`, `fractal` not `fractals`. Longer compound tags are acceptable only when the shorter form would be ambiguous.
- `project`: Optional. Must also be kebab-case (no underscores). Convert repo names: `par_ai_core` → `par-ai-core`.
- `confidence`: `high` = verified across multiple interactions or sources. `medium` = likely correct, single source. `low` = hypothesis or unverified.
- `related`: Must contain at least one wikilink in inline quoted array format: `["[[note-one]]", "[[note-two]]"]`. No orphan notes.

## When to Save Knowledge

Save a note when you encounter:

- **Stable patterns** confirmed across multiple interactions or projects
- **Key architectural decisions** and the reasoning behind them
- **Important file paths** and project structure insights
- **Solutions to recurring problems** -- especially if the fix was non-obvious
- **Debugging insights** -- error messages mapped to root causes and fixes
- **User preferences** for workflow, tools, coding style, communication
- **Explicit requests** -- when the user says "remember this" or equivalent

## When NOT to Save

Do not create notes for:

- **Session-specific context** -- current task details, temporary variables, in-progress work
- **Incomplete or unverified information** -- wait until confidence is at least `medium`
- **Anything already in CLAUDE.md** -- the vault supplements project instructions, it does not duplicate them
- **Raw code dumps** -- code without explanation, context, or the "why" has no vault value
- **Transient state** -- things that will be irrelevant by next session

## Anti-Patterns

| Anti-Pattern | Correct Approach |
|---|---|
| Creating a note without frontmatter | Always include the full frontmatter block |
| Creating a duplicate note | Search the vault first; update the existing note instead |
| Dumping code without context | Explain what the code does, why it matters, and when to use it |
| Orphan notes (no links) | Every note must link to at least one other note via `related` |
| Monolithic notes (multiple concepts) | Split into atomic notes, one concept each, linked together |
| Modifying files in `Templates/` | Templates are managed by the skill -- never edit directly |
| 3+ notes on the same subject left flat | Move them into a named subfolder; update all wikilinks |

## Workflow Integration

### Hooks

All hooks read `~/ClaudeVault/config.yaml` for settings. CLI args override config values.

Claude Code installs the full hook set below. Codex runtime hooks are session lifecycle only: native Codex `SessionStart` and `Stop` hooks registered in `~/.codex/hooks.json` when `codex_hooks = true` is enabled in `~/.codex/config.toml`. Codex integration does not provide full Claude hook parity (no PreCompact, PostCompact, SubagentStop, or tool hooks in this phase).

| Hook | Behavior | Config section |
|---|---|---|
| **SessionStart** | Loads relevant vault notes as a **compact one-line-per-note index** (title + tags) by default — minimal token usage. `--verbose` flag or `verbose_mode: true` config switches to full note summaries. Optional AI selection via `--ai [MODEL]` or `session_start_hook.ai_model` uses the configured prompt AI backend. | `session_start_hook` |
| **SessionEnd** | Captures learnings from the session transcript (fires once at session end); auto-launches the summarizer when pending entries exist | `session_stop_hook` |
| **PreCompact** | Snapshots current working state so context survives compaction | `pre_compact_hook` |

### Rebuilding the Index

The `CLAUDE.md` at the vault root is auto-generated. Rebuild it when:
- The user requests it ("rebuild vault index", "update vault index", "refresh vault index", "refresh the vault", "sync the vault")
- After creating, renaming, moving, or deleting notes
- The index timestamp is older than 24 hours

```bash
uv run ~/.claude/skills/parsidion/scripts/update_index.py
```

This scans all vault folders, reads frontmatter, and produces:
- A structured root `CLAUDE.md` index with tag cloud, recent activity, and per-folder listings
- **Per-folder `MANIFEST.md` files** — table-format indexes (Note | Tags | Summary) written inside each subfolder for quick orientation without loading the full root index
- **Staleness markers** — notes with zero incoming wikilinks AND older than 30 days are flagged `[STALE?]` in the index (surfaced for review, never auto-deleted)

Confirm to the user when the rebuild is complete.

## Summarizing Pending Sessions

The stop hook queues session transcript paths when it detects learnable content.
Run the summarizer on demand to generate structured vault notes.

Supported transcript locations:
- Claude Code: `~/.claude/projects/**/*.jsonl`
- Codex CLI: `~/.codex/sessions/**/*.jsonl`
- pi (global): `~/.pi/agent/sessions/**/*.jsonl`
- pi (project-local): `<project>/.pi/agent-sessions/**/*.jsonl`

### Running the Summarizer

**From a terminal outside Claude Code** (normal usage):
```bash
uv run ~/.claude/skills/parsidion/scripts/summarize_sessions.py
```

**From inside a Claude Code session** (testing/debugging only):
Claude Code sets the `CLAUDECODE` env var which blocks nested Claude sessions.
Unset it for the subprocess:
```bash
env -u CLAUDECODE uv run ~/.claude/skills/parsidion/scripts/summarize_sessions.py
```

**Process a single explicit file** (useful for testing one entry):
```bash
env -u CLAUDECODE uv run ~/.claude/skills/parsidion/scripts/summarize_sessions.py \
  --sessions /path/to/file.jsonl
```

### Options

| Flag | Description | Config key |
|---|---|---|
| `--sessions FILE` | Process an explicit JSONL file instead of the default pending queue | — |
| `--dry-run, -n` | Preview what would be written without creating notes | — |
| `--model MODEL` | Override model (default: `claude-sonnet-4-6`) | `summarizer.model` |
| `--persist` | Enable SDK session persistence (default off; use for debugging) | `summarizer.persist` |

Uses `claude-agent-sdk` — no API key needed, runs via your Max subscription. `summarize_sessions.py` remains Claude Agent SDK-backed in this release; Codex support for SDK-backed summarization is planned separately.
Processes up to 5 sessions in parallel (configurable via `summarizer.max_parallel`).
Rebuilds the vault index automatically when done.
Sessions from today whose generated note type is `daily` are skipped (today's daily note
is still being built by the stop hook).

### Write-Gate Filter

Before generating a note, the summarizer asks Claude to evaluate whether the session contains
reusable insight. Transient sessions (failed experiments, routine builds, dead-ends with no
generalizable lesson) are skipped rather than saved. Skipped sessions are reported separately
from failures in the output summary.

### Hierarchical Summarization

For sessions whose cleaned transcript exceeds `max_cleaned_chars` (default 12,000), the
summarizer splits the transcript into chunks and uses a faster model (`claude-haiku-4-5-20251001`
by default, configurable via `summarizer.cluster_model`) to summarize each chunk first. The
chunk summaries are then fed to the main Sonnet note generator. This preserves context from
very long sessions that would otherwise be truncated.

### Automated Backlinks

After writing a new note, the summarizer scans existing vault notes for tag overlap and
injects bidirectional `[[wikilinks]]` — updating both the new note's `related` field and
adding back-references in matching existing notes. This builds the link graph automatically
rather than requiring manual maintenance.

## Vault Merge

`vault-merge` is a global CLI command for backend-aware AI-assisted note merging. It detects near-duplicate notes, merges their content via the configured prompt AI backend, and updates all bidirectional backlinks.

### Usage

```bash
# Find near-duplicate pairs using embedding similarity (scan only, no merge)
vault-merge --scan

# Merge two specific notes (NOTE_A survives, NOTE_B is trashed)
vault-merge NOTE_A NOTE_B --execute

# Merge without rebuilding the index (for batch merges — rebuild once at the end)
vault-merge NOTE_A NOTE_B --no-index --execute
```

### Key Flags

| Flag | Description |
|---|---|
| `--scan` | List near-duplicate pairs sorted by cosine similarity; no merge performed |
| `--execute` | Actually perform the merge (default is dry-run preview) |
| `--no-index` | Skip rebuilding the vault index after the merge; use during batch operations and run `update_index.py` once when all merges are done |

> Use `--no-index` on every individual merge during a batch deduplication run; run `update_index.py` once at the end. The `vault-deduplicator` agent handles this automatically.

## Vault Doctor

`vault_doctor.py` scans all vault notes for structural issues and repairs repairable issues via the configured prompt AI backend.

### Issues detected

| Code | Severity | Description |
|---|---|---|
| `MISSING_FRONTMATTER` | error | No YAML frontmatter block |
| `MISSING_FIELD` | error | `date`/`type` missing (all notes); `confidence`/`related` missing (non-daily) |
| `INVALID_TYPE` | error | `type` not in allowed set |
| `INVALID_DATE` | warning | `date` not in YYYY-MM-DD format |
| `ORPHAN_NOTE` | warning | No `[[wikilinks]]` in `related` field |
| `BROKEN_WIKILINK` | warning | Link target not found in vault |
| `HEADING_MISMATCH` | warning | No `#` heading found; first `##` heading should be promoted to `#` (skips daily notes) |
| `FLAT_DAILY` | warning | `Daily/YYYY-MM-DD.md` instead of `Daily/YYYY-MM/DD.md` |
| `PREFIX_CLUSTER` | warning | 3+ flat notes share a kebab prefix — should be moved into a subfolder |

Daily notes (`type: daily` or path under `Daily/`) are exempt from `confidence`, `related`, and orphan checks.

### Running the doctor

```bash
# Scan and report only (no writes)
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --dry-run

# Fix everything in one pass (frontmatter + tags + subfolders) — used by nightly cron
env -u CLAUDECODE uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-all

# Individual fix modes:
# Repair frontmatter issues via the configured prompt AI backend (3 parallel workers by default)
env -u CLAUDECODE uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-frontmatter
env -u CLAUDECODE uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-frontmatter --jobs 5 --timeout 180

# Detect and merge duplicate tags (plural/singular, hyphen/underscore, collapsed)
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-tags           # dry-run
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-tags --execute # apply

# Detect and migrate prefix clusters into subfolders
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --migrate-subfolders           # dry-run
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --migrate-subfolders --execute # apply

# Repair up to 20 notes at a time
env -u CLAUDECODE uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-frontmatter --limit 20

# Errors only (skip warnings)
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --errors-only --dry-run
```

For Claude CLI backend calls launched from inside Claude Code, unset `CLAUDECODE` as shown above. Codex backend calls use `codex exec` with an internal recursion guard.

`--fix-all` is equivalent to `--fix-frontmatter --fix-tags --migrate-subfolders --execute`. The nightly cron via `summarize_sessions.py --run-doctor` uses `--fix-all`.

Repairs run in parallel (`--jobs N`, default 3). Each prompt AI subprocess (`claude -p` or `codex exec`) is independent so parallelism is safe; state updates and console output are guarded by a lock so lines are never interleaved. The per-call timeout (`--timeout SECS`) defaults to 120s — increase it when running many parallel workers to avoid spurious timeouts.

Repairable codes (prompt AI backend can fix): `MISSING_FRONTMATTER`, `MISSING_FIELD`, `INVALID_TYPE`, `INVALID_DATE`, `ORPHAN_NOTE`.
Auto-repairable without prompt AI (Python-only): `BROKEN_WIKILINK` (exact/semantic match), `DUPLICATE_TAG` (merge via `--fix-tags`), `HEADING_MISMATCH` (promotes first `##` to `#`; enabled by default, disable with `--no-fix-headings`).
Auto-repairable via Python + prompt AI filter: `PREFIX_CLUSTER` — candidates are detected by Python, then the configured small model filters out generic-word false positives (e.g. 'fixing', 'missing'), keeping only specific subject names (project, library, OS, tool). Files are then moved and wikilinks patched by Python.
Not auto-repairable (require manual fix): `FLAT_DAILY`.

### Singleton guard

Only one doctor may run at a time. On startup the doctor checks `doctor_state.json` for a `pid` field. If that process is still alive it prints an error and exits. Otherwise it writes its own PID immediately to claim the lock and clears it via `atexit` when it exits (including on SIGTERM; a SIGKILL'd process is detected as stale on the next run).

### Auto-commit stale files

Before scanning, the doctor runs `git status --porcelain` on the vault and commits any tracked or untracked files whose mtime is ≥ 15 minutes old. Deletions are skipped (no mtime to check). The commit message is `chore(vault): auto-commit N stale file(s) via vault_doctor`. This is a no-op when the vault has no `.git` directory or `git.auto_commit` is `false` in config. With `--dry-run` the files are listed but not committed.

### State tracking

The doctor writes `~/ClaudeVault/doctor_state.json` to avoid reprocessing notes unnecessarily:

| Status | Meaning | Next run behaviour |
|---|---|---|
| `ok` | No issues found | Skipped for 7 days |
| `fixed` | Prompt AI backend repaired it | Re-checked to confirm |
| `failed` | Prompt AI backend returned no output | Retried |
| `timeout` | Prompt AI backend timed out once | Retried once more |
| `needs_review` | Timed out on retry | Skipped — user must fix manually |
| `skipped` | Only non-repairable issues | Skipped indefinitely |

Use `--no-state` to ignore the state file and rescan all notes.

Run `update_index.py` after repairs — it reads `doctor_state.json` and adds a vault health line to `CLAUDE.md`'s Quick Stats section.

## Configuration

All hooks and the summarizer read `~/ClaudeVault/config.yaml` for settings.
Precedence: **hardcoded defaults → config.yaml → CLI args** (last one wins).

A template with all options lives at `~/.claude/skills/parsidion/templates/config.yaml`.
Copy it to the vault root to get started:

```bash
cp ~/.claude/skills/parsidion/templates/config.yaml ~/ClaudeVault/config.yaml
```

### Config Sections

> **📝 Note:** Model IDs shown in the config block below (e.g. `claude-sonnet-4-6`,
> `claude-haiku-4-5-20251001`, `gpt-5.5`, `BAAI/bge-small-en-v1.5`) are the hardcoded defaults.
> They can be changed via the corresponding keys in `~/ClaudeVault/config.yaml` without
> modifying any scripts. See the template at
> `~/.claude/skills/parsidion/templates/config.yaml` for all available keys.

```yaml
session_start_hook:
  ai_model: null           # Model for AI note selection (null = disabled)
  max_chars: 4000          # Max context injection characters
  ai_timeout: 25           # AI call timeout in seconds
  recent_days: 3           # Days to look back for recent notes
  debug: false             # Append injected context to debug log in $TMPDIR
  verbose_mode: false      # If true, inject full note summaries instead of compact one-line index
  use_embeddings: true     # Blend semantic matches into context; graceful fallback if db absent

session_stop_hook:
  ai_model: null           # Model for AI classification (null = disabled)
  ai_timeout: 25           # AI call timeout in seconds
  auto_summarize: true     # Auto-launch summarizer when pending entries exist
  transcript_tail_lines: 200      # Lines to inspect by default
  pi_transcript_tail_lines: 1000  # Deeper fallback tail for pi transcripts when needed

subagent_stop_hook:
  enabled: true            # Set false to disable subagent transcript capture entirely
  min_messages: 3          # Minimum assistant message count (pi transcripts default to 1 when unset)
  excluded_agents: "vault-explorer,research-agent"  # Never capture these

pre_compact_hook:
  lines: 200               # Transcript lines to analyse

summarizer:
  model: claude-sonnet-4-6
  max_parallel: 5          # Concurrent summarization tasks
  transcript_tail_lines: 400
  max_cleaned_chars: 12000
  persist: false           # SDK session persistence (for debugging)
  cluster_model: claude-haiku-4-5-20251001  # Model for hierarchical chunk summarization (default; override via config.yaml)

ai:
  backend: auto            # auto | claude-cli | codex-cli | none

ai_models:
  claude:
    small: claude-haiku-4-5-20251001
    large: claude-sonnet-4-6
  codex:
    small: gpt-5.5
    large: gpt-5.5

codex_cli:
  command: codex
  timeout: 60
  sandbox: read-only
  ephemeral: true
  skip_git_repo_check: true

embeddings:
  model: BAAI/bge-small-en-v1.5  # fastembed model ID; ~67 MB ONNX model, cached after first run
  min_score: 0.45                 # Minimum cosine similarity for search results
  top_k: 10                       # Default result count

git:
  auto_commit: true        # Auto-commit vault changes after writes
```

- `auto` prefers the active runtime when detectable and falls back to Claude CLI when ambiguous.
- Codex backend uses `codex exec` with default ephemeral/read-only/skip-git-repo-check/output-last-message prompt calls and the normal Codex CLI auth path; Parsidion does not manage Codex auth files such as `~/.codex/auth.json`. This is not OpenAI API-key provider support. `summarize_sessions.py` remains Claude Agent SDK-backed for now.

### Programmatic Access

```python
import vault_common

# Load all config (cached per process)
config = vault_common.load_config()

# Get a single value with fallback
max_chars = vault_common.get_config("session_start_hook", "max_chars", 4000)
```

If `config.yaml` is missing or unreadable, all `get_config()` calls return the default.

## Manual Usage

### Creating a Note

1. Search the vault to confirm the note does not already exist.
2. Create the file in the appropriate folder with kebab-case naming.
3. Include the full frontmatter block.
4. Write the note body with inline wikilinks to related notes.
5. Ensure the `related` frontmatter field is populated.
6. Run `update_index.py` to rebuild the index.

### Searching the Vault

- Dispatch the **`vault-explorer` agent** for natural language queries — it searches priority folders, ranks matches, and returns a synthesized answer with source paths.
- Use `vault_common.py` functions for programmatic search from scripts.
- Use Obsidian's built-in search for interactive visual exploration.
- Use `Grep` or `Glob` tools only for targeted investigation when you know the specific file or exact pattern you need.

### Agents

| Agent | Trigger | Description |
|---|---|---|
| `vault-explorer` | "what do we know about X", "check our notes", "prior art" | Semantic vault search — returns a synthesized answer with source paths |
| `research-agent` | "research X and save to vault", no vault results found | External research via Brave Search + Web Fetch; saves findings to vault |
| `vault-deduplicator` | "deduplicate the vault", "find duplicate notes", "merge duplicate vault notes" | Scans for near-duplicate note pairs by embedding similarity, evaluates each pair, merges confirmed duplicates, and rebuilds the index |

### Linking Notes

- Use `[[wikilinks]]` in both the `related` frontmatter field and the note body.
- When a new note references an existing note, consider updating the existing note's `related` field to create a bidirectional link.

### Updating Existing Notes

- Prefer updating an existing note over creating a new one on the same topic.
- Update the `date` field when making substantial changes.
- Adjust `confidence` as understanding evolves.

## Complementary Tools

### NotebookLM

Vault notes capture research as structured text. The `notebooklm` skill transforms the same
source material into audio overviews, podcasts, quizzes, flashcards, mind maps, slide decks,
and reports — formats the vault cannot produce.

Common patterns after vault research:
- **Audio consumption**: Generate a podcast from the URLs in a vault note's `sources` field.
- **Study materials**: Create quizzes or flashcards from a `Research/` note.
- **Briefing documents**: Generate a briefing-doc from a cluster of related notes.
- **Mind maps**: Visual overview of a notebook containing the same sources as a vault cluster.

**Workflow**: Add the `sources` URLs from your vault note(s) as NotebookLM sources, then use
`notebooklm generate` for the desired artifact. The research-agent does this
automatically when NotebookLM is available.

**Requirement**: `pip install notebooklm-py` then `notebooklm login`. Run `notebooklm status`
to check. If unavailable, the vault workflow is completely unaffected.

## Graph Color Groups

The Obsidian graph at `~/ClaudeVault/.obsidian/graph.json` uses color groups to visually categorize nodes by tag.

### Current Color Groups

| Group | Tags | RGB (decimal) |
|---|---|---|
| Projects | `#synknot`, `#parvitar`, `#parsistant`, `#termflix`, `#parvault`, `#parsidion`, `#pkm`, `#vault`, `#par-cc-bot`, `#parllama`, `#par-dc-bot`, `#par-particle-life`, `#par-shape-2d`, `#par_qr_3d`, `#par_scrape`, `#par-gpt`, `#par-ocr`, `#par_infini_sweeper`, `#parsplat`, `#pim-leaderboard`, `#par-ai-core`, `#cctmux` | 48340 |
| Debugging | `#debugging` | 16733986 |
| Patterns | `#memory`, `#migration`, `#sync`, `#architecture`, `#pattern`, `#automation`, `#config`, `#event-driven`, `#pipeline`, `#subprocess`, `#crdt`, `#layered-config`, `#rate-limiting`, `#semantic-search`, `#vector-search`, `#codesign`, `#state-machine`, `#concurrency` | 5025616 |
| Research | `#research`, `#qdrant`, `#embeddings`, `#vector-database`, `#ocr` | 10233776 |
| Tools & SDKs | `#claude-code`, `#claude-sdk`, `#claude`, `#rich`, `#mcp`, `#ollama`, `#maturin`, `#redis`, `#websockets`, `#sentry`, `#mermaid`, `#mermaid-cli`, `#custom-tools`, `#acp-protocol`, `#tool`, `#api`, `#encryption`, `#discord`, `#security`, `#git`, `#performance`, `#plugin`, `#cli`, `#llm`, `#hooks`, `#sqlite`, `#agent`, `#pydantic`, `#langchain`, `#multi-provider`, `#docker`, `#xdg`, `#image`, `#textual`, `#typer`, `#fastapi`, `#websocket`, `#image-processing`, `#json`, `#yaml`, `#ai` | 2201331 |
| Languages | `#rust`, `#python`, `#swift`, `#swiftui`, `#typescript`, `#nextjs`, `#react`, `#macos`, `#macos-26`, `#rust-packages`, `#ui`, `#ux`, `#documentation`, `#sql`, `#ios`, `#python-bindings` | 16761095 |
| Terminal | `#terminal`, `#tmux`, `#par-term`, `#par-term-emu-core-rust`, `#tui`, `#vt100`, `#vte`, `#pty`, `#ansi`, `#crossterm`, `#svt` | 38536 |
| Graphics / 3D | `#wgpu`, `#sdf`, `#sdf-terrain`, `#voxel`, `#fractals`, `#mandel`, `#vrm`, `#avatar`, `#face-tracking`, `#arkit`, `#graphics`, `#fractal`, `#gpu-rendering`, `#animation`, `#gaussian-splatting`, `#physics`, `#rendering`, `#simulation`, `#glsl`, `#shader`, `#shaders`, `#3d-printing`, `#par-fractal`, `#voxel-world`, `#sparse-voxel-tree`, `#sub-voxel`, `#wgsl`, `#vulkan`, `#game` | 15277667 |
| Daily | `#daily` | 16744448 |

### When to Update

Update `graph.json` when:
- A new project tag is introduced (add to the Projects group)
- A new language or framework tag appears frequently (add to Languages or Tools/AI)
- A new topic cluster emerges that warrants its own color group
- An existing tag is renamed or removed

### How to Update

1. Read `~/ClaudeVault/.obsidian/graph.json`
2. Locate the matching `colorGroups` entry by its query pattern
3. To **add a tag to an existing group**: append `OR tag:#newtag` to the query string
4. To **create a new group**: append a new object to the `colorGroups` array:
   ```json
   {
     "query": "tag:#newtag",
     "color": { "a": 1, "rgb": <decimal_rgb> }
   }
   ```
5. Write the updated file — Obsidian picks up changes automatically

### RGB Format

Colors are stored as a single decimal integer (e.g., `16733986` = `#FF2DA2` in hex). To convert:
- Hex to decimal: `int("FF2DA2", 16)`
- Choose colors that are visually distinct from existing groups

### Color Group Priority

Groups are evaluated in order — the **first matching group wins**. Place more specific queries (e.g., a single project tag) before broad ones (e.g., `#research`).

### Auditing Coverage

Use `check_graph_coverage.py` to find vault tags that are not covered by any color group and to spot stale group entries (tags in `graph.json` that no longer exist in the vault):

```bash
python ~/.claude/skills/parsidion/scripts/check_graph_coverage.py

# Only report uncovered tags used 2+ times
python ~/.claude/skills/parsidion/scripts/check_graph_coverage.py --threshold 2

# Machine-readable JSON output
python ~/.claude/skills/parsidion/scripts/check_graph_coverage.py --json
```

### Full Graph Colorizer Workflow

Run this workflow after batch summarizations, vault doctor runs, or whenever the tag taxonomy changes significantly:

1. **Merge duplicate tags first** — run `vault_doctor.py --fix-tags --execute` to merge plural/singular, hyphen/underscore, and collapsed duplicates. This reduces tag sprawl before colorizing.
2. **Rebuild the index** — run `update_index.py` so the tag cloud reflects the current state.
3. **Audit coverage** — run `check_graph_coverage.py --threshold 2` to see which tags are uncovered and what groups they should belong to.
4. **Update `graph.json`** — read `~/ClaudeVault/.obsidian/graph.json`, add uncovered tags to the appropriate `colorGroups` entry by appending `OR tag:#newtag` to the query string.
5. **Verify** — re-run the audit to confirm coverage is ≥ 90%. Obsidian picks up `graph.json` changes automatically.

The `--json` output from `check_graph_coverage.py` includes a `stats` object with `total_vault_tags`, `covered`, `uncovered`, and `stale` counts for scripting.
