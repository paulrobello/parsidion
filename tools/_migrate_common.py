"""Shared helpers for the two one-time vault migration scripts (QA-018).

``migrate_memory.py`` and ``migrate_research.py`` duplicated the report
banner/footer scaffold, the write-with-error-reporting loop, and the
keyword-tag scan. ARC-005 already moved their frontmatter building onto
``core.vault_index.serialize_frontmatter``; this module is the remaining
shared surface.

Stdlib-only, like both consumers.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPORT_WIDTH = 72


def report_header(title: str, execute: bool) -> None:
    """Print the opening banner of a migration report."""
    mode: str = "EXECUTE" if execute else "DRY-RUN"
    print(f"\n{'=' * _REPORT_WIDTH}")
    print(f"  {title} ({mode})")
    print(f"{'=' * _REPORT_WIDTH}\n")


def report_footer(summary: str, execute: bool, execute_note: str) -> None:
    """Print the summary box closing a migration report.

    Args:
        summary: One-line summary sentence (counts).
        execute: True when files are written in this mode.
        execute_note: Short description of what EXECUTE mode does, e.g.
            ``"files written, originals backed up"``.
    """
    print(f"{'=' * _REPORT_WIDTH}")
    print(f"  Summary: {summary}")
    if not execute:
        print("  Mode: DRY-RUN (no files written). Use --execute to migrate.")
    else:
        print(f"  Mode: EXECUTE ({execute_note}).")
    print(f"{'=' * _REPORT_WIDTH}\n")


def write_note_file(dest: Path, content: str) -> bool:
    """Create *dest*'s parent dirs and write the note; report errors.

    Returns True on success; an OSError prints one stderr line and returns
    False (a single failed note must not abort the batch).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.write_text(content, encoding="utf-8")
        return True
    except OSError as exc:
        print(f"  ERROR writing {dest}: {exc}", file=sys.stderr)
        return False


def append_keyword_tags(
    tags: list[str],
    keyword_tags: dict[str, str],
    text_lower: str,
) -> list[str]:
    """Append tags for every keyword present in *text_lower* (in dict order).

    Shared tail of both scripts' ``_infer_tags``: scan already-lowercased
    text against a keyword→tag map, skipping duplicates.
    """
    for keyword, tag in keyword_tags.items():
        if keyword in text_lower and tag not in tags:
            tags.append(tag)
    return tags
