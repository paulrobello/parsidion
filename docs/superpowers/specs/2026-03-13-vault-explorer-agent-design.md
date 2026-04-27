# Vault Explorer Agent — Design Spec

**Date:** 2026-03-13
**Status:** Implemented

---

## Table of Contents
- [Problem](#problem)
- [Solution](#solution)
- [Components](#components)
- [Files Changed](#files-changed)
- [Out of Scope](#out-of-scope)

---

## Problem

Every vault search runs inside the main Claude session's context window. A typical lookup involves multiple Grep calls across vault subfolders, followed by several Read calls for matching files. This consumes context budget on search mechanics rather than on the actual work the user requested.

---

## Solution

A dedicated `vault-explorer` subagent (Haiku model) that accepts a natural language query, performs all search/Read work in its own context, and returns a compact two-section result to the main session. The main session's context grows by one agent result block instead of N Grep results + M Read results.

**Escalation chain (main session decides — not auto-dispatched):**
vault-explorer → (no results) → main session dispatches research-agent

---

## Components

### 1. `agents/vault-explorer.md`

- **Model:** `haiku` (short alias, consistent with existing agent conventions)
- **Color:** `purple`
- **Role:** Read-only vault search specialist. No writes, no index rebuilds.

**Search Procedure (cascading):**

1. **Semantic search (if available):** Run `vault_search.py` with the full natural-language query. If 3+ results with score ≥ 0.35 are returned, use those paths and skip to step 6.

2. **Metadata search:** Infer filters from the query (folder, type, project, tag, recency) and run `vault-search` with those filters. If 3+ results, use those paths and skip to step 6.

3. **Orient:** Read `~/ClaudeVault/CLAUDE.md` (the vault index) to understand what notes exist and which folders are relevant.

4. **Extract signals:** From the query, identify the key search terms — exception class name, package/library name, feature keyword, or concept. Use the most distinctive term as the primary signal.

5. **Search by priority folder:** Run Grep searches across relevant subfolders per the folder search strategy table below. Widen to lower-priority folders only if the top folder yields 0 or 1 candidate files. Stop widening when you have 3+ files.

6. **Rank and read:** Rank candidate files by: (a) semantic score if available, (b) folder priority position, (c) frequency of the search signal in the file. Read the top 5 ranked files.

7. **Synthesize and return** in the standard two-section format.

**Return format:**
```
## Answer
[Direct synthesized answer to the query — 3-7 sentences.
 "No relevant vault notes found." if nothing matches.]

## Sources
- /absolute/path/to/note.md — one-line relevance note
- /absolute/path/to/other.md — one-line relevance note
```

> **Important:** Use absolute paths only — expand `~` to the full home directory path. Never output tilde paths.

**No-match response (the main session decides what to do next):**
```
## Answer
No relevant vault notes found. Consider dispatching the
`research-agent` to research this topic externally and save
findings to the vault.

## Sources
(none)
```

**Folder search strategy** (baked into the agent's instructions):

| Query type | Folders searched, in priority order |
|---|---|
| Error / exception / bug | `~/ClaudeVault/Debugging/` → `~/ClaudeVault/Frameworks/` → `~/ClaudeVault/Languages/` |
| Feature / pattern / integration | `~/ClaudeVault/Patterns/` → `~/ClaudeVault/Frameworks/` → `~/ClaudeVault/Projects/` |
| Cross-project / prior art | `~/ClaudeVault/Projects/` → `~/ClaudeVault/Patterns/` |
| Library / tool / CLI | `~/ClaudeVault/Tools/` → `~/ClaudeVault/Frameworks/` |
| Research / concepts | `~/ClaudeVault/Research/` → all folders |

---

### 2. `CLAUDE-VAULT.md` — updated search guidance

**Debugging section** — step 2 changes from:
> Search `~/ClaudeVault/Debugging/` first with Grep

To:
> Dispatch the `vault-explorer` agent with the error signal as the query.

**Debugging section** — step 3 (widen search) is removed; the vault-explorer agent handles folder widening internally.

**Implementation section** — steps 1-3 (Grep across Patterns, Frameworks, Projects) change to:
> Dispatch the `vault-explorer` agent with the feature keyword as the query.

**"Efficient Vault Search" section** — the Grep table is replaced with:
> Dispatch the `vault-explorer` agent with a natural language query describing what you're looking for.
>
> - **Answer returned?** Proceed. `Read` specific source files only if you need more depth than the Answer section provides.
> - **"No relevant vault notes found"?** Dispatch the `research-agent` for external research and vault note creation.

The Grep strategy table (currently in this section) moves inside the vault-explorer agent's own instructions.

---

### 3. `skills/parsidion/SKILL.md` — updated search guidance

The following specific sections change (SKILL.md has a different structure from CLAUDE-VAULT.md):

- **"Debugging: Search Before You Diagnose" section** — steps 2-3 (the `grep` shell examples across `Debugging/`, then widening to `Frameworks/` etc.) are replaced with: "Dispatch the `vault-explorer` agent with the error signal as the query."
- **"Implementation: Check for Prior Art" section** — steps 1-3 (the `grep` shell examples across `Patterns/`, `Frameworks/`, `Projects/`) are replaced with: "Dispatch the `vault-explorer` agent with the feature keyword as the query."
- **"Efficient Vault Search" table** — the entire table (Goal / Pattern to search) is replaced with the same vault-explorer dispatch guidance as in CLAUDE-VAULT.md above.
- **"The Vault-First Loop" flowchart** — the `grep` step inside the flowchart is updated to reference the vault-explorer agent dispatch instead.

---

### 4. `install.py` — multi-agent support

`AGENT_SRCS: list[Path]` contains all agents to install:

```python
AGENT_SRCS: list[Path] = [
    REPO_ROOT / "agents" / "research-agent.md",
    REPO_ROOT / "agents" / "vault-explorer.md",
    REPO_ROOT / "agents" / "project-explorer.md",
]
```

> **Note:** Additional agents may be added over time. The installer iterates this list for all install/uninstall operations.

**Implementation details:**
- `install_agents()` iterates the list, copying each file to `~/.claude/agents/`
- `uninstall()` iterates the list to remove each agent
- Installation plan printout shows one line per agent
- `--skip-agent` flag prevents installation of all agents
- Agent source existence guards check each source path individually

---

## Files Changed

| File | Change type |
|---|---|
| `agents/vault-explorer.md` | **New** |
| `CLAUDE-VAULT.md` | Updated — Debugging, Implementation, and Efficient Vault Search sections |
| `skills/parsidion/SKILL.md` | Updated — Debugging, Implementation, Efficient Vault Search sections, and Vault-First Loop flowchart |
| `install.py` | Updated — multi-agent support across install, uninstall, printout, help text, and module docstring |

---

## Out of Scope

- The vault-explorer agent does not write notes, save findings, or rebuild the index.
- The vault-explorer agent does not replace the `session_start_hook.py` AI selection mode.
- No changes to hook scripts, summarizer, or vault structure.
- The main session does not auto-dispatch `research-agent` on no-match — it follows the updated CLAUDE-VAULT.md guidance and decides.
