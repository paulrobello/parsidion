"""Vault directory setup and configuration for the Parsidion installer.

Handles creating vault subdirectories, .gitignore, git init, post-merge hook,
vault config (config.yaml: username, embeddings), and the named-vaults config
(vaults.yaml).
Stdlib-only — no third-party dependencies.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from installer.paths import (
    DEFAULT_VAULT_NAME,
    LEGACY_DEFAULT_VAULT_NAME,
    PROJECT_NAME,
    VAULT_DIRS,
)
from installer.ui import _err, _ok, _print, _step, _warn

# ---------------------------------------------------------------------------
# Vault directory creation
# ---------------------------------------------------------------------------


def create_vault_dirs(vault_root: Path, dry_run: bool = False) -> None:
    """Create required vault subdirectories and the Templates symlink."""
    from installer.ui import dim

    _step(f"Create vault directories in {vault_root}/", dry_run=dry_run)
    if dry_run:
        for d in VAULT_DIRS:
            print(f"    {dim('mkdir')} {vault_root}/{d}")
        return

    vault_root.mkdir(parents=True, exist_ok=True)
    for dirname in VAULT_DIRS:
        (vault_root / dirname).mkdir(exist_ok=True)


def create_templates_symlink(
    vault_root: Path,
    templates_src: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Create/update the Templates symlink in the vault."""
    import shutil

    from installer.ui import dim

    link = vault_root / "Templates"

    if link.is_symlink():
        existing_target = link.resolve()
        if existing_target == templates_src.resolve():
            _print(
                dim("  Templates symlink already correct"),
                verbose_only=True,
                verbose=verbose,
            )
            return
        _step(f"Update Templates symlink → {templates_src}", dry_run=dry_run)
        if not dry_run:
            link.unlink()
            try:
                link.symlink_to(templates_src)
            except OSError:
                shutil.copytree(templates_src, link, dirs_exist_ok=True)
    elif link.exists():
        try:
            is_empty = not any(link.iterdir())
        except OSError:
            is_empty = False
        if is_empty:
            _step(
                f"Replace empty Templates dir with symlink/copy → {templates_src}",
                dry_run=dry_run,
            )
            if not dry_run:
                link.rmdir()
                try:
                    link.symlink_to(templates_src)
                except OSError:
                    shutil.copytree(templates_src, link, dirs_exist_ok=True)
        else:
            _warn("Templates/ exists and is non-empty; skipping symlink creation")
    else:
        _step(f"Create Templates symlink/copy → {templates_src}", dry_run=dry_run)
        if not dry_run:
            try:
                link.symlink_to(templates_src)
            except OSError:
                shutil.copytree(templates_src, link, dirs_exist_ok=True)


# ---------------------------------------------------------------------------
# Vault git setup
# ---------------------------------------------------------------------------


def configure_vault_gitignore(vault_root: Path, dry_run: bool = False) -> None:
    """Ensure machine-local files are listed in the vault ``.gitignore``.

    Args:
        vault_root: Path to the vault root directory.
        dry_run: If True, print actions without writing.
    """
    gitignore = vault_root / ".gitignore"
    entries = [
        "embeddings.db",
        "pending_summaries.jsonl",
        "hook_events.log",
        "graph.json",
        ".obsidian/",
    ]

    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
    else:
        content = ""

    missing = [e for e in entries if e not in content]
    if not missing:
        return

    if gitignore.exists():
        _step(f"Add {', '.join(missing)} to vault .gitignore", dry_run=dry_run)
    else:
        _step(f"Create vault .gitignore with {', '.join(missing)}", dry_run=dry_run)

    if not dry_run:
        addition = "\n".join(missing) + "\n"
        gitignore.write_text(content + addition, encoding="utf-8")


