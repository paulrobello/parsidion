# Multi-Vault Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--vault` flag and `CLAUDE_VAULT` env var support to all vault CLIs and hooks, enabling multiple vaults for personal/work/team contexts.

**Architecture:** Single `resolve_vault()` function in `vault_common.py` handles precedence (flag → project-local → env var → default). All vault-aware functions get optional `vault` parameter.

**Tech Stack:** Python stdlib only (pathlib, os, functools.lru_cache), xdg-base-dirs for config path

---

## File Structure

### New Files
None (config file created on demand)

### Modified Files

| File | Responsibility |
|------|----------------|
| `vault_common.py` | Core resolver, updated function signatures |
| `vault_search.py` | Add `--vault` CLI flag |
| `vault_stats.py` | Add `--vault` CLI flag |
| `vault_new.py` | Add `--vault` CLI flag |
| `vault_doctor.py` | Add `--vault` CLI flag |
| `vault_review.py` | Add `--vault` CLI flag |
| `vault_export.py` | Add `--vault` CLI flag |
| `vault_merge.py` | Add `--vault` CLI flag |
| `update_index.py` | Add `--vault` CLI flag |
| `build_embeddings.py` | Add `--vault` CLI flag |
| `summarize_sessions.py` | Add `--vault` CLI flag |
| `session_start_hook.py` | Resolve vault from cwd |
| `session_stop_hook.py` | Resolve vault from cwd |
| `pre_compact_hook.py` | Resolve vault from cwd |
| `post_compact_hook.py` | Resolve vault from cwd |
| `subagent_stop_hook.py` | Resolve vault from cwd |
| `install.py` | Add `--create-vaults-config`, update uninstall |

---

## Task 1: Add Vault Resolver to vault_common.py

**Files:**
- Modify: `skills/parsidion/scripts/vault_common.py`

- [ ] **Step 1: Add imports and exception class**

Add after existing imports (around line 15):

```python
import functools
from xdg_base_dirs import xdg_config_home
```

Add after `EXCLUDE_DIRS` definition (around line 100):

```python
class VaultConfigError(Exception):
    """Raised when vault configuration is invalid."""
    pass
```

- [ ] **Step 2: Add get_vaults_config_path function**

Add after `VaultConfigError` class:

```python
def get_vaults_config_path() -> Path:
    """Return path to vaults.yaml configuration file.

    Uses XDG config home with fallback to ~/.parsidion/.

    Returns:
        Path to vaults.yaml (file may not exist).
    """
    xdg_path = xdg_config_home() / "parsidion" / "vaults.yaml"
    if xdg_path.parent.exists() or not (Path.home() / ".parsidion").exists():
        return xdg_path
    return Path.home() / ".parsidion" / "vaults.yaml"
```

- [ ] **Step 3: Add list_named_vaults function**

```python
def list_named_vaults() -> dict[str, Path]:
    """Load named vaults from vaults.yaml.

    Returns:
        Dict mapping vault name to absolute Path.
        Empty dict if config file doesn't exist or is invalid.
    """
    config_path = get_vaults_config_path()
    if not config_path.is_file():
        return {}

    try:
        content = config_path.read_text(encoding="utf-8")
        # Simple YAML parsing for top-level 'vaults:' key
        # Format: vaults:\n  name: path\n  ...
        lines = content.splitlines()
        in_vaults = False
        vaults: dict[str, Path] = {}

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            if stripped == "vaults:":
                in_vaults = True
                continue
            if in_vaults:
                if not line.startswith(" ") and not line.startswith("\t"):
                    break  # Left top-level section
                if ":" in stripped:
                    name, path_str = stripped.split(":", 1)
                    name = name.strip()
                    path_str = path_str.strip()
                    if name and path_str:
                        # Expand ~ to home directory
                        if path_str.startswith("~"):
                            path_str = str(Path.home() / path_str[2:])
                        vaults[name] = Path(path_str).resolve()

        return vaults
    except (OSError, ValueError):
        return {}
```

- [ ] **Step 4: Add _resolve_vault_reference helper**

