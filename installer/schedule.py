"""Nightly summarizer scheduler for the Parsidion installer.

Handles macOS launchd plist installation and Linux/other cron job management.
Stdlib-only — no third-party dependencies.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from installer.paths import SKILL_NAME
from installer.ui import _ok, _step, _warn, dim

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
    # SEC-124: XML-escape the user-supplied paths so a non-standard HOME
    # or scripts_dir containing <, >, &, " cannot break the plist.
    from xml.sax.saxutils import escape as _xml_escape

    uv_path_safe = _xml_escape(uv_path)
    script_path_safe = _xml_escape(str(script_path))
    log_path_safe = _xml_escape(
        str(Path.home() / ".claude" / "logs" / "parsidion-summarizer.log")
    )
    home_safe = _xml_escape(str(Path.home()))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_LAUNCHD_PLIST_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{uv_path_safe}</string>
        <string>run</string>
        <string>--no-project</string>
        <string>{script_path_safe}</string>
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
    <string>{log_path_safe}</string>
    <key>StandardErrorPath</key>
    <string>{log_path_safe}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>{home_safe}</string>
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

    # Best-effort unload of any prior registration; ignore failures
    # including timeout so a stale load state doesn't block the fresh
    # load below. QA-005.
    try:
        subprocess.run(
            ["launchctl", "unload", str(plist_path)],
            capture_output=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        pass

    # QA-005: bound the load — a hung launchctl would otherwise stall the
    # installer. On timeout warn and fall through to the script-exists
    # sanity check below without touching load_result.
    try:
        load_result = subprocess.run(
            ["launchctl", "load", str(plist_path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        _warn(
            f"launchctl load timed out after 15s. You may need to run: "
            f"launchctl load {plist_path}"
        )
        load_result = None

    if load_result is not None:
        if load_result.returncode == 0:
            _ok(f"Launchd job loaded — summarizer will run nightly at {hour:02d}:00")
        else:
            _warn(
                f"launchctl load returned {load_result.returncode}. "
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
    _cron_log = Path.home() / ".claude" / "logs" / "parsidion-summarizer.log"
    # SEC-124: quote paths in the cron line so spaces/special characters
    # in the user's HOME or install dir cannot split them into separate
    # arguments or break the redirection target.
    cron_line = (
        f'0 {hour} * * * "{uv_path}" run --no-project "{script_path}" --run-doctor{extra}'
        f' >> "{_cron_log}" 2>&1  {_CRON_MARKER}'
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
            timeout=10,
        )
        # SEC-127b: a non-zero ``crontab -l`` exit is *normal* when the
        # user has no crontab — returncode 1 with stderr "no crontab for
        # <user>" means exactly that, and the install path must treat it
        # as "no existing lines". But a non-zero exit with an UNEXPECTED
        # stderr (permission denied, broken binary, anything that is not
        # the "no crontab" message) must NOT silently proceed, because
        # that path replaces the user's entire crontab with just our
        # line — clobbering real entries the install could not read. The
        # uninstall path bails on any non-zero exit; install matches it
        # unless we can prove the failure is the no-crontab case.
        if result.returncode == 0:
            existing = result.stdout
        else:
            stderr_lower = (result.stderr or "").lower()
            if "no crontab" in stderr_lower or "crontab: no" in stderr_lower:
                existing = ""
            else:
                _warn(
                    f"crontab -l failed unexpectedly (rc={result.returncode}, "
                    f"stderr={result.stderr.strip()!r}); not installing to avoid "
                    "overwriting the existing crontab."
                )
                return
        lines = [ln for ln in existing.splitlines() if _CRON_MARKER not in ln]
        lines.append(cron_line)
        new_crontab = "\n".join(lines) + "\n"
        install_result = subprocess.run(
            ["crontab", "-"],
            input=new_crontab,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if install_result.returncode == 0:
            _ok(f"Cron job installed — summarizer will run nightly at {hour:02d}:00")
        else:
            _warn(f"crontab install failed: {install_result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        # QA-005: crontab hang (NFS-homedir, broken crond) — surface a
        # warning rather than stalling the installer indefinitely.
        _warn("crontab timed out — summarizer not scheduled. Add the line manually.")
        print(f"  {dim('Add this line manually:')}")
        print(f"  {dim(cron_line)}")
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
    if sys.platform == "darwin":
        plist_path = Path.home() / "Library" / "LaunchAgents" / _LAUNCHD_PLIST_NAME
        if not plist_path.exists():
            return
        _step(f"Remove launchd plist: {plist_path}", dry_run=dry_run)
        if dry_run:
            print(f"    {dim('Would run:')} launchctl unload {plist_path}")
            print(f"    {dim('Would delete:')} {plist_path}")
            return
        # QA-005: bound the unload. A hung launchctl would otherwise stall
        # uninstall; on timeout we still unlink the plist file below so
        # the scheduler entry is removed regardless.
        try:
            subprocess.run(
                ["launchctl", "unload", str(plist_path)],
                capture_output=True,
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            _warn(
                "launchctl unload timed out after 15s — plist may still be "
                "loaded; removing file anyway"
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
                timeout=10,
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
                timeout=10,
            )
            if install_result.returncode == 0:
                _ok("Cron job removed")
            else:
                _warn(f"crontab update failed: {install_result.stderr.strip()}")
        except subprocess.TimeoutExpired:
            # QA-005: crontab hang during uninstall — surface a warning
            # rather than stalling uninstall indefinitely.
            _warn("crontab timed out during uninstall — parsidion line may remain.")
        except FileNotFoundError:
            pass
