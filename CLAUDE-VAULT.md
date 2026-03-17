# ClaudeVault — Always-On Guidance

> This file is loaded every session. The rules here are unconditional — they fire
> regardless of whether the parsidion-cc skill is explicitly invoked.

## The Vault-First Rule

**Before debugging, before web search, before writing new code — check the vault.**

The vault at `~/ClaudeVault/` accumulates solutions, patterns, and decisions across
every project and session. Checking it first saves time and avoids re-solving
problems that have already been solved.

## Debugging: Search Before You Diagnose

When you encounter an error, exception, or unexpected behavior:

1. Extract the key signal — exception class name, package name, or the most
   distinctive phrase from the error message (e.g. `IntegrityError`, `pydantic_settings`,
   `query_one`).
2. Dispatch the `vault-explorer` agent with the signal as the query.
3. **Answer returned?** Apply the documented fix. Update the note if you learn
   something new about the problem. `Read` specific source files from the Sources
   section only if you need more depth.
4. **"No relevant vault notes found"?** Diagnose and solve it, then dispatch the
   `research-agent` to save the solution (see "Saving Solutions" below).

## Implementation: Check for Prior Art

Before writing non-trivial code for any feature, integration, or pattern:

1. Dispatch the `vault-explorer` agent with the feature keyword as the query.
2. **Answer returned?** Reuse and adapt — a proven implementation from another
   project beats a fresh one every time. `Read` specific source files from the
   Sources section for implementation details and the `sources` frontmatter field
   for referenced code paths.
3. **"No relevant vault notes found"?** Implement it, then dispatch the
   `research-agent` to save the pattern (see "Saving Solutions" below).

## Efficient Vault Search

Dispatch the **`vault-explorer` agent** with a natural language query describing
what you are looking for. The agent searches all relevant vault folders, ranks
results, reads the top matches, and returns:

- **`## Answer`** — a synthesized answer you can use immediately
- **`## Sources`** — absolute paths to matching notes for targeted deep-dives

**If the answer is sufficient:** proceed without reading any source files.
**If you need more depth:** `Read` specific files from the Sources section.
**If "No relevant vault notes found":** dispatch the `research-agent`
to research externally and save findings to the vault.

## Vault Organization

**Subfolder rule**: when 3 or more notes share a common subject prefix, move them
into a named subfolder (e.g. `Research/fastapi-middleware/basics.md` instead of
`Research/fastapi-middleware-basics.md`). Drop the redundant prefix from filenames
inside the subfolder, update all wikilinks, and rebuild the index. Only one level
of subfolder is allowed — never nest subfolders within subfolders.

After any note reorganization (create, rename, move, delete) rebuild the index:
```bash
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/update_index.py
```

## Saving Solutions

After solving a non-obvious problem or implementing a reusable pattern, save it:

| What you solved | Save to |
|---|---|
| Error / bug fix | `~/ClaudeVault/Debugging/` |
| Reusable code pattern | `~/ClaudeVault/Patterns/` |
| Framework-specific fix | `~/ClaudeVault/Frameworks/` |
| Language-specific knowledge | `~/ClaudeVault/Languages/` |
| CLI tool or package notes | `~/ClaudeVault/Tools/` |
| External research findings | `~/ClaudeVault/Research/` |
| Architectural decision | `~/ClaudeVault/Projects/<project>/` |

Then rebuild the index:
```bash
uv run --no-project ~/.claude/skills/parsidion-cc/scripts/update_index.py
```

Every unsaved solution is a missed opportunity for every future session.
