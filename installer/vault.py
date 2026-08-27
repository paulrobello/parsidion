"""Vault directory setup and configuration for the Parsidion installer.

Handles creating vault subdirectories, .gitignore, git init, post-merge hook,
vault config (config.yaml: username, embeddings), and the named-vaults config
(vaults.yaml).
Stdlib-only — no third-party dependencies.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

from installer.colors import bold, dim
from installer.hooks import _atomic_write_text
from installer.paths import (
    DEFAULT_VAULT_NAME,
    LEGACY_DEFAULT_VAULT_NAME,
    PROJECT_NAME,
    SKILL_NAME,
    VAULT_DIRS,
)
from installer.ui import _err, _ok, _print, _step, _warn

# QA-004: the shared vaults.yaml reader/writer live in core.vault_path; the
# scripts dir is on sys.path via installer/__init__.py (ENH-006 pattern).
from core.vault_path import read_vaults_yaml, render_vaults_yaml

# ---------------------------------------------------------------------------
# Vault directory creation
# ---------------------------------------------------------------------------


def create_vault_dirs(vault_root: Path, dry_run: bool = False) -> None:
    """Create required vault subdirectories and the Templates symlink."""
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
    """Create/update the Templates symlink in the vault.

    ARC-022: the ``unlink`` / ``rmdir`` that prepares the destination are
    inside the surrounding ``try`` so an ``OSError`` warns and aborts cleanly
    rather than killing the process mid-install with a traceback.
    """
    import shutil

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
            # ARC-022: unlink inside the try so a failure (race with another
            # process, read-only mount, etc.) warns instead of raising.
            try:
                link.unlink()
                link.symlink_to(templates_src)
            except OSError as exc:
                _warn(
                    f"Could not replace Templates symlink ({exc}); falling back to copy"
                )
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
                # ARC-022: rmdir inside the try for the same reason as above.
                try:
                    link.rmdir()
                    link.symlink_to(templates_src)
                except OSError as exc:
                    _warn(
                        f"Could not replace Templates dir ({exc}); falling back to copy"
                    )
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
        # Globs cover timestamped/.bak variants produced by migration code
        # (e.g. pending_summaries.jsonl.bak-20260712-092800). SEC-104.
        "embeddings.db*",
        "pending_summaries.jsonl*",
        "dead_letters.jsonl*",
        "hook_events.log*",
        "graph.json",
        "summarizer_state.json",
        "doctor_state.json",
        ".obsidian/",
        # config.yaml / config.local.yaml may hold ANTHROPIC_API_KEY /
        # ANTHROPIC_AUTH_TOKEN (anthropic_env section) — never sync to a remote.
        "config.yaml",
        "config.local.yaml",
        "conflicts/",
        # Defence in depth against SEC-101: a pyproject.toml / uv.toml /
        # setup.py / .venv in the vault worktree is what `uv run` without
        # --no-project would execute the build backend of. SEC-101.
        "pyproject.toml",
        "uv.toml",
        "setup.py",
        ".venv/",
        # atomic_write_text / build_graph stage writes through <name>.tmp
        # siblings before the rename; a stale or planted tmp must never be
        # synced. SEC-005.
        "*.tmp",
        # Staged merge previews (vault-merge --dry-run cache) can hold note
        # bodies; machine-local. SEC-013.
        ".merge_previews/",
        # Doctor singleton lock (SEC-016 flock); machine-local.
        ".doctor.lock",
        # Daily-note sibling locks (SEC-015 flock); machine-local.
        "*.lock",
    ]

    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
    else:
        content = ""

    # Line-wise comparison so a commented `# config.yaml` already present in
    # the file does not suppress the real `config.yaml` entry. SEC-104.
    existing_lines = {ln.strip() for ln in content.splitlines()}
    missing = [e for e in entries if e not in existing_lines]
    if not missing:
        return

    if gitignore.exists():
        _step(f"Add {', '.join(missing)} to vault .gitignore", dry_run=dry_run)
    else:
        _step(f"Create vault .gitignore with {', '.join(missing)}", dry_run=dry_run)

    if not dry_run:
        addition = "\n".join(missing) + "\n"
        _atomic_write_text(gitignore, content + addition)


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

    # QA-005: bound the init/add/commit sequence. These are fast local
    # operations on a fresh vault, but a hung git binary (NFS stall, stale
    # credential helper, locked index) would otherwise stall the installer
    # indefinitely with no error path. 30 s each is generous; on timeout
    # we leave the vault partially initialised and surface a warning
    # rather than propagating the timeout into the install flow.
    try:
        subprocess.run(["git", "init"], cwd=vault_root, capture_output=True, timeout=30)
        subprocess.run(
            ["git", "add", "-A"], cwd=vault_root, capture_output=True, timeout=30
        )
        subprocess.run(
            ["git", "commit", "-m", "chore(vault): initial commit"],
            cwd=vault_root,
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        _warn(
            "git init/commit timed out after 30s — vault left partially "
            "initialised. Run `git init && git add -A && git commit` manually."
        )
        return
    _ok(f"Git repo initialized at {vault_root}")


# Marker comment used to identify our post-merge hook.
# SEC-012: bumped to v2 — the v1 template interpolated "~/..." inside double
# quotes, where neither bash nor uv expands ~, so every post-merge run died
# under `set -e` after each `git pull`. v1 hooks are recognised as stale-ours
# and regenerated on the next install.
_POST_MERGE_MARKER = "# parsidion post-merge hook v2"
# Markers from older parsidion releases whose hooks are still installed on
# real machines. Recognising them lets install_vault_post_merge_hook
# regenerate a stale hook instead of skipping it as "not ours". SEC-101/SEC-012.
_POST_MERGE_LEGACY_MARKERS = (
    "# parsidion-cc post-merge hook",
    "# parsidion post-merge hook",
)

_POST_MERGE_HOOK_TEMPLATE = """\
#!/bin/bash
{marker} — rebuilds vault index and embeddings after pull
set -e
echo "[parsidion] Rebuilding vault index..."
uv run --no-project {update_index_script}
echo "[parsidion] Updating embeddings (incremental)..."
uv run --no-project {build_embeddings_script} --incremental
echo "[parsidion] Post-merge sync complete."
"""


def _is_current_post_merge_hook(existing: str) -> bool:
    """Return True when *existing* is byte-equivalent to the current template.

    A hook that carries our current marker but lacks `--no-project` on every
    `uv run` line is the SEC-101 defect and must be regenerated, not skipped.
    A hook carrying any pre-v2 marker is the SEC-012 double-quoted-`~` defect
    and is likewise not current (the marker itself must match v2 exactly).
    """
    if _POST_MERGE_MARKER not in existing:
        return False
    uv_run_lines = [
        ln for ln in existing.splitlines() if ln.lstrip().startswith("uv run")
    ]
    if not uv_run_lines:
        return False
    return all("--no-project" in ln for ln in uv_run_lines)


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
    git_dir = vault_root / ".git"
    if not git_dir.is_dir():
        return

    hooks_dir = git_dir / "hooks"
    hook_path = hooks_dir / "post-merge"

    scripts_dir = claude_dir / "skills" / SKILL_NAME / "scripts"
    # SEC-012: the hook used to interpolate "~/..." inside double quotes,
    # where neither bash nor uv expands ~ — every post-merge run died under
    # `set -e`. Emit shell-quoted absolute script paths instead, which also
    # survive homes containing spaces.
    update_index_script = shlex.quote(str(scripts_dir / "update_index.py"))
    build_embeddings_script = shlex.quote(str(scripts_dir / "build_embeddings.py"))

    if hook_path.exists():
        existing = hook_path.read_text(encoding="utf-8")
        if _is_current_post_merge_hook(existing):
            return
        # Recognise stale-but-ours: current marker with a body predating the
        # --no-project fix, or any legacy marker from earlier releases. In
        # both cases the hook is ours, so regenerate it rather than leaving
        # the user with a vulnerable or dead hook (SEC-101).
        stale_ours = _POST_MERGE_MARKER in existing or any(
            marker in existing for marker in _POST_MERGE_LEGACY_MARKERS
        )
        if stale_ours:
            _step(
                f"Refresh existing parsidion post-merge hook "
                f"(stale template or legacy marker): {hook_path}",
                dry_run=dry_run,
            )
        else:
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
        update_index_script=update_index_script,
        build_embeddings_script=build_embeddings_script,
    )
    # ARC-018: atomic write + chmod via tmp so a crash mid-write cannot leave
    # the hook half-written (the prior write_text was non-atomic).
    _atomic_write_text(hook_path, hook_content)
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
    # SEC-012: also recognise pre-v2 hooks so uninstall removes them instead
    # of leaving the broken double-quoted-~ version behind.
    if _POST_MERGE_MARKER not in content and not any(
        marker in content for marker in _POST_MERGE_LEGACY_MARKERS
    ):
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
        _atomic_write_text(config_path, new_content)
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
        _atomic_write_text(config_path, new_content)
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

# Optional: default vault used when no flag, project file, or env var names one.
# Value is a vault name from vaults: above (or a registered path); unresolvable values are ignored.
# default: work
"""

    _step(f"Create vaults config template: {config_path}", dry_run=dry_run)
    if dry_run:
        return

    config_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(config_path, content)
    _ok(f"Created {config_path}")


