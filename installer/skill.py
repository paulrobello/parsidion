"""Skill, agent, and script installation for the Parsidion installer.

Handles installing the skill (symlink or copy), agents, scripts, CLI tools,
PARSIDION-VAULT.md, vault index rebuild, AI mode configuration, and legacy
asset cleanup.

ARC-025: ``uninstall()`` moved to ``installer/uninstall.py`` so this module
no longer needs to import from ``hooks``/``schedule``/``vault`` at function
call time — the install path depends only on ``paths``/``ui``/``hooks`` at
module load. Stdlib-only — no third-party dependencies.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import agent_adapter  # ENH-006: runtime registry (scripts/ on sys.path via installer/__init__)
from installer.colors import dim
from installer.hooks import _atomic_write_text, remove_legacy_hooks
from installer.paths import (
    AGENT_INSTRUCTIONS_SRC,
    AGENT_SRCS,
    LEGACY_CLAUDE_VAULT_MD,
    LEGACY_SKILL_NAME,
    PARSIDION_VAULT_MD_SRC,
    SCRIPTS_SRC,
    SKILL_NAME,
    SKILL_SRC,
)
from installer.ui import _confirm, _ok, _print, _step, _warn

# ---------------------------------------------------------------------------
# Skill installation
# ---------------------------------------------------------------------------


def _can_symlink(target: Path) -> bool:
    """Return True if the OS supports directory symlinks at *target*'s location."""
    if sys.platform != "win32":
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    probe = target.parent / f"._symlink_probe_{os.getpid()}"
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
    """Install the Parsidion skill to ``~/.claude/skills/parsidion/``.

    On Unix/macOS the skill is installed as a directory symlink pointing
    at the source tree, so edits to the repo are live without reinstalling.
    On Windows, or when directory symlinks are unavailable (see
    ``_can_symlink``), the function falls back to a recursive copytree.

    Idempotent: when the symlink already points at ``SKILL_SRC`` and
    neither ``force`` nor a confirmation prompt asks for replacement, the
    existing install is left untouched. DOC-039.

    Args:
        claude_dir: Path to the Claude Code config directory
            (``~/.claude``); the skill is created at
            ``claude_dir/skills/parsidion``.
        vault_root: Resolved vault root. Currently unused inside this
            function but retained so the install step list can pass a
            uniform argument shape to every ``install_*`` helper.
        force: When True, replace an existing skill at the destination
            without prompting.
        yes: When True, answer any confirmation prompt affirmatively
            (equivalent to ``--yes``). Implies overwrite when the
            destination already exists.
        dry_run: When True, print the steps that would be taken without
            writing, symlinking, or removing anything.
        verbose: When True, emit verbose diagnostic lines (e.g. "symlink
            already correct").

    Returns:
        The installed skill destination path
        (``claude_dir/skills/parsidion``) whether or not anything was
        actually written.
    """
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
        # QA-005: uv tool install resolves the editable build and installs
        # the tools extras; on a cold cache or a slow network that can
        # take a minute or more, but a truly hung build should not stall
        # the installer indefinitely. 300 s matches the bound the audit
        # applied to the index-rebuild children.
        try:
            result = subprocess.run(
                ["uv", "tool", "install", "--editable", ".[tools]"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            _warn(
                "uv tool install timed out after 300s — vault-search / "
                "vault-new / vault-stats may not be globally available. "
                "Re-run install to retry."
            )
            return
        if result.returncode != 0:
            _warn(
                "uv tool install failed — vault-search / vault-new / vault-stats not globally available.\n"
                f"  stdout: {result.stdout.strip()}\n"
                f"  stderr: {result.stderr.strip()}"
            )
        else:
            _ok("vault-search, vault-new, and vault-stats installed globally")


# ---------------------------------------------------------------------------
# PARSIDION-VAULT.md installation
# ---------------------------------------------------------------------------

_PARSIDION_VAULT_MD_IMPORT = "@PARSIDION-VAULT.md"
# Pre-rename installs (<= 0.13.x) imported CLAUDE-VAULT.md; migrated away below.
_LEGACY_CLAUDE_VAULT_MD_IMPORT = "@CLAUDE-VAULT.md"


def _strip_import_line(claude_md: Path, import_line: str) -> str:
    """Remove *import_line* from *claude_md*; return the cleaned content."""
    content = claude_md.read_text(encoding="utf-8")
    cleaned = "\n".join(
        line for line in content.splitlines() if line.strip() != import_line
    )
    if content.endswith("\n"):
        cleaned += "\n"
    return cleaned


def install_parsidion_vault_md(
    claude_dir: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Copy PARSIDION-VAULT.md to claude_dir and ensure CLAUDE.md imports it.

    Also migrates pre-rename installs: removes the legacy CLAUDE-VAULT.md
    copy and its ``@CLAUDE-VAULT.md`` import line from CLAUDE.md.
    """
    if not PARSIDION_VAULT_MD_SRC.exists():
        _warn(f"PARSIDION-VAULT.md not found at {PARSIDION_VAULT_MD_SRC} — skipping")
        return

    dest = claude_dir / "PARSIDION-VAULT.md"
    _step(f"Install PARSIDION-VAULT.md → {dest}", dry_run=dry_run)
    if not dry_run:
        shutil.copy2(PARSIDION_VAULT_MD_SRC, dest)

    legacy_dest = claude_dir / LEGACY_CLAUDE_VAULT_MD
    if legacy_dest.exists():
        _step(
            f"Remove legacy {legacy_dest} (renamed to PARSIDION-VAULT.md)",
            dry_run=dry_run,
        )
        if not dry_run:
            legacy_dest.unlink()

    claude_md = claude_dir / "CLAUDE.md"
    if not claude_md.exists():
        _print(
            dim(f"  {claude_md} not found — skipping @import"),
            verbose_only=True,
            verbose=verbose,
        )
        return

    content = claude_md.read_text(encoding="utf-8")
    if _LEGACY_CLAUDE_VAULT_MD_IMPORT in content:
        _step(
            f"Replace {_LEGACY_CLAUDE_VAULT_MD_IMPORT} import with {_PARSIDION_VAULT_MD_IMPORT} in {claude_md}",
            dry_run=dry_run,
        )
        if not dry_run:
            content = _strip_import_line(claude_md, _LEGACY_CLAUDE_VAULT_MD_IMPORT)

    if _PARSIDION_VAULT_MD_IMPORT in content:
        _print(
            dim(f"  {claude_md} already imports {_PARSIDION_VAULT_MD_IMPORT}"),
            verbose_only=True,
            verbose=verbose,
        )
        return

    _step(f"Append {_PARSIDION_VAULT_MD_IMPORT} import to {claude_md}", dry_run=dry_run)
    if not dry_run:
        suffix = "" if content.endswith("\n") else "\n"
        _atomic_write_text(
            claude_md, content + suffix + _PARSIDION_VAULT_MD_IMPORT + "\n"
        )


# ---------------------------------------------------------------------------
# Agent instructions injection (codex AGENTS.md / antigravity GEMINI.md)
# ---------------------------------------------------------------------------

_BEGIN_MARKER = "<!-- BEGIN parsidion -->"
_END_MARKER = "<!-- END parsidion -->"


def _inject_instructions_block(dest: Path, dry_run: bool, verbose: bool) -> None:
    """Idempotently inject the parsidion instructions section into *dest*.

    SEC-116: if *dest* is a symlink, refuse to follow it when its target
    resolves outside *dest*'s parent directory. On the user's live machine
    ``~/.codex/AGENTS.md`` is a symlink to ``~/.claude/CLAUDE.md``, so
    without this guard ``connect codex`` would inject the parsidion
    block into the user's *global* agent instructions rather than a
    Codex-specific file. Print the resolved target so the user can see
    where it points and remove the symlink if they want the injection.
    """
    if not AGENT_INSTRUCTIONS_SRC.exists():
        _warn(f"AGENT_INSTRUCTIONS.md not found at {AGENT_INSTRUCTIONS_SRC} — skipping")
        return

    # SEC-116: refuse to follow a symlink that escapes the agent config dir.
    if dest.is_symlink():
        try:
            resolved = dest.resolve()
            config_dir = dest.parent.resolve()
        except OSError as exc:
            _warn(
                f"Cannot resolve symlink {dest} ({exc}); "
                "remove the symlink manually if you want parsidion to write "
                "this file."
            )
            return
        if not resolved.is_relative_to(config_dir):
            _warn(
                f"Refusing to follow symlink {dest} -> {resolved}: target is "
                f"outside {config_dir}. This guard prevents connect from "
                "silently rewriting a shared file (e.g. ~/.claude/CLAUDE.md). "
                "Remove the symlink if you want parsidion to manage this file."
            )
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
        _atomic_write_text(dest, existing + suffix + section)


def _remove_instructions_block(dest: Path, dry_run: bool = False) -> bool:
    """Strip the parsidion instructions block from *dest* if present.

    ARC-022 / SEC-116: closes the uninstall asymmetry where Codex/Antigravity
    disconnect removed hooks but left the injected ``AGENTS.md`` /
    ``GEMINI.md`` instruction block loading on every session. Returns
    True when the file was changed, False when there was nothing to
    remove or the file was absent. Idempotent — safe to call when no
    block exists.

    SEC-116: applies the same symlink-escape guard as
    ``_inject_instructions_block`` so disconnect cannot be tricked into
    editing a shared file via a planted symlink.
    """
    if not dest.exists() or not dest.is_file():
        return False

    # SEC-116: same symlink guard as inject — never edit a symlink that
    # escapes the agent config dir.
    if dest.is_symlink():
        try:
            resolved = dest.resolve()
            config_dir = dest.parent.resolve()
        except OSError:
            return False
        if not resolved.is_relative_to(config_dir):
            _warn(
                f"Refusing to follow symlink {dest} -> {resolved}: target is "
                f"outside {config_dir}. Leaving file unchanged."
            )
            return False

    try:
        content = dest.read_text(encoding="utf-8")
    except OSError:
        return False
    if _BEGIN_MARKER not in content or _END_MARKER not in content:
        return False

    # Strip everything between (and including) the markers. Treats the
    # markers as line-delimited so the surrounding blank-line structure
    # stays clean.
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    skipping = False
    for line in lines:
        if not skipping and line.strip() == _BEGIN_MARKER:
            skipping = True
            # Drop one trailing blank line before the block so we don't
            # leave a double blank gap after stripping.
            while out and out[-1].strip() == "":
                out.pop()
            continue
        if skipping and line.strip() == _END_MARKER:
            skipping = False
            continue
        if not skipping:
            out.append(line)

    cleaned = "".join(out).rstrip("\n") + "\n"
    if cleaned == content:
        return False

    _step(f"Remove parsidion instructions block from {dest}", dry_run=dry_run)
    if dry_run:
        return True
    try:
        _atomic_write_text(dest, cleaned)
    except OSError as exc:
        _warn(f"Could not write {dest}: {exc}")
        return False
    return True


def _instructions_dest(runtime_home: Path, runtime: str) -> Path | None:
    """Resolve the instructions file from the runtime's AgentAdapter.

    ``instructions_filename`` is the registry's declaration of which file the
    installer owns (ENH-006); None means the runtime gets no injected
    instructions file (claude's PARSIDION-VAULT.md path and pi are handled
    elsewhere), so callers treat a None dest as a no-op.
    """
    adapter = agent_adapter.get(runtime)
    if adapter is None or not adapter.instructions_filename:
        return None
    return runtime_home / adapter.instructions_filename


def install_codex_agents_md(
    codex_home: Path, dry_run: bool = False, verbose: bool = False
) -> None:
    """Inject parsidion instructions into the codex instructions file
    (``AGENTS.md`` per the registry's ``instructions_filename``)."""
    dest = _instructions_dest(codex_home, "codex")
    if dest is not None:
        _inject_instructions_block(dest, dry_run, verbose)


def install_antigravity_md(
    antigravity_home: Path, dry_run: bool = False, verbose: bool = False
) -> None:
    """Inject parsidion instructions into Antigravity's GEMINI.md file."""
    dest = _instructions_dest(antigravity_home, "antigravity")
    if dest is not None:
        _inject_instructions_block(dest, dry_run, verbose)


def remove_codex_agents_md(codex_home: Path, dry_run: bool = False) -> bool:
    """Strip the parsidion instructions block from the codex instructions
    file.

    Called by ``disconnect codex`` / full uninstall so the Codex
    integration stops loading parsidion instructions every session.
    """
    dest = _instructions_dest(codex_home, "codex")
    return _remove_instructions_block(dest, dry_run) if dest is not None else False


def remove_antigravity_md(antigravity_home: Path, dry_run: bool = False) -> bool:
    """Strip the parsidion instructions block from Antigravity's GEMINI.md."""
    dest = _instructions_dest(antigravity_home, "antigravity")
    return _remove_instructions_block(dest, dry_run) if dest is not None else False


# ---------------------------------------------------------------------------
# Index rebuild
# ---------------------------------------------------------------------------


def rebuild_index(
    claude_dir: Path,
    dry_run: bool = False,
) -> None:
    """Run update_index.py to rebuild the resolved vault's CLAUDE.md.

    ARC-004: delegates to ``core.vault_index.run_index_rebuild`` — the shared
    owner of the index-rebuild subprocess contract — which also adds the
    ``--no-project`` flag the old inline launcher omitted (uv could otherwise
    sync an unrelated project found in the inherited cwd) and kills the whole
    process group on timeout.
    """
    scripts_dir = claude_dir / "skills" / SKILL_NAME / "scripts"
    script = scripts_dir / "update_index.py"
    if not script.exists():
        _warn(f"update_index.py not found at {script} — skipping index rebuild")
        return

    _step(f"Rebuild vault index ({script.name})", dry_run=dry_run)
    if dry_run:
        return

    from vault_common import run_index_rebuild

    reason, proc = run_index_rebuild(scripts_dir=scripts_dir, timeout=30.0)
    if reason == "ok" and proc is not None and proc.returncode == 0:
        _ok("Vault index rebuilt")
    elif reason == "timeout":
        _warn("update_index.py timed out — skipping")
    elif reason == "launch":
        _warn(
            "`uv` not found — skipping index rebuild (run manually: uv run update_index.py)"
        )
    else:
        stderr = proc.stderr.strip()[:200] if proc is not None else ""
        code = proc.returncode if proc is not None else "?"
        _warn(f"update_index.py exited {code}: {stderr}")


# ---------------------------------------------------------------------------
# AI mode configuration
# ---------------------------------------------------------------------------


def enable_ai_mode(
    vault_root: Path,
    dry_run: bool = False,
) -> None:
    """Write ``ai_model`` into the vault ``config.yaml``.

    ARC-025: this function previously *also* edited ``settings.json`` to
    raise the SessionStart hook timeout to 30000ms — that second RMW has
    been merged into ``hooks.merge_hooks`` (pass ``enable_ai_mode=True``)
    so the file is written once per install instead of twice via two
    independent read-modify-write cycles. The vault-config half stayed
    here because it is the only place that knows the AI model id and the
    YAML section shape.
    """
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
            _atomic_write_text(config_path, new_content)
        except OSError as exc:
            _warn(f"Could not write {config_path}: {exc}")


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