def init_vault_git(vault_root: Path, dry_run: bool = False) -> None:
    """Initialize the vault as a git repository if it isn't one already.

    Args:
        vault_root: Path to the vault root directory.
        dry_run: If True, print what would be done without writing.
    """
    git_dir = vault_root / ".git"
    if git_dir.exists():
        return

    _step("Initialize vault as a git repository", dry_run=dry_run)
    if dry_run:
        return

    subprocess.run(["git", "init"], cwd=vault_root, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=vault_root, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "chore(vault): initial commit"],
        cwd=vault_root,
        capture_output=True,
    )
    _ok(f"Git repo initialized at {vault_root}")


# Marker comment used to identify our post-merge hook.
_POST_MERGE_MARKER = "# parsidion post-merge hook"

_POST_MERGE_HOOK_TEMPLATE = """\
#!/bin/bash
{marker} — rebuilds vault index and embeddings after pull
set -e
echo "[parsidion] Rebuilding vault index..."
uv run --no-project {scripts_dir}/update_index.py
echo "[parsidion] Updating embeddings (incremental)..."
uv run {scripts_dir}/build_embeddings.py --incremental
echo "[parsidion] Post-merge sync complete."
"""


def install_vault_post_merge_hook(
    vault_root: Path,
    claude_dir: Path,
    dry_run: bool = False,
) -> None:
    """Install a git post-merge hook in the vault for multi-machine sync.

    Args:
        vault_root: Path to the vault root directory.
        claude_dir: Path to the Claude configuration directory.
        dry_run: If True, print what would be done without writing.
    """
    from installer.paths import SKILL_NAME

    git_dir = vault_root / ".git"
    if not git_dir.is_dir():
        return

    hooks_dir = git_dir / "hooks"
    hook_path = hooks_dir / "post-merge"

    scripts_dir = claude_dir / "skills" / SKILL_NAME / "scripts"
    try:
        rel = scripts_dir.relative_to(Path.home())
        scripts_rel = f"~/{rel.as_posix()}"
    except ValueError:
        scripts_rel = scripts_dir.as_posix()

    if hook_path.exists():
        existing = hook_path.read_text(encoding="utf-8")
        if _POST_MERGE_MARKER in existing:
            return
        _warn(
            f"Vault post-merge hook already exists (not ours): {hook_path}\n"
            "       Skipping to avoid overwriting your custom hook."
        )
        return

    _step("Install vault git post-merge hook (multi-machine sync)", dry_run=dry_run)
    if dry_run:
        return

    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_content = _POST_MERGE_HOOK_TEMPLATE.format(
        marker=_POST_MERGE_MARKER,
        scripts_dir=scripts_rel,
    )
    hook_path.write_text(hook_content, encoding="utf-8")
    hook_path.chmod(0o755)


def remove_vault_post_merge_hook(
    vault_root: Path,
    dry_run: bool = False,
) -> None:
    """Remove the parsidion post-merge hook from the vault if present.

    Args:
        vault_root: Path to the vault root directory.
        dry_run: If True, print what would be done without writing.
    """
    hook_path = vault_root / ".git" / "hooks" / "post-merge"
    if not hook_path.exists():
        return

    content = hook_path.read_text(encoding="utf-8")
    if _POST_MERGE_MARKER not in content:
        return

    _step(f"Remove vault post-merge hook: {hook_path}", dry_run=dry_run)
    if not dry_run:
        hook_path.unlink()


# ---------------------------------------------------------------------------
# Vault configuration (config.yaml)
# ---------------------------------------------------------------------------


