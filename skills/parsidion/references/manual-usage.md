## Manual Usage

### Creating a Note

1. Search the vault to confirm the note does not already exist.
2. Create the file in the appropriate folder with kebab-case naming.
3. Include the full frontmatter block.
4. Write the note body with inline wikilinks to related notes.
5. Ensure the `related` frontmatter field is populated.
6. Run `update_index.py` to rebuild the index.

### Searching the Vault

- Dispatch the **`vault-explorer` agent** for natural language queries — it searches priority folders, ranks matches, and returns a synthesized answer with source paths.
- Use `vault_common.py` functions for programmatic search from scripts.
- Use Obsidian's built-in search for interactive visual exploration.
- Use `Grep` or `Glob` tools only for targeted investigation when you know the specific file or exact pattern you need.

### Agents

| Agent | Trigger | Description |
|---|---|---|
| `vault-explorer` | "what do we know about X", "check our notes", "prior art" | Semantic vault search — returns a synthesized answer with source paths |
| `research-agent` | "research X and save to vault", no vault results found | External research via Brave Search + Web Fetch; saves findings to vault |
| `vault-deduplicator` | "deduplicate the vault", "find duplicate notes", "merge duplicate vault notes" | Scans for near-duplicate note pairs by embedding similarity, evaluates each pair, merges confirmed duplicates, and rebuilds the index |

### Linking Notes

- Use `[[wikilinks]]` in both the `related` frontmatter field and the note body.
- When a new note references an existing note, consider updating the existing note's `related` field to create a bidirectional link.

### Updating Existing Notes

- Prefer updating an existing note over creating a new one on the same topic.
- Update the `date` field when making substantial changes.
- Adjust `confidence` as understanding evolves.

