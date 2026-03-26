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
    --yes, -y           Skip all confirmation prompts; uses ~/ClaudeVault as the
                        vault path unless --vault PATH is also supplied
    --skip-hooks        Do not modify settings.json
    --skip-agent        Do not install any agents
    --uninstall         Remove installed skill, agent, and hooks
    --enable-ai         Enable AI-powered note selection (writes ai_model to config.yaml, sets 30s timeout)
    --install-tools     Install vault-search, vault-new, and vault-stats as global CLI commands
    --schedule-summarizer  Install nightly cron/launchd job to auto-run summarize_sessions.py
    --summarizer-hour N    Hour of day (0-23) for the scheduled summarizer (default: 3)
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
SKILL_SRC: Path = REPO_ROOT / "skills" / "parsidion-cc"
AGENT_SRCS: list[Path] = [
    REPO_ROOT / "agents" / "research-agent.md",
    REPO_ROOT / "agents" / "vault-explorer.md",
    REPO_ROOT / "agents" / "project-explorer.md",
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
    "PostCompact": "post_compact_hook.py",
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
    """Print *msg*, optionally gating on the *verbose* flag.

    Args:
        msg: The message to print.
        verbose_only: When True, suppress output unless *verbose* is also True.
        verbose: Whether verbose output is enabled (passed through from the CLI flag).
    """
    if verbose_only and not verbose:
        return
    print(msg)


def _make_vprint(verbose: bool):
    """Return a ``vprint(msg)`` closure bound to *verbose*.

    Use this inside functions that receive the ``verbose`` flag to avoid
    passing it at every ``_print`` call site::

        vprint = _make_vprint(verbose)
        vprint("debug info")          # only printed when verbose=True
        vprint("always shown", always=True)

    Args:
        verbose: The global verbosity flag.

    Returns:
        A callable ``vprint(msg, always=False)`` that prints *msg* when
        *verbose* is True, or always when *always* is True.
    """

    def vprint(msg: str, always: bool = False) -> None:
        if always or verbose:
            print(msg)

    return vprint


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

    # SEC-009: Use Path.is_relative_to() instead of str.startswith() to prevent
    # false positives where a forbidden prefix string matches a different path
    # (e.g. "/usr" matching "/usrdata", or "/bin" matching "/binary").
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

    vault_repr = f'Path(r"{vault_root}")'
    templates_repr = f'Path(r"{templates_dir}")'

    new_content = _VAULT_ROOT_RE.sub(lambda m: m.group(1) + vault_repr, content)
    new_content = _TEMPLATES_DIR_RE.sub(
        lambda m: m.group(1) + templates_repr, new_content
    )

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


def _can_symlink(target: Path) -> bool:
    """Return True if the OS supports directory symlinks at *target*'s location.

    On non-Windows platforms symlinks always work. On Windows, Developer Mode or
    Administrator privileges are required; probe with a throwaway symlink since
    privilege checks are unreliable across Windows editions.
    """
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
    """Install skill to ~/.claude/skills/parsidion-cc/.

    On Unix/macOS: creates a directory symlink so edits to the repo are
    immediately live without reinstalling.

    On Windows (or when symlinks are unavailable): falls back to copytree,
    matching the original behaviour. vault_common.py is patched in both cases
    when the vault is in a non-default location.

    Returns the installed skill path.
    """
    dest = claude_dir / "skills" / "parsidion-cc"
    use_symlink = sys.platform != "win32" or _can_symlink(dest)

    # ── Fast-path: symlink already correct ────────────────────────────────────
    if use_symlink and dest.is_symlink() and dest.resolve() == SKILL_SRC.resolve():
        if not force:
            _print(
                dim(f"  Skill symlink already correct: {dest} → {SKILL_SRC}"),
                verbose_only=True,
                verbose=verbose,
            )
            _maybe_patch_vault_common(dest, vault_root, dry_run=dry_run, verbose=verbose)
            return dest

    if (dest.exists() or dest.is_symlink()) and not force and not dry_run:
        _warn(f"Skill already exists at {dest}")
        action = "Replace with symlink to repo?" if use_symlink else "Overwrite existing skill files?"
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
        # Remove whatever is currently there
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        elif dest.exists():
            shutil.rmtree(dest)

    if use_symlink:
        _step(f"Install skill (symlink): {dest} → {SKILL_SRC}", dry_run=dry_run)
        if not dry_run:
            dest.symlink_to(SKILL_SRC)
            # Make scripts executable on the source
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

    _maybe_patch_vault_common(dest, vault_root, dry_run=dry_run, verbose=verbose)

    return dest


def _maybe_patch_vault_common(
    installed_path: Path,
    vault_root: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Patch vault_common.py only when vault_root is non-default.

    Skips patching when vault_root matches the runtime default (Path.home() /
    "ClaudeVault") to avoid dirtying the source repo with a trivial no-op.
    When the vault IS in a non-default location the source is patched in-place
    (acceptable for single-user installs; the patched file can be committed).
    """
    default_vault = Path.home() / "ClaudeVault"
    default_templates = Path.home() / ".claude" / "skills" / "parsidion-cc" / "templates"
    if vault_root.resolve() == default_vault.resolve():
        _print(
            dim("  vault_common.py: default vault path — skipping patch"),
            verbose_only=True,
            verbose=verbose,
        )
        return
    templates_dir = installed_path / "templates"
    patch_vault_common(
        installed_path, vault_root, templates_dir, dry_run=dry_run, verbose=verbose
    )


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
    """Install vault-search, vault-new, and vault-stats as global CLI commands via uv tool.

    Runs ``uv tool install --editable ".[tools]"`` from *repo_root* so that
    ``vault-search``, ``vault-new``, and ``vault-stats`` appear in the user's
    PATH (``~/.local/bin/`` on Linux/macOS, ``%APPDATA%\\Python\\Scripts`` on
    Windows) without copying or moving any script files.

    The install is editable so updates to the source scripts take effect
    immediately without re-running this step.
    """
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
# Cron / launchd scheduler (#19)
# ---------------------------------------------------------------------------

_LAUNCHD_PLIST_LABEL = "com.parsidion.summarize-sessions"
_LAUNCHD_PLIST_NAME = f"{_LAUNCHD_PLIST_LABEL}.plist"
_CRON_MARKER = "# parsidion-cc: nightly summarizer"


def _build_launchd_plist(
    uv_path: str,
    scripts_dir: Path,
    hour: int = 3,
    rebuild_graph: bool = False,
    graph_include_daily: bool = False,
) -> str:
    """Generate a macOS launchd plist XML for nightly summarization.

    Args:
        uv_path: Absolute path to the ``uv`` executable.
        scripts_dir: Directory containing ``summarize_sessions.py``.
        hour: Hour of the day (0-23) to run the job. Default 3 = 3 AM.
        rebuild_graph: When True, append ``--rebuild-graph`` to the command.
        graph_include_daily: When True, also append ``--graph-include-daily``.

    Returns:
        Plist XML string.
    """
    script_path = scripts_dir / "summarize_sessions.py"
    extra_args = ""
    if rebuild_graph:
        extra_args += "\n        <string>--rebuild-graph</string>"
    if graph_include_daily:
        extra_args += "\n        <string>--graph-include-daily</string>"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_LAUNCHD_PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{uv_path}</string>
        <string>run</string>
        <string>--no-project</string>
        <string>{script_path}</string>
        <string>--run-doctor</string>{extra_args}
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{hour}</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/parsidion-cc-summarizer.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/parsidion-cc-summarizer.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>{Path.home()}</string>
    </dict>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
"""


def schedule_summarizer(
    claude_dir: Path,
    dry_run: bool = False,
    hour: int = 3,
    rebuild_graph: bool = False,
    graph_include_daily: bool = False,
) -> None:
    """Install a nightly cron job or launchd plist to run the summarizer.

    On macOS: creates a launchd plist in ``~/Library/LaunchAgents/`` and
    loads it with ``launchctl load``.
    On Linux/other: adds a crontab entry at the specified hour.

    Args:
        claude_dir: The ~/.claude directory (contains installed scripts).
        dry_run: If True, print what would be done without making changes.
        hour: Hour of the day (0-23) to run. Default 3 = 3 AM.
        rebuild_graph: When True, add ``--rebuild-graph`` to the scheduled command
            so the visualizer graph.json is regenerated each night.
        graph_include_daily: When True, also add ``--graph-include-daily``
            (only meaningful when ``rebuild_graph`` is True).
    """
    scripts_dir = claude_dir / "skills" / "parsidion-cc" / "scripts"
    script_path = scripts_dir / "summarize_sessions.py"

    # Find uv
    uv_path = shutil.which("uv") or "uv"

    if sys.platform == "darwin":
        _schedule_summarizer_launchd(
            scripts_dir,
            script_path,
            uv_path,
            dry_run,
            hour,
            rebuild_graph=rebuild_graph,
            graph_include_daily=graph_include_daily,
        )
    else:
        _schedule_summarizer_cron(
            script_path,
            uv_path,
            dry_run,
            hour,
            rebuild_graph=rebuild_graph,
            graph_include_daily=graph_include_daily,
        )


def _schedule_summarizer_launchd(
    scripts_dir: Path,
    script_path: Path,
    uv_path: str,
    dry_run: bool,
    hour: int,
    rebuild_graph: bool = False,
    graph_include_daily: bool = False,
) -> None:
    """Install a launchd plist for macOS.

    Args:
        scripts_dir: Directory containing the script.
        script_path: Path to summarize_sessions.py.
        uv_path: Path to the uv executable.
        dry_run: Preview only when True.
        hour: Hour of day to run (0-23).
        rebuild_graph: When True, include ``--rebuild-graph`` in the plist.
        graph_include_daily: When True, include ``--graph-include-daily``.
    """
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    plist_path = launch_agents / _LAUNCHD_PLIST_NAME
    plist_content = _build_launchd_plist(
        uv_path,
        scripts_dir,
        hour,
        rebuild_graph=rebuild_graph,
        graph_include_daily=graph_include_daily,
    )

    _step(f"Schedule nightly summarizer via launchd ({plist_path})", dry_run=dry_run)
    if dry_run:
        print(f"    {dim('Would write:')} {plist_path}")
        print(f"    {dim('Would run:')} launchctl load {plist_path}")
        return

    launch_agents.mkdir(parents=True, exist_ok=True)
    try:
        plist_path.write_text(plist_content, encoding="utf-8")
        _ok(f"Plist written: {plist_path}")
    except OSError as exc:
        _warn(f"Could not write plist: {exc}")
        return

    # Unload existing job silently (ignore errors if not loaded)
    subprocess.run(
        ["launchctl", "unload", str(plist_path)],
        capture_output=True,
    )
    result = subprocess.run(
        ["launchctl", "load", str(plist_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        _ok(f"Launchd job loaded — summarizer will run nightly at {hour:02d}:00")
    else:
        _warn(
            f"launchctl load returned {result.returncode}. "
            f"You may need to run: launchctl load {plist_path}"
        )

    if not script_path.exists():
        _warn(
            f"Summarizer script not found at {script_path}. "
            "Run 'uv run install.py --force --yes' first."
        )


def _schedule_summarizer_cron(
    script_path: Path,
    uv_path: str,
    dry_run: bool,
    hour: int,
    rebuild_graph: bool = False,
    graph_include_daily: bool = False,
) -> None:
    """Add a crontab entry for Linux/other platforms.

    Args:
        script_path: Path to summarize_sessions.py.
        uv_path: Path to the uv executable.
        dry_run: Preview only when True.
        hour: Hour of day to run (0-23).
        rebuild_graph: When True, append ``--rebuild-graph`` to the cron command.
        graph_include_daily: When True, also append ``--graph-include-daily``.
    """
    extra = ""
    if rebuild_graph:
        extra += " --rebuild-graph"
    if graph_include_daily:
        extra += " --graph-include-daily"
    cron_line = (
        f"0 {hour} * * * {uv_path} run --no-project {script_path} --run-doctor{extra}"
        f" >> /tmp/parsidion-cc-summarizer.log 2>&1  {_CRON_MARKER}"
    )
    _step(f"Schedule nightly summarizer via cron (hour={hour})", dry_run=dry_run)
    if dry_run:
        print(f"    {dim('Would add crontab line:')}")
        print(f"    {dim(cron_line)}")
        return

    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
        )
        existing = result.stdout if result.returncode == 0 else ""
        # Remove any existing parsidion-cc summarizer entry
        lines = [ln for ln in existing.splitlines() if _CRON_MARKER not in ln]
        lines.append(cron_line)
        new_crontab = "\n".join(lines) + "\n"
        install_result = subprocess.run(
            ["crontab", "-"],
            input=new_crontab,
            capture_output=True,
            text=True,
        )
        if install_result.returncode == 0:
            _ok(f"Cron job installed — summarizer will run nightly at {hour:02d}:00")
        else:
            _warn(f"crontab install failed: {install_result.stderr.strip()}")
    except FileNotFoundError:
        _warn("crontab not found — cannot schedule summarizer automatically.")
        print(f"  {dim('Add this line manually:')}")
        print(f"  {dim(cron_line)}")


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
            try:
                link.symlink_to(templates_src)
            except OSError:
                shutil.copytree(templates_src, link, dirs_exist_ok=True)
    elif link.exists():
        # It's a real directory — only replace if empty
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
# Hook registration in settings.json
# ---------------------------------------------------------------------------


def _hook_command(claude_dir: Path, event: str) -> str:
    """Return the hook command string for a given event.

    Uses ~ notation so the path is portable across user accounts.
    Shell scripts (.sh) are invoked directly; Python scripts are run via
    ``uv run --no-project`` to ensure the correct Python interpreter.
    """
    script = _HOOK_SCRIPTS[event]
    script_path = claude_dir / "skills" / "parsidion-cc" / "scripts" / script
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


def enable_ai_mode(
    settings_file: Path,
    vault_root: Path,
    claude_dir: Path,
    dry_run: bool = False,
) -> None:
    """Write ai_model to vault config.yaml and set SessionStart timeout to 30s.

    Called when the user opts into AI-powered note selection during install.
    Updates the vault's config.yaml so ``session_start_hook.py`` uses
    ``claude-haiku`` for intelligent note selection, and bumps the SessionStart
    hook timeout to 30 000 ms so the AI call has time to complete.

    Args:
        settings_file: Path to ``~/.claude/settings.json``.
        vault_root: Path to the vault root directory.
        claude_dir: Path to the ``~/.claude/`` directory (used to locate the hook command).
        dry_run: When True, print actions without writing files.
    """
    # 1. Update vault config.yaml
    config_path = vault_root / "config.yaml"
    ai_model = "claude-haiku-4-5-20251001"

    if config_path.exists():
        try:
            content = config_path.read_text(encoding="utf-8")
        except OSError:
            content = ""
    else:
        content = ""

    # Replace existing ai_model: null or ai_model: <value> in session_start_hook section
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

    # 2. Bump SessionStart hook timeout to 30 000 ms in settings.json
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

    for event, _script_name in _HOOK_SCRIPTS.items():
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
    script = claude_dir / "skills" / "parsidion-cc" / "scripts" / "update_index.py"
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
    yes: bool = False,
) -> None:
    """Remove installed skill, agent, and hook registrations."""
    print(bold("\nUninstalling Parsidion CC..."))

    skill_dir = claude_dir / "skills" / "parsidion-cc"

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

        for event, _script_name in _HOOK_SCRIPTS.items():
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

    # Remove vault post-merge hook
    vault_root = _resolve_vault_root_for_uninstall()
    remove_vault_post_merge_hook(vault_root, dry_run=dry_run)

    # Remove nightly summarizer scheduler
    unschedule_summarizer(dry_run=dry_run)

    # Optionally remove vaults.yaml (named vaults config)
    vaults_config = Path.home() / ".config" / "parsidion-cc" / "vaults.yaml"
    if vaults_config.exists():
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
        _ok("Uninstall complete. Your vault at ~/ClaudeVault/ was not removed.")


def unschedule_summarizer(dry_run: bool = False) -> None:
    """Remove the nightly summarizer cron job or launchd plist if present.

    On macOS: unloads and deletes the launchd plist from ``~/Library/LaunchAgents/``.
    On Linux/other: removes the parsidion-cc line from the user's crontab.
    Silent no-op when no scheduler entry is found.

    Args:
        dry_run: If True, print what would be done without making changes.
    """
    if sys.platform == "darwin":
        plist_path = Path.home() / "Library" / "LaunchAgents" / _LAUNCHD_PLIST_NAME
        if not plist_path.exists():
            return
        _step(f"Remove launchd plist: {plist_path}", dry_run=dry_run)
        if dry_run:
            print(f"    {dim('Would run:')} launchctl unload {plist_path}")
            print(f"    {dim('Would delete:')} {plist_path}")
            return
        subprocess.run(
            ["launchctl", "unload", str(plist_path)],
            capture_output=True,
        )
        try:
            plist_path.unlink()
            _ok("Launchd plist removed")
        except OSError as exc:
            _warn(f"Could not remove plist: {exc}")
    else:
        # Linux/other: remove the crontab line
        try:
            result = subprocess.run(
                ["crontab", "-l"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return  # No crontab
            existing = result.stdout
            if _CRON_MARKER not in existing:
                return  # No parsidion-cc entry
            _step("Remove parsidion-cc line from crontab", dry_run=dry_run)
            if dry_run:
                return
            lines = [ln for ln in existing.splitlines() if _CRON_MARKER not in ln]
            new_crontab = "\n".join(lines) + "\n"
            install_result = subprocess.run(
                ["crontab", "-"],
                input=new_crontab,
                capture_output=True,
                text=True,
            )
            if install_result.returncode == 0:
                _ok("Cron job removed")
            else:
                _warn(f"crontab update failed: {install_result.stderr.strip()}")
        except FileNotFoundError:
            pass  # crontab not available, nothing to remove


def configure_vault_username(
    vault_root: Path,
    dry_run: bool = False,
    username: str = "",
) -> None:
    """Write the vault username into ``config.yaml`` if not already set.

    Sets ``vault.username`` so that daily notes are written as
    ``DD-{username}.md``, preventing git merge conflicts when a team shares a
    vault.  The username is resolved in priority order:

    1. *username* argument (e.g. from interactive prompt)
    2. ``$USER`` / ``$USERNAME`` environment variable

    Does nothing if the key already has a non-empty value.

    Args:
        vault_root: Path to the vault root directory.
        dry_run: If True, print actions without writing.
        username: Explicit username to use; falls back to ``$USER`` if empty.
    """
    import os

    if not username:
        username = os.environ.get("USER", os.environ.get("USERNAME", "")).strip()
    if not username:
        return  # Cannot determine username — leave blank for user to fill in

    config_path = vault_root / "config.yaml"

    if config_path.exists():
        try:
            content = config_path.read_text(encoding="utf-8")
        except OSError:
            content = ""
    else:
        content = ""

    # Check if vault.username is already set to a non-empty value
    # Pattern: inside a "vault:" section, "username: <value>"
    # We treat any non-empty, non-blank value as already configured.
    username_set = re.search(r"(?m)^\s+username\s*:\s*(?!\"?\"\s*$)(\S+)", content)
    if username_set:
        return  # Already configured — respect the user's value

    _step(f"Set vault.username = {username!r} in {config_path}", dry_run=dry_run)
    if dry_run:
        return

    # If vault: section exists with a blank username key, fill it in
    if re.search(r"(?m)^\s+username\s*:\s*\"?\"\s*$", content):
        new_content = re.sub(
            r"(?m)^(\s+username\s*:)\s*\"?\"\s*$",
            rf'\1 "{username}"',
            content,
        )
    elif "vault:" in content:
        # vault section exists but no username key — append it
        new_content = re.sub(
            r"(?m)^(vault:)",
            rf"\1\n  username: \"{username}\"",
            content,
            count=1,
        )
    else:
        # No vault section — append one at the end
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


def create_vaults_config(dry_run: bool = False) -> None:
    """Create vaults.yaml template with example configuration.

    Creates ``~/.config/parsidion-cc/vaults.yaml`` with commented examples for
    named vault configuration. This enables users to reference vaults by name
    via ``--vault NAME`` or ``CLAUDE_VAULT=NAME``.

    Args:
        dry_run: If True, print what would be done without writing.
    """
    config_dir = Path.home() / ".config" / "parsidion-cc"
    config_path = config_dir / "vaults.yaml"

    if config_path.exists():
        print(f"  ℹ {config_path} already exists, skipping")
        return

    content = """# Named vaults for parsidion-cc
# Use with: vault-search --vault NAME or CLAUDE_VAULT=NAME

vaults:
  # personal: ~/ClaudeVault
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


def configure_vault_gitignore(vault_root: Path, dry_run: bool = False) -> None:
    """Ensure machine-local files are listed in the vault ``.gitignore``.

    Adds entries for ``embeddings.db`` (binary SQLite — must be rebuilt
    locally), ``pending_summaries.jsonl`` (machine-local session queue),
    and ``hook_events.log`` (machine-local structured log).

    Args:
        vault_root: Path to the vault root directory.
        dry_run: If True, print actions without writing.
    """
    gitignore = vault_root / ".gitignore"
    entries = ["embeddings.db", "pending_summaries.jsonl", "hook_events.log", "graph.json"]

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

    Runs ``git init``, adds all files, and creates an initial commit.
    Silent no-op when ``.git`` already exists.

    Args:
        vault_root: Path to the vault root directory.
        dry_run: If True, print what would be done without writing.
    """
    git_dir = vault_root / ".git"
    if git_dir.exists():
        return  # Already a git repo.

    _step("Initialize vault as a git repository", dry_run=dry_run)
    if dry_run:
        return

    subprocess.run(
        ["git", "init"],
        cwd=vault_root,
        capture_output=True,
    )
    subprocess.run(
        ["git", "add", "-A"],
        cwd=vault_root,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "chore(vault): initial commit"],
        cwd=vault_root,
        capture_output=True,
    )
    _ok(f"Git repo initialized at {vault_root}")


# Marker comment used to identify our post-merge hook.
_POST_MERGE_MARKER = "# parsidion-cc post-merge hook"

_POST_MERGE_HOOK_TEMPLATE = """\
#!/bin/bash
{marker} — rebuilds vault index and embeddings after pull
set -e
echo "[parsidion-cc] Rebuilding vault index..."
uv run --no-project {scripts_dir}/update_index.py
echo "[parsidion-cc] Updating embeddings (incremental)..."
uv run {scripts_dir}/build_embeddings.py --incremental
echo "[parsidion-cc] Post-merge sync complete."
"""


def install_vault_post_merge_hook(
    vault_root: Path,
    claude_dir: Path,
    dry_run: bool = False,
) -> None:
    """Install a git post-merge hook in the vault for multi-machine sync.

    The hook rebuilds ``note_index`` and refreshes embeddings after every
    ``git pull`` / ``git merge`` so that the local SQLite database stays
    in sync with notes pulled from a remote.

    Skips silently when the vault is not a git repository.  Never overwrites
    a pre-existing hook that was not created by this installer.

    Args:
        vault_root: Path to the vault root directory.
        claude_dir: Path to the Claude configuration directory.
        dry_run: If True, print what would be done without writing.
    """
    git_dir = vault_root / ".git"
    if not git_dir.is_dir():
        return  # Not a git repo — nothing to do.

    hooks_dir = git_dir / "hooks"
    hook_path = hooks_dir / "post-merge"

    # Build portable ~ path to scripts dir.
    scripts_dir = claude_dir / "skills" / "parsidion-cc" / "scripts"
    try:
        rel = scripts_dir.relative_to(Path.home())
        scripts_rel = f"~/{rel.as_posix()}"
    except ValueError:
        scripts_rel = scripts_dir.as_posix()

    if hook_path.exists():
        existing = hook_path.read_text(encoding="utf-8")
        if _POST_MERGE_MARKER in existing:
            return  # Already installed — idempotent.
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
    """Remove the parsidion-cc post-merge hook from the vault if present.

    Only deletes the hook if it was created by this installer (identified
    by the marker comment).  Leaves custom user hooks untouched.

    Args:
        vault_root: Path to the vault root directory.
        dry_run: If True, print what would be done without writing.
    """
    hook_path = vault_root / ".git" / "hooks" / "post-merge"
    if not hook_path.exists():
        return

    content = hook_path.read_text(encoding="utf-8")
    if _POST_MERGE_MARKER not in content:
        return  # Not ours — leave it.

    _step(f"Remove vault post-merge hook: {hook_path}", dry_run=dry_run)
    if not dry_run:
        hook_path.unlink()


def _resolve_vault_root_for_uninstall() -> Path:
    """Best-effort vault root resolution for uninstall (no args available).

    Checks ``~/ClaudeVault/config.yaml`` first, then falls back to the
    default ``~/ClaudeVault``.
    """
    default = Path.home() / "ClaudeVault"
    config = default / "config.yaml"
    if not config.exists():
        return default
    # Minimal stdlib YAML parse — look for a top-level vault_root key.
    try:
        for line in config.read_text(encoding="utf-8").splitlines():
            stripped = line.split("#", 1)[0].strip()
            if stripped.startswith("vault_root:"):
                val = stripped.split(":", 1)[1].strip().strip("'\"")
                if val:
                    return Path(val).expanduser().resolve()
    except OSError:
        pass
    return default


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
    if not args.yes and not enable_ai and not args.skip_hooks:
        print()
        print(bold("AI-Powered Note Selection (optional)"))
        print(
            dim(
                "  When enabled, the SessionStart hook uses claude-haiku to\n"
                "  intelligently select relevant vault notes instead of keyword\n"
                "  matching. Requires a 30s hook timeout and an Anthropic API key."
            )
        )
        enable_ai = _confirm("Enable AI-powered note selection?", default=False)

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
    import os as _os_import

    _detected_user = _os_import.environ.get(
        "USER", _os_import.environ.get("USERNAME", "")
    )
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

    print()
    print(bold("Installation Plan"))
    print(f"  {dim('Claude dir   :')} {claude_dir}")
    print(f"  {dim('Vault path   :')} {vault_root}")
    if install_tools:
        print(f"  {dim('CLI tools    :')} vault-search, vault-new, vault-stats")
    if do_schedule:
        graph_suffix = " + graph rebuild" if args.rebuild_graph else ""
        print(
            f"  {dim('Scheduler    :')} nightly summarizer at {args.summarizer_hour:02d}:00 "
            f"({'launchd' if sys.platform == 'darwin' else 'cron'}){graph_suffix}"
        )
    if enable_ai:
        print(f"  {dim('AI mode      :')} enabled (SessionStart timeout → 30s)")
    print(f"  {dim('Vault username:')} {vault_username or '(auto: $USER)'}")
    print(f"  {dim('Settings     :')} {settings_file}")
    print(f"  {dim('Install skill:')} {claude_dir / 'skills' / 'parsidion-cc'}")
    if not args.skip_agent:
        for agent_src in AGENT_SRCS:
            print(f"  {dim('Install agent:')} {claude_dir / 'agents' / agent_src.name}")
    if not args.skip_hooks:
        print(f"  {dim('Register hooks:')} {', '.join(_HOOK_SCRIPTS.keys())}")
    print(f"  {dim('Install scripts:')} {claude_dir / 'scripts'}/")
    print(
        f"  {dim('Install guidance:')} {claude_dir / 'CLAUDE-VAULT.md'} (@import into CLAUDE.md)"
    )
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
    templates_src = claude_dir / "skills" / "parsidion-cc" / "templates"
    create_templates_symlink(
        vault_root, templates_src, dry_run=dry_run, verbose=verbose
    )

    # 7. Register hooks
    if not args.skip_hooks:
        merge_hooks(claude_dir, settings_file, dry_run=dry_run, verbose=verbose)

    # 7b. Enable AI mode if requested
    if enable_ai and not args.skip_hooks:
        enable_ai_mode(settings_file, vault_root, claude_dir, dry_run=dry_run)

    # 8. Install CLAUDE-VAULT.md and wire @import into CLAUDE.md
    install_claude_vault_md(claude_dir, dry_run=dry_run, verbose=verbose)

    # 9. Rebuild vault index
    rebuild_index(claude_dir, dry_run=dry_run)

    # 10. Configure vault .gitignore for machine-local files
    configure_vault_gitignore(vault_root, dry_run=dry_run)

    # 10b. Initialize vault as a git repo (no-op if already initialized)
    init_vault_git(vault_root, dry_run=dry_run)

    # 10c. Install post-merge git hook for multi-machine sync
    install_vault_post_merge_hook(vault_root, claude_dir, dry_run=dry_run)

    # 10d. Write vault.username to config.yaml (for per-user daily note naming)
    configure_vault_username(vault_root, dry_run=dry_run, username=vault_username)

    # 11. Install global CLI tools (vault-search, vault-new, vault-stats) via uv tool
    if install_tools:
        install_cli_tools(REPO_ROOT, dry_run=dry_run)

    # 12. Schedule nightly summarizer (optional, --schedule-summarizer)
    if do_schedule:
        schedule_summarizer(
            claude_dir,
            dry_run=dry_run,
            hour=args.summarizer_hour,
            rebuild_graph=args.rebuild_graph,
            graph_include_daily=args.graph_include_daily,
        )

    # 13. Create vaults.yaml config template (optional, --create-vaults-config)
    if args.create_vaults_config:
        create_vaults_config(dry_run=dry_run)

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
            f"  3. Run: {cyan('uv run ~/.claude/skills/parsidion-cc/scripts/update_index.py')}"
        )
        print("         to rebuild the vault index at any time")
        print(
            f"  4. Run: {cyan('uv run ~/.claude/skills/parsidion-cc/scripts/build_embeddings.py')}"
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
    """Parse and return command-line arguments for the installer.

    Defines all CLI flags used by ``install.py``, including vault path, target
    Claude directory, dry-run mode, verbosity, force-overwrite, skip flags, and
    the ``--install-tools`` flag for registering ``vault-search`` as a global
    command via ``uv tool install``.

    Returns:
        Parsed argument namespace. Key attributes: ``vault``, ``claude_dir``,
        ``dry_run``, ``verbose``, ``force``, ``yes``, ``skip_hooks``,
        ``skip_agent``, ``uninstall``, ``enable_ai``, ``install_tools``,
        ``schedule_summarizer``, ``summarizer_hour``, ``rebuild_graph``,
        ``graph_include_daily``.
    """
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
        help=(
            "Skip all confirmation prompts. Uses ~/ClaudeVault as the vault "
            "path unless --vault PATH is also supplied. "
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
        "--uninstall",
        action="store_true",
        help="Remove installed skill, agents, and hooks",
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
        metavar="HOUR",
        help="Hour of day (0-23) to run the scheduled summarizer (default: 3 = 3 AM)",
    )
    parser.add_argument(
        "--rebuild-graph",
        action="store_true",
        help=(
            "Add --rebuild-graph to the scheduled summarizer command so the "
            "visualizer graph.json is regenerated each night after indexing. "
            "Only meaningful with --schedule-summarizer."
        ),
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
        help="Create ~/.config/parsidion-cc/vaults.yaml template",
    )
    parser.add_argument(
        "--help",
        "-h",
        action="help",
        help="Show this help message and exit",
    )
    return parser.parse_args()


def main() -> None:
    """Entry point for the Parsidion CC installer.

    Dispatches to either ``uninstall()`` or ``install()`` based on the
    ``--uninstall`` flag. Prompts for confirmation before uninstalling unless
    ``--yes`` or ``--dry-run`` is set. Exits with the return code from the
    chosen operation (0 = success, non-zero = error).
    """
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
        uninstall(claude_dir, settings_file, dry_run=args.dry_run, yes=args.yes)
        sys.exit(0)

    sys.exit(install(args))


if __name__ == "__main__":
    main()
