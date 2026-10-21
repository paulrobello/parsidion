
`vault-merge` is a global CLI command for backend-aware AI-assisted note merging. It detects near-duplicate notes, merges their content via the configured prompt AI backend, and updates all bidirectional backlinks.

### Usage

```bash
# Find near-duplicate pairs using embedding similarity (scan only, no merge)
vault-merge --scan

# Merge two specific notes (NOTE_A survives, NOTE_B is trashed)
vault-merge NOTE_A NOTE_B --execute

# Merge without rebuilding the index (for batch merges — rebuild once at the end)
vault-merge NOTE_A NOTE_B --no-index --execute
```

### Key Flags

| Flag | Description |
|---|---|
| `--scan` | List near-duplicate pairs sorted by cosine similarity; no merge performed |
| `--execute` | Actually perform the merge (default is dry-run preview) |
| `--no-index` | Skip rebuilding the vault index after the merge; use during batch operations and run `update_index.py` once when all merges are done |

> Use `--no-index` on every individual merge during a batch deduplication run; run `update_index.py` once at the end. The `vault-deduplicator` agent handles this automatically.

