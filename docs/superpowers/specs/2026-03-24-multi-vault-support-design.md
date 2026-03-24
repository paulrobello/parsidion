# Multi-Vault Support Design

**Date:** 2026-03-24
**Status:** Draft
**Enhancement:** #18 from ENHANCE.md

## Overview

Add multi-vault support to parsidion-cc, allowing users to maintain separate vaults for different contexts (personal vs work) and team collaboration. Users select vaults via `--vault` flag, `CLAUDE_VAULT` environment variable, or project-local configuration.

## Use Cases

1. **Personal vs Work** — Separate vaults for different professional contexts
2. **Team Vaults** — Shared vaults synced via git for team knowledge accumulation

## Vault Selection Precedence

```
1. --vault PATH|NAME     (explicit CLI flag)
2. .claude/vault file    (project-local, if cwd is in a project)
3. CLAUDE_VAULT env var  (session-wide default)
4. ~/ClaudeVault         (built-in default)
```

## Architecture

### Vault Resolver Function

Single source of truth in `vault_common.py`:

```python
def resolve_vault(
    explicit: str | Path | None = None,
    cwd: str | Path | None = None,
) -> Path:
    """Resolve the vault path following precedence rules.

    Args:
        explicit: --vault argument (path or name), or None.
        cwd: Working directory for project-local lookup, or None.

    Returns:
        Absolute Path to the vault directory.

    Raises:
        VaultConfigError: If named vault doesn't exist or path invalid.
    """
```

### Named Vaults Configuration

**File:** `~/.config/parsidion-cc/vaults.yaml`

```yaml
# Named vaults for quick reference via --vault NAME or CLAUDE_VAULT=NAME
vaults:
  personal: ~/ClaudeVault
  work: ~/WorkVault
  team-alpha: ~/Repos/team-alpha-vault

# Optional: set a default named vault (overrides ~/ClaudeVault)
# default: work
```

**Lookup rules:**
- `--vault work` → lookup `vaults.work` → expand `~` → return Path
- `--vault ~/OtherVault` → detect it's a path → return Path directly
- Invalid name → `VaultConfigError` with available names listed

**Config discovery:**
- Primary: `$XDG_CONFIG_HOME/parsidion-cc/vaults.yaml`
- Fallback: `~/.parsidion-cc/vaults.yaml`

### Project-Local Vault Pointer

**File:** `.claude/vault` (in project root)

Single line containing path or vault name:
```
work
```
or
```
~/WorkVault
```

Empty or whitespace-only file is ignored.

### New Functions in `vault_common.py`

```python
def resolve_vault(explicit: str | Path | None = None, cwd: str | Path | None = None) -> Path:
    """Main vault resolver with caching."""

def list_named_vaults() -> dict[str, Path]:
    """Return {name: path} dict from vaults.yaml."""

def get_vaults_config_path() -> Path:
    """Return XDG path to vaults.yaml."""
```

### Exception Class

```python
class VaultConfigError(Exception):
    """Raised when vault configuration is invalid."""
```

## CLI Integration

### Standard Flag

All CLIs accept:

```python
parser.add_argument(
    "--vault",
    "-V",
    metavar="PATH|NAME",
    help="Vault path or named vault (default: ~/ClaudeVault)",
)
```

### CLIs to Update

| CLI | Changes |
|-----|---------|
| `vault-search` | Add `-V` flag |
| `vault-stats` | Add `-V` flag |
| `vault-new` | Add `-V` flag |
| `vault-doctor` | Add `-V` flag |
| `vault-review` | Add `-V` flag |
| `vault-export` | Add `-V` flag |
| `vault-merge` | Add `-V` flag |
| `update-index` | Add `-V` flag |
| `build-embeddings` | Add `-V` flag |
| `summarize_sessions.py` | Add `-V` flag (PEP 723 script, reads `pending_summaries.jsonl`) |

### Pattern

```python
def main() -> None:
    args = parser.parse_args()
    vault_path = resolve_vault(explicit=args.vault, cwd=os.getcwd())
    # Use vault_path instead of VAULT_ROOT
```

### Error Messages

```
Error: Unknown vault 'foo'. Available: personal, work, team-alpha
```

