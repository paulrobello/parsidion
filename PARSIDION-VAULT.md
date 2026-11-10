# ParsidionVault — Always-On Guidance

> This file is loaded every session. The rules here are unconditional — they fire
> regardless of whether the parsidion skill is explicitly invoked.

## The Vault-First Rule

**Before debugging, before web search, before writing non-trivial new code — check the vault.**

The vault at `~/ParsidionVault/` (or legacy `~/ClaudeVault/` when upgrading)
accumulates solutions, patterns, and decisions across every project and session.
Checking it first avoids re-solving problems that have already been solved.

## Searching the Vault

1. Extract the key signal: for an error, the exception class, package name, or most
   distinctive phrase of the message (e.g. `IntegrityError`, `pydantic_settings`,
   `query_one`); for new work, the feature, integration, or pattern keyword.
2. Search at the cost the question deserves:
   - **One signal:** run `vault-search "<signal>"` directly and read the top note.
     A sub-agent pays its full startup context for the same lookup.
   - **A question that needs several notes combined:** dispatch the `vault-explorer`
     agent with a natural language query. It returns `## Answer` (a synthesized
     answer) and `## Sources` (absolute paths to the matching notes).
3. **Found it?** Apply the fix or reuse and adapt the implementation. `Read` the
   source notes, and the code paths in their `sources` frontmatter, only when you
   need more depth. Update the note if you learn something new.
4. **Nothing relevant?** Solve it, then dispatch the `research-agent` to save the
   solution or pattern (see "Saving Solutions" below).

## Vault Organization

**Subfolder rule**: when 3 or more notes share a common subject prefix, move them
into a named subfolder (e.g. `Research/fastapi-middleware/basics.md` instead of
`Research/fastapi-middleware-basics.md`). Drop the redundant prefix from filenames
inside the subfolder, update all wikilinks, and rebuild the index. Only one level
of subfolder is allowed — never nest subfolders within subfolders.

## Saving Solutions

After solving a non-obvious problem or implementing a reusable pattern, save it:

| What you solved | Save to |
|---|---|
| Error / bug fix | `<resolved vault>/Debugging/` |
| Reusable code pattern | `<resolved vault>/Patterns/` |
| Framework-specific fix | `<resolved vault>/Frameworks/` |
| Language-specific knowledge | `<resolved vault>/Languages/` |
| CLI tool or package notes | `<resolved vault>/Tools/` |
| External research findings | `<resolved vault>/Research/` |
| General knowledge, concepts, reference material | `<resolved vault>/Knowledge/` |
| Architectural decision | `<resolved vault>/Projects/<project>/` |

After saving, or after any note reorganization (create, rename, move, delete),
rebuild the index:
```bash
uv run --no-project ~/.claude/skills/parsidion/scripts/update_index.py
```

Every unsaved solution is a missed opportunity for every future session.
