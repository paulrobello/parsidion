"""Redundant-prefix stripping fix-mode.

Extracted from the original ``vault_doctor.py`` (ARC-008 / QA-003).

Renames notes like ``Projects/cctmux/cctmux-overview.md`` to
``Projects/cctmux/overview.md`` (the subfolder already namespaces the note),
then patches wikilinks vault-wide.

Stdlib-only.
"""

from __future__ import annotations

import sys
from pathlib import Path

import vault_common
import vault_fs
import vault_links

from doctor._state import _active_vault, _backup_note
from doctor.graph import _run_reindex


def _find_redundant_prefixes(
    all_notes: list[Path],
    vault_path: Path,
) -> list[tuple[Path, Path]]:
    """Find notes inside subfolders whose filename redundantly starts with the subfolder name.

    For example, ``Projects/cctmux/cctmux-overview.md`` should be
    ``Projects/cctmux/overview.md`` since the subfolder already provides
    the namespace.

    Returns list of (old_path, new_path) pairs.
    """
    pairs: list[tuple[Path, Path]] = []
    for note in all_notes:
        rel = note.relative_to(vault_path)
        parts = rel.parts
        if len(parts) != 3:  # folder/subfolder/note.md
            continue
        subfolder = parts[1].lower()
        stem = note.stem.lower()
        if stem.startswith(f"{subfolder}-"):
            new_stem = note.stem[len(subfolder) + 1 :]
            if new_stem:
                new_path = note.parent / f"{new_stem}.md"
                # Don't rename if the target already exists
                if not new_path.exists():
                    pairs.append((note, new_path))
    return pairs


def run_strip_prefixes(
    dry_run: bool = True, vault_path: Path | None = None, auto_reindex: bool = True
) -> None:
    """Strip redundant subfolder prefixes from note filenames.

    Renames files and updates all wikilinks vault-wide.

    Args:
        dry_run: When True, only report — do not modify any files.
        vault_path: Vault root path (uses resolver if None).
    """
    if vault_path is None:
        vault_path = _active_vault()
    all_notes = list(vault_common.all_vault_notes(vault_path))
    pairs = _find_redundant_prefixes(all_notes, vault_path)

    if not pairs:
        print("No redundant prefixes found.")
        return

    # Group by subfolder for display
    by_folder: dict[str, list[tuple[Path, Path]]] = {}
    for old, new in pairs:
        folder_key = str(old.parent.relative_to(vault_path))
        by_folder.setdefault(folder_key, []).append((old, new))

    print(f"\nFound {len(pairs)} note(s) with redundant subfolder prefix:\n")
    for folder, folder_pairs in sorted(by_folder.items()):
        print(f"  {folder}/")
        for old, new in folder_pairs:
            print(f"    {old.name}  →  {new.name}")
    print()

    if dry_run:
        print(
            f"[dry-run] {len(pairs)} file(s) would be renamed. Run with --execute to apply."
        )
        return

    # Rename files (skip failures gracefully — a mid-batch crash here would
    # leave already-renamed notes with unpatched, vault-wide broken wikilinks)
    renamed: list[tuple[Path, Path]] = []
    for old, new in pairs:
        try:
            _backup_note(vault_path, old)
            old.rename(new)
        except OSError as exc:
            rel = old.relative_to(vault_path)
            print(f"  ⚠ skipped (rename failed): {rel}: {exc}", file=sys.stderr)
            continue
        renamed.append((old, new))

    if not renamed:
        print("No files were renamed.")
        return

    # Build stem remapping for wikilink patching — only stems that actually renamed
    stem_map: dict[str, str] = {old.stem: new.stem for old, new in renamed}

    # Patch wikilinks vault-wide (including in the renamed files)
    patched_notes = 0
    current_notes = list(vault_common.all_vault_notes(vault_path))
    for note in current_notes:
        try:
            content = note.read_text(encoding="utf-8")
        except OSError:
            continue
        original = content
        content = vault_links.replace_wikilinks_outside_code(content, stem_map)
        if content != original:
            _backup_note(vault_path, note)
            vault_fs.atomic_write_text(note, content)
            patched_notes += 1

    vault_common.git_commit_vault(
        f"refactor(vault): strip redundant subfolder prefix from {len(renamed)} note(s)",
        vault=vault_path,
    )
    print(
        f"Renamed {len(renamed)} file(s), patched wikilinks in {patched_notes} note(s)."
    )
    if auto_reindex:
        _run_reindex(vault_path)
