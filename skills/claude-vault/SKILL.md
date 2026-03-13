---
name: claude-vault
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

# Claude Vault - Knowledge Management System

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
2. **Search `Debugging/`** first:
   ```bash
   # Use the Grep tool with path ~/ClaudeVault and glob **/*.md, e.g.:
   grep -r "ImportError" ~/ClaudeVault/Debugging/ --include="*.md" -l
   grep -r "sqlalchemy" ~/ClaudeVault/Debugging/ --include="*.md" -l
   ```
3. **Widen the search** to `Frameworks/`, `Languages/`, and `Projects/` — the fix may live there
4. **If you find a match**: apply the documented solution; update the note if you discover new nuance
5. **If no match**: diagnose, solve, then save the solution so future sessions benefit

### Implementation: Check for Prior Art

When implementing any feature, pattern, or integration:

1. **Search `Patterns/`** for design patterns relevant to the feature
2. **Search `Frameworks/` and `Languages/`** for the specific stack
3. **Search `Projects/`** for implementations in other projects:
   ```bash
   grep -r "websocket" ~/ClaudeVault/Projects/ --include="*.md" -l
   grep -r "authentication" ~/ClaudeVault/ --include="*.md" -l
   ```
4. **Examine `sources`** in matched notes for code references and file paths
5. **Reuse and adapt** — a proven implementation beats a fresh one every time

### Efficient Vault Search

| Goal | Approach |
|---|---|
| Find by error type | Grep for exception class name across all vault folders |
| Find by library/framework | Grep for the package name in `Frameworks/` or `Languages/` |
| Find by tag | Check vault `CLAUDE.md` index for tag listings |
| Find cross-project patterns | Search `Projects/`, follow `related` wikilinks |
| Find by note type | Grep for `type: debugging` or `type: pattern` in frontmatter |

Prefer the `Grep` tool with `path: ~/ClaudeVault` and `glob: **/*.md` over shell commands.

### The Vault-First Loop

```
Error or implementation question
  → Search vault
    → Found? Apply / adapt solution → Update note with new learnings
    → Not found? Solve it → Save to vault → Rebuild index
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
| Per-project decisions, architecture, key paths | `Projects/` |
| Daily session summaries | `Daily/YYYY-MM/DD.md` (e.g. `Daily/2026-03/13.md`) |

### Frontmatter Standard

Every note must begin with this YAML block:

```yaml
---
date: YYYY-MM-DD
type: pattern|debugging|research|project|daily|tool|language|framework
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
- `tags`: Freeform but prefer existing tags. Check the vault index first. When creating new tags, prefer short single-word or minimal-hyphen tags — e.g. `voxel` not `voxel-engine`, `terminal` not `terminal-emulator`. Longer compound tags are acceptable only when the shorter form would be ambiguous.
- `confidence`: `high` = verified across multiple interactions or sources. `medium` = likely correct, single source. `low` = hypothesis or unverified.
- `related`: Must contain at least one wikilink. No orphan notes.

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

| Hook | Behavior | Config section |
|---|---|---|
| **SessionStart** | Loads relevant vault context based on the current project and recent daily notes; optional AI selection via `--ai [MODEL]` or `session_start_hook.ai_model` config | `session_start_hook` |
| **SessionEnd** | Captures learnings from the session transcript (fires once at session end); auto-launches the summarizer when pending entries exist | `session_stop_hook` |
| **PreCompact** | Snapshots current working state so context survives compaction | `pre_compact_hook` |

### Rebuilding the Index

The `CLAUDE.md` at the vault root is auto-generated. Rebuild it when:
- The user requests it ("rebuild vault index", "update vault index", "refresh vault index", "refresh the vault", "sync the vault")
- After creating, renaming, moving, or deleting notes
- The index timestamp is older than 24 hours

```bash
uv run ~/.claude/skills/claude-vault/scripts/update_index.py
```

This scans all vault folders, reads frontmatter, and produces a structured index with links and tags. Confirm to the user when the rebuild is complete.

## Summarizing Pending Sessions

