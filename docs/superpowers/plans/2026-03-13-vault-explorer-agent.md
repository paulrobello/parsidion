# Vault Explorer Agent Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a read-only `vault-explorer` Haiku subagent that searches `~/ClaudeVault/` and returns a synthesized answer + source paths, then update CLAUDE-VAULT.md, SKILL.md, and install.py to use it instead of direct Grep loops in the main session.

**Architecture:** Four independent file changes applied in order: (1) new agent file, (2) CLAUDE-VAULT.md guidance update, (3) SKILL.md guidance update, (4) install.py multi-agent refactor. No new library dependencies — install.py is stdlib-only and the agent is a markdown config file.

**Tech Stack:** Python stdlib (install.py), Markdown (agent + guidance files), Claude Haiku model.

**Spec:** `docs/superpowers/specs/2026-03-13-vault-explorer-agent-design.md`

---

## Chunk 1: Create the vault-explorer agent

### Task 1: Write `agents/vault-explorer.md`

**Files:**
- Create: `agents/vault-explorer.md`

**Background:** Agent files in this repo are markdown files with YAML frontmatter (`name`, `description`, `model`, `color`) followed by the agent's instructions. They are copied verbatim to `~/.claude/agents/` by `install.py`. The existing agent (`agents/research-documentation-agent.md`) uses `model: sonnet` and `color: pink`. This agent uses `model: haiku` and `color: purple`. The agent must be read-only — no vault writes, no `update_index.py` calls.

- [ ] **Step 1: Create the agent file**

Create `agents/vault-explorer.md` with this exact content:

```markdown
---
name: vault-explorer
description: >
  Use when you need to search ~/ClaudeVault/ for relevant notes, debugging
  solutions, reusable patterns, or prior art from other projects.
  Accepts a natural language query. Returns a synthesized answer and source
  file paths so the caller can do targeted deep-dives if needed.

  Trigger on: "search the vault for X", "check the vault", "have we seen
  this before", "find vault notes about X", "check for prior art on X",
  "what do we know about X", any vault search request.

  Do NOT trigger for vault writes, index rebuilds, or summarization — those
  belong to the research-documentation-agent and parsidion skill.
model: haiku
color: purple
---

You are a read-only vault search specialist. Your only job is to search
`~/ClaudeVault/` for notes relevant to the user's query, synthesize what
you find, and return it in the standard format below.

**You must not write any files, create vault notes, or run update_index.py.**

## Search Procedure

1. **Orient:** Read `~/ClaudeVault/CLAUDE.md` (the vault index) to understand
   what notes exist and which folders are relevant.

2. **Extract signals:** From the query, identify the key search terms —
   exception class name, package/library name, feature keyword, or concept.
   Use the most distinctive term as the primary signal.

3. **Search by priority folder** (use the Grep tool with `path` and `glob: **/*.md`):
   Follow the folder priority order from the table below for the query type.
   Search the highest-priority folder first; widen to lower-priority folders
   only if the top folder yields fewer than 3 candidate files.

   | Query type | Folders, in priority order |
   |---|---|
   | Error / exception / bug | `~/ClaudeVault/Debugging/` → `~/ClaudeVault/Frameworks/` → `~/ClaudeVault/Languages/` |
   | Feature / pattern / integration | `~/ClaudeVault/Patterns/` → `~/ClaudeVault/Frameworks/` → `~/ClaudeVault/Projects/` |
   | Cross-project / prior art | `~/ClaudeVault/Projects/` → `~/ClaudeVault/Patterns/` |
   | Library / tool / CLI | `~/ClaudeVault/Tools/` → `~/ClaudeVault/Frameworks/` |
   | Research / concepts | `~/ClaudeVault/Research/` → all folders |

4. **Rank and read:** Rank candidate files by: (a) folder priority position
   (higher-priority folder = ranked first), then (b) frequency of the search
   signal in the file (count of occurrences — more = ranked higher). Read the
   top 5 ranked files using the Read tool.

5. **Synthesize and return** in the exact format below.

## Return Format

Always respond with exactly these two sections and nothing else:

```
## Answer
[Direct answer to the query in 3-7 sentences, synthesized from vault notes.
 If the vault has no relevant information, write exactly:
 "No relevant vault notes found."]

## Sources
- /absolute/path/to/note.md — one-line note on why this file is relevant
- /absolute/path/to/other.md — one-line note on why this file is relevant
```

Use absolute paths (starting with `/Users/...` or `~/ClaudeVault/...` expanded
to the real path) so the caller can `Read` them directly.

If the vault has no relevant information, your full response must be:

```
## Answer
No relevant vault notes found. Consider dispatching the
`research-documentation-agent` to research this topic externally and save
findings to the vault.

## Sources
(none)
```
```

- [ ] **Step 2: Verify frontmatter is valid**

Read the file back and confirm:
- Frontmatter opens and closes with `---`
- `model: haiku` (short alias, not a full model ID)
- `color: purple`
- `name: vault-explorer`
- No trailing whitespace on frontmatter lines

- [ ] **Step 3: Commit**

```bash
git add agents/vault-explorer.md
git commit -m "feat(agent): add vault-explorer Haiku agent for read-only vault search"
```

---

## Chunk 2: Update CLAUDE-VAULT.md

### Task 2: Replace Grep-based vault search guidance in CLAUDE-VAULT.md

**Files:**
- Modify: `CLAUDE-VAULT.md`

**Background:** `CLAUDE-VAULT.md` is installed to `~/.claude/CLAUDE-VAULT.md` and `@import`ed into `CLAUDE.md` so it loads every session. It currently has three sections that instruct direct Grep searches: "Debugging: Search Before You Diagnose" (steps 2-3), "Implementation: Check for Prior Art" (steps 1-3 with a Grep example), and "Efficient Vault Search" (a table of Grep patterns). All three change to vault-explorer agent dispatch. Step numbers that shift must be renumbered so the list stays consistent.

- [ ] **Step 1: Replace the Debugging section steps 2-5**

The current content in `CLAUDE-VAULT.md` under `## Debugging: Search Before You Diagnose`:

```markdown
1. Extract the key signal — exception class name, package name, or the most
   distinctive phrase from the error message (e.g. `IntegrityError`, `pydantic_settings`,
   `query_one`).
2. Search `~/ClaudeVault/Debugging/` first:
   ```
   Grep: path=~/ClaudeVault/Debugging, glob=**/*.md, pattern=<signal>
   ```
3. Widen to `Frameworks/`, `Languages/`, `Projects/` if nothing found in `Debugging/`.
4. **Found a match?** Apply the documented fix. Update the note if you learn
   something new about the problem.
5. **No match?** Diagnose and solve it, then save the solution so future sessions
   benefit (see "Saving Solutions" below).
```