```python
def _resolve_vault_reference(ref: str) -> Path:
    """Resolve a vault reference (name or path) to an absolute Path.

    Args:
        ref: Vault name (e.g., "work") or path (e.g., "~/MyVault" or "/abs/path").

    Returns:
        Absolute Path to vault directory.

    Raises:
        VaultConfigError: If ref is a name but not found in vaults.yaml.
    """
    # If it looks like a path (contains / or starts with ~), treat as path
    if "/" in ref or ref.startswith("~"):
        path = Path(ref).expanduser().resolve()
        return path

    # Otherwise, lookup as named vault
    named = list_named_vaults()
    if ref in named:
        return named[ref]

    available = ", ".join(sorted(named.keys())) if named else "none configured"
    raise VaultConfigError(f"Unknown vault '{ref}'. Available: {available}")
```

- [ ] **Step 5: Add resolve_vault function with caching**

```python
@functools.lru_cache(maxsize=8)
def resolve_vault(
    explicit: str | Path | None = None,
    cwd: str | Path | None = None,
) -> Path:
    """Resolve the vault path following precedence rules.

    Precedence:
    1. explicit flag (path or name)
    2. cwd/.claude/vault file (project-local)
    3. CLAUDE_VAULT env var
    4. Default ~/ClaudeVault

    Args:
        explicit: --vault argument (path or name), or None.
        cwd: Working directory for project-local lookup, or None.

    Returns:
        Absolute Path to the vault directory.

    Raises:
        VaultConfigError: If named vault doesn't exist or path invalid.
    """
    # 1. Explicit flag takes precedence
    if explicit is not None:
        ref = str(explicit) if isinstance(explicit, Path) else explicit
        return _resolve_vault_reference(ref)

    # 2. Project-local .claude/vault file
    if cwd is not None:
        cwd_path = Path(cwd).resolve()
        project_vault_file = cwd_path / ".claude" / "vault"
        if project_vault_file.is_file():
            try:
                content = project_vault_file.read_text(encoding="utf-8").strip()
                if content:
                    return _resolve_vault_reference(content)
            except OSError:
                pass  # Ignore read errors, fall through

    # 3. CLAUDE_VAULT environment variable
    env_vault = os.environ.get("CLAUDE_VAULT")
    if env_vault:
        return _resolve_vault_reference(env_vault)

    # 4. Default vault
    return VAULT_ROOT
```

- [ ] **Step 6: Update __all__ exports**

Add to `__all__` list:

```python
    "VaultConfigError",
    "get_vaults_config_path",
    "list_named_vaults",
    "resolve_vault",
```

- [ ] **Step 7: Run basic syntax check**

```bash
python -c "from skills.parsidion_cc.scripts.vault_common import resolve_vault, list_named_vaults, get_vaults_config_path, VaultConfigError; print('OK')"
```

Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add skills/parsidion/scripts/vault_common.py
git commit -m "feat(vault): add resolve_vault() and named vaults support"
```

---

## Task 2: Update vault_common.py Functions to Accept vault Parameter

**Files:**
- Modify: `skills/parsidion/scripts/vault_common.py`
- Modify: `skills/parsidion/scripts/vault_links.py` (functions that use VAULT_ROOT)

- [ ] **Step 1: Update get_embeddings_db_path**

Change from:
```python
def get_embeddings_db_path() -> Path:
    return VAULT_ROOT / EMBEDDINGS_DB_FILENAME
```

To:
```python
def get_embeddings_db_path(vault: Path | None = None) -> Path:
    vault = vault or resolve_vault()
    return vault / EMBEDDINGS_DB_FILENAME
```

- [ ] **Step 2: Update _walk_vault_notes**

Change from:
```python
def _walk_vault_notes() -> list[Path]:
```

To:
```python
def _walk_vault_notes(vault: Path | None = None) -> list[Path]:
    vault = vault or resolve_vault()
```

And update references from `VAULT_ROOT` to `vault` within the function.

- [ ] **Step 3: Update ensure_vault_dirs**

Change from:
```python
def ensure_vault_dirs() -> None:
    VAULT_ROOT.mkdir(parents=True, exist_ok=True)
    for dirname in VAULT_DIRS:
        (VAULT_ROOT / dirname).mkdir(exist_ok=True)
    ...
