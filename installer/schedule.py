"""Nightly summarizer scheduler for the Parsidion installer.

Handles macOS launchd plist installation and Linux/other cron job management.
Stdlib-only — no third-party dependencies.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from installer.paths import SKILL_NAME
from installer.ui import _ok, _step, _warn

_LAUNCHD_PLIST_LABEL = "com.parsidion.summarize-sessions"
_LAUNCHD_PLIST_NAME = f"{_LAUNCHD_PLIST_LABEL}.plist"
_CRON_MARKER = "# parsidion: nightly summarizer"


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
    <string>{Path.home() / ".claude" / "logs" / "parsidion-summarizer.log"}</string>
    <key>StandardErrorPath</key>
    <string>{Path.home() / ".claude" / "logs" / "parsidion-summarizer.log"}</string>
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
    from installer.ui import dim

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
    from installer.ui import dim

    extra = ""
    if rebuild_graph:
        extra += " --rebuild-graph"
    if graph_include_daily:
        extra += " --graph-include-daily"
    _cron_log = Path.home() / ".claude" / "logs" / "parsidion-summarizer.log"
    cron_line = (
        f"0 {hour} * * * {uv_path} run --no-project {script_path} --run-doctor{extra}"
        f" >> {_cron_log} 2>&1  {_CRON_MARKER}"
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
        rebuild_graph: When True, add ``--rebuild-graph`` to the scheduled command.
        graph_include_daily: When True, also add ``--graph-include-daily``.
    """
    import shutil

    scripts_dir = claude_dir / "skills" / SKILL_NAME / "scripts"
    script_path = scripts_dir / "summarize_sessions.py"

    log_dir = Path.home() / ".claude" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

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


def unschedule_summarizer(dry_run: bool = False) -> None:
    """Remove the nightly summarizer cron job or launchd plist if present.

    On macOS: unloads and deletes the launchd plist from ``~/Library/LaunchAgents/``.
    On Linux/other: removes the parsidion line from the user's crontab.
    Silent no-op when no scheduler entry is found.

    Args:
        dry_run: If True, print what would be done without making changes.
    """
    from installer.ui import dim

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
        try:
            result = subprocess.run(
                ["crontab", "-l"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return
            existing = result.stdout
            if _CRON_MARKER not in existing:
                return
            _step("Remove parsidion line from crontab", dry_run=dry_run)
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
            pass
