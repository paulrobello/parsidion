#!/usr/bin/env python3
"""Migrate ~/.claude/memory/ contents (built-in auto memory) to the Parsidion vault.

**One-time migration utility** -- this script is intended to be run once during
initial vault setup to import pre-existing Claude Code memory files.  It is not
part of the regular hook pipeline and can be safely ignored after migration.
See ARC-013 in AUDIT.md.

Scans global memory (``~/.claude/memory/``) and per-project memory
(``~/.claude/projects/*/memory/``) for markdown files. Parses ``##`` sections,
classifies each by content heuristics, and creates vault notes with proper
frontmatter.

Dry-run is the **default**; pass ``--execute`` to actually write files and back
up originals. Uses only Python stdlib.
"""

import argparse
import re
import sys
from datetime import date
from pathlib import Path

from vault_common import (
    VAULT_ROOT,
    ensure_vault_dirs,
    slugify,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLAUDE_DIR: Path = Path.home() / ".claude"
GLOBAL_MEMORY_DIR: Path = CLAUDE_DIR / "memory"
PROJECTS_DIR: Path = CLAUDE_DIR / "projects"

# Section classification keywords → (type, vault folder)
_SECTION_RULES: list[tuple[list[str], str, str]] = [
    # Order matters: first match wins
    (
        ["debug", "error", "fix", "bug", "crash", "workaround", "issue"],
        "debugging",
        "Debugging",
    ),
    (
        ["tool", "setup", "install", "config", "configuration", "cli", "mcp"],
        "tool",
        "Tools",
    ),
    (
        ["project", "architecture", "structure", "workspace", "crate", "module"],
        "project",
        "Projects",
    ),
    (
        ["pattern", "convention", "preference", "always", "never", "rule", "standard"],
        "pattern",
        "Patterns",
    ),
]

DEFAULT_TYPE: str = "pattern"
DEFAULT_FOLDER: str = "Patterns"

# Regex for encoded project paths like ``-Users-username-Repos-myproject``
_PROJECT_PATH_RE = re.compile(r"^-.*-([^-]+)$")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


class MemorySection:
    """A single ``##`` section extracted from a memory markdown file."""

    __slots__ = ("heading", "body", "note_type", "vault_folder")

    def __init__(self, heading: str, body: str) -> None:
        self.heading: str = heading
        self.body: str = body
        self.note_type: str = DEFAULT_TYPE
        self.vault_folder: str = DEFAULT_FOLDER


class VaultNote:
    """A vault note to be created from one or more memory sections."""

    __slots__ = (
        "source",
        "dest",
        "heading",
        "content",
        "note_type",
        "vault_folder",
        "project",
    )

    def __init__(
        self,
        source: Path,
        dest: Path,
        heading: str,
        content: str,
        note_type: str,
        vault_folder: str,
        project: str,
    ) -> None:
        self.source: Path = source
        self.dest: Path = dest
        self.heading: str = heading
        self.content: str = content
        self.note_type: str = note_type
        self.vault_folder: str = vault_folder
        self.project: str = project


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_project_name(memory_dir: Path) -> str:
    """Extract a human-readable project name from a memory directory path.

    For per-project memory at ``~/.claude/projects/<encoded-path>/memory/``,
    the encoded path looks like ``-Users-username-Repos-myproject``. We extract
    the last segment (``myproject``).

    For global memory, returns an empty string.
    """
    # memory_dir is e.g. ~/.claude/projects/-Users-username-Repos-myproject/memory
    if memory_dir == GLOBAL_MEMORY_DIR:
        return ""

    # The project identifier is the parent of the memory/ dir
    project_dir: Path = memory_dir.parent
    encoded_name: str = project_dir.name

    m = _PROJECT_PATH_RE.match(encoded_name)
    if m:
        return m.group(1)

    # Fallback: use the full encoded name cleaned up
    return encoded_name.lstrip("-").replace("-", "/").rsplit("/", maxsplit=1)[-1]


def _classify_section(heading: str, body: str) -> tuple[str, str]:
    """Classify a section by its heading and body content.

    Returns ``(note_type, vault_folder)``.
    """
    text: str = (heading + " " + body).lower()

    for keywords, note_type, vault_folder in _SECTION_RULES:
        for keyword in keywords:
            if keyword in text:
                return note_type, vault_folder

    return DEFAULT_TYPE, DEFAULT_FOLDER


def _parse_sections(content: str) -> list[MemorySection]:
    """Parse ``##`` headings from markdown content into sections.

    Content before the first ``##`` heading is treated as a preamble section
    using the first ``#`` heading (if any) or ``Overview`` as the heading.
    """
    lines: list[str] = content.splitlines()
    sections: list[MemorySection] = []

    current_heading: str | None = None
    current_lines: list[str] = []
    preamble_title: str = "Overview"

    for line in lines:
        stripped: str = line.strip()

        # Capture the top-level title for context but don't make it a section
        if stripped.startswith("# ") and not stripped.startswith("## "):
            preamble_title = stripped.lstrip("#").strip()
            continue

        if stripped.startswith("## "):
            # Save previous section
            if current_heading is not None:
                body: str = "\n".join(current_lines).strip()
                if body:
                    sections.append(MemorySection(current_heading, body))
            elif current_lines:
                # Preamble content before any ## heading
                body = "\n".join(current_lines).strip()
                if body:
                    sections.append(MemorySection(preamble_title, body))

            current_heading = stripped.lstrip("#").strip()
            current_lines = []
        else:
            current_lines.append(line)

    # Don't forget the last section
    if current_heading is not None:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append(MemorySection(current_heading, body))
    elif current_lines:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append(MemorySection(preamble_title, body))

    return sections


def _build_frontmatter(
    note_type: str,
    tags: list[str],
    project: str,
) -> str:
    """Build a YAML frontmatter block string."""
    lines: list[str] = ["---"]
    lines.append(f"date: {date.today().isoformat()}")
    lines.append(f"type: {note_type}")

    tags_str: str = ", ".join(tags)
    lines.append(f"tags: [{tags_str}]")

    if project:
        lines.append(f"project: {project}")

    lines.append("confidence: medium")
    lines.append("sources: []")
    lines.append("related: []")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _build_note_content(
    heading: str,
    body: str,
    note_type: str,
    tags: list[str],
    project: str,
) -> str:
    """Build the full vault note content with frontmatter and body."""
    fm: str = _build_frontmatter(note_type, tags, project)
    return fm + "\n" + f"# {heading}\n\n" + body + "\n"


def _infer_tags(note_type: str, project: str, heading: str) -> list[str]:
    """Build a tag list from the note type, project, and heading."""
    tags: list[str] = ["memory", "migration"]

    # Add the note type as a tag
    if note_type and note_type not in tags:
        tags.append(note_type)

    # Add the project as a tag
    if project:
        project_tag: str = slugify(project)
        if project_tag and project_tag not in tags:
            tags.append(project_tag)

    # Extract extra tags from heading keywords
    heading_lower: str = heading.lower()
    keyword_tags: dict[str, str] = {
        "rust": "rust",
        "python": "python",
        "swift": "swift",
        "typescript": "typescript",
        "tmux": "tmux",
        "cursor": "cursor",
        "render": "rendering",
        "gpu": "gpu",
        "wgpu": "wgpu",
        "sync": "sync",
        "encrypt": "encryption",
        "api": "api",
        "ui": "ui",
        "swiftui": "swiftui",
        "automerge": "automerge",
        "ffi": "ffi",
        "vrm": "vrm",
        "sqlite": "sqlite",
        "macos": "macos",
    }
    for keyword, tag in keyword_tags.items():
        if keyword in heading_lower and tag not in tags:
            tags.append(tag)

    return tags


def _resolve_dest_collision(dest: Path) -> Path:
    """If *dest* already exists, append a numeric suffix to avoid collision."""
    if not dest.exists():
        return dest

    stem: str = dest.stem
    suffix: str = dest.suffix
    parent: Path = dest.parent
    counter: int = 2

    while True:
        candidate: Path = parent / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _discover_memory_dirs() -> list[Path]:
    """Find all memory directories: global + per-project."""
    dirs: list[Path] = []

    # Global memory
    if GLOBAL_MEMORY_DIR.is_dir():
        dirs.append(GLOBAL_MEMORY_DIR)

    # Per-project memory dirs
    if PROJECTS_DIR.is_dir():
        for project_dir in sorted(PROJECTS_DIR.iterdir()):
            if not project_dir.is_dir():
                continue
            memory_dir: Path = project_dir / "memory"
            if memory_dir.is_dir():
                dirs.append(memory_dir)

    return dirs


def _discover_memory_files(memory_dir: Path) -> list[Path]:
    """Find all ``.md`` files in a memory directory."""
    files: list[Path] = []

    if not memory_dir.is_dir():
        return files

    for item in sorted(memory_dir.iterdir()):
        if item.is_file() and item.suffix == ".md":
            files.append(item)

    return files


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------


def _process_memory_file(
    memory_file: Path,
    memory_dir: Path,
) -> list[VaultNote]:
    """Process a single memory file into vault notes.

    Each ``##`` section becomes a separate vault note, classified and placed
    in the appropriate vault folder.
    """
    project: str = _extract_project_name(memory_dir)

    try:
        content: str = memory_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"  WARNING: Cannot read {memory_file}: {exc}", file=sys.stderr)
        return []

    content = content.strip()
    if not content:
        print(f"  SKIP: {memory_file} (empty)")
        return []

    sections: list[MemorySection] = _parse_sections(content)

    if not sections:
        print(f"  SKIP: {memory_file} (no sections found)")
        return []

    notes: list[VaultNote] = []

    for section in sections:
        note_type, vault_folder = _classify_section(section.heading, section.body)
        section.note_type = note_type
        section.vault_folder = vault_folder

        tags: list[str] = _infer_tags(note_type, project, section.heading)

        # Build a unique filename from project (if any) and heading
        name_parts: list[str] = []
        if project:
            name_parts.append(project)
        name_parts.append(section.heading)
        filename: str = slugify("-".join(name_parts)) + ".md"

        dest: Path = VAULT_ROOT / vault_folder / filename
        dest = _resolve_dest_collision(dest)

        note_content: str = _build_note_content(
            heading=section.heading,
            body=section.body,
            note_type=note_type,
            tags=tags,
            project=project,
        )

        notes.append(
            VaultNote(
                source=memory_file,
                dest=dest,
                heading=section.heading,
                content=note_content,
                note_type=note_type,
                vault_folder=vault_folder,
                project=project,
            )
        )

    return notes


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _print_report(
    notes: list[VaultNote],
    files_processed: int,
    execute: bool,
) -> None:
    """Print a human-readable migration report."""
    mode: str = "EXECUTE" if execute else "DRY-RUN"
    print(f"\n{'=' * 72}")
    print(f"  Memory Migration Report ({mode})")
    print(f"{'=' * 72}\n")

    if notes:
        # Group by vault folder for clarity
        by_folder: dict[str, list[VaultNote]] = {}
        for note in notes:
            by_folder.setdefault(note.vault_folder, []).append(note)

        for folder in sorted(by_folder):
            folder_notes: list[VaultNote] = by_folder[folder]
            print(f"--- {folder}/ ({len(folder_notes)} notes) ---\n")

            for note in folder_notes:
                try:
                    src_rel: str = str(note.source.relative_to(CLAUDE_DIR))
                except ValueError:
                    src_rel = str(note.source)
                try:
                    dst_rel: str = str(note.dest.relative_to(VAULT_ROOT))
                except ValueError:
                    dst_rel = str(note.dest)

                project_label: str = f"  project={note.project}" if note.project else ""
                print(f"  [{note.heading}]")
                print(f"    source: {src_rel}")
                print(f"    -> {dst_rel}")
                print(f"       type={note.note_type}{project_label}")
                print()
    else:
        print("  No vault notes to create.\n")

    # Summary
    print(f"{'=' * 72}")
    print(
        f"  Summary: {files_processed} memory files processed, "
        f"{len(notes)} vault notes created"
    )
    if not execute:
        print("  Mode: DRY-RUN (no files written). Use --execute to migrate.")
    else:
        print("  Mode: EXECUTE (files written, originals backed up).")
    print(f"{'=' * 72}\n")


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _execute_migration(notes: list[VaultNote], sources_to_backup: set[Path]) -> int:
    """Write vault notes and back up originals. Returns count of notes written."""
    written: int = 0

    for note in notes:
        note.dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            note.dest.write_text(note.content, encoding="utf-8")
            written += 1
        except OSError as exc:
            print(f"  ERROR writing {note.dest}: {exc}", file=sys.stderr)

    # Back up original memory files
    backed_up: int = 0
    for src in sorted(sources_to_backup):
        bak: Path = src.with_suffix(src.suffix + ".bak")
        try:
            src.rename(bak)
            backed_up += 1
            print(f"  Backed up: {src.name} -> {bak.name}")
        except OSError as exc:
            print(f"  ERROR backing up {src}: {exc}", file=sys.stderr)

    if backed_up:
        print(f"  {backed_up} original files renamed to .bak")

    return written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point for the memory migration script."""
    parser = argparse.ArgumentParser(
        description=(
            "Migrate ~/.claude/memory/ contents (built-in auto memory) "
            "to the resolved vault with section parsing and classification."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Actually write files to the vault and back up originals. Default is dry-run.",
    )
    args: argparse.Namespace = parser.parse_args()

    # Ensure vault structure exists
    ensure_vault_dirs()

    # Discover memory directories
    memory_dirs: list[Path] = _discover_memory_dirs()

    if not memory_dirs:
        print("No memory directories found. Nothing to migrate.")
        return

    print(f"Discovered {len(memory_dirs)} memory directory(ies):\n")
    for d in memory_dirs:
        try:
            label: str = str(d.relative_to(CLAUDE_DIR))
        except ValueError:
            label = str(d)
        print(f"  {label}")
    print()

    # Process all memory files
    all_notes: list[VaultNote] = []
    files_processed: int = 0
    sources_to_backup: set[Path] = set()

    print("Processing memory files...\n")

    for memory_dir in memory_dirs:
        memory_files: list[Path] = _discover_memory_files(memory_dir)

        if not memory_files:
            continue

        project: str = _extract_project_name(memory_dir)
        dir_label: str = f"[{project}]" if project else "[global]"
        print(f"  {dir_label} {memory_dir}")

        for memory_file in memory_files:
            files_processed += 1
            notes: list[VaultNote] = _process_memory_file(memory_file, memory_dir)

            if notes:
                all_notes.extend(notes)
                sources_to_backup.add(memory_file)
                print(f"    {memory_file.name}: {len(notes)} section(s)")
            else:
                print(f"    {memory_file.name}: 0 sections (skipped)")

    print()

    # Report
    _print_report(all_notes, files_processed, execute=args.execute)

    # Execute if requested
    if args.execute:
        if not all_notes:
            print("Nothing to write.")
            return

        print("Writing vault notes...")
        written: int = _execute_migration(all_notes, sources_to_backup)
        print(f"\nMigration complete. {written} vault notes written to {VAULT_ROOT}")


if __name__ == "__main__":
    main()
