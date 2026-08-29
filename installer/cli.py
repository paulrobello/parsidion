"""CLI driver for the Parsidion installer (ARC-008).

Holds the argument parser, the ``install()`` flow driver, and the friendly
``connect``/``disconnect`` verb handling previously inlined in the
1,342-line ``install.py`` entrypoint. ``install.py`` itself is now a thin
shim over :func:`main`.

The interface is flag-first with two optional positional verbs
(``install.py connect codex``); every historical flag spelling is preserved
verbatim.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from installer.colors import bold, cyan, dim
from installer.paths import (
    REPO_ROOT,
    SKILL_SRC,
    _RUNTIME_CHOICES,
    _default_vault_path,
    _wants_claude_runtime,
    _wants_codex_runtime,
    _wants_antigravity_runtime,
    validate_vault_path,
)
from installer.plan import InstallPlan, build_install_steps, collect_install_options
from installer.plan import print_install_plan as _print_install_plan
from installer.ui import (
    _confirm,
    _err,
    _ok,
    _step,
    _warn,
    prompt_vault_path,
    resolve_runtime_choice,
)
from installer.uninstall import uninstall
from installer.vault import migrate_default_vault


# ---------------------------------------------------------------------------
# Install flow
# ---------------------------------------------------------------------------


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
    antigravity_home: Path = Path(args.antigravity_home).expanduser().resolve()
    install_claude_runtime = _wants_claude_runtime(runtime)
    install_codex_runtime = _wants_codex_runtime(runtime)
    install_antigravity_runtime = _wants_antigravity_runtime(runtime)
    install_runtime_hooks = runtime != "none" and not args.skip_hooks

    # --- Interactive option prompts (CLI tools, AI mode, embeddings,
    # scheduler, vault username).
    install_tools, enable_ai, enable_embeddings, do_schedule, vault_username = (
        collect_install_options(
            args,
            install_claude_runtime=install_claude_runtime,
            vault_root=vault_root,
        )
    )

    _print_install_plan(
        InstallPlan(
            runtime=runtime,
            install_claude_runtime=install_claude_runtime,
            install_codex_runtime=install_codex_runtime,
            install_antigravity_runtime=install_antigravity_runtime,
            install_runtime_hooks=install_runtime_hooks,
            claude_dir=claude_dir,
            codex_home=codex_home,
            antigravity_home=antigravity_home,
            settings_file=settings_file,
            vault_root=vault_root,
            vault_username=vault_username,
            install_tools=install_tools,
            do_schedule=do_schedule,
            enable_ai=enable_ai,
            enable_embeddings=enable_embeddings,
            summarizer_hour=args.summarizer_hour,
            rebuild_graph=args.rebuild_graph,
            graph_include_daily=args.graph_include_daily,
            skip_agent=args.skip_agent,
            skip_hooks=args.skip_hooks,
            dry_run=dry_run,
            verbose=verbose,
            force=args.force,
            yes=args.yes,
            create_vaults_config=args.create_vaults_config,
            args=args,
        )
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

    plan = InstallPlan(
        runtime=runtime,
        install_claude_runtime=install_claude_runtime,
        install_codex_runtime=install_codex_runtime,
        install_antigravity_runtime=install_antigravity_runtime,
        install_runtime_hooks=install_runtime_hooks,
        claude_dir=claude_dir,
        codex_home=codex_home,
        antigravity_home=antigravity_home,
        settings_file=settings_file,
        vault_root=vault_root,
        vault_username=vault_username,
        install_tools=install_tools,
        do_schedule=do_schedule,
        enable_ai=enable_ai,
        enable_embeddings=enable_embeddings,
        summarizer_hour=args.summarizer_hour,
        rebuild_graph=args.rebuild_graph,
        graph_include_daily=args.graph_include_daily,
        skip_agent=args.skip_agent,
        skip_hooks=args.skip_hooks,
        dry_run=dry_run,
        verbose=verbose,
        force=args.force,
        yes=args.yes,
        create_vaults_config=args.create_vaults_config,
        args=args,
    )
    steps = build_install_steps(plan)

    # Transaction snapshot of settings.json — taken before any step runs,
    # restored below if any settings-mutating step fails. Composes with
    # hooks.merge_hooks' own per-RMW .bak: that one guards a single write so
    # a botched merge is recoverable from disk; this one guards the whole
    # transaction so a later step's failure doesn't leave the Claude runtime
    # half-registered in settings.json. No snapshot under dry_run (no step
    # writes) or when settings.json doesn't yet exist.
    settings_snapshot_bytes: bytes | None = None
    if not dry_run and settings_file.exists():
        try:
            settings_snapshot_bytes = settings_file.read_bytes()
        except OSError:
            settings_snapshot_bytes = None

    steps.run_all()

    # Restore settings.json if any settings-mutating step failed. The names
    # match the steps that call into hooks.* (the only steps that RMW
    # settings.json or codex/antigravity hook config).
    _SETTINGS_MUTATING_STEPS = frozenset(
        {
            "cleanup_legacy_assets",
            "merge_hooks",
            "enable_codex_hooks_config",
            "merge_codex_hooks",
            "merge_antigravity_hooks",
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
    # a broken install.
    if steps.failed_steps:
        from installer.colors import red

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
            tools_arg = '".[tools]"'
            print(
                f"  5. Run: {cyan(f'cd {REPO_ROOT} && uv tool install --editable {tools_arg}')}"
            )
            print(
                "         to add vault-search, vault-new, and vault-stats as global CLI commands"
            )
            print(
                f"         (or re-run with {cyan('--install-tools')} to do this automatically)"
            )

    return 0


# ---------------------------------------------------------------------------
# connect / disconnect verbs
# ---------------------------------------------------------------------------


def _connectable_runtimes() -> list[str]:
    """Runtimes accepted by ``connect``/``disconnect``.

    Data-driven from the agent_adapter registry (ENH-006): every registered
    runtime is accepted. claude/codex/antigravity wire hooks via install()/uninstall();
    pi installs its TypeScript extension via the dedicated installer.
    """
    import agent_adapter  # noqa: PLC0415

    return [a.name for a in agent_adapter.all_adapters()]


def _connect_extension_runtime(
    args: argparse.Namespace, ext_dir: Path, label: str
) -> None:
    """Install the pi-family TypeScript extension via the repo's installer."""
    import subprocess  # noqa: PLC0415

    script = REPO_ROOT / "scripts" / "install-pi-extension"
    if not script.exists():
        _err(f"{label} extension installer not found: {script}")
        sys.exit(1)
    cmd = ["bash", str(script), "--extension-dir", str(ext_dir), "--agent-name", label]
    if args.dry_run:
        _step(f"Would run: {' '.join(cmd)}")
        return
    try:
        completed = subprocess.run(cmd, check=False)
    except OSError as exc:
        _err(f"Could not run {label} extension installer: {exc}")
        sys.exit(1)
    if completed.returncode != 0:
        sys.exit(completed.returncode)


