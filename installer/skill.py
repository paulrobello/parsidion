"""Skill, agent, and script installation for the Parsidion installer.

Handles installing the skill (symlink or copy), agents, scripts, CLI tools,
CLAUDE-VAULT.md, vault index rebuild, AI mode configuration, and legacy
asset cleanup.
Stdlib-only — no third-party dependencies.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from installer.paths import (
    AGENT_INSTRUCTIONS_SRC,
    AGENT_SRCS,
    CLAUDE_VAULT_MD_SRC,
    LEGACY_SKILL_NAME,
    PROJECT_NAME,
    SCRIPTS_SRC,
    SKILL_NAME,
    SKILL_SRC,
)
from installer.ui import _ok, _print, _step, _warn

# ---------------------------------------------------------------------------
# Skill installation
# ---------------------------------------------------------------------------


def _can_symlink(target: Path) -> bool:
    """Return True if the OS supports directory symlinks at *target*'s location."""
    if sys.platform != "win32":
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    probe = target.parent / f"._symlink_probe_{__import__('os').getpid()}"
    try:
        probe.symlink_to(target.parent, target_is_directory=True)
        probe.unlink()
        return True
    except (OSError, NotImplementedError):
        return False


def install_skill(
    claude_dir: Path,
    vault_root: Path,
    force: bool = False,
    yes: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> Path:
    """Install skill to ~/.claude/skills/parsidion/.

    On Unix/macOS: creates a directory symlink so edits to the repo are
    immediately live without reinstalling.
    On Windows (or when symlinks are unavailable): falls back to copytree.

    Returns the installed skill path.
    """
    from installer.ui import _confirm, dim

    dest = claude_dir / "skills" / SKILL_NAME
    use_symlink = sys.platform != "win32" or _can_symlink(dest)

    if use_symlink and dest.is_symlink() and dest.resolve() == SKILL_SRC.resolve():
        if not force:
            _print(
                dim(f"  Skill symlink already correct: {dest} → {SKILL_SRC}"),
                verbose_only=True,
                verbose=verbose,
            )
            return dest

    if (dest.exists() or dest.is_symlink()) and not force and not dry_run:
        _warn(f"Skill already exists at {dest}")
        action = (
            "Replace with symlink to repo?"
            if use_symlink
            else "Overwrite existing skill files?"
        )
        if not yes and not _confirm(action, default=False):
            print(f"  {dim('Skipping skill installation.')}")
            return dest
        elif yes:
            _print(
                dim("  Overwriting existing skill (--yes)"),
                verbose_only=True,
                verbose=verbose,
            )

    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        elif dest.exists():
            shutil.rmtree(dest)

    if use_symlink:
        _step(f"Install skill (symlink): {dest} → {SKILL_SRC}", dry_run=dry_run)
        if not dry_run:
            dest.symlink_to(SKILL_SRC)
            for script in SKILL_SRC.glob("scripts/*.py"):
                script.chmod(script.stat().st_mode | 0o755)
            for script in SKILL_SRC.glob("scripts/*.sh"):
                script.chmod(script.stat().st_mode | 0o755)
    else:
        _step(f"Install skill (copy): {SKILL_SRC} → {dest}", dry_run=dry_run)
        if not dry_run:
            shutil.copytree(SKILL_SRC, dest)
            for pycache in dest.rglob("__pycache__"):
                shutil.rmtree(pycache, ignore_errors=True)
            if sys.platform != "win32":
                for script in (dest / "scripts").glob("*.py"):
                    script.chmod(script.stat().st_mode | 0o755)
                for script in (dest / "scripts").glob("*.sh"):
                    script.chmod(script.stat().st_mode | 0o755)

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
# CLI tools via uv tool install
# ---------------------------------------------------------------------------


def install_cli_tools(
    repo_root: Path,
    dry_run: bool = False,
) -> None:
    """Install vault-search, vault-new, and vault-stats as global CLI commands via uv tool."""
    _step(
        "Install CLI tools: vault-search, vault-new, vault-stats (uv tool install)",
        dry_run=dry_run,
    )
    if not dry_run:
        result = subprocess.run(
            ["uv", "tool", "install", "--editable", ".[tools]"],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            _warn(
                "uv tool install failed — vault-search / vault-new / vault-stats not globally available.\n"
                f"  stdout: {result.stdout.strip()}\n"
                f"  stderr: {result.stderr.strip()}"
            )
        else:
            _ok("vault-search, vault-new, and vault-stats installed globally")


# ---------------------------------------------------------------------------
# CLAUDE-VAULT.md installation
# ---------------------------------------------------------------------------

_CLAUDE_VAULT_MD_IMPORT = "@CLAUDE-VAULT.md"


def install_claude_vault_md(
    claude_dir: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Copy CLAUDE-VAULT.md to claude_dir and ensure CLAUDE.md imports it."""
    from installer.ui import dim

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
# Agent instructions injection (codex AGENTS.md / gemini GEMINI.md)
# ---------------------------------------------------------------------------

_BEGIN_MARKER = "<!-- BEGIN parsidion -->"
_END_MARKER = "<!-- END parsidion -->"


def _inject_instructions_block(dest: Path, dry_run: bool, verbose: bool) -> None:
    """Idempotently inject the parsidion instructions section into *dest*."""
    from installer.ui import dim

    if not AGENT_INSTRUCTIONS_SRC.exists():
        _warn(f"AGENT_INSTRUCTIONS.md not found at {AGENT_INSTRUCTIONS_SRC} — skipping")
        return

    block = AGENT_INSTRUCTIONS_SRC.read_text(encoding="utf-8").strip()
    section = f"{_BEGIN_MARKER}\n{block}\n{_END_MARKER}\n"

    existing = dest.read_text(encoding="utf-8") if dest.exists() else ""
    if _BEGIN_MARKER in existing:
        _print(
            dim(f"  {dest} already has parsidion instructions block"),
            verbose_only=True,
            verbose=verbose,
        )
        return

    _step(f"Inject parsidion instructions → {dest}", dry_run=dry_run)
    if not dry_run:
        suffix = "" if existing.endswith("\n") or existing == "" else "\n"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(existing + suffix + section, encoding="utf-8")


def install_codex_agents_md(
    codex_home: Path, dry_run: bool = False, verbose: bool = False
) -> None:
    """Inject parsidion instructions into ~/.codex/AGENTS.md (global user layer)."""
    _inject_instructions_block(codex_home / "AGENTS.md", dry_run, verbose)


def install_gemini_md(
    gemini_home: Path, dry_run: bool = False, verbose: bool = False
) -> None:
    """Inject parsidion instructions into ~/.gemini/GEMINI.md (global user layer)."""
    _inject_instructions_block(gemini_home / "GEMINI.md", dry_run, verbose)


# ---------------------------------------------------------------------------
# Index rebuild
# ---------------------------------------------------------------------------


def rebuild_index(
    claude_dir: Path,
    dry_run: bool = False,
) -> None:
    """Run update_index.py to rebuild the resolved vault's CLAUDE.md."""
    script = claude_dir / "skills" / SKILL_NAME / "scripts" / "update_index.py"
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
# AI mode configuration
# ---------------------------------------------------------------------------


def enable_ai_mode(
    settings_file: Path,
    vault_root: Path,
    claude_dir: Path,
    dry_run: bool = False,
) -> None:
    """Write ai_model to vault config.yaml and set SessionStart timeout to 30s."""
    from installer.hooks import _hook_command

    config_path = vault_root / "config.yaml"
    ai_model = "claude-haiku-4-5-20251001"

    if config_path.exists():
        try:
            content = config_path.read_text(encoding="utf-8")
        except OSError:
            content = ""
    else:
        content = ""

    if re.search(r"^\s*ai_model\s*:", content, re.MULTILINE):
        new_content = re.sub(
            r"^(\s*ai_model\s*:).*$",
            rf"\1 {ai_model}",
            content,
            flags=re.MULTILINE,
        )
    elif "session_start_hook:" in content:
        new_content = content.replace(
            "session_start_hook:",
            f"session_start_hook:\n  ai_model: {ai_model}",
            1,
        )
    else:
        ai_section = (
            "# Session start hook (session_start_hook.py)\n"
            f"session_start_hook:\n  ai_model: {ai_model}\n\n"
        )
        new_content = ai_section + content

    _step(f"Write ai_model to {config_path}", dry_run=dry_run)
    if not dry_run:
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(new_content, encoding="utf-8")
        except OSError as exc:
            _warn(f"Could not write {config_path}: {exc}")

    if not settings_file.exists():
        return
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    command = _hook_command(claude_dir, "SessionStart")
    modified = False
    for entry in settings.get("hooks", {}).get("SessionStart", []):
        for handler in entry.get("hooks", []):
            if handler.get("command") == command and handler.get("timeout") != 30000:
                _step("Set SessionStart hook timeout to 30000ms", dry_run=dry_run)
                if not dry_run:
                    handler["timeout"] = 30000
                    modified = True

    if modified and not dry_run:
        try:
            settings_file.write_text(
                json.dumps(settings, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            _warn(f"Could not update {settings_file}: {exc}")


# ---------------------------------------------------------------------------
# Legacy asset cleanup
# ---------------------------------------------------------------------------


def cleanup_legacy_assets(
    claude_dir: Path,
    settings_file: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> bool:
    """Remove managed legacy parsidion-cc hooks and installed skill assets.

    This preserves user vault contents and unrelated Claude settings.
    """
    from installer.hooks import remove_legacy_hooks
    from installer.ui import dim

    changed = False

    if remove_legacy_hooks(claude_dir, settings_file, dry_run=dry_run):
        changed = True

    legacy_skill_dir = claude_dir / "skills" / LEGACY_SKILL_NAME
    if legacy_skill_dir.exists() or legacy_skill_dir.is_symlink():
        _step(f"Remove legacy skill {legacy_skill_dir}", dry_run=dry_run)
        changed = True
        if not dry_run:
            try:
                if legacy_skill_dir.is_symlink() or legacy_skill_dir.is_file():
                    legacy_skill_dir.unlink()
                else:
                    shutil.rmtree(legacy_skill_dir)
            except OSError as exc:
                _warn(f"Could not remove legacy skill {legacy_skill_dir}: {exc}")
    else:
        _print(
            dim(f"  No legacy skill found at {legacy_skill_dir}"),
            verbose_only=True,
            verbose=verbose,
        )

    return changed


# ---------------------------------------------------------------------------
# Full uninstall
# ---------------------------------------------------------------------------


def uninstall(
    claude_dir: Path,
    settings_file: Path,
    dry_run: bool = False,
    yes: bool = False,
    hooks_only: bool = False,
    runtime: str = "claude",
    codex_home: Path | None = None,
    gemini_home: Path | None = None,
) -> None:
    """Remove installed Parsidion assets or only managed hooks."""
    import os

    from installer.hooks import (
        remove_codex_hooks,
        remove_gemini_hooks,
        remove_installed_hooks,
        remove_legacy_hooks,
    )
    from installer.paths import (
        _resolve_vault_root_for_uninstall,
        _wants_claude_runtime,
        _wants_codex_runtime,
        _wants_gemini_runtime,
    )
    from installer.schedule import unschedule_summarizer
    from installer.vault import remove_vault_post_merge_hook

    codex_home = (
        codex_home
        or Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()
    )
    gemini_home = gemini_home or (Path.home() / ".gemini")
    uninstall_claude_runtime = _wants_claude_runtime(runtime)
    uninstall_codex_runtime = _wants_codex_runtime(runtime)
    uninstall_gemini_runtime = _wants_gemini_runtime(runtime)

    from installer.colors import bold

    if hooks_only:
        print(bold("\nRemoving Parsidion hooks..."))
        if runtime == "none":
            _warn("Runtime selection is none; no runtime hooks will be removed.")
        removed_hooks = False
        if uninstall_claude_runtime:
            removed_hooks = (
                remove_installed_hooks(claude_dir, settings_file, dry_run=dry_run)
                or removed_hooks
            )
            removed_hooks = (
                remove_legacy_hooks(claude_dir, settings_file, dry_run=dry_run)
                or removed_hooks
            )
        if uninstall_codex_runtime:
            removed_hooks = (
                remove_codex_hooks(codex_home, claude_dir, dry_run=dry_run)
                or removed_hooks
            )
        if uninstall_gemini_runtime:
            removed_hooks = (
                remove_gemini_hooks(gemini_home, claude_dir, dry_run=dry_run)
                or removed_hooks
            )
        if not dry_run:
            print()
            _ok("Hook uninstall complete.")
        return

    print(bold("\nUninstalling Parsidion..."))

    if uninstall_claude_runtime:
        skill_dir = claude_dir / "skills" / SKILL_NAME

        if skill_dir.exists() or skill_dir.is_symlink():
            _step(f"Remove skill directory: {skill_dir}", dry_run=dry_run)
            if not dry_run:
                if skill_dir.is_symlink() or skill_dir.is_file():
                    skill_dir.unlink()
                else:
                    shutil.rmtree(skill_dir)
        else:
            _warn(f"Skill directory not found: {skill_dir}")

        legacy_skill_dir = claude_dir / "skills" / LEGACY_SKILL_NAME
        if legacy_skill_dir.exists() or legacy_skill_dir.is_symlink():
            _step(f"Remove legacy skill {legacy_skill_dir}", dry_run=dry_run)
            if not dry_run:
                try:
                    if legacy_skill_dir.is_symlink() or legacy_skill_dir.is_file():
                        legacy_skill_dir.unlink()
                    else:
                        shutil.rmtree(legacy_skill_dir)
                except OSError as exc:
                    _warn(f"Could not remove legacy skill {legacy_skill_dir}: {exc}")

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

    if uninstall_claude_runtime:
        remove_installed_hooks(claude_dir, settings_file, dry_run=dry_run)
        remove_legacy_hooks(claude_dir, settings_file, dry_run=dry_run)

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
                _step(
                    f"Remove @CLAUDE-VAULT.md import from {claude_md}", dry_run=dry_run
                )
                if not dry_run:
                    cleaned = "\n".join(
                        line
                        for line in content.splitlines()
                        if line.strip() != _CLAUDE_VAULT_MD_IMPORT
                    )
                    if content.endswith("\n"):
                        cleaned += "\n"
                    claude_md.write_text(cleaned, encoding="utf-8")

    if uninstall_codex_runtime:
        remove_codex_hooks(codex_home, claude_dir, dry_run=dry_run)
    elif runtime == "none":
        _warn("Runtime selection is none; no runtime hooks will be removed.")
    if uninstall_gemini_runtime:
        remove_gemini_hooks(gemini_home, claude_dir, dry_run=dry_run)

    vault_root = _resolve_vault_root_for_uninstall()
    remove_vault_post_merge_hook(vault_root, dry_run=dry_run)

    unschedule_summarizer(dry_run=dry_run)

    vaults_config = Path.home() / ".config" / PROJECT_NAME / "vaults.yaml"
    if vaults_config.exists():
        from installer.ui import _confirm

        if yes or _confirm(f"Remove {vaults_config}?", default=False):
            _step(f"Remove {vaults_config}", dry_run=dry_run)
            if not dry_run:
                try:
                    vaults_config.unlink()
                    _ok(f"Removed {vaults_config}")
                except OSError as exc:
                    _warn(f"Could not remove {vaults_config}: {exc}")

    if not dry_run:
        print()
        _ok("Uninstall complete. Your resolved vault directory was not removed.")
