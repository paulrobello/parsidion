# Agents

Parsidion ships subagents that coding-agent runtimes can dispatch during sessions.

| Agent | Model | Purpose |
|-------|-------|---------|
| **vault-explorer** | Haiku | Read-only vault search agent with a 7-step lookup procedure (semantic, metadata, folder grep). Returns a synthesized answer and source paths. Listed in `excluded_agents` to prevent its own transcripts from being captured. |
| **research-agent** | Sonnet | Searches the vault first, then conducts web research via Brave Search (with mcpl fallback). Saves findings to the appropriate vault folder with YAML frontmatter and rebuilds the index. |

See `@CLAUDE.md` for full agent details, configuration, and the vault-explorer search procedure.