The stop hook queues session transcript paths when it detects learnable content.
Run the summarizer on demand to generate structured vault notes.

### Running the Summarizer

**From a terminal outside Claude Code** (normal usage):
```bash
uv run ~/.claude/skills/claude-vault/scripts/summarize_sessions.py
```

**From inside a Claude Code session** (testing/debugging only):
Claude Code sets the `CLAUDECODE` env var which blocks nested Claude sessions.
Unset it for the subprocess:
```bash
env -u CLAUDECODE uv run ~/.claude/skills/claude-vault/scripts/summarize_sessions.py
```

**Process a single explicit file** (useful for testing one entry):
```bash
env -u CLAUDECODE uv run ~/.claude/skills/claude-vault/scripts/summarize_sessions.py \
  --sessions /path/to/file.jsonl
```

### Options

| Flag | Description | Config key |
|---|---|---|
| `--sessions FILE` | Process an explicit JSONL file instead of the default pending queue | — |
| `--dry-run, -n` | Preview what would be written without creating notes | — |
| `--model MODEL` | Override model (default: `claude-sonnet-4-6`) | `summarizer.model` |
| `--persist` | Enable SDK session persistence (default off; use for debugging) | `summarizer.persist` |

Uses the Claude Agent SDK — no API key needed, runs via your Max subscription.
Processes up to 5 sessions in parallel (configurable via `summarizer.max_parallel`).
Rebuilds the vault index automatically when done.
Sessions from today whose generated note type is `daily` are skipped (today's daily note
is still being built by the stop hook).

## Vault Doctor

`vault_doctor.py` scans all vault notes for structural issues and repairs them via Claude haiku.

### Issues detected

| Code | Severity | Description |
|---|---|---|
| `MISSING_FRONTMATTER` | error | No YAML frontmatter block |
| `MISSING_FIELD` | error | `date`/`type` missing (all notes); `confidence`/`related` missing (non-daily) |
| `INVALID_TYPE` | error | `type` not in allowed set |
| `INVALID_DATE` | warning | `date` not in YYYY-MM-DD format |
| `ORPHAN_NOTE` | warning | No `[[wikilinks]]` in `related` field |
| `BROKEN_WIKILINK` | warning | Link target not found in vault |
| `FLAT_DAILY` | warning | `Daily/YYYY-MM-DD.md` instead of `Daily/YYYY-MM/DD.md` |

Daily notes (`type: daily` or path under `Daily/`) are exempt from `confidence`, `related`, and orphan checks.

### Running the doctor

```bash
# Scan and report only (no writes)
uv run --no-project ~/.claude/skills/claude-vault/scripts/vault_doctor.py --dry-run

# Scan and repair repairable issues via Claude haiku
env -u CLAUDECODE uv run --no-project ~/.claude/skills/claude-vault/scripts/vault_doctor.py --fix

# Repair up to 20 notes at a time
env -u CLAUDECODE uv run --no-project ~/.claude/skills/claude-vault/scripts/vault_doctor.py --fix --limit 20

# Errors only (skip warnings)
uv run --no-project ~/.claude/skills/claude-vault/scripts/vault_doctor.py --errors-only --dry-run
```

Repairable codes (Claude can fix): `MISSING_FRONTMATTER`, `MISSING_FIELD`, `INVALID_TYPE`, `INVALID_DATE`, `ORPHAN_NOTE`.
Not auto-repairable (require manual fix): `BROKEN_WIKILINK`, `FLAT_DAILY`.

### State tracking

The doctor writes `~/ClaudeVault/doctor_state.json` to avoid reprocessing notes unnecessarily:

| Status | Meaning | Next run behaviour |
|---|---|---|
| `ok` | No issues found | Skipped for 7 days |
| `fixed` | Claude repaired it | Re-checked to confirm |
| `failed` | Claude returned no output | Retried |
| `timeout` | `claude -p` timed out once | Retried once more |
| `needs_review` | Timed out on retry | Skipped — user must fix manually |
| `skipped` | Only non-repairable issues | Skipped indefinitely |

