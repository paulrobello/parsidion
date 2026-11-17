"""Two-pass index assembly (ARC-005).

Extracted from ``update_index.py``. Re-exported by the entry shim so
``update_index.build_index``, ``update_index._compute_incoming_link_counts``,
and ``update_index._build_note_db_rows`` keep resolving for tests and other
callers.

ARC-2XX (cards: hoist the row engine into core + docs from note_index): the
row builder, link inverses and row/parse NamedTuples now live in
``parsidion.core.note_index`` and the Markdown emitters in
``parsidion.core.note_docs``; this module re-exports the moved names and
keeps ``build_index`` -- the parse-based generation that accumulates the
legacy doc structures alongside the rows. It is the byte-equivalence seam's
parse side: production docs come from the note_index rows (see
``update_index.main``), and this path stays callable for the equivalence
test and as the no-DB fallback.

Stdlib-only at module load.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from parsidion.core.note_docs import build_claude_md
from parsidion.core.note_index import (
    NoteEntry,
    _build_note_db_rows,
    _compute_incoming_link_counts,
    _compute_incoming_link_stems,
    _normalize_tag_tokens,
)
from parsidion.core.vault_fs import ensure_vault_dirs
from parsidion.core.vault_index import all_vault_notes_walk

from parsidion.cli.index._common import (
    RECENT_DAYS,
    RECENT_MAX,
    STALE_DAYS,
)


def build_index(
    vault: Path,
    *,
    now: datetime | None = None,
) -> tuple[
    str,
    int,
    int,
    dict[str, list[tuple[str, str, str, list[str], bool]]],
    list[NoteEntry],
    Counter[str],
]:
    """Build the full CLAUDE.md index content by parsing the vault.

    The parse-based doc generation: walks and parses every note, accumulates
    the legacy doc structures (tag counter, recent list, per-folder manifest
    entries) and the NoteEntry rows, and assembles CLAUDE.md through
    :func:`parsidion.core.note_docs.build_claude_md`. Production docs come
    from the note_index rows instead (``update_index.main`` renders via
    :func:`render_docs_from_rows`), but this path stays callable: it is the
    byte-equivalence test's parse side and the fallback when no
    ``note_index`` table exists yet.

    The walk is sorted and tags are counted per note in sorted order so the
    output is deterministic (``os.walk`` order is not) and byte-identical to
    the row-driven generation -- most_common() tie order agrees because both
    sides insert tags in the same canonical sequence.

    Args:
        vault: Vault root.
        now: Run timestamp; None uses the current time. The equivalence test
            passes a fixed ``now`` so both doc generations share cutoffs and
            header stamps.

    Returns:
        A tuple of (index_content, note_count, tag_count, folder_notes_extended, db_rows, tag_counter).
        folder_notes_extended maps folder name to a list of
        (wikilink, title, summary, tags, is_stale) tuples.
        db_rows is a list of NoteEntry records ready to upsert into the note_index table.
        tag_counter is the Counter[str] used to build TAGS.md separately.
    """
    ensure_vault_dirs(vault=vault)
    notes: list[Path] = sorted(all_vault_notes_walk(vault=vault))

    now = now or datetime.now()
    cutoff_ts: float = (now - timedelta(days=RECENT_DAYS)).timestamp()
    stale_cutoff_ts: float = (now - timedelta(days=STALE_DAYS)).timestamp()

    # Late import: ``_parse_note_record`` stays in the entry shim because
    # ``tests/test_index_enhancements.py`` and ``tests/test_parser_index_fixes.py``
    # patch ``update_index.parse_frontmatter`` and the bare-name call from
    # inside ``_parse_note_record`` must resolve through the patched module's
    # globals. Importing it lazily here keeps the patch surface intact without
    # a circular import (the shim is fully loaded by the time build_index runs).
    import update_index as _ui  # noqa: PLC0415

    # Collected data per note
    tag_counter: Counter[str] = Counter()
    recent_notes: list[
        tuple[float, Path, str, str]
    ] = []  # (mtime, path, folder, summary)

    # Per-note data needed for staleness: stem -> (mtime, related_stems)
    # We collect this in the first pass and compute link_count after.
    per_note_data: dict[
        str, tuple[float, list[str]]
    ] = {}  # stem -> (mtime, related_stems)

    # First pass: read all notes, collect data
    note_contents: dict[
        Path, tuple[str, dict[str, object], str, str, str, float, list[str]]
    ] = {}
    # path -> (content, fm, title, summary, folder, mtime, tags_list)

    for note_path in notes:
        record = _ui._parse_note_record(note_path, vault)
        if record is None:
            continue

        for tag in sorted(_normalize_tag_tokens(record.tags)):
            tag_counter[tag] += 1
        per_note_data[note_path.stem] = (record.mtime, record.related_stems)
        note_contents[note_path] = (
            record.content,
            record.frontmatter,
            record.title,
            record.summary,
            record.folder,
            record.mtime,
            record.tags,
        )

        if record.mtime >= cutoff_ts:
            recent_notes.append(
                (record.mtime, note_path, record.folder, record.summary)
            )

    # Build reverse link count: stem -> number of incoming wikilinks
    link_count: dict[str, int] = _compute_incoming_link_counts(per_note_data)
    # ENH-021: reverse link adjacency, persisted per row (update_index always
    # rebuilds every row, so a full in-memory re-inversion each run keeps the
    # column correct across incremental rebuilds too).
    incoming_stems: dict[str, list[str]] = _compute_incoming_link_stems(per_note_data)

    # Second pass: group by folder, compute staleness, collect DB rows
    db_rows, folder_notes, stale_count = _build_note_db_rows(
        note_contents, per_note_data, link_count, incoming_stems, stale_cutoff_ts
    )

    # Sort recent by mtime descending, limit
    recent_notes.sort(key=lambda x: x[0], reverse=True)
    recent_notes = recent_notes[:RECENT_MAX]

    # Sort notes within each folder alphabetically by wikilink
    for folder in folder_notes:
        folder_notes[folder].sort(key=lambda x: x[0].lower())

    total_notes: int = len(notes)
    total_tags: int = len(tag_counter)

    # CLAUDE.md content: the extracted core emitter (parse side of the
    # byte-equivalence seam; TAGS.md / MANIFEST.md build from the same
    # structures in the caller).
    content: str = build_claude_md(
        vault,
        now.strftime("%Y-%m-%d %H:%M"),
        total_notes=total_notes,
        stale_count=stale_count,
        tag_counter=tag_counter,
        recent_notes=recent_notes,
        folder_notes=folder_notes,
    )

    return content, total_notes, total_tags, folder_notes, db_rows, tag_counter
