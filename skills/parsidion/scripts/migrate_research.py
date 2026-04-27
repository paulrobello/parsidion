#!/usr/bin/env python3
"""Migrate ~/Repos/research/ contents to ~/ClaudeVault/ with proper organization.

**One-time migration utility** -- this script is intended to be run once during
initial vault setup to import pre-existing research notes.  It is not part of
the regular hook pipeline and can be safely ignored after migration.
See ARC-013 in AUDIT.md.

Handles frontmatter injection, deduplication, kebab-case renaming, and date
extraction. Dry-run is the default; pass ``--execute`` to actually write files.
Uses only Python stdlib.
"""

import argparse
import hashlib
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from vault_common import (
    VAULT_ROOT,
    ensure_vault_dirs,
    get_body,
    parse_frontmatter,
    slugify,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_RESEARCH_ROOT: Path = Path.home() / "Repos" / "research"
RESEARCH_ROOT: Path = _DEFAULT_RESEARCH_ROOT

# Regex to extract a date from a filename like ``foo-2026-02-10.md``
_FILENAME_DATE_RE = re.compile(r"-(\d{4}-\d{2}-\d{2})(?:\.md)?$")

# Items to skip entirely during migration
SKIP_ITEMS: set[str] = {"textual.textualize.io", ".git", ".gitignore", ".DS_Store"}

# Type strings that map vault folders to frontmatter ``type`` values
FOLDER_TYPE_MAP: dict[str, str] = {
    "Languages": "language",
    "Frameworks": "framework",
    "Tools": "tool",
    "Research": "research",
    "Patterns": "pattern",
}

# ---------------------------------------------------------------------------
# Category mapping: source name -> (vault_folder, optional_subfolder)
#
# ``None`` as subfolder means flatten into the vault folder directly.
# A string subfolder means keep as a subdirectory under the vault folder.
# ---------------------------------------------------------------------------

# Single .md files  (source filename stem -> vault folder)
FILE_CATEGORY: dict[str, str] = {
    # Languages/
    "automerge-rust": "Languages",
    "autosurgeon-rust": "Languages",
    "rusqlite-sqlcipher": "Languages",
    "rust-crate-compatibility-2026-02-10": "Languages",
    "rust-zero-trust-vault-crates-2026-02-22": "Languages",
    "ed25519-dalek-and-clap": "Languages",
    # Tools/
    "claude-agent-sdk-custom-tools-2025-12-18": "Tools",
    "claude-agent-sdk-python-agent-loop-2026-02-16": "Tools",
    "package-versions-2026-02-16": "Tools",
    # Research/
    "wgpu-28-breaking-changes-2026-01-29": "Research",
    "wgpu-28-breaking-changes-2026-02-05": "Research",
    "apple-vision-face-tracking-macos": "Research",
    "mediapipe-macos-face-tracking": "Research",
    "vrmkit-swift-package-2026-02-11": "Research",
    "VRMKIT_QUICK_REFERENCE": "Research",
    "pandora-api": "Research",
    # Patterns/
    "DOCUMENTATION_STYLE_GUIDE": "Patterns",
}

# Directories -> (vault_folder, keep_as_subdir: bool)
DIR_CATEGORY: dict[str, tuple[str, bool]] = {
    # Languages/
    "rust": ("Languages", False),
    "rust-packages": ("Languages", False),
    # Frameworks/
    "nextjs": ("Frameworks", False),
    "rich": ("Frameworks", False),
    "react-virtualization": ("Frameworks", False),
    "maturin": ("Frameworks", False),
    # Tools/
    "claude-code": ("Tools", False),
    "claude-code-acp": ("Tools", False),
    "claude-agent-sdk": ("Tools", False),
    "mermaid-cli": ("Tools", False),
    "sentry-io": ("Tools", False),
    "e2b": ("Tools", False),
    "ollama-agentic-models": ("Tools", False),
    "terminal-emulators": ("Tools", False),
    "terminal-protocols": ("Tools", False),
    # Research/
    "fractal-flythroughs": ("Research", False),
    "fractals": ("Research", False),
    "mandel": ("Research", False),
    "sdf-terrain": ("Research", False),
    "voxel-engines": ("Research", False),
    "wgpu": ("Research", False),
    "websockets": ("Research", False),
    "acp-protocol": ("Research", False),
    "synknot": ("Research", False),
    "macos-cmioextension": ("Research", False),
    "macos-spaces": ("Research", False),
    "avatar-rendering": ("Research", False),
    "pkm-apps-comparison": ("Research", False),
    "qdrant.tech": ("Research", True),  # keep as subdirectory
}

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


class MigrationEntry:
    """Represents a single file to be migrated."""

    __slots__ = (
        "src",
        "dst",
        "vault_folder",
        "source_dir",
        "frontmatter",
        "content_hash",
        "skipped",
        "skip_reason",
        "duplicate_of",
    )

    def __init__(
        self,
        src: Path,
        dst: Path,
        vault_folder: str,
        source_dir: str,
    ) -> None:
        self.src: Path = src
        self.dst: Path = dst
        self.vault_folder: str = vault_folder
        self.source_dir: str = source_dir
        self.frontmatter: dict[str, Any] = {}
        self.content_hash: str = ""
        self.skipped: bool = False
        self.skip_reason: str = ""
        self.duplicate_of: Path | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_date_from_filename(stem: str) -> str | None:
    """Return a ``YYYY-MM-DD`` date string if one is embedded in *stem*."""
    m = _FILENAME_DATE_RE.search(stem)
    if m:
        return m.group(1)
    return None


def _strip_date_suffix(stem: str) -> str:
    """Remove a trailing ``-YYYY-MM-DD`` from a filename stem."""
    return _FILENAME_DATE_RE.sub("", stem)


def _file_mtime_date(path: Path) -> str:
    """Return the file's mtime as ``YYYY-MM-DD``."""
    try:
        ts: float = path.stat().st_mtime
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except OSError:
        return datetime.now().strftime("%Y-%m-%d")


def _content_hash(text: str) -> str:
    """Return SHA-256 hex digest of *text* (frontmatter stripped)."""
    body: str = get_body(text)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _infer_tags(source_dir: str, stem: str) -> list[str]:
    """Infer tags from the source directory name and filename stem."""
    tags: list[str] = []

    # Use the source directory as a tag (cleaned up)
    if source_dir:
        dir_tag: str = slugify(source_dir)
        if dir_tag:
            tags.append(dir_tag)

    # Add extra tags from well-known keywords in the stem
    keyword_tags: dict[str, str] = {
        "rust": "rust",
        "python": "python",
        "typescript": "typescript",
        "swift": "swift",
        "wgpu": "wgpu",
        "nextjs": "nextjs",
        "react": "react",
        "claude": "claude",
        "macos": "macos",
        "terminal": "terminal",
        "fractal": "fractal",
        "voxel": "voxel",
        "sdf": "sdf",
        "websocket": "websocket",
        "mcp": "mcp",
        "sentry": "sentry",
        "qdrant": "qdrant",
        "ollama": "ollama",
        "e2b": "e2b",
        "rich": "rich",
        "mermaid": "mermaid",
        "vrm": "vrm",
        "face-tracking": "face-tracking",
        "avatar": "avatar",
    }
    lower_stem: str = stem.lower()
    for keyword, tag in keyword_tags.items():
        if keyword in lower_stem and tag not in tags:
            tags.append(tag)

    return tags if tags else ["research"]


def _build_frontmatter(
    existing_fm: dict[str, Any],
    vault_folder: str,
    date_str: str,
    source_dir: str,
    stem: str,
) -> dict[str, Any]:
    """Build a complete frontmatter dict, merging with *existing_fm* if present."""
    note_type: str = FOLDER_TYPE_MAP.get(vault_folder, "research")
    tags: list[str] = _infer_tags(source_dir, stem)

    fm: dict[str, Any] = {
        "date": date_str,
        "type": note_type,
        "tags": tags,
        "confidence": "medium",
        "sources": [],
        "related": [],
    }

    # Merge existing values -- existing takes precedence for populated fields
    for key, value in existing_fm.items():
        if key in fm:
            # Only override if existing value is non-empty / non-default
            if isinstance(value, list) and len(value) > 0:
                fm[key] = value
            elif isinstance(value, str) and value:
                fm[key] = value
            elif not isinstance(value, (str, list)) and value is not None:
                fm[key] = value
        else:
            # Extra fields from existing frontmatter -- keep them
            fm[key] = value

    return fm


def _serialize_frontmatter(fm: dict[str, Any]) -> str:
    """Serialize a frontmatter dict to a YAML frontmatter block string."""
    lines: list[str] = ["---"]

    # Ordered keys for readability
    ordered_keys: list[str] = [
        "date",
        "type",
        "tags",
        "project",
        "confidence",
        "sources",
        "related",
    ]
    remaining_keys: list[str] = [k for k in fm if k not in ordered_keys]

    for key in ordered_keys + remaining_keys:
        if key not in fm:
            continue
        value: Any = fm[key]

        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
            else:
                items_str: str = ", ".join(str(v) for v in value)
                lines.append(f"{key}: [{items_str}]")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif value is None:
            lines.append(f"{key}: ")
        else:
            lines.append(f"{key}: {value}")

    lines.append("---")
    return "\n".join(lines) + "\n"


def _dest_filename(stem: str) -> str:
    """Compute the destination filename: slugified, date suffix stripped, ``.md`` appended."""
    clean_stem: str = _strip_date_suffix(stem)
    slug: str = slugify(clean_stem)
    if not slug:
        slug = "untitled"
    return slug + ".md"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _discover_single_files() -> list[MigrationEntry]:
    """Discover single ``.md`` files in the research root that need migrating."""
    entries: list[MigrationEntry] = []

    for item in sorted(RESEARCH_ROOT.iterdir()):
        if item.name in SKIP_ITEMS:
            continue
        if not item.is_file() or not item.name.endswith(".md"):
            continue

        stem: str = item.stem
        vault_folder: str | None = FILE_CATEGORY.get(stem)
        if vault_folder is None:
            # File not in our mapping -- skip with a note
            entry = MigrationEntry(
                src=item,
                dst=Path(""),
                vault_folder="",
                source_dir="",
            )
            entry.skipped = True
            entry.skip_reason = "Not in category mapping"
            entries.append(entry)
            continue

        dest_name: str = _dest_filename(stem)
        dst: Path = VAULT_ROOT / vault_folder / dest_name

        entry = MigrationEntry(
            src=item,
            dst=dst,
            vault_folder=vault_folder,
            source_dir="",
        )
        entries.append(entry)

    return entries


def _discover_directory_files() -> list[MigrationEntry]:
    """Discover ``.md`` files inside directories that need migrating."""
    entries: list[MigrationEntry] = []

    for item in sorted(RESEARCH_ROOT.iterdir()):
        if item.name in SKIP_ITEMS:
            continue
        if not item.is_dir():
            continue

        dir_name: str = item.name
        mapping: tuple[str, bool] | None = DIR_CATEGORY.get(dir_name)
        if mapping is None:
            # Directory not in our mapping -- skip
            entry = MigrationEntry(
                src=item,
                dst=Path(""),
                vault_folder="",
                source_dir=dir_name,
            )
            entry.skipped = True
            entry.skip_reason = "Not in category mapping"
            entries.append(entry)
            continue

        vault_folder, keep_subdir = mapping

        # Walk all .md files in this directory tree
        for dirpath_str, _dirnames, filenames in os.walk(item):
            dirpath = Path(dirpath_str)
            for fname in sorted(filenames):
                if not fname.endswith(".md"):
                    continue

                src_path: Path = dirpath / fname
                stem: str = Path(fname).stem

                if keep_subdir:
                    # Preserve directory structure under a renamed subdirectory
                    # e.g. qdrant.tech/ -> Research/qdrant-tech/
                    subdir_name: str = slugify(dir_name)
                    try:
                        rel: Path = src_path.relative_to(item)
                    except ValueError:
                        rel = Path(fname)
                    dest_name = _dest_filename(rel.stem)
                    # Preserve intermediate path for nested structures
                    if len(rel.parts) > 1:
                        intermediate: Path = Path(*rel.parts[:-1])
                        dst = (
                            VAULT_ROOT
                            / vault_folder
                            / subdir_name
                            / intermediate
                            / dest_name
                        )
                    else:
                        dst = VAULT_ROOT / vault_folder / subdir_name / dest_name
                else:
                    # Flatten: prefix with source dir name to avoid collisions
                    # For files already prefixed with dir name, don't double-prefix
                    dest_name = _dest_filename(stem)
                    slug_dir: str = slugify(dir_name)

                    # Add directory prefix if the filename doesn't already contain it
                    if not dest_name.startswith(slug_dir):
                        dest_name = slug_dir + "-" + dest_name

                    # Avoid double-prefixing for nested dirs
                    # e.g. e2b/filesystem/overview.md -> e2b-filesystem-overview.md
                    try:
                        rel = src_path.relative_to(item)
                    except ValueError:
                        rel = Path(fname)

                    if len(rel.parts) > 1:
                        # Build prefix from intermediate directories
                        intermediate_parts: list[str] = [
                            slugify(p) for p in rel.parts[:-1]
                        ]
                        prefix: str = "-".join([slug_dir] + intermediate_parts)
                        dest_name = prefix + "-" + _dest_filename(stem)
                        # De-duplicate repeated segments
                        # e.g. "e2b-e2b-overview" -> avoid this
                        if dest_name.startswith(slug_dir + "-" + slug_dir + "-"):
                            dest_name = dest_name[len(slug_dir) + 1 :]

                    dst = VAULT_ROOT / vault_folder / dest_name

                entry = MigrationEntry(
                    src=src_path,
                    dst=dst,
                    vault_folder=vault_folder,
                    source_dir=dir_name,
                )
                entries.append(entry)

    return entries


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------


def _process_entry(entry: MigrationEntry) -> None:
    """Read source file, compute hash, and build frontmatter for an entry."""
    if entry.skipped:
        return

    try:
        content: str = entry.src.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = entry.src.read_text(encoding="latin-1")
        except OSError as exc:
            entry.skipped = True
            entry.skip_reason = f"Encoding error: {exc}"
            return
    except OSError as exc:
        entry.skipped = True
        entry.skip_reason = f"Read error: {exc}"
        return

    # Content hash (on body only, stripped of frontmatter)
    entry.content_hash = _content_hash(content)

    # Parse existing frontmatter
    existing_fm: dict[str, Any] = parse_frontmatter(content)

    # Determine date
    stem: str = entry.src.stem
    date_str: str | None = _extract_date_from_filename(stem)
    if date_str is None:
        # Check existing frontmatter for a date
        fm_date: Any = existing_fm.get("date")
        if isinstance(fm_date, str) and re.match(r"\d{4}-\d{2}-\d{2}", fm_date):
            date_str = fm_date
        else:
            date_str = _file_mtime_date(entry.src)

    entry.frontmatter = _build_frontmatter(
        existing_fm=existing_fm,
        vault_folder=entry.vault_folder,
        date_str=date_str,
        source_dir=entry.source_dir,
        stem=stem,
    )


def _deduplicate(entries: list[MigrationEntry]) -> None:
    """Detect duplicates by content hash. Keep the newer file, mark older as duplicate."""
    hash_map: dict[str, MigrationEntry] = {}

    for entry in entries:
        if entry.skipped or not entry.content_hash:
            continue

        existing: MigrationEntry | None = hash_map.get(entry.content_hash)
        if existing is None:
            hash_map[entry.content_hash] = entry
            continue

        # Duplicate found -- keep the newer file by mtime
        try:
            existing_mtime: float = existing.src.stat().st_mtime
        except OSError:
            existing_mtime = 0.0
        try:
            entry_mtime: float = entry.src.stat().st_mtime
        except OSError:
            entry_mtime = 0.0

        if entry_mtime > existing_mtime:
            # New entry is newer -- mark existing as duplicate
            existing.skipped = True
            existing.skip_reason = "Duplicate (older)"
            existing.duplicate_of = entry.src
            hash_map[entry.content_hash] = entry
        else:
            # Existing is newer or same -- mark new entry as duplicate
            entry.skipped = True
            entry.skip_reason = "Duplicate (older)"
            entry.duplicate_of = existing.src


def _resolve_collisions(entries: list[MigrationEntry]) -> None:
    """If two non-skipped entries map to the same destination, add numeric suffix."""
    dst_map: dict[Path, list[MigrationEntry]] = {}
    for entry in entries:
        if entry.skipped:
            continue
        dst_map.setdefault(entry.dst, []).append(entry)

    for dst, group in dst_map.items():
        if len(group) <= 1:
            continue
        # Keep first as-is, suffix the rest
        for i, entry in enumerate(group[1:], start=2):
            stem: str = dst.stem
            entry.dst = dst.with_name(f"{stem}-{i}{dst.suffix}")


def _build_file_content(entry: MigrationEntry) -> str:
    """Build the final file content with injected/merged frontmatter."""
    try:
        content: str = entry.src.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = entry.src.read_text(encoding="latin-1")

    body: str = get_body(content)
    fm_block: str = _serialize_frontmatter(entry.frontmatter)

    return fm_block + "\n" + body


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _print_report(
    entries: list[MigrationEntry],
    execute: bool,
) -> None:
    """Print a human-readable migration report."""
    migrated: list[MigrationEntry] = []
    duplicates: list[MigrationEntry] = []
    skipped: list[MigrationEntry] = []

    for entry in entries:
        if entry.skipped:
            if entry.duplicate_of is not None:
                duplicates.append(entry)
            else:
                skipped.append(entry)
        else:
            migrated.append(entry)

    mode: str = "EXECUTE" if execute else "DRY-RUN"
    print(f"\n{'=' * 72}")
    print(f"  Migration Report ({mode})")
    print(f"{'=' * 72}\n")

    # Migrated files
    if migrated:
        print(f"--- Files to migrate ({len(migrated)}) ---\n")
        for entry in migrated:
            try:
                src_rel: str = str(entry.src.relative_to(RESEARCH_ROOT))
            except ValueError:
                src_rel = str(entry.src)
            try:
                dst_rel: str = str(entry.dst.relative_to(VAULT_ROOT))
            except ValueError:
                dst_rel = str(entry.dst)

            print(f"  {src_rel}")
            print(f"    -> {dst_rel}")

            # Frontmatter preview (compact)
            fm: dict[str, Any] = entry.frontmatter
            tags_str: str = ", ".join(str(t) for t in fm.get("tags", []))
            print(
                f"       date={fm.get('date', '?')}  type={fm.get('type', '?')}  tags=[{tags_str}]"
            )
            print()

    # Duplicates
    if duplicates:
        print(f"--- Duplicates detected ({len(duplicates)}) ---\n")
        for entry in duplicates:
            try:
                src_rel = str(entry.src.relative_to(RESEARCH_ROOT))
            except ValueError:
                src_rel = str(entry.src)
            dup_of: str = ""
            if entry.duplicate_of is not None:
                try:
                    dup_of = str(entry.duplicate_of.relative_to(RESEARCH_ROOT))
                except ValueError:
                    dup_of = str(entry.duplicate_of)
            print(f"  SKIP  {src_rel}")
            print(f"        Duplicate of: {dup_of}")
            print()

    # Skipped
    if skipped:
        print(f"--- Skipped ({len(skipped)}) ---\n")
        for entry in skipped:
            try:
                src_rel = str(entry.src.relative_to(RESEARCH_ROOT))
            except ValueError:
                src_rel = str(entry.src)
            print(f"  SKIP  {src_rel}  ({entry.skip_reason})")

    # Summary
    print(f"\n{'=' * 72}")
    print(
        f"  Summary: {len(migrated)} files to migrate, "
        f"{len(duplicates)} duplicates, {len(skipped)} skipped"
    )
    if not execute:
        print("  Mode: DRY-RUN (no files written). Use --execute to migrate.")
    else:
        print("  Mode: EXECUTE (files copied to vault).")
    print(f"{'=' * 72}\n")


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _execute_migration(entries: list[MigrationEntry]) -> int:
    """Copy files to the vault. Returns the number of files written."""
    written: int = 0

    for entry in entries:
        if entry.skipped:
            continue

        # Ensure destination directory exists
        entry.dst.parent.mkdir(parents=True, exist_ok=True)

        try:
            content: str = _build_file_content(entry)
            entry.dst.write_text(content, encoding="utf-8")
            written += 1
        except OSError as exc:
            print(f"  ERROR writing {entry.dst}: {exc}", file=sys.stderr)

    return written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the migration script."""
    global RESEARCH_ROOT

    parser = argparse.ArgumentParser(
        description="Migrate a research directory to ~/ClaudeVault/ with frontmatter and organization.",
    )
    parser.add_argument(
        "research_path",
        nargs="?",
        default=str(_DEFAULT_RESEARCH_ROOT),
        help=f"Path to the research directory to migrate (default: {_DEFAULT_RESEARCH_ROOT})",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Actually write files to the vault. Default is dry-run.",
    )
    args: argparse.Namespace = parser.parse_args()

    RESEARCH_ROOT = Path(args.research_path).expanduser().resolve()

    if not RESEARCH_ROOT.is_dir():
        print(f"ERROR: Research directory not found: {RESEARCH_ROOT}", file=sys.stderr)
        sys.exit(1)

    # Ensure vault structure exists
    ensure_vault_dirs()

    # Discover all files
    print("Discovering files...")
    entries: list[MigrationEntry] = []
    entries.extend(_discover_single_files())
    entries.extend(_discover_directory_files())
    print(f"  Found {len(entries)} items")

    # Process each entry (read, hash, build frontmatter)
    print("Processing files...")
    for entry in entries:
        _process_entry(entry)

    # Deduplicate
    print("Checking for duplicates...")
    _deduplicate(entries)

    # Resolve destination collisions
    _resolve_collisions(entries)

    # Report
    _print_report(entries, execute=args.execute)

    # Execute if requested
    if args.execute:
        print("Writing files to vault...")
        written: int = _execute_migration(entries)
        print(f"\nMigration complete. {written} files written to {VAULT_ROOT}")


if __name__ == "__main__":
    main()
