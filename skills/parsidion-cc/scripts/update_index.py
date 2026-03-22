#!/usr/bin/env python3
"""Rebuild the ~/ClaudeVault/CLAUDE.md index file.

Walks the vault tree, parses frontmatter from all notes, and generates a
comprehensive index with tag cloud, recent activity, and per-folder listings.
Also generates per-folder MANIFEST.md files for quick orientation.
Uses only Python stdlib.
"""

import argparse
import atexit
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

# These scripts are not a proper package — sys.path.insert is intentional so
# each script can run standalone via ``uv run`` or ``python`` without requiring
# pip install or editable installs.  See ARC-009 in AUDIT.md.
sys.path.insert(0, str(Path(__file__).parent))

from vault_common import (
    VAULT_ROOT,
    all_vault_notes,
    ensure_vault_dirs,
    extract_title,
    get_body,
    get_config,
    get_embeddings_db_path,
    git_commit_vault,
    parse_frontmatter,
)

# Canonical folder order for index sections
FOLDER_ORDER: list[str] = [
    "Daily",
    "Projects",
    "Languages",
    "Frameworks",
    "Patterns",
    "Debugging",
    "Tools",
    "Research",
    "History",
]

RECENT_DAYS: int = 7
RECENT_MAX: int = 20
SUMMARY_MAX_CHARS: int = 80
STALE_DAYS: int = 30

PID_FILE: Path = VAULT_ROOT / "index.pid"

# Regex to extract wikilink stems like [[note-stem]] from a string
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


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


