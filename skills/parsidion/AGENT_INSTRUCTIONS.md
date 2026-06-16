# Parsidion Vault (agent-agnostic)

Before debugging or implementing, check the knowledge vault at the path resolved by the parsidion tooling.

- **Recall prior knowledge:** `vault-search "<query>"` (semantic) or `vault-search --grep "<pattern>"`.
- **Find contradictions:** `vault-conflicts` then resolve interactively.
- **What changed recently:** `vault-search --changed-since 2026-06-01`.
- **Scaffold a note:** `vault-new --type pattern --title "..."`.
- After saving a reusable solution, rebuild the index: `update_index.py`.

Vault-first rule: search before you diagnose, and save non-obvious solutions.