```

To:
```python
def ensure_vault_dirs(vault: Path | None = None) -> None:
    vault = vault or resolve_vault()
    vault.mkdir(parents=True, exist_ok=True)
    for dirname in VAULT_DIRS:
        (vault / dirname).mkdir(exist_ok=True)
    ...
```

- [ ] **Step 4: Update today_daily_path**

Change from:
```python
def today_daily_path() -> Path:
    ...
    return VAULT_ROOT / "Daily" / month_dir / day_file
```

To:
```python
def today_daily_path(vault: Path | None = None) -> Path:
    vault = vault or resolve_vault()
    ...
    return vault / "Daily" / month_dir / day_file
```

- [ ] **Step 5: Update load_config**

Change from:
```python
def load_config() -> dict[str, Any]:
    config_path = VAULT_ROOT / "config.yaml"
```

To:
```python
def load_config(vault: Path | None = None) -> dict[str, Any]:
    vault = vault or resolve_vault()
    config_path = vault / "config.yaml"
```

Also update the docstring.

- [ ] **Step 6: Update _load_config_cached**

Change signature to accept vault and use it:

```python
def _load_config_cached(vault_root: Path | None = None) -> dict[str, Any]:
    vault = vault_root or resolve_vault()
    config_path = vault / "config.yaml"
    ...
```

And update `load_config()` to pass vault:

```python
def load_config(vault: Path | None = None) -> dict[str, Any]:
    ...
    return _load_config_cached(vault)
```

- [ ] **Step 7: Update write_hook_event**

Add `vault` parameter:

```python
def write_hook_event(
    hook: str,
    project: str,
    duration_ms: int,
    vault: Path | None = None,
    **extra: Any,
) -> None:
    vault = vault or resolve_vault()
    ...
    log_path = vault / _HOOK_EVENTS_FILENAME
    vault.mkdir(parents=True, exist_ok=True)
    ...
```

- [ ] **Step 8: Update queue_for_summarization**

Add `vault` parameter:

```python
def queue_for_summarization(
    transcript_path: Path,
    project: str,
    session_id: str | None = None,
    categories: dict[str, list[str]] | None = None,
    source: str = "session",
    vault: Path | None = None,
    **extra: Any,
) -> None:
    vault = vault or resolve_vault()
    ...
    pending_path = vault / "pending_summaries.jsonl"
    ...
```

- [ ] **Step 9: Update fix_pending_paths**

Add `vault` parameter and use it.

- [ ] **Step 10: Update git_commit_vault**

Add `vault` parameter:

```python
def git_commit_vault(message: str, vault: Path | None = None, paths: list[Path] | None = None) -> bool:
    vault = vault or resolve_vault()
    ...
    git_marker = vault / ".git"
    ...
    cwd=str(vault),
    ...
```

- [ ] **Step 11: Update build_compact_index**

Add `vault` parameter and update internal calls to `_walk_vault_notes(vault)`.

- [ ] **Step 12: Update get_last_seen_path, load_last_seen, save_last_seen**

Add `vault` parameter to each, using vault path for the `.last_seen.json` file location.

- [ ] **Step 13: Update all_vault_notes**

Add `vault` parameter and call `_walk_vault_notes(vault)`.

- [ ] **Step 13a: Update vault_links.py functions**

In `vault_links.py`, update functions that use VAULT_ROOT:
- `find_related_by_tags()` - add `vault` parameter, pass to `vault_common.get_embeddings_db_path(vault)`
- `find_related_by_semantic()` - add `vault` parameter, pass to `vault_common.get_embeddings_db_path(vault)`
- `add_backlinks_to_existing()` - add `vault` parameter

Pass vault to internal vault_common calls.

- [ ] **Step 14: Verify syntax**

```bash
python -c "from skills.parsidion_cc.scripts import vault_common; print('OK')"
```

- [ ] **Step 15: Commit**

```bash
git add skills/parsidion/scripts/vault_common.py
git commit -m "refactor(vault): add vault parameter to core functions"
```

---

## Task 3: Update vault_search.py

**Files:**
- Modify: `skills/parsidion/scripts/vault_search.py`

- [ ] **Step 1: Add --vault argument to parser**

Find the argument parser section and add:

```python
    parser.add_argument(
        "--vault",
        "-V",
        metavar="PATH|NAME",
        default=None,
        help="Vault path or named vault (default: ~/ClaudeVault)",
    )