def configure_vault_username(
    vault_root: Path,
    dry_run: bool = False,
    username: str = "",
) -> None:
    """Write the vault username into ``config.yaml`` if not already set.

    Args:
        vault_root: Path to the vault root directory.
        dry_run: If True, print actions without writing.
        username: Explicit username to use; falls back to ``$USER`` if empty.
    """
    if not username:
        username = os.environ.get("USER", os.environ.get("USERNAME", "")).strip()
    if not username:
        return

    config_path = vault_root / "config.yaml"

    if config_path.exists():
        try:
            content = config_path.read_text(encoding="utf-8")
        except OSError:
            content = ""
    else:
        content = ""

    username_set = re.search(r"(?m)^\s+username\s*:\s*(?!\"?\"\s*$)(\S+)", content)
    if username_set:
        return

    _step(f"Set vault.username = {username!r} in {config_path}", dry_run=dry_run)
    if dry_run:
        return

    if re.search(r"(?m)^\s+username\s*:\s*\"?\"\s*$", content):
        new_content = re.sub(
            r"(?m)^(\s+username\s*:)\s*\"?\"\s*$",
            rf'\1 "{username}"',
            content,
        )
    elif "vault:" in content:
        new_content = re.sub(
            r"(?m)^(vault:)",
            rf"\1\n  username: \"{username}\"",
            content,
            count=1,
        )
    else:
        vault_section = (
            "\n# Vault identity — used for per-user daily note filenames (team vault sharing)\n"
            f'vault:\n  username: "{username}"  # Username suffix for daily notes (DD-{{username}}.md). Change if desired.\n'
        )
        new_content = content.rstrip("\n") + "\n" + vault_section

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(new_content, encoding="utf-8")
    except OSError as exc:
        _warn(f"Could not write vault.username to {config_path}: {exc}")


def read_embeddings_enabled(vault_root: Path, *, default: bool = True) -> bool:
    """Read the current ``embeddings.enabled`` value from the vault config.

    Returns *default* when the config file, the ``embeddings:`` section, or the
    ``enabled:`` key is absent — so a fresh install with no prior setting still
    defaults to enabled.
    """
    config_path = vault_root / "config.yaml"
    if not config_path.exists():
        return default
    try:
        content = config_path.read_text(encoding="utf-8")
    except OSError:
        return default
    emb_match = re.search(r"(?m)^embeddings:", content)
    if not emb_match:
        return default
    rest = content[emb_match.start() + len("embeddings:") :]
    next_section = re.search(r"(?m)^\S", rest)
    section = rest[: next_section.start() if next_section else len(rest)]
    enabled = re.search(r"(?m)^\s+enabled\s*:\s*(true|false)", section)
    if not enabled:
        return default
    return enabled.group(1) == "true"


def configure_embeddings(
    vault_root: Path, *, enabled: bool, dry_run: bool = False
) -> None:
    """Write ``embeddings.enabled`` to the vault's ``config.yaml``.

    Args:
        vault_root: Path to the vault root directory.
        enabled: Whether embeddings should be enabled.
        dry_run: If True, print actions without writing.
    """
    config_path = vault_root / "config.yaml"

    if config_path.exists():
        try:
            content = config_path.read_text(encoding="utf-8")
        except OSError:
            content = ""
    else:
        content = ""

    enabled_str = "true" if enabled else "false"

    match = re.search(r"(?m)^\s+enabled\s*:\s*(true|false)", content)
    if match:
        emb_match = re.search(r"(?m)^embeddings:", content)
        if emb_match:
            section_start = emb_match.start()
            next_section = re.search(
                r"(?m)^\S", content[section_start + len("embeddings:") :]
            )
            section_end = (
                section_start + len("embeddings:") + next_section.start()
                if next_section
                else len(content)
            )
            section = content[section_start:section_end]

            enabled_in_section = re.search(
                r"(?m)^\s+enabled\s*:\s*(true|false)", section
            )
            if enabled_in_section:
                if enabled_in_section.group(1) == enabled_str:
                    return
                abs_start = section_start + enabled_in_section.start(1)
                abs_end = section_start + enabled_in_section.end(1)
                new_content = content[:abs_start] + enabled_str + content[abs_end:]
            else:
                new_content = content.replace(
                    "embeddings:",
                    f"embeddings:\n  enabled: {enabled_str}",
                    1,
                )
        else:
            emb_section = (
                "\n# Embeddings / semantic search (build_embeddings.py, vault_search.py)\n"
                f"embeddings:\n  enabled: {enabled_str}\n"
            )
            new_content = content.rstrip("\n") + "\n" + emb_section
    elif "embeddings:" in content:
        new_content = content.replace(
            "embeddings:",
            f"embeddings:\n  enabled: {enabled_str}",
            1,
        )
    else:
        emb_section = (
            "\n# Embeddings / semantic search (build_embeddings.py, vault_search.py)\n"
            f"embeddings:\n  enabled: {enabled_str}\n"
        )
        new_content = content.rstrip("\n") + "\n" + emb_section

    _step(f"Set embeddings.enabled = {enabled_str} in {config_path}", dry_run=dry_run)
    if dry_run:
        return

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(new_content, encoding="utf-8")
    except OSError as exc:
        _warn(f"Could not write embeddings.enabled to {config_path}: {exc}")