def _is_process_running(pid: int) -> bool:
    """Return True if a process with *pid* is currently running."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists; we lack permission to signal it


def _write_pid() -> None:
    """Write current PID to the PID file."""
    tmp = PID_FILE.with_suffix(".pid.tmp")
    tmp.write_text(str(os.getpid()), encoding="utf-8")
    tmp.replace(PID_FILE)


def _release_pid() -> None:
    """Remove the PID file at process exit."""
    try:
        if PID_FILE.exists() and PID_FILE.read_text(encoding="utf-8").strip() == str(
            os.getpid()
        ):
            PID_FILE.unlink()
    except Exception:  # noqa: BLE001
        pass  # best-effort cleanup


def _singleton_guard() -> None:
    """Exit early if another update_index is already running."""
    try:
        existing_pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        existing_pid = None

    if (
        existing_pid
        and existing_pid != os.getpid()
        and _is_process_running(existing_pid)
    ):
        print(
            f"update_index is already running (PID {existing_pid}). Exiting.",
            file=sys.stderr,
        )
        sys.exit(0)

    _write_pid()
    atexit.register(_release_pid)


def _extract_title(content: str, filename_stem: str) -> str:
    """Extract the title from the first ``#`` heading in the body, falling back to filename.

    Delegates to ``vault_common.extract_title`` — the canonical implementation.
    See ARC-009.
    """
    return extract_title(content, filename_stem)


def _extract_summary(content: str) -> str:
    """Return the first non-empty, non-heading, non-comment body line, truncated to 80 chars."""
    body: str = get_body(content)
    for line in body.splitlines():
        stripped: str = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("<!--"):
            continue
        if len(stripped) > SUMMARY_MAX_CHARS:
            return stripped[: SUMMARY_MAX_CHARS - 3] + "..."
        return stripped
    return ""


def _folder_name(note_path: Path) -> str:
    """Return the immediate parent folder name relative to VAULT_ROOT.

    For notes directly in VAULT_ROOT, returns an empty string.
    """
    try:
        rel: Path = note_path.relative_to(VAULT_ROOT)
    except ValueError:
        return ""
    parts: tuple[str, ...] = rel.parts
    if len(parts) <= 1:
        return ""
    return parts[0]


def _wikilink(note_path: Path) -> str:
    """Return a wikilink ``[[stem]]`` for the note."""
    return f"[[{note_path.stem}]]"


def _extract_wikilink_stems(related: object) -> list[str]:
    """Extract note stems from a ``related`` frontmatter field.

    The field is expected to be a list of strings like ``["[[note-a]]", "[[note-b]]"]``,
    but also handles bare wikilinks and plain strings.

    Args:
        related: The value of the ``related`` frontmatter field.

    Returns:
        A list of note stem strings (without brackets).
    """
    stems: list[str] = []
    if not isinstance(related, list):
        return stems
    for item in related:
        if not isinstance(item, str):
            continue
        # Extract all [[stem]] patterns from the item
        found = _WIKILINK_RE.findall(item)
        if found:
            stems.extend(found)
        else:
            # Bare string (no brackets) — treat as a stem directly
            stripped = item.strip()
            if stripped:
                stems.append(stripped)
    return stems


def build_index() -> tuple[
    str,
    int,
    int,
    dict[str, list[tuple[str, str, str, list[str], bool]]],
    list[NoteEntry],
]:
    """Build the full CLAUDE.md index content.

    Returns:
        A tuple of (index_content, note_count, tag_count, folder_notes_extended, db_rows).
        folder_notes_extended maps folder name to a list of
        (wikilink, title, summary, tags, is_stale) tuples.
        db_rows is a list of NoteEntry records ready to upsert into the note_index table.
    """
    ensure_vault_dirs()
    # Filter out MANIFEST.md files — they are auto-generated and should not be
    # indexed as vault notes.
    notes: list[Path] = [p for p in all_vault_notes() if p.name != "MANIFEST.md"]

    now: datetime = datetime.now()
    now_str: str = now.strftime("%Y-%m-%d %H:%M")
    cutoff_ts: float = (now - timedelta(days=RECENT_DAYS)).timestamp()
    stale_cutoff_ts: float = (now - timedelta(days=STALE_DAYS)).timestamp()

    # Collected data per note
    tag_counter: Counter[str] = Counter()
    recent_notes: list[
        tuple[float, Path, str, str]
    ] = []  # (mtime, path, folder, summary)

    # Extended folder_notes: folder -> [(wikilink, title, summary, tags, is_stale)]
    folder_notes: dict[str, list[tuple[str, str, str, list[str], bool]]] = {}

    # Per-note data needed for staleness: stem -> (mtime, related_stems)
    # We collect this in the first pass and compute link_count after.
    per_note_data: dict[
        str, tuple[float, list[str]]
    ] = {}  # stem -> (mtime, related_stems)

    # First pass: read all notes, collect data
    note_contents: dict[Path, tuple[str, dict, str, str, str, float, list[str]]] = {}
    # path -> (content, fm, title, summary, folder, mtime, tags_list)

    for note_path in notes:
        try:
            content: str = note_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        fm: dict[str, object] = parse_frontmatter(content)
        title: str = _extract_title(content, note_path.stem)
        summary: str = _extract_summary(content)
        folder: str = _folder_name(note_path)

        # Collect tags
        tags_raw: object = fm.get("tags")
        tags_list: list[str] = []
        if isinstance(tags_raw, list):
            for tag in tags_raw:
                if isinstance(tag, str) and tag:
                    tag_counter[tag] += 1
                    tags_list.append(tag)

        # Mtime
        try:
            mtime: float = note_path.stat().st_mtime
        except OSError:
            mtime = 0.0

        # Collect wikilink stems from related field
        related_stems: list[str] = _extract_wikilink_stems(fm.get("related"))

        per_note_data[note_path.stem] = (mtime, related_stems)
        note_contents[note_path] = (
            content,
            fm,
            title,
            summary,
            folder,
            mtime,
            tags_list,
        )

        if mtime >= cutoff_ts:
            recent_notes.append((mtime, note_path, folder, summary))

    # Build reverse link count: stem -> number of incoming wikilinks
    link_count: dict[str, int] = {stem: 0 for stem in per_note_data}
    for _stem, (_, related_stems) in per_note_data.items():
        for target_stem in related_stems:
            if target_stem in link_count:
                link_count[target_stem] += 1

    # Second pass: group by folder, compute staleness, collect DB rows
    stale_count: int = 0
    db_rows: list[NoteEntry] = []
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
            )
        )

        if folder:
            folder_notes.setdefault(folder, []).append(
                (_wikilink(note_path), title, summary, tags_list, is_stale)
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
    lines.append("# Claude Vault Index")
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
    state_file: Path = VAULT_ROOT / "doctor_state.json"
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

    # Tag Cloud (human-readable, sorted by frequency)
    lines.append("## Tag Cloud")
    if tag_counter:
        tag_parts: list[str] = []
        for tag, count in tag_counter.most_common():
            tag_parts.append(f"`{tag}` ({count})")
        lines.append(" | ".join(tag_parts))
    else:
        lines.append("_No tags found._")
    lines.append("")

    # Existing Tags (machine-readable for the AI summarizer)
    lines.append("## Existing Tags")
    if tag_counter:
        lines.append(", ".join(sorted(tag_counter.keys())))
    else:
        lines.append("")
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

    # Folder sections
    lines.append("## Folders")
    lines.append("")

    for folder_name_str in FOLDER_ORDER:
        entries: list[tuple[str, str, str, list[str], bool]] = folder_notes.get(
            folder_name_str, []
        )
        count: int = len(entries)
        lines.append(f"### {folder_name_str} ({count} notes)")
        if entries:
            for wlink, _title, summary, _tags, is_stale in entries:
                summary_label = f" - {summary}" if summary else ""
                stale_marker = " [STALE?]" if is_stale else ""
                lines.append(f"- {wlink}{summary_label}{stale_marker}")
        else:
            lines.append(f"_No notes in {folder_name_str}._")
        lines.append("")

    # Handle any extra folders not in FOLDER_ORDER
    extra_folders: list[str] = sorted(f for f in folder_notes if f not in FOLDER_ORDER)
    for folder_name_str in extra_folders:
        entries = folder_notes[folder_name_str]
        count = len(entries)
        lines.append(f"### {folder_name_str} ({count} notes)")
        for wlink, _title, summary, _tags, is_stale in entries:
            summary_label = f" - {summary}" if summary else ""
            stale_marker = " [STALE?]" if is_stale else ""
            lines.append(f"- {wlink}{summary_label}{stale_marker}")
        lines.append("")

    return "\n".join(lines), total_notes, total_tags, folder_notes, db_rows


def build_manifests(
    folder_notes: dict[str, list[tuple[str, str, str, list[str], bool]]],
) -> list[Path]:
    """Generate a MANIFEST.md file inside each non-empty vault subfolder.

    Each manifest contains a Markdown table with one row per note, showing the
    note stem as a wikilink, its tags, and a one-line summary. Stale notes are
    marked with a warning emoji in the Note column.

    Args:
        folder_notes: Mapping from folder name to list of
            (wikilink, title, summary, tags, is_stale) tuples, as returned by
            ``build_index()``.

    Returns:
        A list of Path objects for all MANIFEST.md files written.
    """
    now_str: str = datetime.now().strftime("%Y-%m-%d %H:%M")
    written: list[Path] = []

    for folder_name, entries in folder_notes.items():
        if not entries:
            continue

        folder_dir: Path = VAULT_ROOT / folder_name
        if not folder_dir.is_dir():
            continue

        note_count: int = len(entries)
        lines: list[str] = []
        lines.append(f"# {folder_name} — Vault Notes ({note_count} notes)")
        lines.append(
            f"<!-- Auto-generated by update_index.py on {now_str} — do not edit manually -->"
        )
        lines.append("")
        lines.append("| Note | Tags | Summary |")
        lines.append("|------|------|---------|")

        for wlink, _title, summary, tags, is_stale in entries:
            # wlink is like [[stem]] — extract stem for display
            stem_match = _WIKILINK_RE.match(wlink)
            stem: str = stem_match.group(1) if stem_match else wlink

            stale_prefix: str = "⚠️ " if is_stale else ""
            note_cell: str = f"{stale_prefix}[[{stem}]]"

            tags_cell: str = " ".join(f"`{t}`" for t in tags) if tags else ""
            # Escape pipe characters in summary to avoid breaking the table
            summary_cell: str = summary.replace("|", "\\|") if summary else ""

            lines.append(f"| {note_cell} | {tags_cell} | {summary_cell} |")

        lines.append("")
        content: str = "\n".join(lines)

        manifest_path: Path = folder_dir / "MANIFEST.md"
        manifest_path.write_text(content, encoding="utf-8")
        written.append(manifest_path)

    return written


def _write_note_index_to_db(db_rows: list[NoteEntry], current_stems: set[str]) -> None:
    """Write per-note metadata rows to the note_index table in embeddings.db.

    No-op if embeddings are disabled or the DB file does not exist. Errors are
    printed to stderr so DB failures are visible without crashing the indexer.

    Args:
        db_rows: List of NoteEntry records to upsert into note_index.
        current_stems: Set of stems currently in the vault (used to prune deleted notes).
    """
    if not get_config("embeddings", "enabled", True):
        return
    try:
        import sqlite3 as _sqlite3
        from vault_common import ensure_note_index_schema

        db_path = get_embeddings_db_path()
        if not db_path.exists():
            return

        conn = _sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        ensure_note_index_schema(conn)

        conn.executemany(
            """
            INSERT INTO note_index (
                stem, path, folder, title, summary, tags, note_type,
                project, confidence, mtime, related, is_stale, incoming_links
            ) VALUES (
                :stem, :path, :folder, :title, :summary, :tags, :note_type,
                :project, :confidence, :mtime, :related, :is_stale, :incoming_links
            )
            ON CONFLICT(stem) DO UPDATE SET
                path=excluded.path,
                folder=excluded.folder,
                title=excluded.title,
                summary=excluded.summary,
                tags=excluded.tags,
                note_type=excluded.note_type,
                project=excluded.project,
                confidence=excluded.confidence,
                mtime=excluded.mtime,
                related=excluded.related,
                is_stale=excluded.is_stale,
                incoming_links=excluded.incoming_links
            """,
            [row._asdict() for row in db_rows],
        )

        # Prune rows for notes that no longer exist in the vault
        db_stems = conn.execute("SELECT stem FROM note_index").fetchall()
        stale = [(row[0],) for row in db_stems if row[0] not in current_stems]
        if stale:
            conn.executemany("DELETE FROM note_index WHERE stem = ?", stale)

        conn.commit()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"update_index DB error: {exc}", file=sys.stderr)


def _find_build_graph_script() -> Path | None:
    """Locate build_graph.py in known locations.

    Checks two candidates in order:
    1. Same directory as this script (works when build_graph.py is co-installed).
    2. ``<repo-root>/scripts/build_graph.py`` (works when running from source,
       where ``__file__`` lives at ``skills/parsidion-cc/scripts/``).

    Returns:
        Path to the script if found, else None.
    """
    # Candidate 1: alongside this script (co-installed scenario)
    candidate = Path(__file__).parent / "build_graph.py"
    if candidate.exists():
        return candidate
    # Candidate 2: repo-root/scripts/ (source-repo scenario)
    # __file__ = <repo>/skills/parsidion-cc/scripts/update_index.py
    # four parents up  → <repo>/
    candidate = Path(__file__).resolve().parents[3] / "scripts" / "build_graph.py"
    if candidate.exists():
        return candidate
    return None


def _rebuild_graph(include_daily: bool) -> None:
    """Run build_graph.py synchronously and print its output.

    Args:
        include_daily: When True, pass ``--include-daily`` to build_graph.py.
    """
    graph_script = _find_build_graph_script()
    if graph_script is None:
        print(
            "Graph rebuild skipped: build_graph.py not found. "
            "Run from the parsidion-cc repo or co-install build_graph.py.",
            file=sys.stderr,
        )
        return

    cmd = ["uv", "run", "--no-project", str(graph_script)]
    if include_daily:
        cmd.append("--include-daily")

    print(f"Graph: rebuilding graph.json ({'with' if include_daily else 'without'} Daily notes)...")
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        print(f"Graph rebuild failed (exit {result.returncode})", file=sys.stderr)


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments for update_index."""
    parser = argparse.ArgumentParser(
        description="Rebuild the vault index (CLAUDE.md, MANIFEST.md, note_index DB).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--rebuild-graph",
        action="store_true",
        default=False,
        help="Also rebuild visualizer graph.json after the index update",
    )
    parser.add_argument(
        "--graph-include-daily",
        action="store_true",
        default=False,
        help="Include Daily folder notes in the graph (only used with --rebuild-graph)",
    )
    return parser.parse_args()


