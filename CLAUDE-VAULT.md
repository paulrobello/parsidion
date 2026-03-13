# ClaudeVault — Always-On Guidance

> This file is loaded every session. The rules here are unconditional — they fire
> regardless of whether the claude-vault skill is explicitly invoked.

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
2. Search `~/ClaudeVault/Debugging/` first:
   ```
   Grep: path=~/ClaudeVault/Debugging, glob=**/*.md, pattern=<signal>
   ```
3. Widen to `Frameworks/`, `Languages/`, `Projects/` if nothing found in `Debugging/`.
4. **Found a match?** Apply the documented fix. Update the note if you learn
   something new about the problem.
5. **No match?** Diagnose and solve it, then save the solution so future sessions
   benefit (see "Saving Solutions" below).

## Implementation: Check for Prior Art

Before writing non-trivial code for any feature, integration, or pattern:

1. Search `~/ClaudeVault/Patterns/` for relevant design patterns.
2. Search `~/ClaudeVault/Frameworks/` and `Languages/` for the specific stack.
3. Search `~/ClaudeVault/Projects/` for implementations in other projects:
   ```
   Grep: path=~/ClaudeVault/Projects, glob=**/*.md, pattern=<feature-keyword>
   ```
4. **Found prior art?** Read it. Reuse and adapt — a proven implementation from
   another project beats a fresh one every time.
5. Check the `sources` field of matched notes for relevant code paths and references.

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

## Saving Solutions

After solving a non-obvious problem or implementing a reusable pattern, save it:

| What you solved | Save to |
|---|---|
| Error / bug fix | `~/ClaudeVault/Debugging/` |
| Reusable code pattern | `~/ClaudeVault/Patterns/` |
| Framework-specific fix | `~/ClaudeVault/Frameworks/` |
| Architectural decision | `~/ClaudeVault/Projects/<project>/` |

Then rebuild the index:
```bash
uv run ~/.claude/skills/claude-vault/scripts/update_index.py
```

Every unsaved solution is a missed opportunity for every future session.
