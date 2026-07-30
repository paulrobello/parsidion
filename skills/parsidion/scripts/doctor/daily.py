"""Daily-note migration — rename legacy ``DD.md`` to ``DD-{username}.md``.

Extracted from the original ``vault_doctor.py`` (ARC-008 / QA-003).

Stdlib-only.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import vault_common
import vault_fs
import vault_links

from doctor._state import SCRIPTS_DIR, _backup_note


def run_migrate_daily_notes(
    vault_root: Path, dry_run: bool = True, username: str = ""
) -> None:
    """Rename legacy ``Daily/YYYY-MM/DD.md`` notes to ``DD-{username}.md``.

    The un-namespaced ``DD.md`` format causes git merge conflicts when a team
    shares a vault — multiple users write the same filename on the same day.
    This migration renames existing notes once so future writes use the new
    ``DD-{username}.md`` format.

    After renaming, wikilinks inside rollup notes (``week-NN.md``,
    ``monthly.md``) that reference the old stem are updated automatically.

    Args:
        vault_root: Root path of the vault.
        dry_run: When True, only print candidates — do not rename any files.
        username: Username suffix to append.  Resolved from vault config /
            ``$USER`` environment variable when empty.
    """
    if not username:
        username = vault_common.get_vault_username()

    daily_root = vault_root / "Daily"
    if not daily_root.exists():
        print("No Daily/ directory found — nothing to migrate.")
        return

    # Pattern for un-namespaced day files: exactly two digits, no hyphen suffix
    stem_re = re.compile(r"^\d{2}$")

    candidates: list[tuple[Path, Path]] = []  # (old_path, new_path)

    for month_dir in sorted(daily_root.iterdir()):
        if not month_dir.is_dir():
            continue
        for note in sorted(month_dir.glob("[0-9][0-9].md")):
            if stem_re.match(note.stem):
                new_name = f"{note.stem}-{username}.md"
                new_path = note.parent / new_name
                candidates.append((note, new_path))

    if not candidates:
        print(
            f"No legacy daily notes found to migrate (already using DD-{username}.md format or vault is empty)."
        )
        return

    print(f"Found {len(candidates)} legacy daily note(s) to rename:\n")
    for old, new in candidates:
        old_rel = old.relative_to(vault_root)
        new_rel = new.relative_to(vault_root)
        status = ""
        if new.exists():
            status = "  [SKIP — target already exists]"
        print(f"  {old_rel}  →  {new_rel}{status}")

    if dry_run:
        print(
            f"\n[dry-run] {len(candidates)} note(s) would be renamed. "
            "Run with --execute to apply."
        )
        return

    # --- Execute renames ---
    moved: list[tuple[Path, Path]] = []
    skipped = 0
    for old, new in candidates:
        if new.exists():
            print(f"  Skipped (target exists): {old.relative_to(vault_root)}")
            skipped += 1
            continue
        _backup_note(vault_root, old)
        old.rename(new)
        print(
            f"  Renamed: {old.relative_to(vault_root)}  →  {new.relative_to(vault_root)}"
        )
        moved.append((old, new))

    if not moved:
        print("No files renamed.")
        return

    # --- Update wikilinks in rollup notes ---
    # Rollup notes (week-NN.md, monthly.md) contain [[DD]] wikilinks.
    # Update them to [[DD-username]].
    rollup_pattern = re.compile(r"week-\d+\.md|monthly\.md")
    updated_rollups: list[Path] = []

    for month_dir in sorted(daily_root.iterdir()):
        if not month_dir.is_dir():
            continue
        for rollup in month_dir.iterdir():
            if not rollup_pattern.match(rollup.name):
                continue
            try:
                text = rollup.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            # Match [[DD]] but not [[DD-something]] (avoid double-rename)
            stem_map = {
                old.stem: new.stem for old, new in moved if old.parent == month_dir
            }
            new_text = vault_links.replace_wikilinks_outside_code(text, stem_map)

            if new_text != text:
                _backup_note(vault_root, rollup)
                vault_fs.atomic_write_text(rollup, new_text)
                updated_rollups.append(rollup)
                print(f"  Updated wikilinks: {rollup.relative_to(vault_root)}")

    # --- Commit and rebuild index ---
    all_changed = [new for _, new in moved] + updated_rollups
    vault_common.git_commit_vault(
        f"refactor(vault): migrate {len(moved)} daily note(s) to DD-{username}.md format",
        paths=all_changed,
    )
    print(f"\nMigrated {len(moved)} note(s). Running update_index.py…")
    update_index_script = SCRIPTS_DIR / "update_index.py"
    try:
        subprocess.run(
            ["uv", "run", "--no-project", str(update_index_script)],
            check=True,
            env=vault_common.env_without_claudecode(),
            timeout=60,
        )
        print("Index rebuilt.")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"Warning: update_index.py failed: {exc}", file=sys.stderr)
        print("Run manually: uv run --no-project update_index.py", file=sys.stderr)
    if skipped:
        print(f"Note: {skipped} file(s) skipped because target already existed.")
