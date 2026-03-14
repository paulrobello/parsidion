# Vault Explorer Agent — Design Spec

**Date:** 2026-03-13
**Status:** Approved

---

## Problem

Every vault search runs inside the main Claude session's context window. A typical lookup involves multiple Grep calls across vault subfolders, followed by several Read calls for matching files. This consumes context budget on search mechanics rather than on the actual work the user requested.

---

## Solution

A dedicated `vault-explorer` subagent (Haiku model) that accepts a natural language query, performs all Grep/Read work in its own context, and returns a compact two-section result to the main session. The main session's context grows by one agent result block instead of N Grep results + M Read results.

**Escalation chain (main session decides — not auto-dispatched):**
vault-explorer → (no results) → main session dispatches research-documentation-agent

---

## Components

### 1. `agents/vault-explorer.md`

- **Model:** `haiku` (short alias, consistent with existing agent conventions)
- **Color:** `purple`
- **Role:** Read-only vault search specialist. No writes, no index rebuilds.

**Behaviour:**
1. Check `~/ClaudeVault/CLAUDE.md` index to orient on available notes. No staleness check — the index check is informational only; actual search uses Grep directly on vault files, so index freshness does not affect correctness.
2. Extract key signals from the query (error class, package name, feature keyword).
3. Run targeted Grep searches across relevant subfolders per the folder search strategy table below.
4. Rank results: files in the highest-priority folder from the strategy table rank first; within a folder, files are ranked by frequency of the search signal (most occurrences first). Read the top 5 ranked files.
5. Synthesize and return in the standard two-section format.

**Return format:**
```
## Answer
[Direct synthesized answer to the query — 3-7 sentences.
 "No relevant vault notes found." if nothing matches.]

## Sources
- ~/ClaudeVault/Debugging/foo.md — one-line relevance note
- ~/ClaudeVault/Patterns/retry-pattern.md — one-line relevance note
```

**No-match response (the main session decides what to do next):**
> "No relevant vault notes found. Consider dispatching the `research-documentation-agent` to research this topic externally and save findings to the vault."

**Folder search strategy** (baked into the agent's instructions):

| Query type | Folders searched, in priority order |
|---|---|
| Error / exception / bug | `Debugging/` → `Frameworks/` → `Languages/` |
| Feature / pattern / integration | `Patterns/` → `Frameworks/` → `Projects/` |
| Cross-project / prior art | `Projects/` → `Patterns/` |
| Library / tool / CLI | `Tools/` → `Frameworks/` |
| Research / concepts | `Research/` → all |

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
> - **"No relevant vault notes found"?** Dispatch the `research-documentation-agent` for external research and vault note creation.

The Grep strategy table (currently in this section) moves inside the vault-explorer agent's own instructions.

---

### 3. `skills/claude-vault/SKILL.md` — updated search guidance

The following specific sections change (SKILL.md has a different structure from CLAUDE-VAULT.md):

- **"Debugging: Search Before You Diagnose" section** — steps 2-3 (the `grep` shell examples across `Debugging/`, then widening to `Frameworks/` etc.) are replaced with: "Dispatch the `vault-explorer` agent with the error signal as the query."
- **"Implementation: Check for Prior Art" section** — steps 1-3 (the `grep` shell examples across `Patterns/`, `Frameworks/`, `Projects/`) are replaced with: "Dispatch the `vault-explorer` agent with the feature keyword as the query."
- **"Efficient Vault Search" table** — the entire table (Goal / Pattern to search) is replaced with the same vault-explorer dispatch guidance as in CLAUDE-VAULT.md above.
- **"The Vault-First Loop" flowchart** — the `grep` step inside the flowchart is updated to reference the vault-explorer agent dispatch instead.

---

### 4. `install.py` — multi-agent support

`AGENT_SRC: Path` → `AGENT_SRCS: list[Path]`:

```python
AGENT_SRCS: list[Path] = [
    REPO_ROOT / "agents" / "research-documentation-agent.md",
    REPO_ROOT / "agents" / "vault-explorer.md",
]
```

All of the following code paths must be updated to iterate `AGENT_SRCS` rather than referencing the single `AGENT_SRC`:

1. **`install_agent()` → `install_agents()`** — iterates list, copies each file to `~/.claude/agents/`
2. **`uninstall()` function** — currently builds `agent_dest = claude_dir / "agents" / AGENT_SRC.name`; must iterate `AGENT_SRCS` and remove each
3. **Installation plan printout** — currently prints a single `Install agent:` line; must print one line per agent in `AGENT_SRCS`
4. **`--skip-agent` help text** — update from "Do not install the research agent" to "Do not install any agents"
5. **Module docstring** (top of `install.py`) — update `--skip-agent` description to match
6. **Call site in `install()`** — the `install_agent(claude_dir, dry_run=dry_run)` call must become `install_agents(claude_dir, dry_run=dry_run)`
7. **Agent source existence guard** — the `if AGENT_SRC.exists(): ... _warn(f"... {AGENT_SRC} ...")` block must iterate `AGENT_SRCS` and check/warn for each source path individually

---

## Files Changed

| File | Change type |
|---|---|
| `agents/vault-explorer.md` | **New** |
| `CLAUDE-VAULT.md` | Updated — Debugging, Implementation, and Efficient Vault Search sections |
| `skills/claude-vault/SKILL.md` | Updated — Debugging, Implementation, Efficient Vault Search sections, and Vault-First Loop flowchart |
| `install.py` | Updated — multi-agent support across install, uninstall, printout, help text, and module docstring |

---

## Out of Scope

- The vault-explorer agent does not write notes, save findings, or rebuild the index.
- The vault-explorer agent does not replace the `session_start_hook.py` AI selection mode.
- No changes to hook scripts, summarizer, or vault structure.
- The main session does not auto-dispatch `research-documentation-agent` on no-match — it follows the updated CLAUDE-VAULT.md guidance and decides.
