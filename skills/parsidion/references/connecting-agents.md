#### Connecting other agents

The `connect` / `disconnect` installer verbs wire parsidion into a coding agent's hooks and instructions file in one command. `connect` writes the agent instructions file (`~/.codex/AGENTS.md` for codex, `~/.gemini/GEMINI.md` for gemini) and enables the agent's runtime hooks; `disconnect` removes only that agent's integration:

```bash
uv run install.py connect codex      # wire codex: AGENTS.md + codex hooks
uv run install.py connect gemini     # wire gemini: GEMINI.md + gemini hooks
uv run install.py disconnect codex   # remove the codex integration only
```

`connect`/`disconnect` are a friendlier front-end for the existing `--runtime` flag; they also inject the agent instructions file that `--runtime` alone does not.
