## Vault Conflicts

`vault-conflicts` is a global CLI command (companion to `vault-merge`) that detects contradictions between similar notes and resolves them via the configured prompt AI backend. Where `vault-merge` collapses near-duplicate notes saying the *same* thing, `vault-conflicts` surfaces pairs that say *opposite* things.

### Usage

```bash
# List candidate conflict pairs only (no AI, no writes)
vault-conflicts --scan-only

# Machine-readable output for scripting
vault-conflicts --json

# Interactive resolution (default): review each pair, resolve via prompt AI backend
vault-conflicts

# List pairs without invoking the AI backend
vault-conflicts --no-ai
```

### Key Flags

| Flag | Description |
|---|---|
| `--scan-only` | List candidate conflict pairs (embedding-similarity candidates); no AI call, no writes |
| `--json` | Emit machine-readable JSON instead of interactive output |
| `--no-ai` | List pairs without invoking the prompt AI backend |
| `--threshold FLOAT` | Cosine similarity threshold for candidate pairs |
| `--top N` | Limit to the top N candidate pairs |
| `--vault PATH` | Target vault (path or name from `~/.config/parsidion/vaults.yaml`) |