```

- [ ] **Step 2: Resolve vault at start of main()**

After `args = parser.parse_args()`, add:

```python
    vault_path = vault_common.resolve_vault(explicit=args.vault, cwd=os.getcwd())
```

- [ ] **Step 3: Pass vault to vault_common functions**

Update calls like:
- `vault_common.get_embeddings_db_path()` → `vault_common.get_embeddings_db_path(vault_path)`
- `vault_common.build_compact_index()` → `vault_common.build_compact_index(vault=stack_path)` (if applicable)
- Any `VAULT_ROOT` references → `vault_path`

- [ ] **Step 4: Verify**

```bash
vault-search --help | grep -A2 "\-V"
```

Expected: Shows `-V, --vault PATH|NAME`

- [ ] **Step 5: Commit**

```bash
git add skills/parsidion/scripts/vault_search.py
git commit -m "feat(vault-search): add --vault flag for multi-vault support"
```

---

## Task 4: Update vault_stats.py

**Files:**
- Modify: `skills/parsidion/scripts/vault_stats.py`

- [ ] **Step 1: Add --vault argument**

Add to parser:

```python
    parser.add_argument(
        "--vault",
        "-V",
        metavar="PATH|NAME",
        default=None,
        help="Vault path or named vault (default: ~/ClaudeVault)",
    )
```

- [ ] **Step 2: Resolve vault at start of main()**

```python
    vault_path = vault_common.resolve_vault(explicit=args.vault, cwd=os.getcwd())
```

- [ ] **Step 3: Update all VAULT_ROOT references**

Find and replace `vault_common.VAULT_ROOT` with `vault_path` throughout.

Update function calls:
- `vault_common.get_embeddings_db_path()` → `vault_common.get_embeddings_db_path(vault_path)`
- `vault_common.load_config()` → `vault_common.load_config(vault_path)`
- etc.

- [ ] **Step 4: Verify**

```bash
vault-stats --help | grep -A2 "\-V"
```

- [ ] **Step 5: Commit**

```bash
git add skills/parsidion/scripts/vault_stats.py
git commit -m "feat(vault-stats): add --vault flag for multi-vault support"
```

---

## Task 5: Update vault_new.py

**Files:**
- Modify: `skills/parsidion/scripts/vault_new.py`

- [ ] **Step 1: Add --vault argument**

- [ ] **Step 2: Resolve vault at start of main()**

- [ ] **Step 3: Update ensure_vault_dirs call**

```python
vault_common.ensure_vault_dirs(vault_path)
```

- [ ] **Step 4: Verify and commit**

---

## Task 6: Update vault_doctor.py

**Files:**
- Modify: `skills/parsidion/scripts/vault_doctor.py`

This file has more VAULT_ROOT references (~25). Systematic update required.

- [ ] **Step 1: Add --vault argument**

- [ ] **Step 2: Resolve vault at start of main()**

- [ ] **Step 3: Update all VAULT_ROOT references**

Use sed or manual replacement for:
- `vault_common.VAULT_ROOT` → `vault_path`
- `vault_common.get_embeddings_db_path()` → `vault_common.get_embeddings_db_path(vault_path)`
- `vault_common.ensure_vault_dirs()` → `vault_common.ensure_vault_dirs(vault_path)`
- `vault_common.all_vault_notes()` → `vault_common.all_vault_notes(vault_path)`
- `vault_common.git_commit_vault()` → `vault_common.git_commit_vault(..., vault=vault_path)`

- [ ] **Step 4: Verify and commit**

---

## Task 7: Update vault_review.py

**Files:**
- Modify: `skills/parsidion/scripts/vault_review.py`

- [ ] **Step 1: Add --vault argument**

- [ ] **Step 2: Resolve vault and update references**

- [ ] **Step 3: Verify and commit**

---

## Task 8: Update vault_export.py

**Files:**
- Modify: `skills/parsidion/scripts/vault_export.py`

- [ ] **Step 1: Add --vault argument**

- [ ] **Step 2: Resolve vault and update references**

- [ ] **Step 3: Verify and commit**

---

## Task 9: Update vault_merge.py

**Files:**
- Modify: `skills/parsidion/scripts/vault_merge.py`

- [ ] **Step 1: Add --vault argument**

- [ ] **Step 2: Resolve vault and update references**

- [ ] **Step 3: Verify and commit**

---

## Task 10: Update update_index.py

**Files:**
- Modify: `skills/parsidion/scripts/update_index.py`

- [ ] **Step 1: Add --vault argument**

- [ ] **Step 2: Resolve vault and update all VAULT_ROOT references**

- [ ] **Step 3: Verify and commit**

---

## Task 11: Update build_embeddings.py

**Files:**
- Modify: `skills/parsidion/scripts/build_embeddings.py`

- [ ] **Step 1: Add --vault argument**

- [ ] **Step 2: Resolve vault and update references**

- [ ] **Step 3: Verify and commit**

---

## Task 12: Update summarize_sessions.py

**Files:**
- Modify: `skills/parsidion/scripts/summarize_sessions.py`

Note: This is a PEP 723 script with inline dependencies.

- [ ] **Step 1: Add --vault argument to argparse**

- [ ] **Step 2: Resolve vault at start of async main**

- [ ] **Step 3: Update pending_summaries.jsonl path**

```python
pending_path = vault_path / "pending_summaries.jsonl"
```

- [ ] **Step 4: Update vault_common function calls**

- [ ] **Step 5: Verify and commit**

---

## Task 13: Update session_start_hook.py

**Files:**
- Modify: `skills/parsidion/scripts/session_start_hook.py`

- [ ] **Step 1: Resolve vault from input cwd**

After reading JSON input:

```python
def main() -> None:
    input_data = json.load(sys.stdin)
    cwd = input_data.get("cwd", os.getcwd())
    vault_path = vault_common.resolve_vault(cwd=cwd)
