"""Full uninstall and hook-only teardown for Parsidion.

ARC-025: ``uninstall()`` lived in ``installer/skill.py`` alongside the
install flow, forcing ``skill`` to import from ``hooks``, ``schedule``,
and ``vault`` at function-call time (a defensive load-order trick from
before the installer layering was tightened). With the install side no
longer mutating settings.json outside ``hooks.merge_hooks``, the uninstall
path moves here so the dependency graph stays acyclic without the
function-local imports.

Stdlib-only — no third-party dependencies.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from installer.colors import bold
from installer.hooks import (
    disable_codex_hooks_config,
    remove_codex_hooks,
    remove_gemini_hooks,
    remove_installed_hooks,
    remove_legacy_hooks,
)
from installer.paths import (
    AGENT_SRCS,
    LEGACY_SKILL_NAME,
    PROJECT_NAME,
    SCRIPTS_SRC,
    SKILL_NAME,
    _resolve_vault_root_for_uninstall,
    _wants_claude_runtime,
    _wants_codex_runtime,
    _wants_gemini_runtime,
)
from installer.schedule import unschedule_summarizer
from installer.ui import _ok, _step, _warn
from installer.vault import remove_vault_post_merge_hook

# Local re-import so the CLAUDE.md @import stripping stays alongside the
# uninstall path that owns it. The constant itself lives in skill.py.
from installer.skill import (
    _CLAUDE_VAULT_MD_IMPORT,
    remove_codex_agents_md,
    remove_gemini_md,
)


def uninstall(
    claude_dir: Path,
    settings_file: Path,
    dry_run: bool = False,
    yes: bool = False,
    hooks_only: bool = False,
    runtime: str = "claude",
    codex_home: Path | None = None,
    gemini_home: Path | None = None,
    purge_config: bool = False,
) -> None:
    """Remove installed Parsidion assets or only managed hooks.

    ARC-003 (preserved here when the function moved): ``codex_home``, the
    post-merge hook, the summarizer schedule, and ``vaults.yaml`` are shared
    global infrastructure that the Claude install depends on. They are only
    torn down when the Claude integration itself is being removed
    (``is_full_teardown``). A targeted ``disconnect codex`` or
    ``disconnect gemini`` must not touch them.

    ARC-003 (also preserved): ``vaults.yaml`` additionally requires an
    explicit ``--purge-config`` (``purge_config=True``) — under ``--yes``
    alone it is always preserved.
    """
    codex_home = (
        codex_home
        or Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()
    )
    gemini_home = gemini_home or (Path.home() / ".gemini")
    uninstall_claude_runtime = _wants_claude_runtime(runtime)
    uninstall_codex_runtime = _wants_codex_runtime(runtime)
    uninstall_gemini_runtime = _wants_gemini_runtime(runtime)

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
        # ARC-022 / SEC-116: disconnect must remove the AGENTS.md block
        # too, otherwise Codex keeps loading parsidion instructions every
        # session. Also revert the [features] hooks flag so Codex stops
        # invoking parsidion hooks even if a stale hooks.json lingers.
        remove_codex_agents_md(codex_home, dry_run=dry_run)
        disable_codex_hooks_config(codex_home, dry_run=dry_run)
    elif runtime == "none":
        _warn("Runtime selection is none; no runtime hooks will be removed.")
    if uninstall_gemini_runtime:
        remove_gemini_hooks(gemini_home, claude_dir, dry_run=dry_run)
        # ARC-022 / SEC-116: same instruction-block removal for Gemini.
        remove_gemini_md(gemini_home, dry_run=dry_run)

    # ARC-003: the post-merge hook, summarizer schedule, and vaults.yaml are
    # shared global infrastructure that the Claude install depends on. Only
    # tear them down when the Claude integration itself is being removed
    # (runtime contains "claude"). A targeted 'disconnect codex' or
    # 'disconnect gemini' must not touch them.
    is_full_teardown = uninstall_claude_runtime

    if is_full_teardown:
        vault_root = _resolve_vault_root_for_uninstall()
        remove_vault_post_merge_hook(vault_root, dry_run=dry_run)

        unschedule_summarizer(dry_run=dry_run)

    # vaults.yaml additionally requires an explicit --purge-config, even under
    # --yes. Without --purge-config it is always preserved.
    vaults_config = Path.home() / ".config" / PROJECT_NAME / "vaults.yaml"
    if vaults_config.exists() and is_full_teardown and purge_config:
        _step(f"Remove {vaults_config}", dry_run=dry_run)
        if not dry_run:
            try:
                vaults_config.unlink()
                _ok(f"Removed {vaults_config}")
            except OSError as exc:
                _warn(f"Could not remove {vaults_config}: {exc}")
    elif vaults_config.exists() and is_full_teardown and not purge_config:
        _step(
            f"Preserving {vaults_config} (use --purge-config to remove)",
            dry_run=dry_run,
        )

    if not dry_run:
        print()
        _ok("Uninstall complete. Your resolved vault directory was not removed.")
