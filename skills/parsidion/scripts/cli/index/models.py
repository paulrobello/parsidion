"""NamedTuples shared across the cli.index subpackage (ARC-005).

Extracted from ``update_index.py``. Re-exported by the entry shim so
``update_index.NoteEntry`` and ``update_index.NoteRecord`` keep resolving
for tests and other callers.

Stdlib-only at module load.
"""

from __future__ import annotations

from typing import NamedTuple


class NoteEntry(NamedTuple):
    """Per-note metadata row for the note_index SQLite table.

    Used as the element type in the ``db_rows`` list returned by
    ``build_index()`` and consumed by ``_write_note_index_to_db()``.

    Attributes:
        stem: Filename without extension (primary key in note_index).
        path: Absolute path string to the note file.
        folder: Immediate parent folder name relative to VAULT_ROOT.
        title: First ``#`` heading text, or filename stem as fallback.
        summary: First non-heading body line, truncated to 80 chars.
        tags: Comma-separated tag string (canonical: sorted, ", " delimiter).
        note_type: ``type`` frontmatter value.
        project: ``project`` frontmatter value.
        confidence: ``confidence`` frontmatter value.
        mtime: File modification timestamp (float seconds since epoch).
        related: Comma-separated wikilink stems from ``related`` frontmatter.
        is_stale: 1 if the note has no incoming links and is >30 days old, else 0.
        incoming_links: Number of other notes that link to this one.
        date: ``date`` frontmatter value (``YYYY-MM-DD`` string) for point-in-time search.
        prompt_version: ``prompt_version`` frontmatter value (``<id>@<semver>``, e.g.
            ``summarize-session@1.0.0``) for AI-generated notes. Empty for notes
            written by hand or by older summarizer versions. Lets evaluation slice
            note quality by the prompt that produced it (ENH-008 Step 3).
        incoming_stems: ENH-021 -- JSON array of the sorted stems whose
            ``related`` field links to this note (the reverse-link adjacency,
            inverted at index time). ``"[]"`` when no note links here.
    """

    stem: str
    path: str
    folder: str
    title: str
    summary: str
    tags: str
    note_type: str
    project: str
    confidence: str
    mtime: float
    related: str
    is_stale: int
    incoming_links: int
    date: str = ""
    prompt_version: str = ""
    incoming_stems: str = ""


class NoteRecord(NamedTuple):
    """First-pass parsed fields for a single note.

    Produced by ``_parse_note_record`` and consumed by ``build_index`` to
    update its accumulators (``tag_counter``, ``per_note_data``,
    ``note_contents``, ``recent_notes``).
    """

    content: str
    frontmatter: dict[str, object]
    title: str
    summary: str
    folder: str
    tags: list[str]
    mtime: float
    related_stems: list[str]