```

- [ ] **Step 2: Pass vault_path to all vault_common calls**

- `vault_common.load_config()` → `vault_common.load_config(vault_path)`
- `vault_common.today_daily_path()` → `vault_common.today_daily_path(vault_path)`
- `vault_common.build_compact_index()` → `vault_common.build_compact_index(vault=vault_path)` (update signature)
- `vault_common.get_embeddings_db_path()` → `vault_common.get_embeddings_db_path(vault_path)`
- `vault_common.save_last_seen()` → `vault_common.save_last_seen(..., vault=vault_path)`
- `vault_common.load_last_seen()` → `vault_common.load_last_seen(vault_path)`

- [ ] **Step 3: Update write_hook_event call**

```python
vault_common.write_hook_event(
    "SessionStart",
    project_name,
    duration_ms,
    vault=vault_path,
    notes_injected=notes_injected,
    chars=len(context),
)
```

- [ ] **Step 4: Verify and commit**

---

## Task 14: Update session_stop_hook.py

**Files:**
- Modify: `skills/parsidion/scripts/session_stop_hook.py`

- [ ] **Step 1: Resolve vault from input cwd**

- [ ] **Step 2: Pass vault to queue_for_summarization and other calls**

- [ ] **Step 3: Verify and commit**

---

## Task 15: Update pre_compact_hook.py

**Files:**
- Modify: `skills/parsidion/scripts/pre_compact_hook.py`

- [ ] **Step 1: Resolve vault from input cwd**

- [ ] **Step 2: Pass vault to write_hook_event and daily note functions**

- [ ] **Step 3: Verify and commit**

---

## Task 16: Update post_compact_hook.py

**Files:**
- Modify: `skills/parsidion/scripts/post_compact_hook.py`

- [ ] **Step 1: Resolve vault from input cwd**

- [ ] **Step 2: Pass vault to functions that read daily note**

- [ ] **Step 3: Verify and commit**

---

## Task 17: Update subagent_stop_hook.py

**Files:**
- Modify: `skills/parsidion/scripts/subagent_stop_hook.py`

- [ ] **Step 1: Resolve vault from input cwd**

- [ ] **Step 2: Pass vault to queue_for_summarization**

- [ ] **Step 3: Verify and commit**

---

## Task 18: Update install.py

**Files:**
- Modify: `install.py`

- [ ] **Step 1: Add --create-vaults-config argument**

```python
    parser.add_argument(
        "--create-vaults-config",
        action="store_true",
        help="Create ~/.config/parsidion/vaults.yaml template",
    )
