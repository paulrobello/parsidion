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
from installer.steps import Step, StepList
from installer.ui import _ok, _step, _warn
from installer.vault import remove_vault_post_merge_hook

# Local re-import so the CLAUDE.md @import stripping stays alongside the
# uninstall path that owns it. The constant itself lives in skill.py.
from installer.skill import (
    _CLAUDE_VAULT_MD_IMPORT,
    remove_codex_agents_md,
    remove_gemini_md,
)


def _build_hooks_only_steps(
    *,
    claude_dir: Path,
    settings_file: Path,
    codex_home: Path,
    gemini_home: Path,
    dry_run: bool,
    uninstall_claude_runtime: bool,
    uninstall_codex_runtime: bool,
    uninstall_gemini_runtime: bool,
) -> StepList:
    """Build the hooks-only teardown step list (the ``disconnect`` path).

    Each step removes one runtime's managed hook registrations and nothing
    else — the skill directory, agents, CLAUDE-VAULT.md, and other assets
    are left in place. Mirrors install()'s matrix: a step is included only
    when its runtime is selected.
    """
    steps = StepList()
    if uninstall_claude_runtime:
        steps.append(
            Step(
                "remove_installed_hooks",
                lambda: remove_installed_hooks(
                    claude_dir, settings_file, dry_run=dry_run
                ),
            )
        )
        steps.append(
            Step(
                "remove_legacy_hooks",
                lambda: remove_legacy_hooks(
                    claude_dir, settings_file, dry_run=dry_run
                ),
            )
        )
    if uninstall_codex_runtime:
        steps.append(
            Step(
                "remove_codex_hooks",
                lambda: remove_codex_hooks(codex_home, claude_dir, dry_run=dry_run),
            )
        )
    if uninstall_gemini_runtime:
        steps.append(
            Step(
                "remove_gemini_hooks",
                lambda: remove_gemini_hooks(gemini_home, claude_dir, dry_run=dry_run),
            )
        )
    return steps


