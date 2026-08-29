"""Install plan construction for the Parsidion installer (ARC-008).

Holds the resolved install matrix (:class:`InstallPlan`), the interactive
option prompts, the ``Installation Plan`` printer, and the ordered
:class:`installer.steps.StepList` builder previously inlined in the
1,342-line ``install.py`` entrypoint.

The step functions (``install_skill``, ``merge_hooks``, ...) are imported at
module level so this module's globals are the monkeypatch surface — tests
patch ``installer.plan.<name>`` the way they used to patch
``install.<name>``.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from installer.colors import bold, dim, yellow
from installer.paths import (
    AGENT_SRCS,
    REPO_ROOT,
    SKILL_NAME,
    _CODEX_HOOK_SCRIPTS,
    _ANTIGRAVITY_HOOK_SCRIPTS,
    _HOOK_SCRIPTS,
)
from installer.schedule import schedule_summarizer
from installer.skill import (
    cleanup_legacy_assets,
    enable_ai_mode,
    install_agents,
    install_cli_tools,
    install_parsidion_vault_md,
    install_codex_agents_md,
    install_antigravity_md,
    install_scripts,
    install_skill,
    rebuild_index,
)
from installer.steps import Step, StepList
from installer.ui import _ask, _confirm
from installer.vault import (
    configure_embeddings,
    configure_vault_gitignore,
    configure_vault_username,
    create_templates_symlink,
    create_vault_dirs,
    create_vaults_config,
    init_vault_git,
    install_vault_post_merge_hook,
    read_embeddings_enabled,
    record_installed_vault,
)
from installer.hooks import (
    enable_codex_hooks_config,
    merge_codex_hooks,
    merge_antigravity_hooks,
    merge_hooks,
)


@dataclass(frozen=True)
class InstallPlan:
    """The resolved install matrix (ARC-008).

    Everything ``build_install_steps`` / ``print_install_plan`` need, frozen
    at resolution time by ``installer.cli.install()`` — replacing the 16-20
    loose parameters the helpers used to take.
    """

    runtime: str
    install_claude_runtime: bool
    install_codex_runtime: bool
    install_antigravity_runtime: bool
    install_runtime_hooks: bool
    claude_dir: Path
    codex_home: Path
    antigravity_home: Path
    settings_file: Path
    vault_root: Path
    vault_username: str
    install_tools: bool
    do_schedule: bool
    enable_ai: bool
    enable_embeddings: bool
    summarizer_hour: int
    rebuild_graph: bool
    graph_include_daily: bool
    skip_agent: bool
    skip_hooks: bool
    dry_run: bool
    verbose: bool
    force: bool
    yes: bool
    create_vaults_config: bool
    args: object = field(default=None, repr=False)
    """The raw argparse namespace (kept for prompts that read flags not in
    this dataclass yet); new code should add the flag to the plan instead."""


def collect_install_options(
    plan_args: argparse.Namespace,
    *,
    install_claude_runtime: bool,
    vault_root: Path,
) -> tuple[bool, bool, bool, bool, str]:
    """Resolve the four interactive install options plus vault username.

    Prompt helpers (``_ask`` / ``_confirm``) and ``read_embeddings_enabled``
    are resolved through THIS module's globals so the test suite's
    ``monkeypatch.setattr(installer.plan, ...)`` keeps working.

    --yes short-circuits every prompt; the embeddings branch under --yes
    intentionally PRESERVES the existing config setting rather than clobbering
    it (regression guard).
    """
    args = plan_args
    # --- CLI tools prompt ---
    install_tools: bool = args.install_tools
    if not args.yes and not install_tools:
        print()
        print(bold("CLI Tools (optional)"))
        print(
            dim(
                "  Installs vault-search, vault-new, and vault-stats as global\n"
                "  commands via 'uv tool install --editable .[tools]'.\n"
                "  Requires uv to be installed."
            )
        )
        install_tools = _confirm(
            "Install CLI tools (vault-search, vault-new, vault-stats)?", default=True
        )

    # --- AI mode prompt ---
    enable_ai: bool = args.enable_ai
    if (
        not args.yes
        and not enable_ai
        and install_claude_runtime
        and not args.skip_hooks
    ):
        print()
        print(bold("AI-Powered Note Selection (optional)"))
        print(
            dim(
                "  When enabled, the SessionStart hook uses claude-haiku to\n"
                "  intelligently select relevant vault notes instead of keyword\n"
                "  matching. Requires a 30s hook timeout and an Anthropic API key."
            )
        )
        enable_ai = _confirm("Enable AI-powered note selection?", default=True)

    # --- Embeddings prompt ---
    enable_embeddings: bool = args.enable_embeddings
    if not enable_embeddings and args.yes:
        # Non-interactive sync without --enable-embeddings: PRESERVE the current
        # setting instead of clobbering it. (Regression: every `install.py --yes`
        # silently disabled embeddings because the flag defaults False and --yes
        # skipped the interactive prompt that defaulted True.)
        enable_embeddings = read_embeddings_enabled(vault_root)
    elif not args.yes and not enable_embeddings:
        print()
        print(bold("Semantic Search Embeddings (optional)"))
        print(
            dim(
                "  When enabled, builds a vector index of vault notes for semantic\n"
                "  search (vault-search, session_start_hook with use_embeddings).\n"
                "  Requires ~67 MB model download on first run."
            )
        )
        enable_embeddings = _confirm("Enable embeddings?", default=True)

    # --- Nightly summarizer scheduler prompt ---
    do_schedule: bool = args.schedule_summarizer
    if not args.yes and not do_schedule:
        scheduler = "launchd" if sys.platform == "darwin" else "cron"
        print()
        print(bold("Nightly Summarizer Scheduler (optional)"))
        print(
            dim(
                f"  Installs a {scheduler} job that runs summarize_sessions.py\n"
                f"  automatically at {args.summarizer_hour:02d}:00 each night.\n"
                "  Keeps the vault up to date without manual intervention."
            )
        )
        do_schedule = _confirm("Schedule nightly summarizer?", default=False)

    # --- Vault username prompt ---
    _detected_user = os.environ.get("USER", os.environ.get("USERNAME", ""))
    vault_username: str = args.vault_username
    if not args.yes and not vault_username:
        print()
        print(bold("Vault Username"))
        print(
            dim(
                "  Daily notes are stored as Daily/YYYY-MM/DD-{username}.md so\n"
                "  multiple team members can share a vault via git without conflicts.\n"
                f"  Auto-detected: {_detected_user or '(unknown)'}"
            )
        )
        vault_username = _ask(
            "Username for daily notes", default=_detected_user
        ).strip()
    if not vault_username:
        vault_username = _detected_user

    return install_tools, enable_ai, enable_embeddings, do_schedule, vault_username


def print_install_plan(plan: InstallPlan) -> None:
    """Print the ``Installation Plan`` block verbatim.

    Output must stay byte-identical — the dry-run baseline diff gates this.
    """
    print()
    print(bold("Installation Plan"))
    print(f"  {dim('Runtime     :')} {plan.runtime}")
    if plan.install_claude_runtime:
        print(f"  {dim('Claude dir   :')} {plan.claude_dir}")
    if plan.install_codex_runtime:
        print(f"  {dim('Codex home  :')} {plan.codex_home}")
    if plan.install_antigravity_runtime:
        print(f"  {dim('Antigravity home :')} {plan.antigravity_home}")
    print(f"  {dim('Vault path   :')} {plan.vault_root}")
    if plan.install_tools:
        print(f"  {dim('CLI tools    :')} vault-search, vault-new, vault-stats")
    if plan.do_schedule:
        graph_suffix = " + graph rebuild" if plan.rebuild_graph else ""
        print(
            f"  {dim('Scheduler    :')} nightly summarizer at "
            f"{plan.summarizer_hour:02d}:00 "
            f"({'launchd' if sys.platform == 'darwin' else 'cron'}){graph_suffix}"
        )
    if plan.enable_ai:
        print(f"  {dim('AI mode      :')} enabled (SessionStart timeout → 30s)")
    print(
        f"  {dim('Embeddings   :')} "
        f"{'enabled' if plan.enable_embeddings else 'disabled'}"
    )
    print(f"  {dim('Vault username:')} {plan.vault_username or '(auto: $USER)'}")
    if plan.install_claude_runtime:
        print(f"  {dim('Settings     :')} {plan.settings_file}")
    print(f"  {dim('Install skill:')} {plan.claude_dir / 'skills' / SKILL_NAME}")
    if plan.install_claude_runtime and not plan.skip_agent:
        for agent_src in AGENT_SRCS:
            print(
                f"  {dim('Install agent:')} "
                f"{plan.claude_dir / 'agents' / agent_src.name}"
            )
    if plan.install_runtime_hooks:
        if plan.install_claude_runtime:
            print(f"  {dim('Claude hooks:')} {', '.join(_HOOK_SCRIPTS.keys())}")
        if plan.install_codex_runtime:
            print(f"  {dim('Codex hooks :')} {', '.join(_CODEX_HOOK_SCRIPTS.keys())}")
        if plan.install_antigravity_runtime:
            print(
                f"  {dim('Antigravity hooks:')} {', '.join(_ANTIGRAVITY_HOOK_SCRIPTS.keys())}"
            )
    else:
        reason = "runtime none" if plan.runtime == "none" else "--skip-hooks"
        print(f"  {dim('Runtime hooks:')} skipped ({reason})")
    print(f"  {dim('Install scripts:')} {plan.claude_dir / 'scripts'}/")
    if plan.install_claude_runtime:
        print(
            f"  {dim('Install guidance:')} {plan.claude_dir / 'PARSIDION-VAULT.md'} "
            "(@import into CLAUDE.md)"
        )
    if plan.dry_run:
        print(f"\n  {yellow('[DRY RUN — no changes will be made]')}")


def build_install_steps(plan: InstallPlan) -> StepList:
    """Build the ordered :class:`StepList` for the install transaction.

    The matrix predicate (``plan.install_claude_runtime and not
    plan.skip_hooks``, etc.) is evaluated exactly once per step at build
    time. Each step's ``on_run`` lambda references THIS module's global
    function names (``install_skill``, ``merge_hooks``, ...) so the test
    suite's ``monkeypatch.setattr(installer.plan, '<name>', ...)`` keeps
    working.

    Order is load-bearing: the Templates symlink step needs the skill
    installed first (it derives ``templates_src`` from the skill dir); hooks
    must be registered before the AI-mode config write; the vault must exist
    before the index rebuild. Do not reorder without re-running the full
    install test suite + the dry-run baseline diff.
    """
    steps = StepList()
    templates_src = plan.claude_dir / "skills" / SKILL_NAME / "templates"

    # 1. Install skill
    steps.append(
        Step(
            "install_skill",
            lambda: install_skill(
                plan.claude_dir,
                plan.vault_root,
                force=plan.force,
                yes=plan.yes,
                dry_run=plan.dry_run,
                verbose=plan.verbose,
            ),
        )
    )

    # 2. Install agents
    if plan.install_claude_runtime and not plan.skip_agent:
        steps.append(
            Step(
                "install_agents",
                lambda: install_agents(plan.claude_dir, dry_run=plan.dry_run),
            )
        )

    # 3. Install scripts
    steps.append(
        Step(
            "install_scripts",
            lambda: install_scripts(plan.claude_dir, dry_run=plan.dry_run),
        )
    )

    # 4. Create vault directories
    steps.append(
        Step(
            "create_vault_dirs",
            lambda: create_vault_dirs(plan.vault_root, dry_run=plan.dry_run),
        )
    )

    # 5. Create Templates symlink (needs the skill installed first).
    steps.append(
        Step(
            "create_templates_symlink",
            lambda: create_templates_symlink(
                plan.vault_root,
                templates_src,
                dry_run=plan.dry_run,
                verbose=plan.verbose,
            ),
        )
    )

    # 6. Clean up legacy managed parsidion-cc hooks/assets, then register hooks.
    if plan.install_claude_runtime and not plan.skip_hooks:
        steps.append(
            Step(
                "cleanup_legacy_assets",
                lambda: cleanup_legacy_assets(
                    plan.claude_dir,
                    plan.settings_file,
                    dry_run=plan.dry_run,
                    verbose=plan.verbose,
                ),
            )
        )
        steps.append(
            Step(
                "merge_hooks",
                lambda: merge_hooks(
                    plan.claude_dir,
                    plan.settings_file,
                    dry_run=plan.dry_run,
                    verbose=plan.verbose,
                    enable_ai_mode=plan.enable_ai,
                ),
            )
        )

    if plan.install_codex_runtime and not plan.skip_hooks:
        steps.append(
            Step(
                "enable_codex_hooks_config",
                lambda: enable_codex_hooks_config(
                    plan.codex_home, dry_run=plan.dry_run, yes=plan.yes
                ),
            )
        )
        steps.append(
            Step(
                "merge_codex_hooks",
                lambda: merge_codex_hooks(
                    plan.codex_home,
                    plan.claude_dir,
                    dry_run=plan.dry_run,
                    verbose=plan.verbose,
                ),
            )
        )

    if plan.install_antigravity_runtime and not plan.skip_hooks:
        steps.append(
            Step(
                "merge_antigravity_hooks",
                lambda: merge_antigravity_hooks(
                    plan.antigravity_home,
                    plan.claude_dir,
                    dry_run=plan.dry_run,
                    verbose=plan.verbose,
                ),
            )
        )

    # 6b. Write ai_model to vault config.yaml (the settings.json half of the
    # AI-mode flow is merged into merge_hooks above).
    if plan.enable_ai and plan.install_claude_runtime and not plan.skip_hooks:
        steps.append(
            Step(
                "enable_ai_mode",
                lambda: enable_ai_mode(plan.vault_root, dry_run=plan.dry_run),
            )
        )

    # 7. Install PARSIDION-VAULT.md and wire @import into CLAUDE.md.
    if plan.install_claude_runtime:
        steps.append(
            Step(
                "install_parsidion_vault_md",
                lambda: install_parsidion_vault_md(
                    plan.claude_dir, dry_run=plan.dry_run, verbose=plan.verbose
                ),
            )
        )

    # 7b. Inject parsidion instructions into codex/antigravity config dirs.
    if plan.install_codex_runtime:
        steps.append(
            Step(
                "install_codex_agents_md",
                lambda: install_codex_agents_md(
                    plan.codex_home, dry_run=plan.dry_run, verbose=plan.verbose
                ),
            )
        )
    if plan.install_antigravity_runtime:
        steps.append(
            Step(
                "install_antigravity_md",
                lambda: install_antigravity_md(
                    plan.antigravity_home, dry_run=plan.dry_run, verbose=plan.verbose
                ),
            )
        )

    # 8. Rebuild vault index.
    steps.append(
        Step(
            "rebuild_index",
            lambda: rebuild_index(plan.claude_dir, dry_run=plan.dry_run),
        )
    )

    # 9. Configure vault .gitignore for machine-local files.
    steps.append(
        Step(
            "configure_vault_gitignore",
            lambda: configure_vault_gitignore(plan.vault_root, dry_run=plan.dry_run),
        )
    )

    # 9b. Initialize vault as a git repo (no-op if already initialized).
    steps.append(
        Step(
            "init_vault_git",
            lambda: init_vault_git(plan.vault_root, dry_run=plan.dry_run),
        )
    )

    # 9c. Install post-merge git hook for multi-machine sync.
    steps.append(
        Step(
            "install_vault_post_merge_hook",
            lambda: install_vault_post_merge_hook(
                plan.vault_root, plan.claude_dir, dry_run=plan.dry_run
            ),
        )
    )

    # 9d. Write vault.username to config.yaml (per-user daily note naming).
    steps.append(
        Step(
            "configure_vault_username",
            lambda: configure_vault_username(
                plan.vault_root, dry_run=plan.dry_run, username=plan.vault_username
            ),
        )
    )

    # 9e. Write embeddings.enabled to config.yaml.
    steps.append(
        Step(
            "configure_embeddings",
            lambda: configure_embeddings(
                plan.vault_root,
                enabled=plan.enable_embeddings,
                dry_run=plan.dry_run,
            ),
        )
    )

    # 10. Install global CLI tools (vault-search, vault-new, vault-stats).
    if plan.install_tools:
        steps.append(
            Step(
                "install_cli_tools",
                lambda: install_cli_tools(REPO_ROOT, dry_run=plan.dry_run),
            )
        )

    # 11. Schedule nightly summarizer (optional, --schedule-summarizer).
    if plan.do_schedule:
        steps.append(
            Step(
                "schedule_summarizer",
                lambda: schedule_summarizer(
                    plan.claude_dir,
                    dry_run=plan.dry_run,
                    hour=plan.summarizer_hour,
                    rebuild_graph=plan.rebuild_graph,
                    graph_include_daily=plan.graph_include_daily,
                ),
            )
        )

    # 12. Create vaults.yaml config template (optional, --create-vaults-config).
    if plan.create_vaults_config:
        steps.append(
            Step(
                "create_vaults_config",
                lambda: create_vaults_config(dry_run=plan.dry_run),
            )
        )

    # 12b. Persist a non-default --vault into vaults.yaml so the installed
    # hooks (which call resolve_vault() with no explicit arg) can find it.
    # The reference is deliberately through this module's global so the
    # monkeypatch surface stays uniform (installer.plan.record_installed_vault).
    if not plan.dry_run:
        steps.append(
            Step(
                "record_installed_vault",
                lambda: record_installed_vault(plan.vault_root, dry_run=plan.dry_run),
            )
        )

    return steps