Use `--no-state` to ignore the state file and rescan all notes.

Run `update_index.py` after repairs — it reads `doctor_state.json` and adds a vault health line to `CLAUDE.md`'s Quick Stats section.

## Configuration

All hooks and the summarizer read `~/ClaudeVault/config.yaml` for settings.
Precedence: **hardcoded defaults → config.yaml → CLI args** (last one wins).

A template with all options lives at `~/.claude/skills/claude-vault/templates/config.yaml`.
Copy it to the vault root to get started:

```bash
cp ~/.claude/skills/claude-vault/templates/config.yaml ~/ClaudeVault/config.yaml
```

### Config Sections

```yaml
session_start_hook:
  ai_model: null           # Model for AI note selection (null = disabled)
  max_chars: 4000          # Max context injection characters
  ai_timeout: 25           # AI call timeout in seconds
  recent_days: 3           # Days to look back for recent notes
  debug: false             # Append injected context to debug log in $TMPDIR

session_stop_hook:
  ai_model: null           # Model for AI classification (null = disabled)
  ai_timeout: 25           # AI call timeout in seconds
  auto_summarize: true     # Auto-launch summarizer when pending entries exist

pre_compact_hook:
  lines: 200               # Transcript lines to analyse

summarizer:
  model: claude-sonnet-4-6
  max_parallel: 5          # Concurrent summarization tasks
  transcript_tail_lines: 400
  max_cleaned_chars: 12000
  persist: false           # SDK session persistence (for debugging)

git:
  auto_commit: true        # Auto-commit vault changes after writes
```

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

- Use `vault_common.py` functions for programmatic search.
- Use Obsidian's built-in search for interactive exploration.
- Use `Grep` or `Glob` tools to search vault files directly.

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
`notebooklm generate` for the desired artifact. The research-documentation-agent does this
automatically when NotebookLM is available.

**Requirement**: `pip install notebooklm-py` then `notebooklm login`. Run `notebooklm status`
to check. If unavailable, the vault workflow is completely unaffected.

## Graph Color Groups

The Obsidian graph at `~/ClaudeVault/.obsidian/graph.json` uses color groups to visually categorize nodes by tag.

### Current Color Groups

| Group | Tags | RGB (decimal) |
|---|---|---|
| Projects | `#synknot`, `#fractal-flythroughs`, `#parvitar`, `#parsistant`, `#termflix`, `#parvault`, `#cctmux`, `#parsidion-cc` | 48340 |
| Debugging | `#debugging` | 16733986 |
| Patterns | `#memory`, `#migration`, `#sync` | 5025616 |
| Research | `#research`, `#e2b`, `#qdrant`, `#pkm-apps-comparison` | 10233776 |
| Tools & SDKs | `#claude-code`, `#claude-agent-sdk`, `#claude`, `#rich`, `#mcp`, `#ollama`, `#maturin`, `#redis`, `#websockets`, `#sentry`, `#mermaid-cli`, `#custom-tools`, `#acp-protocol`, `#tool`, `#api`, `#encryption` | 2201331 |
| Languages | `#rust`, `#python`, `#swift`, `#swiftui`, `#typescript`, `#nextjs`, `#react`, `#macos`, `#macos-26`, `#rust-packages` | 16761095 |
| Terminal | `#terminal`, `#par-term`, `#par-term-emu-core-rust` | 38536 |
| Graphics / 3D | `#wgpu`, `#sdf`, `#sdf-terrain`, `#voxel`, `#fractals`, `#mandel`, `#vrm`, `#avatar`, `#face-tracking` | 15277667 |

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
python ~/.claude/skills/claude-vault/scripts/check_graph_coverage.py

# Only report uncovered tags used 2+ times
python ~/.claude/skills/claude-vault/scripts/check_graph_coverage.py --threshold 2

# Machine-readable JSON output
python ~/.claude/skills/claude-vault/scripts/check_graph_coverage.py --json
```

Run this script after a batch of session summarizations or whenever the vault index is rebuilt, then update `graph.json` accordingly.