Replace steps 2-5 with:

```markdown
1. Extract the key signal — exception class name, package name, or the most
   distinctive phrase from the error message (e.g. `IntegrityError`, `pydantic_settings`,
   `query_one`).
2. Dispatch the `vault-explorer` agent with the signal as the query.
3. **Answer returned?** Apply the documented fix. `Read` specific source files
   from the Sources section only if you need more depth.
4. **"No relevant vault notes found"?** Diagnose and solve it, then dispatch the
   `research-documentation-agent` to save the solution (see "Saving Solutions" below).
```

- [ ] **Step 2: Replace the Implementation section steps 1-5**

The current content under `## Implementation: Check for Prior Art`:

```markdown
1. Search `~/ClaudeVault/Patterns/` for relevant design patterns.
2. Search `~/ClaudeVault/Frameworks/` and `Languages/` for the specific stack.
3. Search `~/ClaudeVault/Projects/` for implementations in other projects:
   ```
   Grep: path=~/ClaudeVault/Projects, glob=**/*.md, pattern=<feature-keyword>
   ```
4. **Found prior art?** Read it. Reuse and adapt — a proven implementation from
   another project beats a fresh one every time.
5. Check the `sources` field of matched notes for relevant code paths and references.
```

Replace with:

```markdown
1. Dispatch the `vault-explorer` agent with the feature keyword as the query.
2. **Answer returned?** Reuse and adapt — a proven implementation from another
   project beats a fresh one every time. `Read` specific source files from the
   Sources section for implementation details and the `sources` frontmatter field
   for referenced code paths.
3. **"No relevant vault notes found"?** Implement it, then dispatch the
   `research-documentation-agent` to save the pattern (see "Saving Solutions" below).
```

- [ ] **Step 3: Replace the Efficient Vault Search section**

The current section:

```markdown
## Efficient Vault Search

Always use the **Grep tool** with `path: ~/ClaudeVault` and `glob: **/*.md`
rather than shell commands.

| Goal | Pattern to search |
|---|---|
| Error by type | Exception class name (e.g. `IntegrityError`) |
| Error by package | Package/library name (e.g. `sqlalchemy`, `pydantic`) |
| Pattern by keyword | Feature keyword (e.g. `websocket`, `oauth2`, `retry`) |
| Cross-project | Search `Projects/` subfolder only |
| By note type | `type: debugging` or `type: pattern` in frontmatter |
```

