"""Two-pass index assembly (ARC-005).

Extracted from ``update_index.py``. Re-exported by the entry shim so
``update_index.build_index``, ``update_index._compute_incoming_link_counts``,
and ``update_index._build_note_db_rows`` keep resolving for tests and other
callers.

Stdlib-only at module load.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from core.vault_fs import ensure_vault_dirs
from core.vault_index import all_vault_notes_walk

from cli.index._common import FOLDER_ORDER, RECENT_DAYS, RECENT_MAX, STALE_DAYS
from cli.index.models import NoteEntry
from cli.index.parse import _wikilink


def _compute_incoming_link_counts(
    per_note_data: dict[str, tuple[float, list[str]]],
) -> dict[str, int]:
    """Build the reverse link count: stem -> number of incoming wikilinks."""
    link_count: dict[str, int] = {stem: 0 for stem in per_note_data}
    for _stem, (_, related_stems) in per_note_data.items():
        for target_stem in related_stems:
            if target_stem in link_count:
                link_count[target_stem] += 1
    return link_count


def _build_note_db_rows(
    note_contents: dict[
        Path, tuple[str, dict[str, object], str, str, str, float, list[str]]
    ],
    per_note_data: dict[str, tuple[float, list[str]]],
    link_count: dict[str, int],
    stale_cutoff_ts: float,
) -> tuple[
    list[NoteEntry],
    dict[str, list[tuple[str, str, str, list[str], bool]]],
    int,
]:
    """Second pass: compute staleness, build NoteEntry rows, group by folder.

    Returns ``(db_rows, folder_notes, stale_count)``.
    """
    stale_count: int = 0
    db_rows: list[NoteEntry] = []
    # Extended folder_notes: folder -> [(wikilink, title, summary, tags, is_stale)]
    folder_notes: dict[str, list[tuple[str, str, str, list[str], bool]]] = {}
    for note_path, (
        _content,
        fm,
        title,
        summary,
        folder,
        mtime,
        tags_list,
    ) in note_contents.items():
        stem: str = note_path.stem
        incoming: int = link_count.get(stem, 0)
        is_stale: bool = incoming == 0 and mtime < stale_cutoff_ts
        if is_stale:
            stale_count += 1

        db_rows.append(
            NoteEntry(
                stem=stem,
                path=str(note_path),
                folder=folder,
                title=title,
                summary=summary,
                # ARC-004: canonical tag format is ", ".join(sorted(tags)) — sorted
                # alphabetically with a single space after each comma.  This ensures
                # consistent LIKE matching in query_note_index and vault_search.py.
                tags=", ".join(sorted(tags_list)),
                note_type=str(fm.get("type", "") or ""),
                project=str(fm.get("project", "") or ""),
                confidence=str(fm.get("confidence", "") or ""),
                mtime=mtime,
                related=", ".join(per_note_data.get(stem, (0.0, []))[1]),
                is_stale=1 if is_stale else 0,
                incoming_links=incoming,
                date=str(fm.get("date", "") or ""),
                prompt_version=str(fm.get("prompt_version", "") or ""),
            )
        )

        if folder:
            folder_notes.setdefault(folder, []).append(
                (_wikilink(note_path), title, summary, tags_list, is_stale)
            )

    return db_rows, folder_notes, stale_count


def build_index(
    vault: Path,
) -> tuple[
    str,
    int,
    int,
    dict[str, list[tuple[str, str, str, list[str], bool]]],
    list[NoteEntry],
    Counter[str],
]:
    """Build the full CLAUDE.md index content.

    Returns:
        A tuple of (index_content, note_count, tag_count, folder_notes_extended, db_rows, tag_counter).
        folder_notes_extended maps folder name to a list of
        (wikilink, title, summary, tags, is_stale) tuples.
        db_rows is a list of NoteEntry records ready to upsert into the note_index table.
        tag_counter is the Counter[str] used to build TAGS.md separately.
    """
    ensure_vault_dirs(vault=vault)
    notes: list[Path] = all_vault_notes_walk()

    now: datetime = datetime.now()
    now_str: str = now.strftime("%Y-%m-%d %H:%M")
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

        for tag in record.tags:
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

    # Second pass: group by folder, compute staleness, collect DB rows
    db_rows, folder_notes, stale_count = _build_note_db_rows(
        note_contents, per_note_data, link_count, stale_cutoff_ts
    )

    # Sort recent by mtime descending, limit
    recent_notes.sort(key=lambda x: x[0], reverse=True)
    recent_notes = recent_notes[:RECENT_MAX]

    # Sort notes within each folder alphabetically by wikilink
    for folder in folder_notes:
        folder_notes[folder].sort(key=lambda x: x[0].lower())

    total_notes: int = len(notes)
    total_tags: int = len(tag_counter)

    # --- Build output ---
    lines: list[str] = []
    lines.append("# Parsidion vault Index")
    lines.append("")
    lines.append(f"> Auto-generated by update_index.py on {now_str}")
    lines.append("> Do not edit manually - changes will be overwritten")
    lines.append("")

    # Quick Stats
    lines.append("## Quick Stats")
    lines.append(f"- **Total notes**: {total_notes}")
    lines.append(f"- **Last updated**: {now_str}")
    lines.append(
        f"- Stale notes (no incoming links, >{STALE_DAYS} days): {stale_count}"
    )

    # Doctor state summary
    state_file: Path = vault / "doctor_state.json"
    try:
        state_data: dict = json.loads(state_file.read_text(encoding="utf-8"))
        last_run: str | None = state_data.get("last_run")
        notes_state: dict = state_data.get("notes", {})
        counts: Counter[str] = Counter(
            v.get("status", "unknown") for v in notes_state.values()
        )
        ok_count = counts.get("ok", 0) + counts.get("fixed", 0)
        pending_count = counts.get("failed", 0) + counts.get("timeout", 0)
        review_count = counts.get("needs_review", 0)
        skipped_count = counts.get("skipped", 0)
        run_str = last_run[:10] if last_run else "never"
        parts = [f"{ok_count} clean", f"{pending_count} pending repair"]
        if review_count:
            parts.append(f"**{review_count} need user review**")
        if skipped_count:
            parts.append(f"{skipped_count} manual fix needed")
        lines.append(f"- **Vault health** (doctor run: {run_str}): {', '.join(parts)}")
    except (OSError, json.JSONDecodeError, KeyError):
        pass  # doctor has not been run yet

    lines.append("")

    # Conventions (always emitted so they survive index rebuilds)
    lines.append("## Conventions")
    lines.append("")
    lines.append(
        "- **Frontmatter required** on every note (date, type, tags, confidence, sources, related)."
    )
    lines.append(
        "- **Kebab-case filenames**, 3-5 words, no date suffix — date goes in frontmatter."
    )
    lines.append(
        "- **No orphan notes** — every note must link to at least one other note via `related`."
    )
    lines.append(
        "- **Search before create** — update existing notes rather than creating duplicates."
    )
    lines.append(
        "- **Subfolder rule** — when 3 or more notes share a common subject prefix, move them"
    )
    lines.append(
        "  into a named subfolder. Drop the redundant prefix from filenames inside the folder."
    )
    lines.append(
        "  Only one level of subfolder is allowed — never nest subfolders within subfolders."
    )
    lines.append(
        "  Example: `Research/fastapi-middleware-basics.md` + `fastapi-middleware-auth.md` + `fastapi-middleware-cors.md`"
    )
    lines.append("  → `Research/fastapi-middleware/basics.md`, `auth.md`, `cors.md`.")
    lines.append(
        "  Update all `[[wikilinks]]` and run `update_index.py` after reorganizing."
    )
    lines.append("")

    # Top tags (compact — full tag cloud is in TAGS.md)
    top_tags: list[tuple[str, int]] = tag_counter.most_common(20)
    if top_tags:
        tag_parts = [f"`{tag}` ({count})" for tag, count in top_tags]
        lines.append("## Top Tags")
        lines.append(" | ".join(tag_parts))
        lines.append(f"_Full tag list ({total_tags} tags) → see [[TAGS]]_")
    lines.append("")

    # Recent Activity
    lines.append(f"## Recent Activity ({RECENT_DAYS} days)")
    if recent_notes:
        for _mtime, note_path, folder, summary in recent_notes:
            wlink: str = _wikilink(note_path)
            folder_label: str = f" ({folder})" if folder else ""
            summary_label: str = f" - {summary}" if summary else ""
            lines.append(f"- {wlink}{folder_label}{summary_label}")
    else:
        lines.append("_No recent activity._")
    lines.append("")

    # Folder summary (counts + pointers to MANIFEST.md files)
    lines.append("## Folders")
    lines.append("")
    for folder_name_str in FOLDER_ORDER:
        count: int = len(folder_notes.get(folder_name_str, []))
        lines.append(
            f"- **{folder_name_str}** ({count} notes) → see `{folder_name_str}/MANIFEST.md`"
        )
    extra_folders: list[str] = sorted(f for f in folder_notes if f not in FOLDER_ORDER)
    for folder_name_str in extra_folders:
        count = len(folder_notes[folder_name_str])
        lines.append(
            f"- **{folder_name_str}** ({count} notes) → see `{folder_name_str}/MANIFEST.md`"
        )
    lines.append("")

    return "\n".join(lines), total_notes, total_tags, folder_notes, db_rows, tag_counter
