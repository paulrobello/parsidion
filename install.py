#!/usr/bin/env python3
"""Parsidion CC installer.

Installs the Claude Vault skill, hooks, and research agent into ~/.claude/.
Prompts interactively for the Obsidian vault location and customizes the
installation accordingly. Merges hooks into ~/.claude/settings.json without
overwriting existing configuration.

Usage:
    uv run install.py [options]
    python install.py [options]

Options:
    --vault PATH        Vault path (skips interactive prompt)
    --claude-dir PATH   Target ~/.claude directory (default: ~/.claude)
    --dry-run, -n       Preview actions without making changes
    --verbose, -v       Show detailed output
    --force, -f         Overwrite existing skill files
    --skip-hooks        Do not modify settings.json
    --skip-agent        Do not install any agents
    --uninstall         Remove installed skill, agent, and hooks
    --help, -h          Show this help message
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# ANSI colours (disabled when not a TTY or NO_COLOR is set)
# ---------------------------------------------------------------------------

_USE_COLOUR = sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _colorize(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text


def bold(t: str) -> str:
    return _colorize("1", t)


def green(t: str) -> str:
    return _colorize("92", t)


def yellow(t: str) -> str:
    return _colorize("93", t)


def red(t: str) -> str:
    return _colorize("91", t)


def cyan(t: str) -> str:
    return _colorize("96", t)


def dim(t: str) -> str:
    return _colorize("2", t)


# ---------------------------------------------------------------------------
# Source layout (relative to this script)
# ---------------------------------------------------------------------------

REPO_ROOT: Path = Path(__file__).parent.resolve()
SKILL_SRC: Path = REPO_ROOT / "skills" / "claude-vault"
AGENT_SRCS: list[Path] = [
    REPO_ROOT / "agents" / "research-documentation-agent.md",
    REPO_ROOT / "agents" / "vault-explorer.md",
]
SCRIPTS_SRC: Path = REPO_ROOT / "scripts"
CLAUDE_VAULT_MD_SRC: Path = REPO_ROOT / "CLAUDE-VAULT.md"

# Hook script filenames installed inside the skill.
# SessionEnd uses a shell wrapper that outputs {} immediately and runs the
# real hook detached — prevents "Hook cancelled" when Claude Code exits fast.
_HOOK_SCRIPTS: dict[str, str] = {
    "SessionStart": "session_start_hook.py",
    "SessionEnd": "session_stop_wrapper.sh",
    "PreCompact": "pre_compact_hook.py",
    "SubagentStop": "subagent_stop_hook.py",
}

# Per-event hook options merged into the hook handler entry in settings.json.
# Keys match event names in _HOOK_SCRIPTS.
_HOOK_OPTIONS: dict[str, dict] = {
    "SubagentStop": {"async": True},
}

# Vault subdirectories to create.
# IMPORTANT: vault_common.py VAULT_DIRS is the canonical source for this list.
# This copy exists because install.py must remain stdlib-only and cannot import
# vault_common at runtime (it runs before the skill is installed).
# Keep this list identical to vault_common.VAULT_DIRS.  See ARC-012 in AUDIT.md.
VAULT_DIRS: list[str] = [
    "Daily",
    "Projects",
    "Languages",
    "Frameworks",
    "Patterns",
    "Debugging",
    "Tools",
    "Research",
    "Templates",
    "History",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print(msg: str, verbose_only: bool = False, verbose: bool = False) -> None:
    if verbose_only and not verbose:
        return
    print(msg)


def _ask(prompt: str, default: str = "") -> str:
    """Prompt the user for input, returning *default* on empty reply."""
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{cyan('?')} {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    return answer if answer else default


def _confirm(prompt: str, default: bool = True) -> bool:
    """Ask a yes/no question; return True for yes."""
    hint = "Y/n" if default else "y/N"
    try:
        answer = input(f"{cyan('?')} {prompt} [{hint}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    if not answer:
        return default
    return answer in ("y", "yes")


def _step(label: str, dry_run: bool = False) -> None:
    prefix = yellow("[dry-run]") if dry_run else green("  +")
    print(f"{prefix} {label}")


def _warn(msg: str) -> None:
    print(f"{yellow('  !')} {msg}")


def _err(msg: str) -> None:
    print(f"{red('  ✗')} {msg}", file=sys.stderr)


def _ok(msg: str) -> None:
    print(f"{green('  ✓')} {msg}")


# ---------------------------------------------------------------------------
# Vault path validation
# ---------------------------------------------------------------------------

_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    str(Path.home() / ".claude"),
    # Unix system directories
    "/System",
    "/usr",
    "/bin",
    "/sbin",
    "/etc",
    "/var",
    "/tmp",
    str(Path.home() / "Library"),
    # Windows system directories
    str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))),
    str(Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))),
    str(Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))),
    str(Path(os.environ.get("SYSTEMDRIVE", "C:") + "\\Windows")),
)


def validate_vault_path(raw: str) -> tuple[Path, str | None]:
    """Expand and validate the vault path.

    Returns:
        (resolved_path, error_message) — error is None when valid.
    """
    if not raw.strip():
        return Path(), "Path cannot be empty."

    expanded = Path(raw).expanduser().resolve()

    for forbidden in _FORBIDDEN_PREFIXES:
        if str(expanded).startswith(forbidden):
            return expanded, f"Cannot use system or Claude config directory: {expanded}"

    return expanded, None


def prompt_vault_path(default: Path) -> Path:
    """Interactively prompt for the Obsidian vault path with validation."""
    print()
    print(bold("Obsidian Vault Location"))
    print(
        dim(
            "This is where Claude Vault will store your knowledge notes.\n"
            "It can be an existing Obsidian vault or a new directory."
        )
    )
    while True:
        raw = _ask("Vault path", str(default))
        vault_path, error = validate_vault_path(raw)
        if error:
            _err(error)
            continue
        if vault_path.exists() and not vault_path.is_dir():
            _err(f"Path exists but is not a directory: {vault_path}")
            continue
        if not vault_path.exists():
            print(f"  {dim(str(vault_path))} does not exist.")
            if not _confirm("Create it?", default=True):
                continue
        return vault_path


# ---------------------------------------------------------------------------
# Skill installation
# ---------------------------------------------------------------------------

# Regex patterns to patch vault_common.py paths
_VAULT_ROOT_RE = re.compile(r"^(VAULT_ROOT\s*:\s*Path\s*=\s*).*$", re.MULTILINE)
_TEMPLATES_DIR_RE = re.compile(r"^(TEMPLATES_DIR\s*:\s*Path\s*=\s*).*$", re.MULTILINE)


def patch_vault_common(
    installed_path: Path,
    vault_root: Path,
    templates_dir: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Patch VAULT_ROOT and TEMPLATES_DIR in the installed vault_common.py."""
    target = installed_path / "scripts" / "vault_common.py"
    if not target.exists():
        _warn(f"vault_common.py not found at {target} — skipping patch")
        return

    content = target.read_text(encoding="utf-8")

    vault_repr = f'Path("{vault_root}")'
    templates_repr = f'Path("{templates_dir}")'

    new_content = _VAULT_ROOT_RE.sub(rf"\g<1>{vault_repr}", content)
    new_content = _TEMPLATES_DIR_RE.sub(rf"\g<1>{templates_repr}", new_content)

    if new_content == content:
        _print(
            dim("  vault_common.py paths already match — no patch needed"),
            verbose_only=True,
            verbose=verbose,
        )
        return

    _step(
        f"Patch vault_common.py: VAULT_ROOT={vault_root}, TEMPLATES_DIR={templates_dir}",
        dry_run=dry_run,
    )
    if not dry_run:
        target.write_text(new_content, encoding="utf-8")