def _build_uninstall_steps(
    *,
    claude_dir: Path,
    settings_file: Path,
    codex_home: Path,
    gemini_home: Path,
    dry_run: bool,
    runtime: str,
    uninstall_claude_runtime: bool,
    uninstall_codex_runtime: bool,
    uninstall_gemini_runtime: bool,
    purge_config: bool,
) -> StepList:
    """Build the full-uninstall step list (``--uninstall`` path).

    ARC-017: the uninstall flow is decomposed into ordered, individually-
    testable steps driven through the same :class:`StepList` abstraction as
    install(), so the two flows share their execution machinery. Each step
    is one logical removal (skill dir, agents, hooks, CLAUDE-VAULT.md, a
    runtime's full integration, shared infra, vaults.yaml); the runtime /
    full-teardown / purge-config gating is evaluated once at build time.

    Order is load-bearing only in that the skill dir must be removable
    independently of the hooks (a partial uninstall that fails on the skill
    dir must still attempt the hooks). The matrix is the same one install()
    uses: a step is included iff its runtime is selected. ``is_full_teardown``
    (shared infrastructure — post-merge hook, summarizer schedule,
    vaults.yaml) only fires when the Claude integration itself is being
    removed (ARC-003), so a targeted ``disconnect codex``/``gemini`` does
    not touch it.

    Unlike install(), the vault directory and its config (embeddings,
    username, .gitignore, git init) are deliberately PRESERVED across
    uninstall ("Your resolved vault directory was not removed") — those
    install steps have no counterpart here by long-standing contract.

    Each step runs inside ``StepList.run_all``'s try/except, so a single
    removal failure no longer aborts the whole uninstall (mirrors install's
    ARC-022 fail-accumulation contract). The per-step ``_warn`` for missing
    files is preserved inside each step body.
    """
    steps = StepList()

    if uninstall_claude_runtime:
        skill_dir = claude_dir / "skills" / SKILL_NAME

        def _remove_skill_dir() -> None:
            if skill_dir.exists() or skill_dir.is_symlink():
                _step(f"Remove skill directory: {skill_dir}", dry_run=dry_run)
                if not dry_run:
                    if skill_dir.is_symlink() or skill_dir.is_file():
                        skill_dir.unlink()
                    else:
                        shutil.rmtree(skill_dir)
            else:
                _warn(f"Skill directory not found: {skill_dir}")

        steps.append(Step("remove_skill_dir", _remove_skill_dir))

        legacy_skill_dir = claude_dir / "skills" / LEGACY_SKILL_NAME

        def _remove_legacy_skill_dir() -> None:
            if legacy_skill_dir.exists() or legacy_skill_dir.is_symlink():
                _step(f"Remove legacy skill {legacy_skill_dir}", dry_run=dry_run)
                if not dry_run:
                    try:
                        if legacy_skill_dir.is_symlink() or legacy_skill_dir.is_file():
                            legacy_skill_dir.unlink()
                        else:
                            shutil.rmtree(legacy_skill_dir)
                    except OSError as exc:
                        _warn(
                            f"Could not remove legacy skill {legacy_skill_dir}: {exc}"
                        )

        steps.append(Step("remove_legacy_skill_dir", _remove_legacy_skill_dir))

        def _remove_agents() -> None:
            for agent_src in AGENT_SRCS:
                agent_dest = claude_dir / "agents" / agent_src.name
                if agent_dest.exists():
                    _step(f"Remove agent: {agent_dest}", dry_run=dry_run)
                    if not dry_run:
                        agent_dest.unlink()
                else:
                    _warn(f"Agent not found: {agent_dest}")

        steps.append(Step("remove_agents", _remove_agents))

        def _remove_scripts() -> None:
            scripts_dir = claude_dir / "scripts"
            if SCRIPTS_SRC.exists() and scripts_dir.exists():
                for script in SCRIPTS_SRC.iterdir():
                    if script.is_file():
                        script_dest = scripts_dir / script.name
                        if script_dest.exists():
                            _step(f"Remove script: {script_dest}", dry_run=dry_run)
                            if not dry_run:
                                script_dest.unlink()

        steps.append(Step("remove_scripts", _remove_scripts))

        def _remove_claude_hooks() -> None:
            remove_installed_hooks(claude_dir, settings_file, dry_run=dry_run)
            remove_legacy_hooks(claude_dir, settings_file, dry_run=dry_run)

        steps.append(Step("remove_claude_hooks", _remove_claude_hooks))

        claude_vault_md = claude_dir / "CLAUDE-VAULT.md"
        claude_md = claude_dir / "CLAUDE.md"

        def _remove_claude_vault_md() -> None:
            if claude_vault_md.exists():
                _step(f"Remove {claude_vault_md}", dry_run=dry_run)
                if not dry_run:
                    claude_vault_md.unlink()
            else:
                _warn(f"CLAUDE-VAULT.md not found: {claude_vault_md}")

            if claude_md.exists():
                content = claude_md.read_text(encoding="utf-8")
                if _CLAUDE_VAULT_MD_IMPORT in content:
                    _step(
                        f"Remove @CLAUDE-VAULT.md import from {claude_md}",
                        dry_run=dry_run,
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

        steps.append(Step("remove_claude_vault_md", _remove_claude_vault_md))

    if uninstall_codex_runtime:
        # ARC-022 / SEC-116: disconnect must remove the AGENTS.md block too,
        # otherwise Codex keeps loading parsidion instructions every session.
        # Also revert the [features] hooks flag so Codex stops invoking
        # parsidion hooks even if a stale hooks.json lingers.

        def _remove_codex_integration() -> None:
            remove_codex_hooks(codex_home, claude_dir, dry_run=dry_run)
            remove_codex_agents_md(codex_home, dry_run=dry_run)
            disable_codex_hooks_config(codex_home, dry_run=dry_run)

        steps.append(Step("remove_codex_integration", _remove_codex_integration))
    elif runtime == "none":
        # Preserved from the pre-refactor flow: emit the none-warning at the
        # same point (where the codex block would have run).
        _warn("Runtime selection is none; no runtime hooks will be removed.")

    if uninstall_gemini_runtime:
        # ARC-022 / SEC-116: same instruction-block removal for Gemini.

        def _remove_gemini_integration() -> None:
            remove_gemini_hooks(gemini_home, claude_dir, dry_run=dry_run)
            remove_gemini_md(gemini_home, dry_run=dry_run)

        steps.append(Step("remove_gemini_integration", _remove_gemini_integration))

    # ARC-003: the post-merge hook, summarizer schedule, and vaults.yaml are
    # shared global infrastructure that the Claude install depends on. Only
    # tear them down when the Claude integration itself is being removed
    # (runtime contains "claude"). A targeted 'disconnect codex' or 'disconnect
    # gemini' must not touch them.
    is_full_teardown = uninstall_claude_runtime

    if is_full_teardown:
        vault_root = _resolve_vault_root_for_uninstall()

        def _remove_shared_infra() -> None:
            remove_vault_post_merge_hook(vault_root, dry_run=dry_run)
            unschedule_summarizer(dry_run=dry_run)

        steps.append(Step("remove_shared_infra", _remove_shared_infra))

    # vaults.yaml additionally requires an explicit --purge-config, even under
    # --yes. Without --purge-config it is always preserved.
    vaults_config = Path.home() / ".config" / PROJECT_NAME / "vaults.yaml"

    def _remove_or_preserve_vaults_config(purge: bool) -> None:
        if vaults_config.exists() and is_full_teardown and purge:
            _step(f"Remove {vaults_config}", dry_run=dry_run)
            if not dry_run:
                try:
                    vaults_config.unlink()
                    _ok(f"Removed {vaults_config}")
                except OSError as exc:
                    _warn(f"Could not remove {vaults_config}: {exc}")
        elif vaults_config.exists() and is_full_teardown and not purge:
            _step(
                f"Preserving {vaults_config} (use --purge-config to remove)",
                dry_run=dry_run,
            )

    steps.append(
        Step("remove_vaults_config", lambda: _remove_or_preserve_vaults_config(purge_config))
    )

    return steps


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
    """Remove installed Parsidion assets, or only managed hook registrations.

    By default this is a full uninstall of the Claude integration plus any
    Codex/Gemini runtimes selected via ``runtime``. When ``hooks_only`` is
    True the function instead removes only managed hook entries and leaves
    the skill directory, agents, CLAUDE-VAULT.md, and other assets in place
    — the path used by ``disconnect codex`` / ``disconnect gemini``.

    ARC-003 (preserved here when the function moved): ``codex_home``, the
    post-merge hook, the summarizer schedule, and ``vaults.yaml`` are shared
    global infrastructure that the Claude install depends on. They are only
    torn down when the Claude integration itself is being removed
    (``is_full_teardown``). A targeted ``disconnect codex`` or
    ``disconnect gemini`` must not touch them.

    ARC-003 (also preserved): ``vaults.yaml`` additionally requires an
    explicit ``--purge-config`` (``purge_config=True``) — under ``--yes``
    alone it is always preserved.

    Args:
        claude_dir: Path to the Claude Code config directory (``~/.claude``).
        settings_file: Path to ``~/.claude/settings.json`` — managed Claude
            hook registrations are removed from here.
        dry_run: When True, print the steps that would be taken without
            writing or removing anything.
        yes: When True, skip interactive confirmation prompts. Does NOT
            imply ``purge_config`` — see below.
        hooks_only: When True, remove only Parsidion-managed hook
            registrations (Claude ``settings.json``, Codex ``hooks.json``,
            Gemini ``settings.json``) and leave the skill, agents, scripts,
            and vault untouched.
        runtime: Selector controlling which runtime integrations to tear
            down: ``"claude"``, ``"codex"``, ``"gemini"``, ``"both"``
            (Claude + Codex), ``"all"`` (every runtime), or ``"none"``.
            Accepts the same vocabulary as ``install.py connect``.
        codex_home: Path to the Codex config directory (``~/.codex``); when
            None, resolved from the ``CODEX_HOME`` env var (default
            ``~/.codex``).
        gemini_home: Path to the Gemini config directory (``~/.gemini``);
            when None, defaults to ``~/.gemini``.
        purge_config: When True and a full Claude teardown is selected,
            also remove ``~/.config/parsidion/vaults.yaml``. Always
            preserved otherwise, even under ``yes``.
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
        steps = _build_hooks_only_steps(
            claude_dir=claude_dir,
            settings_file=settings_file,
            codex_home=codex_home,
            gemini_home=gemini_home,
            dry_run=dry_run,
            uninstall_claude_runtime=uninstall_claude_runtime,
            uninstall_codex_runtime=uninstall_codex_runtime,
            uninstall_gemini_runtime=uninstall_gemini_runtime,
        )
        steps.run_all()
        if not dry_run:
            print()
            _ok("Hook uninstall complete.")
        return

    print(bold("\nUninstalling Parsidion..."))

    steps = _build_uninstall_steps(
        claude_dir=claude_dir,
        settings_file=settings_file,
        codex_home=codex_home,
        gemini_home=gemini_home,
        dry_run=dry_run,
        runtime=runtime,
        uninstall_claude_runtime=uninstall_claude_runtime,
        uninstall_codex_runtime=uninstall_codex_runtime,
        uninstall_gemini_runtime=uninstall_gemini_runtime,
        purge_config=purge_config,
    )
    steps.run_all()

    if not dry_run:
        print()
        _ok("Uninstall complete. Your resolved vault directory was not removed.")