## Hook Integration

Hooks receive JSON input with `cwd` field from Claude Code. They resolve the vault based on this context.

### Hooks to Update

| Hook | Change |
|------|--------|
| `session_start_hook.py` | Resolve vault from `cwd` |
| `session_stop_hook.py` | Resolve vault from `cwd` |
| `pre_compact_hook.py` | Resolve vault from `cwd` |
| `post_compact_hook.py` | Resolve vault from `cwd` |
| `subagent_stop_hook.py` | Resolve vault from `cwd` |

### Pattern

```python
def main() -> None:
    input_data = json.load(sys.stdin)
    cwd = input_data.get("cwd", os.getcwd())
    vault_path = resolve_vault(cwd=cwd)
    # Pass vault_path to functions that need it
```

## Function Signatures Update

Functions in `vault_common.py` that currently reference `VAULT_ROOT` directly will accept an optional `vault` parameter:

```python
def get_daily_note_path(vault: Path | None = None) -> Path:
    vault = vault or resolve_vault()
    ...

def ensure_vault_dirs(vault: Path | None = None) -> None:
    vault = vault or resolve_vault()
    ...

def get_embeddings_db_path(vault: Path | None = None) -> Path:
    vault = vault or resolve_vault()
    ...

def load_config(vault: Path | None = None) -> dict[str, Any]:
    vault = vault or resolve_vault()
    ...

def write_hook_event(hook: str, project: str, duration_ms: int, vault: Path | None = None, **extra) -> None:
    vault = vault or resolve_vault()
    ...

def queue_for_summarization(transcript_path: Path, project: str, vault: Path | None = None, ...) -> None:
    vault = vault or resolve_vault()
    ...

def _walk_vault_notes(vault: Path | None = None) -> list[Path]:
    vault = vault or resolve_vault()
    ...

def build_compact_index(vault: Path | None = None, ...) -> list[str]:
    vault = vault or resolve_vault()
    ...

def git_commit_vault(message: str, vault: Path | None = None, paths: list[Path] | None = None) -> bool:
    vault = vault or resolve_vault()
    ...
```

**Migration strategy:**
- All functions get optional `vault: Path | None = None` parameter
- Default behavior: `vault = vault or resolve_vault()`
- Backward compatible — existing callers without vault arg still work

## Files to Modify

### Core

| File | Changes |
|------|---------|
| `vault_common.py` | Add resolver, update ~15 functions |

### CLIs

| File | Est. Lines |
|------|------------|
| `vault_search.py` | ~5 |
| `vault_stats.py` | ~8 |
| `vault_new.py` | ~3 |
| `vault_doctor.py` | ~25 |
| `vault_review.py` | ~3 |
| `vault_export.py` | ~5 |
| `vault_merge.py` | ~5 |
| `update_index.py` | ~10 |
| `build_embeddings.py` | ~5 |
| `summarize_sessions.py` | ~10 |

### Installer

| File | Est. Lines |
|------|------------|
| `install.py` | ~15 |

**Installer changes:**
- Add `--create-vaults-config` flag to scaffold `~/.config/parsidion-cc/vaults.yaml`
- Update `--uninstall` to remove `vaults.yaml` (with confirmation)
- Add help text documenting multi-vault feature
- Ensure `~/.config/parsidion-cc/` directory is created during install

### Hooks

| File | Est. Lines |
|------|------------|
| `session_start_hook.py` | ~3 |
| `session_stop_hook.py` | ~3 |
| `pre_compact_hook.py` | ~3 |
| `post_compact_hook.py` | ~3 |
| `subagent_stop_hook.py` | ~3 |

**Total estimated:** ~120-180 lines changed across 16 files.

## Backward Compatibility

- `VAULT_ROOT` constant remains for installer patching
- All existing behavior preserved when no vault is specified
- Default vault is still `~/ClaudeVault`
- No breaking changes to existing users

## Testing Considerations

- Unit tests for `resolve_vault()` with various precedence scenarios
- Test named vault lookup and path detection
- Test project-local `.claude/vault` resolution
- Test env var `CLAUDE_VAULT` handling
- Integration tests for each CLI with `--vault` flag
- Hook tests with mock JSON input containing `cwd`