def _disconnect_extension_runtime(
    args: argparse.Namespace, ext_dir: Path, label: str
) -> None:
    """Remove the parsidion extension files from a pi-family *ext_dir*."""
    removed: list[str] = []
    for name in (
        "parsidion.ts",
        "parsidion.md",
        "lib/parsidion-status.ts",
        "lib/scriptRunner.ts",
        "lib/transcript.ts",
        "lib/promptRecall.ts",
    ):
        candidate = ext_dir / name
        if candidate.is_symlink() or candidate.exists():
            removed.append(str(candidate))
            if not args.dry_run:
                try:
                    candidate.unlink()
                except OSError:
                    pass
    if removed:
        _ok(f"Removed {label} extension: {', '.join(removed)}")
    else:
        _warn(f"No {label} extension found to remove.")


def _connect_pi(args: argparse.Namespace) -> None:
    """Install the pi extension into ``~/.pi/agent/extensions``."""
    _connect_extension_runtime(args, Path.home() / ".pi" / "agent" / "extensions", "pi")


def _disconnect_pi(args: argparse.Namespace) -> None:
    """Remove the pi extension from ``~/.pi/agent/extensions``."""
    _disconnect_extension_runtime(
        args, Path.home() / ".pi" / "agent" / "extensions", "pi"
    )


def _omp_extensions_dir(args: argparse.Namespace) -> Path:
    """omp extensions dir: ``<omp-home>/agent/extensions``."""
    return Path(args.omp_home).expanduser() / "agent" / "extensions"


def _connect_omp(args: argparse.Namespace) -> None:
    """Install the omp extension into ``~/.omp/agent/extensions``."""
    _connect_extension_runtime(args, _omp_extensions_dir(args), "omp")


def _disconnect_omp(args: argparse.Namespace) -> None:
    """Remove the omp extension from ``~/.omp/agent/extensions``."""
    _disconnect_extension_runtime(args, _omp_extensions_dir(args), "omp")


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
            "Runtime integration target: claude, codex, antigravity, both, all, or none. "
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
        "--antigravity-home",
        default="~/.gemini",
        help="Antigravity CLI home directory for hook config (default: ~/.gemini)",
    )
    parser.add_argument(
        "--omp-home",
        metavar="PATH",
        default=os.environ.get("PI_CONFIG_DIR", "~/.omp"),
        help=(
            "omp config home for 'connect omp' extension install "
            "(default: $PI_CONFIG_DIR or ~/.omp); the extension is installed "
            "into <omp-home>/agent/extensions"
        ),
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
            "under --yes; without it, vaults.yaml is always preserved."
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
        choices=_connectable_runtimes(),
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
    if args.verb in ("connect", "disconnect"):
        if args.agent is None:
            _err(
                f"{args.verb} requires an agent: {' | '.join(_connectable_runtimes())}"
            )
            sys.exit(2)
        args.runtime = args.agent
        if args.verb == "disconnect":
            if args.agent == "pi":
                _disconnect_pi(args)
                return
            if args.agent == "omp":
                _disconnect_omp(args)
                return
            claude_dir = Path(args.claude_dir).expanduser().resolve()
            settings_file = claude_dir / "settings.json"
            runtime = resolve_runtime_choice(
                args.runtime, yes=args.yes, interactive=not args.yes
            )
            codex_home = Path(args.codex_home).expanduser().resolve()
            antigravity_home = Path(args.antigravity_home).expanduser().resolve()
            uninstall(
                claude_dir,
                settings_file,
                dry_run=args.dry_run,
                yes=args.yes,
                hooks_only=False,
                runtime=runtime,
                codex_home=codex_home,
                antigravity_home=antigravity_home,
                purge_config=args.purge_config,
            )
            return
        # connect == targeted install for one runtime
        if args.agent == "pi":
            _connect_pi(args)
            return
        if args.agent == "omp":
            _connect_omp(args)
            return
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
        antigravity_home = Path(args.antigravity_home).expanduser().resolve()
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
            if _wants_antigravity_runtime(runtime):
                print(f"  {dim('Antigravity home:')} {antigravity_home}")
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
            antigravity_home=antigravity_home,
            purge_config=args.purge_config,
        )
        sys.exit(0)

    sys.exit(install(args))
