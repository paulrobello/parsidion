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

This creates `~/.config/parsidion/vaults.yaml` (the location honors `$XDG_CONFIG_HOME`) with commented examples. Edit it to register each vault's name and path:

```yaml
vaults:
  work: ~/WorkVault
  personal: ~/PersonalVault
  team: ~/team-vault
```

Each name is then accepted wherever a vault reference is: the `--vault` flag, the `CLAUDE_VAULT` environment variable, or a project's `.claude/vault` file.

Installing to a custom path also registers the vault: `uv run install.py --vault ~/WorkVault` records the path as a named entry, plus a top-level `default:` line that uninstall reads to locate that vault. Runtime resolution itself follows the order in [Default Vault Resolution](#default-vault-resolution).

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
| `vault-search` | Yes — path or name |
| `vault-new` | Yes — path or name |
| `vault-stats` | Yes — path or name |
| `vault-review` | Yes — path or name |
| `vault-export` | Yes — path or name |
| `vault-merge` | Yes — path or name |
| `vault-conflicts` | Yes — path or name |
| `vault_doctor.py` | Yes — path or name |
| `build_embeddings.py` | Yes — path or name |
| `update_index.py` | Yes — path or name |
| `summarize_sessions.py` | Yes — path or name |
| `build_graph.py` | Yes — path only |
| `vault_embed_serve.py` | Yes — path only (required) |

The `vault-*` names are global commands installed by `uv tool install --editable ".[tools]"`. The rest are scripts run via `uv run --no-project ~/.claude/skills/parsidion/scripts/<name>.py`.

## Vault-Aware Hooks

Hooks take no `--vault` flag. Each one resolves its vault with the order in [Default Vault Resolution](#default-vault-resolution), using the session's project directory as the lookup context. To bind a project to a specific vault, write the vault name (or a registered path) into a `.claude/vault` file at the project root:

```bash
echo work > ~/code/client-app/.claude/vault
```

The reference must resolve to a named vault registered in `vaults.yaml` or to the default vault path; an unregistered reference is skipped and resolution falls through to `CLAUDE_VAULT` and the default vault.

- `session_start_hook.py` — loads context from the project's associated vault
- `session_stop_hook.py` — queues sessions to the appropriate vault
- `pre_compact_hook.py` — snapshots to the project's vault
- `post_compact_hook.py` — restores from the project's vault
- `subagent_stop_hook.py` — queues to the active vault

The Codex and Gemini adapter hooks run the same resolution through the shared session pipeline.

## Default Vault Resolution

When no explicit vault is specified, tools use this resolution order:

1. `--vault PATH_OR_NAME` CLI flag (path or name from `~/.config/parsidion/vaults.yaml`)
2. Project-local `.claude/vault` file (path or configured name)
3. `CLAUDE_VAULT` environment variable (path or configured name)
4. `~/ParsidionVault` (or legacy `~/ClaudeVault` if it exists)

> **Note:** A vault reference must resolve to a named vault registered in `vaults.yaml` or to the default vault path. An arbitrary unregistered path is rejected: an explicit `--vault` reference fails with a `VaultConfigError`, while `.claude/vault` and `CLAUDE_VAULT` references that fail the check are skipped and resolution falls through to the next step.

## Related Documentation

- [VAULT_SYNC.md](VAULT_SYNC.md) — Multi-machine and team vault sync via git
- [ARCHITECTURE.md](ARCHITECTURE.md) — System architecture and configuration reference
- [README.md](../README.md) — Project overview, installation, and quick start