Replace with:

```markdown
## Efficient Vault Search

Dispatch the **`vault-explorer` agent** with a natural language query describing
what you are looking for. The agent searches all relevant vault folders, ranks
results, reads the top matches, and returns:

- **`## Answer`** — a synthesized answer you can use immediately
- **`## Sources`** — absolute paths to matching notes for targeted deep-dives

**If the answer is sufficient:** proceed without reading any source files.
**If you need more depth:** `Read` specific files from the Sources section.
**If "No relevant vault notes found":** dispatch the `research-documentation-agent`
to research externally and save findings to the vault.
```

- [ ] **Step 4: Verify the file reads coherently end-to-end**

Read the full `CLAUDE-VAULT.md` and confirm:
- No remaining `Grep:` instructions in the Debugging/Implementation/Search sections
- Step numbering is consistent (no gaps or duplicates)
- The "Saving Solutions" section is unchanged
- The "Vault Organization" section is unchanged

- [ ] **Step 5: Commit**

```bash
git add CLAUDE-VAULT.md
git commit -m "docs(vault): replace direct Grep guidance with vault-explorer agent dispatch"
```

---

## Chunk 3: Update SKILL.md

### Task 3: Replace Grep-based guidance in SKILL.md

**Files:**
- Modify: `skills/parsidion/SKILL.md`

**Background:** `SKILL.md` has a different structure than `CLAUDE-VAULT.md`. The sections to update are: (a) "Debugging: Search Before You Diagnose" — contains `grep` shell examples; (b) "Implementation: Check for Prior Art" — contains `grep` shell examples; (c) "Efficient Vault Search" table; (d) "The Vault-First Loop" flowchart code block. The Grep examples in SKILL.md use shell `grep` syntax (not the Grep tool syntax used in CLAUDE-VAULT.md).

- [ ] **Step 1: Replace the Debugging section**

Locate the section `### Debugging: Search Before You Diagnose` in `skills/parsidion/SKILL.md`. The current steps 2-3 contain:

```markdown
2. **Search `Debugging/`** first:
   ```bash
   # Use the Grep tool with path ~/ClaudeVault and glob **/*.md, e.g.:
   grep -r "ImportError" ~/ClaudeVault/Debugging/ --include="*.md" -l
   grep -r "sqlalchemy" ~/ClaudeVault/Debugging/ --include="*.md" -l
   ```
3. **Widen the search** to `Frameworks/`, `Languages/`, and `Projects/` — the fix may live there
4. **If you find a match**: apply the documented solution; update the note if you discover new nuance
5. **If no match**: diagnose, solve, then save the solution so future sessions benefit
```

Replace steps 2-5 with:

```markdown
2. **Dispatch the `vault-explorer` agent** with the signal as the query.
3. **Answer returned?** Apply the documented solution; update the note if you discover new nuance. `Read` specific files from the Sources section only if you need more depth.
4. **"No relevant vault notes found"?** Diagnose, solve, then save the solution so future sessions benefit.
```

- [ ] **Step 2: Replace the Implementation section**

Locate `### Implementation: Check for Prior Art`. The current steps 1-3 contain:

```markdown
1. **Search `Patterns/`** for design patterns relevant to the feature
2. **Search `Frameworks/` and `Languages/`** for the specific stack
3. **Search `Projects/`** for implementations in other projects:
   ```bash
   grep -r "websocket" ~/ClaudeVault/Projects/ --include="*.md" -l
   grep -r "authentication" ~/ClaudeVault/ --include="*.md" -l
   ```
4. **Examine `sources`** in matched notes for code references and file paths
5. **Reuse and adapt** — a proven implementation beats a fresh one every time
```

Replace steps 1-5 with:

```markdown
1. **Dispatch the `vault-explorer` agent** with the feature keyword as the query.
2. **Answer returned?** Reuse and adapt — a proven implementation beats a fresh one every time. `Read` specific files from the Sources section for details; check the `sources` frontmatter field for code references.
3. **"No relevant vault notes found"?** Implement it, then save the pattern so future sessions benefit.
```

- [ ] **Step 3: Replace the Efficient Vault Search table**

Locate `### Efficient Vault Search`. The current content:

