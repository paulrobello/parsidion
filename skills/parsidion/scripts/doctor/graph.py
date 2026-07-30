"""External-command wrappers — git stale-commit + update_index reindex.

Extracted from the original ``vault_doctor.py`` (ARC-008 / QA-003).  Both
functions shell out to git or ``update_index.py``; grouping them here keeps
the subprocess surface in one place.

Stdlib-only.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

import vault_common

from doctor._state import SCRIPTS_DIR, STALE_COMMIT_MINUTES, _active_vault


def commit_stale_files(
    dry_run: bool = False, vault_path: Path | None = None
) -> list[Path]:
    """Stage and commit uncommitted vault files whose mtime is older than STALE_COMMIT_MINUTES.

    Skips deleted files (no mtime to check) and respects the git.auto_commit
    config flag.  Returns the list of paths that were (or would be) committed.
    Does nothing when the vault has no .git directory.
    """
    if vault_path is None:
        vault_path = _active_vault()
    git_marker = vault_path / ".git"
    if not (git_marker.is_dir() or git_marker.is_file()):
        return []

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-u"],
            cwd=str(vault_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0 or not result.stdout.strip():
        return []

    cutoff = datetime.now().timestamp() - STALE_COMMIT_MINUTES * 60
    stale: list[Path] = []

    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        xy = line[:2]
        # Skip deletions — no file on disk to check mtime
        if "D" in xy:
            continue
        filepath_part = line[3:]
        # Handle renames: "old -> new"
        if " -> " in filepath_part:
            filepath_part = filepath_part.split(" -> ", 1)[1]
        path = vault_path / filepath_part.strip()
        try:
            if path.stat().st_mtime <= cutoff:
                stale.append(path)
        except OSError:
            continue

    if not stale:
        return []

    if dry_run:
        return stale

    committed = vault_common.git_commit_vault(
        f"chore(vault): auto-commit {len(stale)} stale file(s) via vault_doctor",
        paths=stale,
        vault=vault_path,
    )
    return stale if committed else []


def _run_reindex(vault_path: Path | None = None) -> None:
    """Run update_index.py to rebuild the vault index."""
    if vault_path is None:
        vault_path = _active_vault()

    script = SCRIPTS_DIR / "update_index.py"
    if not script.exists():
        script = (
            Path.home()
            / ".claude"
            / "skills"
            / "parsidion"
            / "scripts"
            / "update_index.py"
        )

    if not script.exists():
        print("Warning: update_index.py not found, skipping re-index.", file=sys.stderr)
        return

    print(f"\nRebuilding vault index at {vault_path}...")
    try:
        # QA-005: bound the index rebuild. vault_doctor --fix-all runs
        # unattended nightly; without a timeout a hung child stalls the cron
        # job indefinitely. 600 s is generous for a full re-index on a large
        # vault (the embedding-rebuild phase is itself bounded separately).
        subprocess.run(
            ["uv", "run", "--no-project", str(script), "--vault", str(vault_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
        )
        print("Index rebuilt successfully.")
    except subprocess.TimeoutExpired:
        print(
            "Warning: update_index.py timed out after 600s — index left stale.",
            file=sys.stderr,
        )
    except OSError as exc:
        print(f"Warning: update_index.py failed: {exc}", file=sys.stderr)
