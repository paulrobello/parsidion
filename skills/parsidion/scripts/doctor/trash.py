"""Trash-sprawl detection and opt-in pruning for ``<vault>/.trash``.

Retired notes land in ``.trash`` (gitignored, excluded from every retrieval
surface) and nothing bounded its growth — the 2026-10-25 stack audit found
13,556 files there, roughly half of all vault files.  The scan stage reports
the finding; deletion is a separate, explicitly-requested mode because it is
irreversible (gitignored, so no git recovery) and needs an owner-approved
retention window.

Stdlib-only.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

TRASH_NOTE_THRESHOLD = 1000


def trash_stats(vault_path: Path) -> tuple[int, int]:
    """Count files and total bytes under ``<vault>/.trash`` (0, 0 when absent)."""
    trash_dir = vault_path / ".trash"
    if not trash_dir.is_dir():
        return 0, 0
    count = 0
    size = 0
    for root, _dirs, files in os.walk(trash_dir):
        for name in files:
            count += 1
            try:
                size += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return count, size


def report_trash_sprawl(vault_path: Path) -> None:
    """Print a TRASH_SIZE finding when .trash exceeds TRASH_NOTE_THRESHOLD files."""
    count, size = trash_stats(vault_path)
    if count < TRASH_NOTE_THRESHOLD:
        return
    print(
        f"\n⚠ TRASH_SIZE: .trash holds {count} files "
        f"({size / (1024 * 1024):.0f} MB) — retired notes are never "
        "auto-removed.\n"
        "  Fix: uv run --no-project ~/.claude/skills/parsidion/scripts/"
        "vault_doctor.py --prune-trash --trash-retention-days N --execute\n"
        "  (deletion is opt-in and never part of --fix-all)"
    )


def run_prune_trash(
    vault_path: Path, retention_days: int, dry_run: bool = True
) -> None:
    """Delete ``.trash`` entries whose mtime is older than *retention_days*.

    mtime survives the move into .trash, so it approximates "retired long
    ago" — the practical proxy, since .trash entries carry no trash-date
    metadata.  Directories left empty by the sweep are removed too.
    """
    if retention_days <= 0:
        raise ValueError("retention_days must be positive")
    trash_dir = vault_path / ".trash"
    count, size = trash_stats(vault_path)
    if count == 0:
        print("No .trash files to prune.")
        return
    cutoff = time.time() - retention_days * 86400
    removed = 0
    reclaimed = 0
    for root, _dirs, files in os.walk(trash_dir, topdown=False):
        for name in files:
            path = Path(root) / name
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_mtime >= cutoff:
                continue
            if not dry_run:
                try:
                    path.unlink()
                except OSError as exc:
                    print(f"  ! could not delete {path.name}: {exc}")
                    continue
            removed += 1
            reclaimed += st.st_size
        if not dry_run:
            emptied = Path(root)
            if emptied != trash_dir:
                # rmdir raises when non-empty — exactly the guard we want.
                try:
                    emptied.rmdir()
                except OSError:
                    pass
    label = "Would delete" if dry_run else "Deleted"
    print(
        f"{label} {removed} of {count} .trash file(s) "
        f"({reclaimed / (1024 * 1024):.1f} MB of {size / (1024 * 1024):.1f} MB) "
        f"older than {retention_days} day(s)."
    )
