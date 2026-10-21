---
name: parsidion
description: >
  Use first when a request references past knowledge or future memory: "have we
  seen this before", "what do we know", "check notes", "prior art", "save to
  the vault", "remember this", "don't forget", "capture this", "the vault",
  "ParsidionVault", or "ClaudeVault". Also for vault-specific queries and upkeep:
  "what changed in the vault since <date>" or "what did the vault know as of
  <date>" (temporal search), "do my notes contradict each other" or "find
  conflicts in the vault" (vault-conflicts), and "connect parsidion to codex or
  gemini" or "wire parsidion into another agent" (multi-agent install). Parsidion
  owns the persistent markdown vault (default ~/ParsidionVault, legacy
  ~/ClaudeVault fallback) for debugging fixes, reusable patterns, research, and
  cross-project context. Use it to search existing knowledge, save new
  discoveries, rebuild the index, run the session summarizer, detect
  contradictions between notes, or configure vault hooks and agent integrations.
  Do not invoke for pure coding/debugging with no memory, notes, or persistence
  angle.
---

# Parsidion vault - Knowledge Management System

> A richly organized Obsidian vault (default `~/ParsidionVault/`, with legacy `~/ClaudeVault/` fallback) that replaces built-in auto memory with structured, searchable, cross-linked knowledge.

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
~/ParsidionVault/        # Or legacy ~/ClaudeVault/ when upgrading
├── CLAUDE.md            # Auto-generated lean index (stats, conventions, recent activity, folder pointers)
├── TAGS.md              # Auto-generated full tag cloud + tag list (for summarizer tag reuse)
├── config.yaml          # Optional — hook/summarizer settings (see Configuration)
├── Daily/               # Session summaries (Daily/YYYY-MM/DD-{username}.md)
├── Projects/            # Per-project context and decisions
├── Languages/           # Python, Rust, TypeScript, Swift, etc.
├── Frameworks/          # Next.js, FastAPI, Textual, Rich, etc.
├── Patterns/            # Design patterns, architectural solutions
├── Debugging/           # Error patterns, diagnostic steps, fixes
├── Tools/               # CLI tools, libraries, packages
├── Research/            # Deep-dive research documents
├── Knowledge/           # General knowledge, concepts, reference material
├── Rules/               # Trigger-scoped behavioral rules (auto-injected on match)
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
| Behavioral rules auto-injected when a prompt/file trigger matches | `Rules/` |
| Per-project decisions, architecture, key paths | `Projects/` |
| Daily session summaries | `Daily/YYYY-MM/DD-{username}.md` (e.g. `Daily/2026-03/13-probello.md`; username from `vault.username`, default `$USER`) |

### Frontmatter Standard

Every note must begin with this YAML block:

```yaml
---
date: YYYY-MM-DD
type: pattern|debugging|research|project|daily|tool|language|framework|knowledge
tags: [tag1, tag2]
project: project-name        # optional - omit if not project-specific
confidence: high|medium|low
provenance: inferred         # optional - explicit|inferred|corrected|observed|imported (default: inferred)
sources: []                  # URLs, file paths, or references
related: []                  # [[wikilinks]] to other vault notes
---
```

Per-field rules (tag casing, confidence, provenance, `related` format): `references/frontmatter-fields.md`.

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

All hooks read `<resolved vault>/config.yaml` for settings. CLI args override config values.

Claude Code installs the full hook set below. Codex runtime hooks are session lifecycle only: native Codex `SessionStart` and `Stop` hooks registered in `~/.codex/hooks.json` when `hooks = true` is enabled in `~/.codex/config.toml`. Gemini runtime hooks are also lifecycle only: `SessionStart` and `SessionEnd` commands registered in `~/.gemini/settings.json` via `--runtime gemini` or `--runtime all`. Gemini runtime hooks are separate from prompt AI backend selection and do not add a Gemini prompt backend; Gemini has no native subagent lifecycle capture in this first pass.

The `connect` / `disconnect` installer verbs wire parsidion into Codex or Gemini in one command — usage: `references/connecting-agents.md`.

| Hook | Behavior | Config section |
|---|---|---|
| **SessionStart** | Loads relevant vault notes as a **compact one-line-per-note index** (title + tags) by default — minimal token usage. `--verbose` flag or `verbose_mode: true` config switches to full note summaries. Optional AI selection via `--ai [MODEL]` or `session_start_hook.ai_model` uses the configured prompt AI backend. | `session_start_hook` |
| **SessionEnd** | Captures learnings from the session transcript (fires once at session end); auto-launches the summarizer when pending entries exist | `session_stop_hook` |
| **PreCompact** | Snapshots current working state so context survives compaction | `pre_compact_hook` |

### Rebuilding the Index

Rebuild after creating, renaming, moving, or deleting notes, on request, or when the index is older than 24 hours — commands: `references/rebuilding-index.md`. Confirm completion to the user.

## Summarizing Pending Sessions

The stop hook queues session transcript paths when it detects learnable content.
Run the summarizer on demand to generate structured vault notes.

Supported transcript locations:
- Claude Code: `~/.claude/projects/**/*.jsonl`
- Codex CLI: `~/.codex/sessions/**/*.jsonl`
- Gemini CLI: `~/.gemini/**/*.jsonl` and `<project>/.gemini/**/*.jsonl`
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

Flags, single-file mode, write-gate, hierarchical summarization, automated backlinks: `references/summarizing-pending-sessions.md`.

## Vault Merge

`vault-merge` detects near-duplicate notes and merges them via the prompt AI backend, updating all bidirectional backlinks. Usage and flags: `references/vault-merge.md`.

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

## References Map

Deeper operational detail lives in `references/`, read when the topic comes up:

| File | Read when |
|---|---|
| `references/temporal-search.md` | temporal `vault-search`: "changed since <date>", "as of <date>" |
| `references/vault-conflicts.md` | finding/resolving contradictions between notes (`vault-conflicts`) |
| `references/vault-merge.md` | merging near-duplicates, batch dedup (`vault-merge`) |
| `references/vault-doctor.md` | scan/repair structural issues, per-rule selection, state tracking |
| `references/configuration.md` | `config.yaml` sections, AI backend settings, programmatic access |
| `references/manual-usage.md` | creating, searching, linking, updating notes; agent triggers |
| `references/summarizing-pending-sessions.md` | summarizer flags, write-gate, hierarchical summarization, backlinks |
| `references/graph-color-groups.md` | updating Obsidian `graph.json` color groups, coverage audit |
| `references/frontmatter-fields.md` | detailed frontmatter per-field rules |
| `references/rebuilding-index.md` | `update_index.py` usage and outputs |
| `references/connecting-agents.md` | `connect`/`disconnect` wiring for Codex or Gemini |
