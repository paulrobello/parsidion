# Multi-Vault Support

How to use more than one Parsidion vault on a single machine — separate work from personal notes, give each codebase its own knowledge base, or share a team vault while keeping a private one. This is the reference for the `--vault` flag, the `vaults.yaml` registry, and the resolution order every vault-aware tool follows.

## Table of Contents

- [Overview](#overview)
- [Setup](#setup)
- [Using Multiple Vaults](#using-multiple-vaults)
- [Vault-Aware Tools](#vault-aware-tools)
- [Vault-Aware Hooks](#vault-aware-hooks)
- [Default Vault Resolution](#default-vault-resolution)
- [Related Documentation](#related-documentation)

## Overview

Parsidion supports multiple isolated vaults with per-vault configuration. This enables:

- **Separate work/personal vaults** — keep client work isolated from personal notes
- **Project-specific vaults** — each codebase can have its own knowledge base
- **Team vaults** — share a vault via git with teammates while maintaining a private vault

## Setup

Use `--create-vaults-config` to generate a vaults configuration file:

```bash
uv run install.py --create-vaults-config
```

This creates `~/.config/parsidion/vaults.yaml`:

```yaml
vaults:
  default: ~/ParsidionVault
  work: ~/WorkVault
  personal: ~/PersonalVault
```

## Using Multiple Vaults

All vault tools support a `--vault` flag to specify the target vault:

```bash
# Search in a specific vault
vault-search "error patterns" --vault work

# Create a note in the personal vault
vault-new --type pattern --title "My Pattern" --vault personal

# View stats for work vault
vault-stats --summary --vault work

# Run doctor on a specific vault (script — not a global CLI)
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --vault work --dry-run

# Build embeddings / rebuild index for a vault
uv run --no-project ~/.claude/skills/parsidion/scripts/build_embeddings.py --vault work
uv run --no-project ~/.claude/skills/parsidion/scripts/update_index.py --vault work
```

## Vault-Aware Tools

| Tool | `--vault` Flag |
|------|----------------|
| `vault-search` | Yes |
| `vault-new` | Yes |
| `vault-stats` | Yes |
| `vault-review` | Yes |
| `vault-export` | Yes |
| `vault-merge` | Yes |
| `vault-conflicts` | Yes |
| `vault-doctor` | Yes |
| `build_embeddings.py` | Yes |
| `update_index.py` | Yes |
| `summarize_sessions.py` | Yes |

## Vault-Aware Hooks

All session hooks support multi-vault via the vaults config:

- `session_start_hook.py` — loads context from the project's associated vault
- `session_stop_hook.py` — queues sessions to the appropriate vault
- `pre_compact_hook.py` — snapshots to the project's vault
- `post_compact_hook.py` — restores from the project's vault
- `subagent_stop_hook.py` — queues to the active vault

## Default Vault Resolution

When no explicit vault is specified, tools use this resolution order:

1. `--vault PATH_OR_NAME` CLI flag (path or name from `~/.config/parsidion/vaults.yaml`)
2. Project-local `.claude/vault` file (path or configured name)
3. `CLAUDE_VAULT` environment variable (path or configured name)
4. `~/ParsidionVault` (or legacy `~/ClaudeVault` if it exists)

## Related Documentation

- [VAULT_SYNC.md](VAULT_SYNC.md) — Multi-machine and team vault sync via git
- [ARCHITECTURE.md](ARCHITECTURE.md) — System architecture and configuration reference
- [README.md](../README.md) — Project overview, installation, and quick start