```

- [ ] **Step 2: Add function to create vaults config template**

```python
def create_vaults_config(dry_run: bool = False) -> None:
    """Create vaults.yaml template with example configuration."""
    from pathlib import Path
    import os

    config_dir = Path.home() / ".config" / "parsidion"
    config_path = config_dir / "vaults.yaml"

    if config_path.exists():
        print(f"  ℹ {config_path} already exists, skipping")
        return

    content = '''# Named vaults for parsidion
# Use with: vault-search --vault NAME or CLAUDE_VAULT=NAME

vaults:
  # personal: ~/ClaudeVault
  # work: ~/WorkVault
  # team: ~/team-vault

# Optional: override default vault
# default: work
'''

    if dry_run:
        print(f"  Would create {config_path}")
        return

    config_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(content, encoding="utf-8")
    print(f"  ✓ Created {config_path}")
```

- [ ] **Step 3: Call create_vaults_config in main install flow**

Add to install steps (after installing hooks):

```python
    # Create vaults config if requested
    if args.create_vaults_config:
        create_vaults_config(dry_run=dry_run)
```

- [ ] **Step 4: Update uninstall to remove vaults.yaml**

Add to uninstall function (with confirmation):

```python
    vaults_config = Path.home() / ".config" / "parsidion" / "vaults.yaml"
    if vaults_config.exists():
        if args.yes or input(f"Remove {vaults_config}? [y/N] ").lower() == "y":
            vaults_config.unlink()
            print(f"  ✓ Removed {vaults_config}")
```

- [ ] **Step 5: Update help text**

Add to description or epilog:

```python
    epilog = """
...
Multi-vault support:
  --create-vaults-config    Create ~/.config/parsidion/vaults.yaml template
  Set CLAUDE_VAULT env var or use --vault flag on CLIs to switch vaults
"""
```

- [ ] **Step 6: Verify**

```bash
python install.py --help | grep -A2 "create-vaults-config"
```

- [ ] **Step 7: Commit**

```bash
git add install.py
git commit -m "feat(install): add --create-vaults-config for multi-vault setup"
```

---

## Task 19: Update ENHANCE.md

**Files:**
- Modify: `ENHANCE.md`

- [ ] **Step 1: Mark enhancement #18 as implemented**

Change:
```markdown
| 18 | Multi-vault support | 3 | ❌ Not Implemented |
```

To:
```markdown
| 18 | Multi-vault support | 3 | ✅ Implemented |
```

- [ ] **Step 2: Commit**

```bash
git add ENHANCE.md
git commit -m "docs: mark enhancement #18 multi-vault as implemented"
```

---

## Task 20: Final Verification

- [ ] **Step 1: Run ruff format and check**

```bash
cd /Users/probello/Repos/parsidion
uv run ruff format skills/parsidion/scripts/
uv run ruff check skills/parsidion/scripts/
```

- [ ] **Step 2: Test resolve_vault with env var**

```bash
CLAUDE_VAULT=/tmp/test-vault python -c "
import sys
sys.path.insert(0, 'skills/parsidion/scripts')
from vault_common import resolve_vault
print(resolve_vault())
"
```

Expected: `/tmp/test-vault`

- [ ] **Step 3: Test vault-search with --vault**

```bash
vault-search --vault ~/ClaudeVault "test" -n 1
```

Should work without error.

- [ ] **Step 4: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: lint and format for multi-vault support"
```

---

## Summary

| Task | Files | Est. Time |
|------|-------|-----------|
| 1 | vault_common.py (resolver) | 15 min |
| 2 | vault_common.py (functions) | 20 min |
| 3-9 | CLI scripts (7 files) | 25 min |
| 10-11 | update_index, build_embeddings | 10 min |
| 12 | summarize_sessions.py | 10 min |
| 13-17 | Hook scripts (5 files) | 15 min |
| 18 | install.py | 10 min |
| 19 | ENHANCE.md | 2 min |
| 20 | Verification | 10 min |

**Total estimated:** ~2 hours
