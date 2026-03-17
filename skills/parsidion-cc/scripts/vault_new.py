#!/usr/bin/env python3
"""CLI tool to scaffold a new vault note with correct frontmatter.

Creates a new Markdown note in the appropriate vault folder based on note type,
generates required YAML frontmatter, and optionally opens the note in $EDITOR.
"""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

# These scripts are not a proper package — sys.path.insert is intentional so
# each script can run standalone via ``uv run`` or ``python`` without requiring
# pip install or editable installs.  See ARC-009 in AUDIT.md.
# SEC-011: SHADOWING RISK — a ``vault_common.py`` in the process cwd at hook
# invocation time would shadow the real module.  Accepted risk under the
# stdlib-only constraint; proper packaging would eliminate it.
sys.path.insert(0, str(Path(__file__).parent))

import vault_common  # noqa: E402

# Mapping from note type to vault folder name
_TYPE_TO_FOLDER: dict[str, str] = {
    "pattern": "Patterns",
    "debugging": "Debugging",
    "research": "Research",
    "project": "Projects",
    "tool": "Tools",
    "language": "Languages",
    "framework": "Frameworks",
}

# Maximum recommended slug word count before truncation warning
_MAX_SLUG_WORDS = 5


def _build_slug(title: str) -> str:
    """Build a filename slug from a title, truncating if it exceeds 5 words.

    Args:
        title: The note title to convert into a slug.

    Returns:
        A kebab-case slug derived from the title.
    """
    words = title.split()
    if len(words) > _MAX_SLUG_WORDS:
        print(
            f"Warning: title has {len(words)} words; truncating slug to first {_MAX_SLUG_WORDS} words.",
            file=sys.stderr,
        )
        title = " ".join(words[:_MAX_SLUG_WORDS])
    return vault_common.slugify(title)


def _build_frontmatter(
    note_type: str,
    tags: list[str],
    project: str | None,
) -> str:
    """Generate YAML frontmatter for a new vault note.

    Args:
        note_type: The note type (pattern, debugging, research, etc.).
        tags: List of tag strings to include.
        project: Optional project name.

    Returns:
        A multi-line string containing the YAML frontmatter block.
    """
    today = date.today().strftime("%Y-%m-%d")

    # Build tags inline list
    if tags:
        tags_str = "[" + ", ".join(tags) + "]"
    else:
        tags_str = "[]"

    lines = [
        "---",
        f"date: {today}",
        f"type: {note_type}",
        f"tags: {tags_str}",
    ]

    if project:
        lines.append(f"project: {project}")

    lines.extend(
        [
            "confidence: medium",
            "sources: []",
            'related: ["[[vault-index]]"]',
            "---",
        ]
    )

    return "\n".join(lines) + "\n"


def _build_note_content(
    title: str, note_type: str, tags: list[str], project: str | None
) -> str:
    """Build the complete note content including frontmatter and body.

    Args:
        title: The note title.
        note_type: The note type.
        tags: List of tag strings.
        project: Optional project name.

    Returns:
        Complete note content as a string.
    """
    frontmatter = _build_frontmatter(note_type, tags, project)
    body = f"\n# {title}\n\n<!-- Add content here -->\n"
    return frontmatter + body


def main() -> None:
    """Entry point: parse arguments and create a new vault note."""
    parser = argparse.ArgumentParser(
        description="Scaffold a new Claude Vault note with correct frontmatter.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  vault-new --type pattern --title "My Useful Pattern" --tags "python,hooks"
  vault-new -t debugging -T "Fix SQLite WAL mode" -p my-project --open
  vault-new --type research --title "FastAPI Middleware" --dry-run
""",
    )
    parser.add_argument(
        "--type",
        "-t",
        required=True,
        choices=list(_TYPE_TO_FOLDER.keys()),
        metavar="TYPE",
        help=(
            "Note type — determines the target folder. "
            "Choices: " + ", ".join(_TYPE_TO_FOLDER.keys())
        ),
    )
    parser.add_argument(
        "--title",
        "-T",
        required=True,
        metavar="TITLE",
        help="Note title (used to generate the filename slug).",
    )
    parser.add_argument(
        "--project",
        "-p",
        default=None,
        metavar="PROJECT",
        help="Project name to embed in frontmatter (optional).",
    )
    parser.add_argument(
        "--tags",
        "-g",
        default=None,
        metavar="TAGS",
        help="Comma-separated list of tags, e.g. 'python,hooks,sqlite'.",
    )
    parser.add_argument(
        "--open",
        "-o",
        action="store_true",
        default=False,
        help="Open the new note in $EDITOR after creation.",
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        default=False,
        help="Print what would be created without writing anything.",
    )

    args = parser.parse_args()

    # Parse tags
    tags: list[str] = []
    if args.tags:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    # Build slug and target path
    slug = _build_slug(args.title)
    folder_name = _TYPE_TO_FOLDER[args.type]
    target_dir = vault_common.VAULT_ROOT / folder_name

    # Ensure vault directories exist (unless dry-run)
    if not args.dry_run:
        vault_common.ensure_vault_dirs()

    # Verify the target directory exists
    if not target_dir.exists() and not args.dry_run:
        print(
            f"Error: target directory does not exist: {target_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    target_path = target_dir / f"{slug}.md"

    # Check for duplicates
    if not args.dry_run and target_path.exists():
        print(
            f"Error: note already exists: {target_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Build note content
    content = _build_note_content(args.title, args.type, tags, args.project)

    if args.dry_run:
        print(f"[dry-run] Would create: {target_path.resolve()}")
        print()
        print(content)
        return

    # Write the note
    target_path.write_text(content, encoding="utf-8")
    print(str(target_path.resolve()))

    # Open in editor if requested
    if args.open:
        editor = os.environ.get("EDITOR", "")
        if editor:
            os.system(f'{editor} "{target_path}"')  # noqa: S605
        else:
            print(
                "Warning: $EDITOR is not set; cannot open note.",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