def main() -> None:
    """Entry point: rebuild the index, write CLAUDE.md, and generate MANIFEST.md files."""
    args = _parse_args()
    _singleton_guard()
    content, note_count, tag_count, folder_notes, db_rows = build_index()
    index_path: Path = VAULT_ROOT / "CLAUDE.md"
    index_path.write_text(content, encoding="utf-8")

    manifest_paths: list[Path] = build_manifests(folder_notes)
    manifest_count: int = len(manifest_paths)

    # Commit CLAUDE.md + all MANIFEST.md files together
    commit_paths: list[Path] = [index_path] + manifest_paths
    git_commit_vault("chore(vault): rebuild index and manifests", paths=commit_paths)

    current_stems: set[str] = {row.stem for row in db_rows}
    _write_note_index_to_db(db_rows, current_stems)

    print(
        f"Updated CLAUDE.md: {note_count} notes indexed, {tag_count} tags; "
        f"{manifest_count} MANIFEST.md file(s) generated"
    )

    # Update embeddings.db in the background when enabled.
    # Incremental when the DB already exists; full rebuild when it does not.
    # ARC-011: stderr is redirected to a log file so silent failures are
    # visible.  Check /tmp/parsidion-cc-embed.log when embeddings seem stale.
    if get_config("embeddings", "enabled", True):
        db_path = get_embeddings_db_path()
        build_script = Path(__file__).parent / "build_embeddings.py"
        if build_script.exists():
            cmd = ["uv", "run", "--no-project", str(build_script)]
            if db_path.exists():
                cmd.append("--incremental")
                label = "incremental"
            else:
                label = "full"
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=open("/tmp/parsidion-cc-embed.log", "a"),  # noqa: SIM115
                start_new_session=True,
            )
            print(f"Embeddings: {label} rebuild launched in background")

    if args.rebuild_graph:
        _rebuild_graph(include_daily=args.graph_include_daily)


if __name__ == "__main__":
    main()