# ---------------------------------------------------------------------------
# Named vaults config
# ---------------------------------------------------------------------------


def create_vaults_config(dry_run: bool = False) -> None:
    """Create vaults.yaml template with example configuration.

    Creates ``~/.config/parsidion/vaults.yaml`` with commented examples for
    named vault configuration.

    Args:
        dry_run: If True, print what would be done without writing.
    """
    config_dir = Path.home() / ".config" / PROJECT_NAME
    config_path = config_dir / "vaults.yaml"

    if config_path.exists():
        print(f"  ℹ {config_path} already exists, skipping")
        return

    content = """# Named vaults for parsidion
# Use with: vault-search --vault NAME or CLAUDE_VAULT=NAME

vaults:
  # personal: ~/ParsidionVault
  # legacy: ~/ClaudeVault
  # work: ~/WorkVault
  # team: ~/team-vault

# Optional: override default vault
# default: work
"""

    _step(f"Create vaults config template: {config_path}", dry_run=dry_run)
    if dry_run:
        return

    config_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(content, encoding="utf-8")
    _ok(f"Created {config_path}")


# ---------------------------------------------------------------------------
# Vault migration
# ---------------------------------------------------------------------------


def migrate_default_vault(
    *,
    dry_run: bool = False,
    create_legacy_symlink: bool = True,
    home: Path | None = None,
) -> int:
    """Rename legacy ``~/ClaudeVault`` to ``~/ParsidionVault`` safely.

    Returns:
        Process-style status code: 0 on success/no-op, 2 for unsafe states.
    """
    from installer.colors import bold, dim

    root = home or Path.home()
    legacy = root / LEGACY_DEFAULT_VAULT_NAME
    current = root / DEFAULT_VAULT_NAME

    print()
    print(bold("Parsidion Vault Migration"))
    print(f"  {dim('Legacy:')} {legacy}")
    print(f"  {dim('Target:')} {current}")
    print()

    if current.exists():
        if legacy.is_symlink() and legacy.resolve() == current.resolve():
            _ok("Vault is already migrated; legacy path is a compatibility symlink.")
            return 0
        if not legacy.exists():
            _ok("Vault is already migrated.")
            return 0
        _err(
            f"Both {legacy} and {current} exist. Refusing to guess which vault to keep."
        )
        return 2

    if legacy.is_symlink():
        _err(f"Legacy path is a symlink but target vault does not exist: {legacy}")
        return 2

    if not legacy.exists():
        _err(f"No legacy vault found at {legacy}")
        return 2

    if not legacy.is_dir():
        _err(f"Legacy vault path is not a directory: {legacy}")
        return 2

    _step(f"Move {legacy} -> {current}", dry_run=dry_run)
    if create_legacy_symlink:
        _step(f"Create compatibility symlink {legacy} -> {current}", dry_run=dry_run)

    if dry_run:
        _ok("Dry run complete — no changes were made.")
        return 0

    try:
        legacy.rename(current)
    except OSError as exc:
        _err(f"Could not move vault: {exc}")
        return 2

    if create_legacy_symlink:
        try:
            legacy.symlink_to(current, target_is_directory=True)
        except OSError as exc:
            _warn(f"Vault moved, but compatibility symlink could not be created: {exc}")

    _ok(f"Migrated vault to {current}")
    if create_legacy_symlink and legacy.is_symlink():
        print(dim(f"  Legacy compatibility path: {legacy} -> {current}"))
    print(dim("  Run update_index.py after migration to refresh generated indexes."))
    return 0