```markdown
| Goal | Approach |
|---|---|
| Find by error type | Grep for exception class name across all vault folders |
| Find by library/framework | Grep for the package name in `Frameworks/` or `Languages/` |
| Find by tag | Check vault `CLAUDE.md` index for tag listings |
| Find cross-project patterns | Search `Projects/`, follow `related` wikilinks |
| Find by note type | Grep for `type: debugging` or `type: pattern` in frontmatter |

Prefer the `Grep` tool with `path: ~/ClaudeVault` and `glob: **/*.md` over shell commands.
```

Replace with:

```markdown
Dispatch the **`vault-explorer` agent** with a natural language query. The agent
searches all relevant vault folders using the appropriate Grep strategy, ranks
matches, reads the top files, and returns:

- **`## Answer`** — synthesized answer ready to use
- **`## Sources`** — absolute paths for targeted `Read` calls if deeper context is needed

**No results?** Dispatch the `research-documentation-agent` to research externally
and save findings to the vault.
```

- [ ] **Step 4: Update the Vault-First Loop flowchart**

Locate `### The Vault-First Loop` section. The current flowchart:

```markdown
```
Error or implementation question
  → Search vault
    → Found? Apply / adapt solution → Update note with new learnings
    → Not found? Solve it → Save to vault → Rebuild index
```
```

Replace with:

```markdown
```
Error or implementation question
  → Dispatch vault-explorer agent
    → Found? Apply / adapt solution → Update note with new learnings
    → Not found? Solve it → Dispatch research-documentation-agent to save
```
```

- [ ] **Step 5: Verify the file reads coherently end-to-end**

Read the full `skills/parsidion/SKILL.md` and confirm:
- No remaining `grep` shell examples in the Debugging/Implementation sections
- No remaining "Prefer the Grep tool" instructions in the Efficient Vault Search section
- The Vault-First Loop flowchart references the vault-explorer agent
- All other sections (Vault Structure, Conventions, Frontmatter, Hooks, etc.) are unchanged

- [ ] **Step 6: Commit**

```bash
git add skills/parsidion/SKILL.md
git commit -m "docs(skill): replace direct Grep guidance with vault-explorer agent dispatch"
```

---

## Chunk 4: Update install.py

### Task 4: Refactor install.py to support multiple agents

**Files:**
- Modify: `install.py`

**Background:** `install.py` currently has `AGENT_SRC: Path` (a single constant), `install_agent()` (installs one file), and references to `AGENT_SRC` in `uninstall()`, the printout block, and the call site in `install()`. The refactor renames the constant to a list `AGENT_SRCS: list[Path]`, renames the function to `install_agents()`, and updates all 7 reference sites. Install.py is stdlib-only — no new imports needed.

The 7 code paths to touch (all in `install.py`):
1. Module-level constant `AGENT_SRC` → `AGENT_SRCS`
2. `install_agent()` definition → `install_agents()`
3. Call site in `install()` (line ~758)
4. Existence guard in `install()` (line ~757)
5. `uninstall()` function agent block
6. Installation plan printout (line ~724-725)
7. `--skip-agent` help text + module docstring

- [ ] **Step 1: Replace the module-level constant**

Find:
```python
AGENT_SRC: Path = REPO_ROOT / "agents" / "research-documentation-agent.md"
```

Replace with:
```python
AGENT_SRCS: list[Path] = [
    REPO_ROOT / "agents" / "research-documentation-agent.md",
    REPO_ROOT / "agents" / "vault-explorer.md",
]
```

- [ ] **Step 2: Replace `install_agent()` with `install_agents()`**

Find:
```python
def install_agent(
    claude_dir: Path,
    dry_run: bool = False,
) -> None:
    """Copy the research agent to ~/.claude/agents/."""
    agents_dir = claude_dir / "agents"
    dest = agents_dir / AGENT_SRC.name

    _step(f"Install agent: {AGENT_SRC.name} → {agents_dir}/", dry_run=dry_run)

    if not dry_run:
        agents_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(AGENT_SRC, dest)
```

Replace with:
```python
def install_agents(
    claude_dir: Path,
    dry_run: bool = False,
) -> None:
    """Copy all agents to ~/.claude/agents/, skipping missing sources with a warning."""
    agents_dir = claude_dir / "agents"
    if not dry_run:
        agents_dir.mkdir(parents=True, exist_ok=True)
    for agent_src in AGENT_SRCS:
        if not agent_src.exists():
            _warn(f"Agent source not found: {agent_src} — skipping")
            continue
        dest = agents_dir / agent_src.name
        _step(f"Install agent: {agent_src.name} → {agents_dir}/", dry_run=dry_run)
        if not dry_run:
            shutil.copy2(agent_src, dest)
```

- [ ] **Step 3: Update the call site in `install()`**

Find:
```python
    # 2. Install agent
    if not args.skip_agent:
        if AGENT_SRC.exists():
            install_agent(claude_dir, dry_run=dry_run)
        else:
            _warn(f"Agent source not found: {AGENT_SRC} — skipping")
```

Replace with:
```python
    # 2. Install agents
    if not args.skip_agent:
        install_agents(claude_dir, dry_run=dry_run)
```

Note: existence checking and per-agent warnings are now handled inside `install_agents()` — the outer guard is no longer needed.

- [ ] **Step 4: Update the installation plan printout**

Find:
```python
    if not args.skip_agent:
        print(f"  {dim('Install agent:')} {claude_dir / 'agents' / AGENT_SRC.name}")
```

Replace with:
```python
    if not args.skip_agent:
        for agent_src in AGENT_SRCS:
            print(f"  {dim('Install agent:')} {claude_dir / 'agents' / agent_src.name}")
```

- [ ] **Step 5: Update `uninstall()` to remove all agents**

Find in `uninstall()`:
```python
    agent_dest = claude_dir / "agents" / AGENT_SRC.name
    ...
    if agent_dest.exists():
        _step(f"Remove agent: {agent_dest}", dry_run=dry_run)
        if not dry_run:
            agent_dest.unlink()
    else:
        _warn(f"Agent not found: {agent_dest}")
```

Replace with:
```python
    for agent_src in AGENT_SRCS:
        agent_dest = claude_dir / "agents" / agent_src.name
        if agent_dest.exists():
            _step(f"Remove agent: {agent_dest}", dry_run=dry_run)
            if not dry_run:
                agent_dest.unlink()
        else:
            _warn(f"Agent not found: {agent_dest}")
```

- [ ] **Step 6: Update `--skip-agent` help text**

Find:
```python
    parser.add_argument(
        "--skip-agent",
        action="store_true",
        help="Do not install the research agent",
    )
```

Replace with:
```python
    parser.add_argument(
        "--skip-agent",
        action="store_true",
        help="Do not install any agents",
    )
```

- [ ] **Step 7: Update the module docstring**

Find in the module docstring at the top of `install.py`:
```
    --skip-agent        Do not install the research agent
```

Replace with:
```
    --skip-agent        Do not install any agents
```

- [ ] **Step 8: Verify with dry-run**

Run the installer in dry-run mode and confirm both agents appear in the plan:

```bash
uv run install.py --dry-run --yes
```

Expected output includes:
```
  Install agent: research-documentation-agent.md → ~/.claude/agents/
  Install agent: vault-explorer.md → ~/.claude/agents/
```

- [ ] **Step 9: Run existing tests**

```bash
uv run pytest tests/ -v
```

Expected: all existing `test_vault_common.py` tests pass (install.py changes do not touch vault_common).

- [ ] **Step 10: Commit**

```bash
git add install.py
git commit -m "feat(install): support multiple agents with AGENT_SRCS list"
```

---

## Chunk 5: Sync installed files

### Task 5: Install updated files to `~/.claude/`

**Files:** none in repo (this task copies to live install location)

**Background:** After editing source files, the live `~/.claude/` copies must be updated. The CLAUDE.md at the project root has a "Making Changes" section with the canonical sync commands. For a full reinstall, `uv run install.py --force --yes` is the authoritative command.

- [ ] **Step 1: Run the installer**

```bash
uv run install.py --force --yes
```

Expected: installer copies `research-documentation-agent.md`, `vault-explorer.md`, and all skill files to `~/.claude/`. Both agent lines appear in the output.

- [ ] **Step 2: Confirm vault-explorer is installed**

```bash
ls ~/.claude/agents/
```

Expected: both `research-documentation-agent.md` and `vault-explorer.md` are present.

- [ ] **Step 3: Confirm CLAUDE-VAULT.md is installed**

```bash
head -20 ~/.claude/CLAUDE-VAULT.md
```

Expected: the file opens with `# ClaudeVault — Always-On Guidance` and the Debugging section shows vault-explorer agent dispatch (no `Grep:` instruction).

- [ ] **Step 4: Commit**

Nothing to commit — this task only writes to `~/.claude/` (not tracked in this repo).

---

## Final Verification

- [ ] `agents/vault-explorer.md` exists with `model: haiku`, `color: purple`
- [ ] `CLAUDE-VAULT.md` has no `Grep:` instructions in Debugging/Implementation/Search sections
- [ ] `skills/parsidion/SKILL.md` has no `grep` shell examples in those sections
- [ ] `install.py` uses `AGENT_SRCS: list[Path]` and `install_agents()`
- [ ] `uv run install.py --dry-run --yes` shows both agents
- [ ] `uv run pytest tests/ -v` passes
- [ ] `~/.claude/agents/vault-explorer.md` exists after reinstall