def record_installed_vault(vault_root: Path, dry_run: bool = False) -> None:
    """Persist *vault_root* into ``~/.config/parsidion/vaults.yaml``.

    ARC-019: when ``install.py --vault /custom/path`` populates a non-default
    vault, the chosen path was previously written into *none* of the four
    channels ``resolve_vault()`` reads (explicit arg, ``.claude/vault`` file,
    ``$CLAUDE_VAULT``, default root). Every installed hook therefore kept
    reading ``~/ParsidionVault`` while the user had explicitly installed
    elsewhere. This function records the install path both as a named entry
    under ``vaults:`` (named after the directory stem — e.g. ``WorkVault``)
    and as the ``default:`` so subsequent ``resolve_vault()`` calls land on
    it without any extra env var.

    Creates the file when absent — does *not* require ``--create-vaults-config``.
    No-op when *vault_root* already matches the default vault path (the
    common case — there's nothing to persist).
    """
    # Avoid touching the file for the default-vault install: the resolver's
    # branch 4 already finds ~/ParsidionVault and writing a `default:` for
    # it would just add noise.
    if vault_root == _default_vault_path():
        return

    config_dir = Path.home() / ".config" / PROJECT_NAME
    config_path = config_dir / "vaults.yaml"

    # Stable-ish name from the directory stem — what a human would pick.
    vault_name = vault_root.name or "custom"

    if config_path.exists():
        try:
            content = config_path.read_text(encoding="utf-8")
        except OSError:
            content = ""
    else:
        content = ""

    # QA-004: shared reader/renderer from core.vault_path — the installer
    # no longer carries its own vaults.yaml parser/writer.
    vaults_lines, default_line = read_vaults_yaml(config_path)
    vault_path_str = str(vault_root)

    # Idempotency: if both the named entry and default already point at this
    # path, there's nothing to do.
    if (
        vaults_lines.get(vault_name) == vault_path_str
        and default_line == vault_path_str
    ):
        return

    new_content = render_vaults_yaml(
        vaults_lines, default_line or "", vault_name, vault_path_str, content
    )

    _step(
        f"Record vault {vault_name} → {vault_root} in {config_path}",
        dry_run=dry_run,
    )
    if dry_run:
        return

    config_dir.mkdir(parents=True, exist_ok=True)
    try:
        _atomic_write_text(config_path, new_content)
        _ok(f"Recorded {vault_name} as default vault in {config_path}")
    except OSError as exc:
        _warn(f"Could not record vault in {config_path}: {exc}")


def _default_vault_path() -> Path:
    """Return the default vault root, mirroring ``vault_path.default_vault_root``.

    Local copy (rather than importing vault_path) so the installer keeps its
    stdlib-only constraint — vault_path imports fine standalone, but the
    installer layering rule is to depend only on installer.colors/paths/ui.
    """
    root = Path.home()
    current = root / DEFAULT_VAULT_NAME
    legacy = root / LEGACY_DEFAULT_VAULT_NAME
    if legacy.exists() and not current.exists():
        return legacy
    return current


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
