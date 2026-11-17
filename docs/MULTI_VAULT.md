# Multi-Vault Support

How to use more than one Parsidion vault on a single machine — separate work from personal notes, give each codebase its own knowledge base, or share a team vault while keeping a private one. This is the reference for the `--vault` flag, the `vaults.yaml` registry, and the resolution order every vault-aware tool follows.

## Table of Contents

- [Overview](#overview)
- [Setup](#setup)
- [Using Multiple Vaults](#using-multiple-vaults)
- [Personal Vault](#personal-vault)
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

This creates `~/.config/parsidion/vaults.yaml` with commented examples. Edit it to register each vault's name and path. At lookup time the registry is read through `get_vaults_config_path()` in `core/vault_path.py`, which honors `$XDG_CONFIG_HOME`, falls back to `~/.config`, and reads the legacy pre-rebrand locations (`~/.config/parsidion-cc` or `~/.parsidion-cc`) when `~/.config/parsidion` does not exist:

```yaml
vaults:
  work: ~/WorkVault
  personal: ~/PersonalVault
  team: ~/team-vault
```

Each name is then accepted wherever a vault reference is: the `--vault` flag, the `CLAUDE_VAULT` environment variable, or a project's `.claude/vault` file.

Installing to a custom path also registers the vault: `uv run install.py --vault ~/WorkVault` records the path as a named entry (named after the directory stem, e.g. `WorkVault`) plus a top-level `default:` line, so subsequent runtime resolution (order in [Default Vault Resolution](#default-vault-resolution)) lands on that vault with no extra env var. The uninstaller reads the same line to locate that vault.

## Using Multiple Vaults

All vault tools accept a `--vault` flag specifying the target vault; every tool except the two path-only scripts at the end of the table below also accepts the short form `-V`:

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

## Personal Vault

A personal vault holds household knowledge (home, family, pets, appliances, vehicles, health) with its own folders instead of the engineering ones. It is registered by name but is not made the default, so engineering sessions keep resolving to the main vault.

> **Warning:** Never store secrets in vault notes. Alarm codes, passwords, SSNs, PINs and similar live-auth credentials belong in a secrets manager (parvault), not in markdown. Vault notes are plain text, are git-committed, and are injected into agent context. **Account numbers, customer IDs, policy numbers and similar identifiers are allowed** in vault notes (Paul's ruling 2026-11-16) — they identify an account and are not by themselves usable to authenticate.

### Bootstrap

```bash
uv run install.py --vault ~/ParsidionPersonalVault --vault-name personal --no-set-default --vault-username <name>
```

`--vault-name` registers the vault under that name in `vaults.yaml`. `--no-set-default` leaves the top-level `default:` entry unchanged.

### Taxonomy config

Add a `taxonomy` section to `~/ParsidionPersonalVault/config.yaml`. Values are scalar-encoded because the config parser has no list support:

```yaml
taxonomy:
  folders: Home, Family, Pets, Appliances, Vehicles, Health, Finance, Reference
  default_folder: Reference
  type_folder.knowledge: Reference
  type_folder.debugging: Home
  type_folder.tool: Appliances
```

- `folders` replaces the built-in folder set. Without it, `default_folder` and `type_folder.*` are ignored.
- `type_folder.<type>` routes a note type to a folder. Unlisted types keep their built-in folder if it is in `folders`, otherwise they go to `default_folder`.
- `default_folder` must appear in `folders`, otherwise the first folder is used.

### Dedicated folder wiring

Bind a directory to the vault so sessions started there use it:

```bash
mkdir -p ~/Repos/personal/.claude
echo personal > ~/Repos/personal/.claude/vault
```

### Nightly maintenance

`summarize_sessions.py --all-vaults` processes the default vault and every named vault in one run, each against its own queue. A failure in one vault is reported and the remaining vaults still run. It cannot be combined with `--vault` or `--sessions`. An already-scheduled nightly job keeps its old command line, so re-run `install.py --schedule-summarizer` to pick up `--all-vaults` (it is opt-in).

```bash
uv run --no-project ~/.claude/skills/parsidion/scripts/summarize_sessions.py --all-vaults
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
| `vault-supersede` | Yes — path or name |
| `vault_doctor.py` | Yes — path or name |
| `build_embeddings.py` | Yes — path or name |
| `update_index.py` | Yes — path or name |
| `summarize_sessions.py` | Yes — path or name |
| `build_graph.py` | Yes — path only |
| `vault_embed_serve.py` | Yes — path only (required) |

The `vault-*` names are global commands installed by `uv tool install --editable ".[tools]"`. The rest are scripts run via `uv run --no-project ~/.claude/skills/parsidion/scripts/<name>.py`.

Two hosted surfaces are vault-aware as well: every `parsidion-mcp` tool except `code_search` takes an optional per-call `vault` argument (a name from `vaults.yaml` or a registered vault path, per the allowlist note below), and the visualizer resolves named vaults from the same registry, taking its default from the `default:` key in `vaults.yaml` (the `VAULT_ROOT` environment variable is only a fallback when no default is configured). See [MCP.md](MCP.md) and [VISUALIZER.md](VISUALIZER.md).

## Vault-Aware Hooks

Hooks take no `--vault` flag. Each one resolves its vault with the order in [Default Vault Resolution](#default-vault-resolution), using the session's project directory as the lookup context. To bind a project to a specific vault, write the vault name (or a registered path) into a `.claude/vault` file at the project root:

```bash
echo work > ~/code/client-app/.claude/vault
```

The reference must resolve to a named vault registered in `vaults.yaml` or to the default vault path; an unregistered reference is skipped and resolution falls through to `CLAUDE_VAULT` and the default vault. A `.claude/vault` file must therefore name a REGISTERED vault: otherwise the session is served the default vault, a warning is shown, and the SessionStart header names the vault actually served. The installer checks that the name it registers resolves.

- `session_start_hook.py` — loads context from the project's associated vault
- `session_stop_hook.py` — queues sessions to the appropriate vault
- `pre_compact_hook.py` — snapshots to the project's vault
- `post_compact_hook.py` — logs snapshot availability to the project's vault (re-injection happens via `session_start_hook.py` on `source="compact"`)
- `subagent_stop_hook.py` — queues to the active vault
- `user_prompt_submit_hook.py` — injects vault recall for each user prompt
- `pre_tool_use_hook.py` — injects rule notes matching the file being read or edited

The Codex and Antigravity adapter hooks (registered in `~/.codex/hooks.json` and `~/.gemini/config/hooks.json`) and the pi extension run the same resolution through the shared pipeline in `agent_adapter.py`.

## Default Vault Resolution

When no explicit vault is specified, tools use this resolution order:

1. `--vault PATH_OR_NAME` CLI flag (path or name from `~/.config/parsidion/vaults.yaml`)
2. Project-local `.claude/vault` file (path or configured name)
3. `CLAUDE_VAULT` environment variable (path or configured name)
4. The top-level `default:` key in `~/.config/parsidion/vaults.yaml` (a vault name or a registered path; unset or unresolvable values are ignored)
5. `~/ParsidionVault` (or legacy `~/ClaudeVault` if it exists)

> **Note:** A vault reference must resolve to a named vault registered in `vaults.yaml` or to the default vault path. A linked Git worktree of a registered vault also resolves, verified through Git's own worktree registry at the allowlisted root. An arbitrary unregistered path is rejected: an explicit `--vault` reference fails with a `VaultConfigError`, while `.claude/vault` and `CLAUDE_VAULT` references that fail the check are skipped and resolution falls through to the next step.

## Related Documentation

- [VAULT_SYNC.md](VAULT_SYNC.md) — Multi-machine and team vault sync via git
- [ARCHITECTURE.md](ARCHITECTURE.md) — System architecture and configuration reference
- [MCP.md](MCP.md) — MCP server tools and their per-call `vault` argument
- [VISUALIZER.md](VISUALIZER.md) — Visualizer vault resolution and named-vault setup
- [README.md](../README.md) — Project overview, installation, and quick start
