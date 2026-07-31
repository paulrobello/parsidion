#!/usr/bin/env python3
"""Parsidion installer.

Installs the Claude Vault skill, hooks, and research agent into ~/.claude/.
Prompts interactively for the Obsidian vault location and customizes the
installation accordingly. Merges hooks into ~/.claude/settings.json without
overwriting existing configuration.

Usage:
    uv run install.py [options]
    python install.py [options]

Options:
    --vault PATH           Vault path (skips interactive prompt)
    --claude-dir PATH      Target ~/.claude directory (default: ~/.claude)
    --dry-run, -n          Preview actions without making changes
    --verbose, -v          Show detailed output
    --force, -f            Overwrite existing skill files
    --yes, -y              Skip all confirmation prompts; uses ~/ParsidionVault as
                           the vault path unless legacy ~/ClaudeVault exists or
                           --vault PATH is also supplied
    --skip-hooks           Do not modify settings.json
    --skip-agent           Do not install any agents
    --migrate-vault        Rename legacy ~/ClaudeVault to ~/ParsidionVault
    --no-legacy-vault-symlink
                           Do not leave ~/ClaudeVault as a compatibility symlink
    --uninstall            Remove installed skill, agent, hooks, and related assets
    --uninstall-hooks      Remove only installed hook registrations from settings.json
    --enable-ai            Enable AI-powered note selection (writes ai_model to config.yaml, sets 30s timeout)
    --install-tools     Install vault-search, vault-new, and vault-stats as global CLI commands
    --schedule-summarizer  Install nightly cron/launchd job to auto-run summarize_sessions.py
    --summarizer-hour N    Hour of day (0-23) for the scheduled summarizer (default: 3)
    --help, -h          Show this help message
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Re-export the full public API from submodules so callers (and tests) that
# do ``import install; install.<name>`` continue to work without change.
#
# IMPORTANT: _ask and _FORBIDDEN_PREFIXES are imported into THIS module's
# namespace so that:
#   - monkeypatch.setattr(install, "_ask", ...) patches the binding that
#     resolve_runtime_choice and prompt_vault_path use (both live here).
#   - monkeypatch.setattr(install, "_FORBIDDEN_PREFIXES", ...) patches the
#     binding that validate_vault_path uses (also lives here).
# ---------------------------------------------------------------------------

# colours
from installer.colors import bold, cyan, dim, green, red, yellow  # noqa: F401

# UI helpers — _ask and _FORBIDDEN_PREFIXES MUST be in this module's namespace
from installer.ui import (  # noqa: F401
    _ask,
    _confirm,
    _err,
    _make_vprint,
    _ok,
    _print,
    _step,
    _warn,
)

# paths / constants
from installer.paths import (  # noqa: F401
    AGENT_SRCS,
    CLAUDE_VAULT_MD_SRC,
    DEFAULT_VAULT_NAME,
    LEGACY_DEFAULT_VAULT_NAME,
    LEGACY_PROJECT_NAME,
    LEGACY_SKILL_NAME,
    LEGACY_SKILL_SRC,
    PROJECT_NAME,
    REPO_ROOT,
    SCRIPTS_SRC,
    SKILL_NAME,
    SKILL_SRC,
    VAULT_DIRS,
    _CODEX_HOOK_SCRIPTS,
    _GEMINI_HOOK_NAMES,
    _GEMINI_HOOK_SCRIPTS,
    _HOOK_OPTIONS,
    _HOOK_SCRIPTS,
    _RUNTIME_CHOICES,
    _default_vault_path,
    _extract_vault_dirs,
    _resolve_vault_root_for_uninstall,
    _wants_claude_runtime,
    _wants_codex_runtime,
    _wants_gemini_runtime,
)

# _FORBIDDEN_PREFIXES must be imported into THIS namespace for monkeypatching
from installer.paths import _FORBIDDEN_PREFIXES  # noqa: F401

# hooks
from installer.hooks import (  # noqa: F401
    _filter_hook_entries,
    _find_hook_handler,
    _hook_already_registered,
    _hook_command,
    _is_legacy_managed_hook_command,
    _legacy_hook_command,
    _managed_hook_command,
    _normalize_hook_command,
    _set_codex_hooks_in_features_section,
    enable_codex_hooks_config,
    merge_codex_hooks,
    merge_gemini_hooks,
    merge_hooks,
    remove_codex_hooks,
    remove_gemini_hooks,
    remove_installed_hooks,
    remove_legacy_hooks,
)

# schedule
from installer.schedule import (  # noqa: F401
    _CRON_MARKER,
    _LAUNCHD_PLIST_LABEL,
    _LAUNCHD_PLIST_NAME,
    _build_launchd_plist,
    _schedule_summarizer_cron,
    _schedule_summarizer_launchd,
    schedule_summarizer,
    unschedule_summarizer,
)

# vault
from installer.vault import (  # noqa: F401
    _POST_MERGE_HOOK_TEMPLATE,
    _POST_MERGE_MARKER,
    configure_embeddings,
    read_embeddings_enabled,
    configure_vault_gitignore,
    configure_vault_username,
    create_templates_symlink,
    create_vault_dirs,
    create_vaults_config,
    init_vault_git,
    install_vault_post_merge_hook,
    migrate_default_vault,
    record_installed_vault,
    remove_vault_post_merge_hook,
)

# skill / uninstall (ARC-025: uninstall lives in installer.uninstall now)
from installer.skill import (  # noqa: F401
    _CLAUDE_VAULT_MD_IMPORT,
    _can_symlink,
    cleanup_legacy_assets,
    enable_ai_mode,
    install_agents,
    install_cli_tools,
    install_claude_vault_md,
    install_codex_agents_md,
    install_gemini_md,
    install_scripts,
    install_skill,
    rebuild_index,
)
from installer.steps import Step, StepList  # noqa: F401
from installer.uninstall import uninstall  # noqa: F401

# ---------------------------------------------------------------------------
# Functions that call _ask or _FORBIDDEN_PREFIXES must live HERE so that
# monkeypatch.setattr(install, "_ask", ...) and
# monkeypatch.setattr(install, "_FORBIDDEN_PREFIXES", ...) affect them.
# ---------------------------------------------------------------------------


def validate_vault_path(raw: str) -> tuple[Path, str | None]:
    """Expand and validate the vault path.

    Returns:
        (resolved_path, error_message) — error is None when valid.
    """
    if not raw.strip():
        return Path(), "Path cannot be empty."

    expanded = Path(raw).expanduser().resolve()

    # SEC-009: Use Path.is_relative_to() instead of str.startswith() to prevent
    # false positives where a forbidden prefix string matches a different path
    # (e.g. "/usr" matching "/usrdata", or "/bin" matching "/binary").
    # NOTE: references module-level _FORBIDDEN_PREFIXES so monkeypatch works.
    for forbidden in _FORBIDDEN_PREFIXES:
        forbidden_path = Path(forbidden).resolve()
        if expanded == forbidden_path or expanded.is_relative_to(forbidden_path):
            return expanded, f"Cannot use system or Claude config directory: {expanded}"

    return expanded, None


def prompt_vault_path(default: Path) -> Path:
    """Interactively prompt for the Obsidian vault path with validation."""
    print()
    print(bold("Obsidian Vault Location"))
    print(
        dim(
            "This is where Parsidion will store your knowledge notes.\n"
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


def resolve_runtime_choice(
    runtime: str | None,
    *,
    yes: bool,
    interactive: bool,
) -> str:
    """Resolve runtime selection for install/uninstall flows."""
    if runtime:
        return runtime
    if yes or not interactive:
        return "claude"

    print()
    print(bold("Runtime Integrations"))
    print(
        dim(
            "  1. Claude only — ~/.claude settings, skills, agents, and hooks.\n"
            "  2. Codex only — ~/.codex hooks for SessionStart and Stop.\n"
            "  3. Gemini only — ~/.gemini settings hooks for SessionStart and SessionEnd.\n"
            "  4. Claude + Codex.\n"
            "  5. All runtimes — Claude + Codex + Gemini.\n"
            "  6. Shared tooling only — no runtime hooks."
        )
    )
    answer = _ask("Install runtime integrations", default="both").strip().lower()
    if answer in ("", "4", "both", "claude+codex", "claude + codex"):
        return "both"
    if answer in ("1", "claude", "claude only"):
        return "claude"
    if answer in ("2", "codex", "codex only"):
        return "codex"
    if answer in ("3", "gemini", "gemini only"):
        return "gemini"
    if answer in ("5", "all", "all runtimes", "claude+codex+gemini"):
        return "all"
    if answer in ("6", "none", "shared", "shared tooling only"):
        return "none"
    _warn(f"Unknown runtime selection {answer!r}; defaulting to both")
    return "both"


# ---------------------------------------------------------------------------
# Main install flow
# ---------------------------------------------------------------------------


def _collect_install_options(
    args: argparse.Namespace,
    *,
    install_claude_runtime: bool,
    vault_root: Path,
) -> tuple[bool, bool, bool, bool, str]:
    """Resolve the four interactive install options plus vault username.

    Pulled out of ``install()`` (ARC-017) so the prompt logic is individually
    testable and ``install()``'s cyclomatic complexity drops. Returns
    ``(install_tools, enable_ai, enable_embeddings, do_schedule,
    vault_username)``. Prompt helpers (``_ask`` / ``_confirm``) and
    ``read_embeddings_enabled`` are resolved through THIS module's globals so
    the test suite's ``monkeypatch.setattr(install, ...)`` keeps working.

    --yes short-circuits every prompt; the embeddings branch under --yes
    intentionally PRESERVES the existing config setting rather than clobbering
    it (regression guard).
    """
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


def _print_install_plan(
    *,
    runtime: str,
    install_claude_runtime: bool,
    install_codex_runtime: bool,
    install_gemini_runtime: bool,
    install_runtime_hooks: bool,
    claude_dir: Path,
    codex_home: Path,
    gemini_home: Path,
    settings_file: Path,
    vault_root: Path,
    vault_username: str,
    install_tools: bool,
    do_schedule: bool,
    enable_ai: bool,
    enable_embeddings: bool,
    summarizer_hour: int,
    rebuild_graph: bool,
    skip_agent: bool,
    skip_hooks: bool,
    dry_run: bool,
) -> None:
    """Print the ``Installation Plan`` block verbatim.

    Extracted from ``install()`` (ARC-017) for testability and CC reduction.
    Output must stay byte-identical — the dry-run baseline diff gates this.
    """
    print()
    print(bold("Installation Plan"))
    print(f"  {dim('Runtime     :')} {runtime}")
    if install_claude_runtime:
        print(f"  {dim('Claude dir   :')} {claude_dir}")
    if install_codex_runtime:
        print(f"  {dim('Codex home  :')} {codex_home}")
    if install_gemini_runtime:
        print(f"  {dim('Gemini home :')} {gemini_home}")
    print(f"  {dim('Vault path   :')} {vault_root}")
    if install_tools:
        print(f"  {dim('CLI tools    :')} vault-search, vault-new, vault-stats")
    if do_schedule:
        graph_suffix = " + graph rebuild" if rebuild_graph else ""
        print(
            f"  {dim('Scheduler    :')} nightly summarizer at {summarizer_hour:02d}:00 "
            f"({'launchd' if sys.platform == 'darwin' else 'cron'}){graph_suffix}"
        )
    if enable_ai:
        print(f"  {dim('AI mode      :')} enabled (SessionStart timeout → 30s)")
    print(f"  {dim('Embeddings   :')} {'enabled' if enable_embeddings else 'disabled'}")
    print(f"  {dim('Vault username:')} {vault_username or '(auto: $USER)'}")
    if install_claude_runtime:
        print(f"  {dim('Settings     :')} {settings_file}")
    print(f"  {dim('Install skill:')} {claude_dir / 'skills' / SKILL_NAME}")
    if install_claude_runtime and not skip_agent:
        for agent_src in AGENT_SRCS:
            print(f"  {dim('Install agent:')} {claude_dir / 'agents' / agent_src.name}")
    if install_runtime_hooks:
        if install_claude_runtime:
            print(f"  {dim('Claude hooks:')} {', '.join(_HOOK_SCRIPTS.keys())}")
        if install_codex_runtime:
            print(f"  {dim('Codex hooks :')} {', '.join(_CODEX_HOOK_SCRIPTS.keys())}")
        if install_gemini_runtime:
            print(f"  {dim('Gemini hooks:')} {', '.join(_GEMINI_HOOK_SCRIPTS.keys())}")
    else:
        reason = "runtime none" if runtime == "none" else "--skip-hooks"
        print(f"  {dim('Runtime hooks:')} skipped ({reason})")
    print(f"  {dim('Install scripts:')} {claude_dir / 'scripts'}/")
    if install_claude_runtime:
        print(
            f"  {dim('Install guidance:')} {claude_dir / 'CLAUDE-VAULT.md'} (@import into CLAUDE.md)"
        )
    if dry_run:
        print(f"\n  {yellow('[DRY RUN — no changes will be made]')}")


def _build_install_steps(
    *,
    args: argparse.Namespace,
    claude_dir: Path,
    vault_root: Path,
    codex_home: Path,
    gemini_home: Path,
    settings_file: Path,
    dry_run: bool,
    verbose: bool,
    install_claude_runtime: bool,
    install_codex_runtime: bool,
    install_gemini_runtime: bool,
    enable_ai: bool,
    enable_embeddings: bool,
    vault_username: str,
    install_tools: bool,
    do_schedule: bool,
) -> StepList:
    """Build the ordered :class:`StepList` for the install transaction.

    ARC-017: the ~12-step install plan is constructed here from the resolved
    runtime/flag matrix, then driven by a single ``steps.run_all()`` call in
    ``install()``. Each step's ``on_run`` lambda references install.py's
    module-global function names (``install_skill``, ``merge_hooks``, ...) so
    the test suite's ``monkeypatch.setattr(install, '<name>', ...)`` keeps
    working — this is why the helper lives in install.py rather than in
    installer/.

    The matrix predicate (``install_claude_runtime and not args.skip_hooks``,
    etc.) is now evaluated exactly once per step at build time, rather than
    being re-checked 15-20× as bare ``if`` blocks inside a monolithic
    ``install()`` — collapsing the predicate re-evaluation the audit flagged.

    Order is load-bearing: the Templates symlink step needs the skill
    installed first (it derives ``templates_src`` from the skill dir); hooks
    must be registered before the AI-mode config write; the vault must exist
    before the index rebuild. Do not reorder without re-running the full
    install test suite + the dry-run baseline diff.
    """
    steps = StepList()
    templates_src = claude_dir / "skills" / SKILL_NAME / "templates"

    # 1. Install skill
    steps.append(
        Step(
            "install_skill",
            lambda: install_skill(
                claude_dir,
                vault_root,
                force=args.force,
                yes=args.yes,
                dry_run=dry_run,
                verbose=verbose,
            ),
        )
    )

    # 2. Install agents
    if install_claude_runtime and not args.skip_agent:
        steps.append(
            Step(
                "install_agents",
                lambda: install_agents(claude_dir, dry_run=dry_run),
            )
        )

    # 3. Install scripts
    steps.append(
        Step(
            "install_scripts",
            lambda: install_scripts(claude_dir, dry_run=dry_run),
        )
    )

    # 4. Create vault directories
    steps.append(
        Step(
            "create_vault_dirs",
            lambda: create_vault_dirs(vault_root, dry_run=dry_run),
        )
    )

    # 5. Create Templates symlink (needs the skill installed first).
    steps.append(
        Step(
            "create_templates_symlink",
            lambda: create_templates_symlink(
                vault_root, templates_src, dry_run=dry_run, verbose=verbose
            ),
        )
    )

    # 6. Clean up legacy managed parsidion-cc hooks/assets, then register hooks.
    # ARC-025: pass enable_ai_mode through to merge_hooks so settings.json is
    # written once (the SessionStart timeout raise happens in the same RMW as
    # the hook registration instead of a second independent write). The
    # vault-config half of the AI-mode flow still runs separately below.
    if install_claude_runtime and not args.skip_hooks:
        steps.append(
            Step(
                "cleanup_legacy_assets",
                lambda: cleanup_legacy_assets(
                    claude_dir,
                    settings_file,
                    dry_run=dry_run,
                    verbose=verbose,
                ),
            )
        )
        steps.append(
            Step(
                "merge_hooks",
                lambda: merge_hooks(
                    claude_dir,
                    settings_file,
                    dry_run=dry_run,
                    verbose=verbose,
                    enable_ai_mode=enable_ai,
                ),
            )
        )

    if install_codex_runtime and not args.skip_hooks:
        steps.append(
            Step(
                "enable_codex_hooks_config",
                lambda: enable_codex_hooks_config(
                    codex_home, dry_run=dry_run, yes=args.yes
                ),
            )
        )
        steps.append(
            Step(
                "merge_codex_hooks",
                lambda: merge_codex_hooks(
                    codex_home, claude_dir, dry_run=dry_run, verbose=verbose
                ),
            )
        )

    if install_gemini_runtime and not args.skip_hooks:
        steps.append(
            Step(
                "merge_gemini_hooks",
                lambda: merge_gemini_hooks(
                    gemini_home, claude_dir, dry_run=dry_run, verbose=verbose
                ),
            )
        )

    # 6b. Write ai_model to vault config.yaml (the settings.json half of the
    # AI-mode flow is now merged into merge_hooks above). ARC-025.
    if enable_ai and install_claude_runtime and not args.skip_hooks:
        steps.append(
            Step(
                "enable_ai_mode",
                lambda: enable_ai_mode(vault_root, dry_run=dry_run),
            )
        )

    # 7. Install CLAUDE-VAULT.md and wire @import into CLAUDE.md.
    if install_claude_runtime:
        steps.append(
            Step(
                "install_claude_vault_md",
                lambda: install_claude_vault_md(
                    claude_dir, dry_run=dry_run, verbose=verbose
                ),
            )
        )

    # 7b. Inject parsidion instructions into codex/gemini config dirs.
    if install_codex_runtime:
        steps.append(
            Step(
                "install_codex_agents_md",
                lambda: install_codex_agents_md(
                    codex_home, dry_run=dry_run, verbose=verbose
                ),
            )
        )
    if install_gemini_runtime:
        steps.append(
            Step(
                "install_gemini_md",
                lambda: install_gemini_md(
                    gemini_home, dry_run=dry_run, verbose=verbose
                ),
            )
        )

    # 8. Rebuild vault index.
    steps.append(
        Step(
            "rebuild_index",
            lambda: rebuild_index(claude_dir, dry_run=dry_run),
        )
    )

    # 9. Configure vault .gitignore for machine-local files.
    steps.append(
        Step(
            "configure_vault_gitignore",
            lambda: configure_vault_gitignore(vault_root, dry_run=dry_run),
        )
    )

    # 9b. Initialize vault as a git repo (no-op if already initialized).
    steps.append(
        Step(
            "init_vault_git",
            lambda: init_vault_git(vault_root, dry_run=dry_run),
        )
    )

    # 9c. Install post-merge git hook for multi-machine sync.
    steps.append(
        Step(
            "install_vault_post_merge_hook",
            lambda: install_vault_post_merge_hook(
                vault_root, claude_dir, dry_run=dry_run
            ),
        )
    )

    # 9d. Write vault.username to config.yaml (per-user daily note naming).
    steps.append(
        Step(
            "configure_vault_username",
            lambda: configure_vault_username(
                vault_root, dry_run=dry_run, username=vault_username
            ),
        )
    )

    # 9e. Write embeddings.enabled to config.yaml.
    steps.append(
        Step(
            "configure_embeddings",
            lambda: configure_embeddings(
                vault_root, enabled=enable_embeddings, dry_run=dry_run
            ),
        )
    )

    # 10. Install global CLI tools (vault-search, vault-new, vault-stats).
    if install_tools:
        steps.append(
            Step(
                "install_cli_tools",
                lambda: install_cli_tools(REPO_ROOT, dry_run=dry_run),
            )
        )

    # 11. Schedule nightly summarizer (optional, --schedule-summarizer).
    if do_schedule:
        steps.append(
            Step(
                "schedule_summarizer",
                lambda: schedule_summarizer(
                    claude_dir,
                    dry_run=dry_run,
                    hour=args.summarizer_hour,
                    rebuild_graph=args.rebuild_graph,
                    graph_include_daily=args.graph_include_daily,
                ),
            )
        )

    # 12. Create vaults.yaml config template (optional, --create-vaults-config).
    if args.create_vaults_config:
        steps.append(
            Step(
                "create_vaults_config",
                lambda: create_vaults_config(dry_run=dry_run),
            )
        )

    # 12b. ARC-019: persist a non-default --vault into vaults.yaml so the
    # installed hooks (which call resolve_vault() with no explicit arg) can
    # find it. Without this, ``install.py --vault ~/WorkVault`` populated
    # the vault while every hook kept reading ~/ParsidionVault.
    #
    # The import is deliberately function-local to match the pre-refactor
    # binding: tests that stub install.record_installed_vault (module global)
    # do not affect this lambda, so the real helper runs in the test env —
    # same as before ARC-017. Moving it to a module-level import would change
    # test behaviour.
    if not dry_run:
        from installer.vault import record_installed_vault

        steps.append(
            Step(
                "record_installed_vault",
                lambda: record_installed_vault(vault_root, dry_run=dry_run),
            )
        )

    return steps


def install(args: argparse.Namespace) -> int:
    """Run the full installation. Returns an exit code.

    ARC-022: each install step runs inside a try/except so a single failure
    no longer aborts the entire run silently while ``install()`` returns 0.
    Failures are accumulated into ``failed_steps`` and surfaced in a summary
    at the end; the function returns 1 if any step failed.
    """
    claude_dir: Path = Path(args.claude_dir).expanduser().resolve()
    settings_file: Path = claude_dir / "settings.json"
    dry_run: bool = args.dry_run
    verbose: bool = args.verbose

    # ARC-017 / QA-002: install() drives an ordered :class:`StepList`
    # (installer/steps.py) built by _build_install_steps() below. Each step
    # runs inside its own try/except inside ``StepList.run_all``; failures are
    # accumulated into ``steps.failed_steps`` and surfaced in a summary before
    # install() returns, preserving the ARC-022 contract that a broken install
    # is diagnosed from the exit-code-1 message rather than from a missing
    # settings.json that the prior unconditional ``return 0`` hid. The same
    # Step abstraction backs uninstall()'s reverse-order undo() so the two
    # flows cannot drift.

    print()
    print(bold("Parsidion Installer"))
    print(dim("Skills, hooks, and knowledge vault for coding agents"))
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
        default_vault = _default_vault_path()
        if args.yes:
            vault_root = default_vault
        else:
            vault_root = prompt_vault_path(default_vault)

    runtime = resolve_runtime_choice(
        args.runtime, yes=args.yes, interactive=not args.yes
    )
    codex_home: Path = Path(args.codex_home).expanduser().resolve()
    gemini_home: Path = Path(args.gemini_home).expanduser().resolve()
    install_claude_runtime = _wants_claude_runtime(runtime)
    install_codex_runtime = _wants_codex_runtime(runtime)
    install_gemini_runtime = _wants_gemini_runtime(runtime)
    install_runtime_hooks = runtime != "none" and not args.skip_hooks

    # --- Interactive option prompts (CLI tools, AI mode, embeddings, scheduler,
    # vault username). Extracted to _collect_install_options for testability.
    install_tools, enable_ai, enable_embeddings, do_schedule, vault_username = (
        _collect_install_options(
            args,
            install_claude_runtime=install_claude_runtime,
            vault_root=vault_root,
        )
    )

    _print_install_plan(
        runtime=runtime,
        install_claude_runtime=install_claude_runtime,
        install_codex_runtime=install_codex_runtime,
        install_gemini_runtime=install_gemini_runtime,
        install_runtime_hooks=install_runtime_hooks,
        claude_dir=claude_dir,
        codex_home=codex_home,
        gemini_home=gemini_home,
        settings_file=settings_file,
        vault_root=vault_root,
        vault_username=vault_username,
        install_tools=install_tools,
        do_schedule=do_schedule,
        enable_ai=enable_ai,
        enable_embeddings=enable_embeddings,
        summarizer_hour=args.summarizer_hour,
        rebuild_graph=args.rebuild_graph,
        skip_agent=args.skip_agent,
        skip_hooks=args.skip_hooks,
        dry_run=dry_run,
    )

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

    # ARC-017: build the ordered step list from the resolved runtime/flag
    # matrix once, then drive it with a single run_all(). The matrix predicate
    # is evaluated per-step at build time rather than re-checked 15-20× as
    # bare if-blocks inside this function.
    steps = _build_install_steps(
        args=args,
        claude_dir=claude_dir,
        vault_root=vault_root,
        codex_home=codex_home,
        gemini_home=gemini_home,
        settings_file=settings_file,
        dry_run=dry_run,
        verbose=verbose,
        install_claude_runtime=install_claude_runtime,
        install_codex_runtime=install_codex_runtime,
        install_gemini_runtime=install_gemini_runtime,
        enable_ai=enable_ai,
        enable_embeddings=enable_embeddings,
        vault_username=vault_username,
        install_tools=install_tools,
        do_schedule=do_schedule,
    )

    # ARC-017: transaction snapshot of settings.json — taken before any step
    # runs, restored below if any settings-mutating step fails. Composes with
    # hooks.merge_hooks' own per-RMW .bak (ARC-018/SEC-105): that one guards a
    # single write so a botched merge is recoverable from disk; this one
    # guards the whole transaction so a later step's failure (e.g.
    # merge_codex_hooks raises after merge_hooks succeeded) doesn't leave the
    # Claude runtime half-registered in settings.json. No snapshot under
    # dry_run (no step writes) or when settings.json doesn't yet exist.
    settings_snapshot_bytes: bytes | None = None
    if not dry_run and settings_file.exists():
        try:
            settings_snapshot_bytes = settings_file.read_bytes()
        except OSError:
            settings_snapshot_bytes = None

    steps.run_all()

    # Restore settings.json if any settings-mutating step failed. The names
    # match the steps that call into hooks.* (the only steps that RMW
    # settings.json or codex/gemini hook config).
    _SETTINGS_MUTATING_STEPS = frozenset(
        {
            "cleanup_legacy_assets",
            "merge_hooks",
            "enable_codex_hooks_config",
            "merge_codex_hooks",
            "merge_gemini_hooks",
        }
    )
    if settings_snapshot_bytes is not None and any(
        name in _SETTINGS_MUTATING_STEPS for name, _ in steps.failed_steps
    ):
        try:
            mode = os.stat(settings_file).st_mode & 0o777
            settings_file.write_bytes(settings_snapshot_bytes)
            try:
                os.chmod(settings_file, mode)
            except OSError:
                pass
            _warn(
                f"Restored {settings_file} to its pre-install state after a "
                f"hook step failed."
            )
        except OSError as exc:
            _warn(f"Could not restore {settings_file} after hook failure: {exc}")

    # ARC-022: surface failed steps and return non-zero so make/CI can detect
    # a broken install. The prior unconditional ``return 0`` masked every
    # failure that wasn't a missing SKILL_SRC.
    if steps.failed_steps:
        print()
        _err(f"{len(steps.failed_steps)} step(s) failed:")
        for name, exc in steps.failed_steps:
            print(f"    {red('-')} {name}: {exc}")
        return 1

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
            f"  3. Run: {cyan('uv run ~/.claude/skills/parsidion/scripts/update_index.py')}"
        )
        print("         to rebuild the vault index at any time")
        print(
            f"  4. Run: {cyan('uv run ~/.claude/skills/parsidion/scripts/build_embeddings.py')}"
        )
        print("         to build the semantic search index (~30s on first run)")
        if not install_tools:
            print(
                f"  5. Run: {cyan(f'cd {REPO_ROOT} && uv tool install --editable ".[tools]"')}"
            )
            print(
                "         to add vault-search, vault-new, and vault-stats as global CLI commands"
            )
            print(
                f"         (or re-run with {cyan('--install-tools')} to do this automatically)"
            )

    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    """Parse and return command-line arguments for the installer."""
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="Install Parsidion skills, hooks, and vault tooling.",
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
        "--runtime",
        choices=_RUNTIME_CHOICES,
        default=None,
        help=(
            "Runtime integration target: claude, codex, gemini, both, all, or none. "
            "Interactive default is both; --yes default is claude for backwards compatibility."
        ),
    )
    parser.add_argument(
        "--codex-home",
        metavar="PATH",
        default=os.environ.get("CODEX_HOME", "~/.codex"),
        help="Codex home directory for hooks/config (default: $CODEX_HOME or ~/.codex)",
    )
    parser.add_argument(
        "--gemini-home",
        default="~/.gemini",
        help="Gemini CLI home directory for hook settings (default: ~/.gemini)",
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
        help=(
            "Skip all confirmation prompts. Uses ~/ParsidionVault as the vault "
            "path unless legacy ~/ClaudeVault exists or --vault PATH is supplied. "
            "Combine with --vault for fully non-interactive installs to a "
            "custom path: uv run install.py --yes --vault /path/to/vault"
        ),
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
        "--migrate-vault",
        action="store_true",
        help="Rename legacy ~/ClaudeVault to ~/ParsidionVault and leave a compatibility symlink",
    )
    parser.add_argument(
        "--no-legacy-vault-symlink",
        action="store_true",
        help="Do not create ~/ClaudeVault -> ~/ParsidionVault when using --migrate-vault",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove installed skill, agents, hooks, and related assets",
    )
    parser.add_argument(
        "--uninstall-hooks",
        action="store_true",
        help="Remove only installed hook registrations from settings.json",
    )
    parser.add_argument(
        "--purge-config",
        action="store_true",
        help=(
            "During uninstall, also remove ~/.config/parsidion/vaults.yaml. "
            "Has no effect unless --uninstall (or 'disconnect <agent>') is also "
            "removing the Claude integration. Required to delete vaults.yaml even "
            "under --yes; without it, vaults.yaml is always preserved. "
            "(ARC-003: previously 'disconnect codex --yes' silently deleted it.)"
        ),
    )
    parser.add_argument(
        "--enable-ai",
        action="store_true",
        help=(
            "Enable AI-powered note selection: writes ai_model to vault config.yaml "
            "and sets the SessionStart hook timeout to 30s so claude-haiku can "
            "intelligently select relevant vault notes. "
            "The interactive installer prompts for this; use this flag to enable "
            "it non-interactively (e.g. with --yes)."
        ),
    )
    parser.add_argument(
        "--enable-embeddings",
        action="store_true",
        help=(
            "Enable semantic search embeddings: writes embeddings.enabled = true "
            "to vault config.yaml. When enabled, build_embeddings.py generates a "
            "vector index used by vault-search and session_start_hook. "
            "The interactive installer prompts for this; use this flag to enable "
            "it non-interactively (e.g. with --yes)."
        ),
    )
    parser.add_argument(
        "--install-tools",
        action="store_true",
        help=(
            "Also install vault-search, vault-new, and vault-stats as global CLI "
            "commands via 'uv tool install --editable .[tools]' (cross-platform; "
            "adds commands to ~/.local/bin/ or platform equivalent). "
            "The interactive installer prompts for this; use this flag to enable "
            "it non-interactively (e.g. with --yes)."
        ),
    )
    parser.add_argument(
        "--schedule-summarizer",
        action="store_true",
        help=(
            "Install a nightly cron job (Linux) or launchd plist (macOS) that runs "
            "summarize_sessions.py automatically at 3 AM. "
            "Use --summarizer-hour to change the hour. "
            "On macOS this creates ~/Library/LaunchAgents/com.parsidion.summarize-sessions.plist."
        ),
    )
    parser.add_argument(
        "--summarizer-hour",
        type=int,
        default=3,
        choices=range(24),
        metavar="HOUR",
        help="Hour of day (0-23) to run the scheduled summarizer (default: 3 = 3 AM)",
    )
    parser.add_argument(
        "--rebuild-graph",
        action="store_true",
        default=True,
        help=(
            "Add --rebuild-graph to the scheduled summarizer command so the "
            "visualizer graph.json is regenerated each night after indexing. "
            "Enabled by default. Only meaningful with --schedule-summarizer."
        ),
    )
    parser.add_argument(
        "--no-rebuild-graph",
        action="store_false",
        dest="rebuild_graph",
        help="Disable graph rebuild in the scheduled summarizer.",
    )
    parser.add_argument(
        "--graph-include-daily",
        action="store_true",
        help=(
            "Also add --graph-include-daily to the scheduled command to include "
            "Daily folder notes in the graph. Only meaningful with --rebuild-graph."
        ),
    )
    parser.add_argument(
        "--vault-username",
        default="",
        metavar="NAME",
        help=(
            "Username suffix for per-user daily notes (DD-{username}.md). "
            "Written to vault config.yaml so it persists across sessions. "
            "Defaults to $USER when not set. "
            "The interactive installer prompts for this."
        ),
    )
    parser.add_argument(
        "--create-vaults-config",
        action="store_true",
        help="Create ~/.config/parsidion/vaults.yaml template",
    )
    parser.add_argument(
        "verb",
        nargs="?",
        choices=["connect", "disconnect"],
        default=None,
        help="Friendly multi-agent verb: 'connect <agent>' or 'disconnect <agent>'.",
    )
    parser.add_argument(
        "agent",
        nargs="?",
        choices=["claude", "codex", "gemini"],
        default=None,
        help="Target agent for the connect/disconnect verb.",
    )
    parser.add_argument(
        "--help",
        "-h",
        action="help",
        help="Show this help message and exit",
    )
    return parser.parse_args()


def main() -> None:
    """Entry point for the Parsidion installer."""
    args = parse_args()

    # Friendly multi-agent verbs: 'connect <agent>' / 'disconnect <agent>'.
    # Rewrite the args namespace so the existing install()/uninstall() flow
    # targets exactly one runtime, then delegate.
    if args.verb in ("connect", "disconnect"):
        if args.agent is None:
            _err(f"{args.verb} requires an agent: claude | codex | gemini")
            sys.exit(2)
        args.runtime = args.agent
        if args.verb == "disconnect":
            claude_dir = Path(args.claude_dir).expanduser().resolve()
            settings_file = claude_dir / "settings.json"
            runtime = resolve_runtime_choice(
                args.runtime, yes=args.yes, interactive=not args.yes
            )
            codex_home = Path(args.codex_home).expanduser().resolve()
            gemini_home = Path(args.gemini_home).expanduser().resolve()
            uninstall(
                claude_dir,
                settings_file,
                dry_run=args.dry_run,
                yes=args.yes,
                hooks_only=False,
                runtime=runtime,
                codex_home=codex_home,
                gemini_home=gemini_home,
                purge_config=args.purge_config,
            )
            return
        # connect == targeted install for one runtime
        install(args)
        return

    claude_dir = Path(args.claude_dir).expanduser().resolve()
    settings_file = claude_dir / "settings.json"

    if args.uninstall and args.uninstall_hooks:
        _err("Choose only one uninstall mode: --uninstall or --uninstall-hooks")
        sys.exit(2)

    if args.migrate_vault:
        if args.uninstall or args.uninstall_hooks:
            _err(
                "Choose only one mode: --migrate-vault, --uninstall, or --uninstall-hooks"
            )
            sys.exit(2)
        if args.vault:
            _err(
                "--migrate-vault migrates the default legacy vault; do not combine it with --vault"
            )
            sys.exit(2)
        if not args.yes and not args.dry_run:
            print()
            print(bold("Parsidion Vault Migration"))
            print("This will move ~/ClaudeVault to ~/ParsidionVault.")
            if not args.no_legacy_vault_symlink:
                print("It will also leave ~/ClaudeVault as a compatibility symlink.")
            if not _confirm("Proceed with vault migration?", default=False):
                print(dim("Aborted."))
                sys.exit(0)
        sys.exit(
            migrate_default_vault(
                dry_run=args.dry_run,
                create_legacy_symlink=not args.no_legacy_vault_symlink,
            )
        )

    if args.uninstall or args.uninstall_hooks:
        runtime = resolve_runtime_choice(
            args.runtime,
            yes=args.yes,
            interactive=not args.yes,
        )
        codex_home = Path(args.codex_home).expanduser().resolve()
        gemini_home = Path(args.gemini_home).expanduser().resolve()
        if not args.yes and not args.dry_run:
            print()
            print(
                bold(
                    "Parsidion Hook Uninstaller"
                    if args.uninstall_hooks
                    else "Parsidion Uninstaller"
                )
            )
            print(f"  {dim('Runtime   :')} {runtime}")
            print(f"  {dim('Claude dir:')} {claude_dir}")
            if _wants_codex_runtime(runtime):
                print(f"  {dim('Codex home:')} {codex_home}")
            if _wants_gemini_runtime(runtime):
                print(f"  {dim('Gemini home:')} {gemini_home}")
            prompt = (
                "Proceed with hook uninstall?"
                if args.uninstall_hooks
                else "Proceed with uninstall?"
            )
            if not _confirm(prompt, default=False):
                print(dim("Aborted."))
                sys.exit(0)
        uninstall(
            claude_dir,
            settings_file,
            dry_run=args.dry_run,
            yes=args.yes,
            hooks_only=args.uninstall_hooks,
            runtime=runtime,
            codex_home=codex_home,
            gemini_home=gemini_home,
            purge_config=args.purge_config,
        )
        sys.exit(0)

    sys.exit(install(args))


if __name__ == "__main__":
    main()