def install_skill(
    claude_dir: Path,
    vault_root: Path,
    force: bool = False,
    yes: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> Path:
    """Copy skill directory to ~/.claude/skills/claude-vault/.

    Returns the installed skill path.
    """
    dest = claude_dir / "skills" / "claude-vault"

    if dest.exists() and not force and not dry_run:
        _warn(f"Skill already exists at {dest}")
        if not yes and not _confirm("Overwrite existing skill files?", default=False):
            print(f"  {dim('Skipping skill installation.')}")
            return dest
        elif yes:
            _print(
                dim("  Overwriting existing skill (--yes)"),
                verbose_only=True,
                verbose=verbose,
            )

    _step(f"Install skill: {SKILL_SRC} → {dest}", dry_run=dry_run)

    if not dry_run:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(SKILL_SRC, dest)
        # Remove any compiled cache from source
        for pycache in dest.rglob("__pycache__"):
            shutil.rmtree(pycache, ignore_errors=True)
        # Make hook scripts executable (Unix only; no-op on Windows)
        if sys.platform != "win32":
            for script in (dest / "scripts").glob("*.py"):
                script.chmod(script.stat().st_mode | 0o755)
            for script in (dest / "scripts").glob("*.sh"):
                script.chmod(script.stat().st_mode | 0o755)

    templates_dir = claude_dir / "skills" / "claude-vault" / "templates"
    patch_vault_common(
        dest, vault_root, templates_dir, dry_run=dry_run, verbose=verbose
    )

    return dest


def install_agents(
    claude_dir: Path,
    dry_run: bool = False,
) -> None:
    """Copy all agents to ~/.claude/agents/, skipping missing sources with a warning."""
    agents_dir = claude_dir / "agents"
    if not dry_run:
        agents_dir.mkdir(parents=True, exist_ok=True)
    for agent_src in AGENT_SRCS:
        if not agent_src.exists():
            _warn(f"Agent source not found: {agent_src} — skipping")
            continue
        dest = agents_dir / agent_src.name
        _step(f"Install agent: {agent_src.name} → {agents_dir}/", dry_run=dry_run)
        if not dry_run:
            shutil.copy2(agent_src, dest)


def install_scripts(
    claude_dir: Path,
    dry_run: bool = False,
) -> None:
    """Copy scripts/ to ~/.claude/scripts/, making each script executable."""
    if not SCRIPTS_SRC.exists():
        _warn(f"Scripts source not found: {SCRIPTS_SRC} — skipping")
        return
    scripts_dir = claude_dir / "scripts"
    _step(f"Install scripts: {SCRIPTS_SRC} → {scripts_dir}/", dry_run=dry_run)
    if not dry_run:
        scripts_dir.mkdir(parents=True, exist_ok=True)
        for script in SCRIPTS_SRC.iterdir():
            if script.is_file():
                dest = scripts_dir / script.name
                shutil.copy2(script, dest)
                if sys.platform != "win32":
                    dest.chmod(dest.stat().st_mode | 0o755)


# ---------------------------------------------------------------------------
# Vault directory setup
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
    """Create/update the Templates symlink in the vault."""
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
            link.symlink_to(templates_src)
    elif link.exists():
        # It's a real directory — only replace if empty
        try:
            is_empty = not any(link.iterdir())
        except OSError:
            is_empty = False
        if is_empty:
            _step(
                f"Replace empty Templates dir with symlink → {templates_src}",
                dry_run=dry_run,
            )
            if not dry_run:
                link.rmdir()
                link.symlink_to(templates_src)
        else:
            _warn("Templates/ exists and is non-empty; skipping symlink creation")
    else:
        _step(f"Create Templates symlink → {templates_src}", dry_run=dry_run)
        if not dry_run:
            try:
                link.symlink_to(templates_src)
            except OSError as exc:
                _warn(f"Could not create symlink ({exc}); leaving as plain directory")
                link.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Hook registration in settings.json
# ---------------------------------------------------------------------------


def _hook_command(claude_dir: Path, event: str) -> str:
    """Return the hook command string for a given event.

    Uses ~ notation so the path is portable across user accounts.
    Shell scripts (.sh) are invoked directly; Python scripts are run via
    ``uv run --no-project`` to ensure the correct Python interpreter.
    """
    script = _HOOK_SCRIPTS[event]
    script_path = claude_dir / "skills" / "claude-vault" / "scripts" / script
    # Replace home dir with ~ for portability; use forward slashes so the
    # command works on both Unix and Windows (Claude Code and uv handle ~ expansion).
    try:
        rel = script_path.relative_to(Path.home())
        rel_str = f"~/{rel.as_posix()}"
    except ValueError:
        rel_str = script_path.as_posix()

    if script.endswith(".sh"):
        return rel_str
    return f"uv run --no-project {rel_str}"


def _hook_already_registered(hooks_list: list[dict], command: str) -> bool:
    """Return True if any entry in hooks_list already has this command."""
    for entry in hooks_list:
        for hook in entry.get("hooks", []):
            if hook.get("command", "") == command:
                return True
    return False


def merge_hooks(
    claude_dir: Path,
    settings_file: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Load settings.json, add vault hooks if missing, write back."""
    # Load existing settings
    settings: dict = {}
    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            _warn(f"Could not read {settings_file}: {exc}")
            settings = {}
    else:
        _warn(f"{settings_file} not found — creating a minimal one")

    hooks_section: dict = settings.setdefault("hooks", {})
    added: list[str] = []
    skipped: list[str] = []

    for event, script_name in _HOOK_SCRIPTS.items():
        command = _hook_command(claude_dir, event)
        event_hooks: list[dict] = hooks_section.setdefault(event, [])

        if _hook_already_registered(event_hooks, command):
            _print(
                dim(f"  Hook {event} already registered"),
                verbose_only=True,
                verbose=verbose,
            )
            skipped.append(event)
            continue

        hook_handler: dict = {
            "type": "command",
            "command": command,
            "timeout": 10000,
        }
        # Apply per-event options (e.g. async: true for SubagentStop)
        hook_handler.update(_HOOK_OPTIONS.get(event, {}))

        new_entry: dict = {
            "matcher": "",
            "hooks": [hook_handler],
        }
        _step(f"Register hook {bold(event)}: {dim(command)}", dry_run=dry_run)
        if not dry_run:
            event_hooks.append(new_entry)
        added.append(event)

    if dry_run:
        return

    if added:
        try:
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            settings_file.write_text(
                json.dumps(settings, indent=2) + "\n", encoding="utf-8"
            )
            _ok(f"Updated {settings_file}")
        except OSError as exc:
            _err(f"Could not write {settings_file}: {exc}")
    elif skipped:
        _ok("All hooks already registered")


# ---------------------------------------------------------------------------
# CLAUDE-VAULT.md installation
# ---------------------------------------------------------------------------

_CLAUDE_VAULT_MD_IMPORT = "@CLAUDE-VAULT.md"


def install_claude_vault_md(
    claude_dir: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Copy CLAUDE-VAULT.md to claude_dir and ensure CLAUDE.md imports it.

    If CLAUDE.md exists but does not already contain an @CLAUDE-VAULT.md
    reference, the import line is appended to the end of the file.
    """
    if not CLAUDE_VAULT_MD_SRC.exists():
        _warn(f"CLAUDE-VAULT.md not found at {CLAUDE_VAULT_MD_SRC} — skipping")
        return

    dest = claude_dir / "CLAUDE-VAULT.md"
    _step(f"Install CLAUDE-VAULT.md → {dest}", dry_run=dry_run)
    if not dry_run:
        shutil.copy2(CLAUDE_VAULT_MD_SRC, dest)

    claude_md = claude_dir / "CLAUDE.md"
    if not claude_md.exists():
        _print(
            dim(f"  {claude_md} not found — skipping @import"),
            verbose_only=True,
            verbose=verbose,
        )
        return

    content = claude_md.read_text(encoding="utf-8")
    if _CLAUDE_VAULT_MD_IMPORT in content:
        _print(
            dim(f"  {claude_md} already imports @CLAUDE-VAULT.md"),
            verbose_only=True,
            verbose=verbose,
        )
        return

    _step(f"Append @CLAUDE-VAULT.md import to {claude_md}", dry_run=dry_run)
    if not dry_run:
        suffix = "" if content.endswith("\n") else "\n"
        claude_md.write_text(
            content + suffix + _CLAUDE_VAULT_MD_IMPORT + "\n",
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Index rebuild
# ---------------------------------------------------------------------------


def rebuild_index(
    claude_dir: Path,
    dry_run: bool = False,
) -> None:
    """Run update_index.py to rebuild ~/ClaudeVault/CLAUDE.md."""
    script = claude_dir / "skills" / "claude-vault" / "scripts" / "update_index.py"
    if not script.exists():
        _warn(f"update_index.py not found at {script} — skipping index rebuild")
        return

    _step(f"Rebuild vault index ({script.name})", dry_run=dry_run)
    if dry_run:
        return

    try:
        result = subprocess.run(
            ["uv", "run", str(script)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            _ok("Vault index rebuilt")
        else:
            _warn(
                f"update_index.py exited {result.returncode}: {result.stderr.strip()[:200]}"
            )
    except FileNotFoundError:
        _warn(
            "`uv` not found — skipping index rebuild (run manually: uv run update_index.py)"
        )
    except subprocess.TimeoutExpired:
        _warn("update_index.py timed out — skipping")


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------


def uninstall(
    claude_dir: Path,
    settings_file: Path,
    dry_run: bool = False,
) -> None:
    """Remove installed skill, agent, and hook registrations."""
    print(bold("\nUninstalling Parsidion CC..."))

    skill_dir = claude_dir / "skills" / "claude-vault"

    if skill_dir.exists():
        _step(f"Remove skill directory: {skill_dir}", dry_run=dry_run)
        if not dry_run:
            shutil.rmtree(skill_dir)
    else:
        _warn(f"Skill directory not found: {skill_dir}")

    for agent_src in AGENT_SRCS:
        agent_dest = claude_dir / "agents" / agent_src.name
        if agent_dest.exists():
            _step(f"Remove agent: {agent_dest}", dry_run=dry_run)
            if not dry_run:
                agent_dest.unlink()
        else:
            _warn(f"Agent not found: {agent_dest}")

    scripts_dir = claude_dir / "scripts"
    if SCRIPTS_SRC.exists() and scripts_dir.exists():
        for script in SCRIPTS_SRC.iterdir():
            if script.is_file():
                script_dest = scripts_dir / script.name
                if script_dest.exists():
                    _step(f"Remove script: {script_dest}", dry_run=dry_run)
                    if not dry_run:
                        script_dest.unlink()

    # Remove hook registrations
    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            _warn(f"Could not read settings.json: {exc}")
            return

        hooks_section: dict = settings.get("hooks", {})
        changed = False

        for event, script_name in _HOOK_SCRIPTS.items():
            command = _hook_command(claude_dir, event)
            event_hooks: list[dict] = hooks_section.get(event, [])
            filtered = [
                entry
                for entry in event_hooks
                if not _hook_already_registered([entry], command)
            ]
            if len(filtered) < len(event_hooks):
                _step(f"Remove hook {bold(event)}", dry_run=dry_run)
                if not dry_run:
                    hooks_section[event] = filtered
                    if not hooks_section[event]:
                        del hooks_section[event]
                changed = True

        if changed and not dry_run:
            try:
                settings_file.write_text(
                    json.dumps(settings, indent=2) + "\n", encoding="utf-8"
                )
                _ok(f"Updated {settings_file}")
            except OSError as exc:
                _err(f"Could not write {settings_file}: {exc}")

    # Remove CLAUDE-VAULT.md and its @import from CLAUDE.md
    claude_vault_md = claude_dir / "CLAUDE-VAULT.md"
    if claude_vault_md.exists():
        _step(f"Remove {claude_vault_md}", dry_run=dry_run)
        if not dry_run:
            claude_vault_md.unlink()
    else:
        _warn(f"CLAUDE-VAULT.md not found: {claude_vault_md}")

    claude_md = claude_dir / "CLAUDE.md"
    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        if _CLAUDE_VAULT_MD_IMPORT in content:
            _step(f"Remove @CLAUDE-VAULT.md import from {claude_md}", dry_run=dry_run)
            if not dry_run:
                cleaned = "\n".join(
                    line
                    for line in content.splitlines()
                    if line.strip() != _CLAUDE_VAULT_MD_IMPORT
                )
                # Preserve trailing newline
                if content.endswith("\n"):
                    cleaned += "\n"
                claude_md.write_text(cleaned, encoding="utf-8")

    if not dry_run:
        print()
        _ok("Uninstall complete. Your vault at ~/ClaudeVault/ was not removed.")


def configure_vault_gitignore(vault_root: Path, dry_run: bool = False) -> None:
    """Add embeddings.db to vault .gitignore to prevent committing the binary DB.

    Args:
        vault_root: Path to the vault root directory.
        dry_run: If True, print actions without writing.
    """
    gitignore = vault_root / ".gitignore"
    entry = "embeddings.db\n"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if "embeddings.db" not in content:
            _step("Add embeddings.db to vault .gitignore", dry_run=dry_run)
            if not dry_run:
                gitignore.write_text(content + entry, encoding="utf-8")
    else:
        _step("Create vault .gitignore with embeddings.db", dry_run=dry_run)
        if not dry_run:
            gitignore.write_text(entry, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main install flow
# ---------------------------------------------------------------------------


def install(args: argparse.Namespace) -> int:
    """Run the full installation. Returns an exit code."""
    claude_dir: Path = Path(args.claude_dir).expanduser().resolve()
    settings_file: Path = claude_dir / "settings.json"
    dry_run: bool = args.dry_run
    verbose: bool = args.verbose

    print()
    print(bold("Parsidion CC Installer"))
    print(dim("Skills, hooks, and knowledge vault for Claude Code"))
    print()

    # --- Determine vault path ---
    if args.vault:
        vault_root, error = validate_vault_path(args.vault)
        if error:
            _err(error)
            return 2
        if vault_root.exists() and not vault_root.is_dir():
            _err(f"Vault path is not a directory: {vault_root}")
            return 2
    else:
        default_vault = Path.home() / "ClaudeVault"
        if args.yes:
            vault_root = default_vault
        else:
            vault_root = prompt_vault_path(default_vault)

    print()
    print(bold("Installation Plan"))
    print(f"  {dim('Claude dir   :')} {claude_dir}")
    print(f"  {dim('Vault path   :')} {vault_root}")
    print(f"  {dim('Settings     :')} {settings_file}")
    print(f"  {dim('Install skill:')} {claude_dir / 'skills' / 'claude-vault'}")
    if not args.skip_agent:
        for agent_src in AGENT_SRCS:
            print(f"  {dim('Install agent:')} {claude_dir / 'agents' / agent_src.name}")
    if not args.skip_hooks:
        print(f"  {dim('Register hooks:')} SessionStart, SessionEnd, PreCompact")
    print(f"  {dim('Install scripts:')} {claude_dir / 'scripts'}/")
    print(f"  {dim('Install guidance:')} {claude_dir / 'CLAUDE-VAULT.md'} (@import into CLAUDE.md)")
    if dry_run:
        print(f"\n  {yellow('[DRY RUN — no changes will be made]')}")

    print()

    if not dry_run and not args.yes:
        if not _confirm("Proceed with installation?", default=True):
            print(dim("Aborted."))
            return 0

    print()

    # 1. Install skill
    if not SKILL_SRC.exists():
        _err(f"Skill source not found: {SKILL_SRC}")
        return 1

    install_skill(
        claude_dir,
        vault_root,
        force=args.force,
        yes=args.yes,
        dry_run=dry_run,
        verbose=verbose,
    )

    # 2. Install agents
    if not args.skip_agent:
        install_agents(claude_dir, dry_run=dry_run)

    # 3. Install scripts
    install_scripts(claude_dir, dry_run=dry_run)

    # 5. Create vault directories
    create_vault_dirs(vault_root, dry_run=dry_run)

    # 6. Create Templates symlink
    templates_src = claude_dir / "skills" / "claude-vault" / "templates"
    create_templates_symlink(
        vault_root, templates_src, dry_run=dry_run, verbose=verbose
    )

    # 7. Register hooks
    if not args.skip_hooks:
        merge_hooks(claude_dir, settings_file, dry_run=dry_run, verbose=verbose)

    # 8. Install CLAUDE-VAULT.md and wire @import into CLAUDE.md
    install_claude_vault_md(claude_dir, dry_run=dry_run, verbose=verbose)

    # 9. Rebuild vault index
    rebuild_index(claude_dir, dry_run=dry_run)

    # 10. Configure vault .gitignore for embeddings.db
    configure_vault_gitignore(vault_root, dry_run=dry_run)

    print()
    if dry_run:
        _ok("Dry run complete — no changes were made.")
    else:
        _ok("Installation complete!")
        print()
        print(dim("  Next steps:"))
        print(f"  1. Open {vault_root} in Obsidian as a vault")
        print("  2. Restart Claude Code to activate hooks")
        print(
            f"  3. Run: {cyan('uv run ~/.claude/skills/claude-vault/scripts/update_index.py')}"
        )
        print("         to rebuild the vault index at any time")
        print(
            f"  4. Run: {cyan('uv run ~/.claude/skills/claude-vault/scripts/build_embeddings.py')}"
        )
        print("         to build the semantic search index (~30s on first run)")

    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="Install Parsidion CC skills, hooks, and Claude Vault.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    parser.add_argument(
        "--vault",
        metavar="PATH",
        help="Obsidian vault path (skips interactive prompt)",
    )
    parser.add_argument(
        "--claude-dir",
        metavar="PATH",
        default="~/.claude",
        help="Claude config directory (default: ~/.claude)",
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Preview actions without making changes",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed output",
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Overwrite existing skill files without prompting",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompts",
    )
    parser.add_argument(
        "--skip-hooks",
        action="store_true",
        help="Do not modify settings.json",
    )
    parser.add_argument(
        "--skip-agent",
        action="store_true",
        help="Do not install any agents",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove installed skill, agents, and hooks",
    )
    parser.add_argument(
        "--help",
        "-h",
        action="help",
        help="Show this help message and exit",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    claude_dir = Path(args.claude_dir).expanduser().resolve()
    settings_file = claude_dir / "settings.json"

    if args.uninstall:
        if not args.yes and not args.dry_run:
            print()
            print(bold("Parsidion CC Uninstaller"))
            print(f"  {dim('Claude dir:')} {claude_dir}")
            if not _confirm("Proceed with uninstall?", default=False):
                print(dim("Aborted."))
                sys.exit(0)
        uninstall(claude_dir, settings_file, dry_run=args.dry_run)
        sys.exit(0)

    sys.exit(install(args))


if __name__ == "__main__":
    main()
