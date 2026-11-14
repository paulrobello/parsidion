"""Vault note discovery: filesystem walk + DB-first read policy (ARC-203).

The former monolithic ``core/vault_index.py`` has been split (ARC-203):

- ``core.frontmatter`` — frontmatter parsing/serialization, titles, slugs;
- ``core.note_index`` — the ``note_index`` DB access layer (schema, SQL,
  queries, the per-run snapshot);
- this module — the filesystem walk primitives and the DB-first *policy*
  layer that chooses between a DB read and a walk
  (``find_notes_by_*``, ``all_vault_notes``), plus context-block builders
  and the index-rebuild subprocess owner. It re-exports the moved names so
  ``core.vault_index`` (and the ``vault_index`` / ``vault_common`` shims
  that star-import its ``__all__``) remain the single stable import
  surface for the 50+ existing callers.

Provides vault note discovery, frontmatter/body parsing, metadata-based search
(by tag, project, type, recency), and context block construction for hook injection.

This module is part of the vault_common split (ARC-005).  All public symbols
are re-exported from ``vault_common`` for backward compatibility.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from .frontmatter import (  # noqa: F401 -- re-exported (ARC-203)
    _FRONTMATTER_RE,
    _PARSE_WARNINGS_MAX,
    drain_parse_warnings,
    extract_title,
    get_body,
    parse_frontmatter,
    record_parse_warning,
    serialize_frontmatter,
    slugify,
)
from .note_index import (  # noqa: F401 -- re-exported (ARC-203)
    SessionIndexRow,
    SessionIndexSnapshot,
    _build_note_index_where,
    _incoming_stems_from_column,
    _load_note_index_map,
    _paths_from_rows,
    ensure_note_index_schema,
    load_graph_metadata,
    load_note_index_metadata,
    load_session_index_snapshot,
    parse_related_stems,
    query_note_index,
)
from .subproc_util import run_with_pgkill
from .vault_config import get_config, load_config
from .vault_hooks import env_without_claudecode
from .vault_path import (
    EXCLUDE_DIRS,
    SCRIPTS_DIR,
    VAULT_DIRS,
    get_embeddings_db_path,
    is_symlink_inside_vault,
    resolve_vault,
)

# Supersession status constant lives in the note contract module (a leaf).
from parsidion.note_schema import STATUS_SUPERSEDED

__all__: list[str] = [
    # Constants (re-exported from vault_path for convenience)
    "VAULT_DIRS",
    "EXCLUDE_DIRS",
    # Frontmatter and content parsing
    "parse_frontmatter",
    "serialize_frontmatter",
    "get_body",
    "extract_title",
    # Parse warning collector
    "record_parse_warning",
    "drain_parse_warnings",
    "_PARSE_WARNINGS_MAX",  # tests assert the cap value
    # Slug utility
    "slugify",
    # Note search
    "find_notes_by_project",
    "find_notes_by_tag",
    "find_notes_by_type",
    "find_recent_notes",
    "all_vault_notes",
    "all_vault_notes_walk",
    "read_note_summary",
    # Context building
    "build_context_block",
    "build_compact_index",
    # DB helpers
    "ensure_note_index_schema",
    "query_note_index",
    "load_note_index_metadata",
    "load_graph_metadata",
    # Per-run note_index snapshot (PRF-104)
    "SessionIndexRow",
    "SessionIndexSnapshot",
    "load_session_index_snapshot",
    # Index-rebuild subprocess owner (ARC-004)
    "run_index_rebuild",
    "note_index_age",
    # Graph parsing
    "parse_related_stems",
    # Private helpers re-exported because tests reach into them via the
    # vault_index shim (e.g. ``parsidion.vault_index._walk_vault_notes(tmp_vault)``).
    "_find_notes_by_project_walk",
    "_find_notes_by_tag_walk",
    "_find_notes_by_type_walk",
    "_find_recent_notes_walk",
    "_walk_vault_notes",
    "_note_index_enabled",
]

# query_note_index defaults to a 200-row cap (sufficient for its original
# context-injection callers). The find_notes_by_* wrappers semantically return
# EVERY matching note (the walk does), so they pass this effectively-unbounded
# limit to override that cap.
_FIND_ALL_LIMIT = 10**9


# ---------------------------------------------------------------------------
# Vault note traversal
# ---------------------------------------------------------------------------


def _walk_vault_notes(vault: str | Path | None = None) -> list[Path]:
    """Walk the vault tree and return all .md files, excluding EXCLUDE_DIRS, CLAUDE.md, TAGS.md, and MANIFEST.md.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().
    """
    if isinstance(vault, str):  # be liberal: accept str paths from callers
        vault = Path(vault)
    vault = vault or resolve_vault()
    notes: list[Path] = []
    if not vault.is_dir():
        return notes

    # SEC-106: resolve once so the symlink guard is not defeated by a
    # symlinked vault root. ``Templates/`` is excluded via EXCLUDE_DIRS
    # *before* the symlink guard runs, so the intentional
    # ``Templates -> skills/parsidion/templates`` symlink is preserved.
    vault_root_resolved = vault.resolve()

    for dirpath, dirnames, filenames in os.walk(vault):
        # Prune excluded directories in-place so os.walk skips them
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            if Path(dirpath) == vault and fname in ("CLAUDE.md", "TAGS.md"):
                continue
            if fname == "MANIFEST.md":
                continue
            p = Path(dirpath) / fname
            # SEC-106: skip symlinked .md files whose target escapes the
            # vault; ``os.walk`` lists them like regular files.
            if not is_symlink_inside_vault(p, vault_root_resolved):
                continue
            notes.append(p)

    return notes


def _find_notes_by_field(
    field: str,
    value: str,
    vault: str | Path | None = None,
    include_superseded: bool = False,
) -> list[Path]:
    """Find all notes where a frontmatter *field* matches *value* (case-insensitive).

    For scalar fields (``project``, ``type``), matches the value directly.
    For list fields (``tags``), matches if any element equals *value*.
    Superseded notes are skipped unless *include_superseded* — the walk path
    mirrors the SQL path's default exclusion (see query_note_index).

    Args:
        field: The frontmatter field name to search (e.g. ``"project"``).
        value: The target value to match (compared case-insensitively).
        vault: Optional vault path. Defaults to resolve_vault().
        include_superseded: Include retired notes in the result.

    Returns:
        List of matching note paths.
    """
    matches: list[Path] = []
    target = value.lower()
    for note_path in _walk_vault_notes(vault):
        try:
            content = note_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = parse_frontmatter(content)
        if not include_superseded and fm.get("status") == STATUS_SUPERSEDED:
            continue
        field_val = fm.get(field)
        if isinstance(field_val, str) and field_val.lower() == target:
            matches.append(note_path)
        elif isinstance(field_val, list):
            if any(
                isinstance(item, str) and item.lower() == target for item in field_val
            ):
                matches.append(note_path)
    return matches


# ---------------------------------------------------------------------------
# Walk-based implementations (fallback + differential-test oracle).
#
# These iterate the filesystem directly via _walk_vault_notes. They are kept
# verbatim as the no-DB fallback for the public DB-first find_notes_by_*
# functions, and as the oracle the differential tests compare the DB path
# against so the two cannot silently drift.
# ---------------------------------------------------------------------------


def _find_notes_by_project_walk(
    project: str,
    vault: str | Path | None = None,
    include_superseded: bool = False,
) -> list[Path]:
    """Walk fallback for :func:`find_notes_by_project`."""
    return _find_notes_by_field(
        "project", project, vault=vault, include_superseded=include_superseded
    )


def _find_notes_by_tag_walk(
    tag: str,
    vault: str | Path | None = None,
    include_superseded: bool = False,
) -> list[Path]:
    """Walk fallback for :func:`find_notes_by_tag`."""
    return _find_notes_by_field(
        "tags", tag, vault=vault, include_superseded=include_superseded
    )


def _find_notes_by_type_walk(
    note_type: str,
    vault: str | Path | None = None,
    include_superseded: bool = False,
) -> list[Path]:
    """Walk fallback for :func:`find_notes_by_type`."""
    return _find_notes_by_field(
        "type", note_type, vault=vault, include_superseded=include_superseded
    )


def _find_recent_notes_walk(
    days: int = 3,
    vault: str | Path | None = None,
    include_superseded: bool = False,
) -> list[Path]:
    """Walk fallback for :func:`find_recent_notes`."""
    cutoff = datetime.now() - timedelta(days=days)
    cutoff_ts = cutoff.timestamp()
    recent: list[tuple[float, Path]] = []

    for note_path in _walk_vault_notes(vault):
        try:
            mtime = note_path.stat().st_mtime
        except OSError:
            continue
        if not include_superseded and _is_superseded_note(note_path):
            continue
        if mtime >= cutoff_ts:
            recent.append((mtime, note_path))

    recent.sort(key=lambda x: x[0], reverse=True)
    return [path for _, path in recent]


def _is_superseded_note(note_path: Path) -> bool:
    """True when the note's frontmatter carries ``status: superseded``.

    Read failure counts as not superseded (fail-open for retrieval — a
    broken file must not vanish from every surface).
    """
    try:
        content = note_path.read_text(encoding="utf-8")
        return parse_frontmatter(content).get("status") == STATUS_SUPERSEDED
    except (OSError, UnicodeDecodeError):
        return False


# ---------------------------------------------------------------------------
# DB-first read policy
# ---------------------------------------------------------------------------


def _note_index_enabled(vault: str | Path | None = None) -> bool:
    """Whether DB-first note reads are enabled.

    Reads ``search.use_note_index`` (default ``true``). When false, every
    ``find_notes_by_*`` and ``all_vault_notes`` call takes the filesystem-walk
    path regardless of DB availability -- the user-facing escape hatch when
    index staleness is suspected. The *vault* arg is accepted for API symmetry
    with the callers; ``get_config`` resolves the vault via the cached
    ``load_config()`` (CLAUDE_VAULT-aware, so tests with ``tmp_vault`` see the
    right config without an explicit thread).

    Additionally falls back to filesystem walk when:
    - ``embeddings.enabled`` is False (unless ``search.use_note_index`` is explicitly True),
      because the embeddings pipeline does not maintain ``note_index``.
    - ``note_index_age()`` exceeds ``search.max_index_age_seconds`` (default 300s).
    """
    vault_path: Path | None = Path(vault) if vault is not None else None
    if not get_config("search", "use_note_index", True, vault=vault_path):
        return False

    # When embeddings are disabled, note_index is not maintained by the embeddings
    # pipeline unless search.use_note_index is explicitly set to True.
    if not get_config("embeddings", "enabled", True, vault=vault_path):
        raw_config = load_config(vault=vault_path)
        search_cfg = raw_config.get("search", {})
        explicit_true = (
            isinstance(search_cfg, dict) and search_cfg.get("use_note_index") is True
        )
        if not explicit_true:
            return False

    # Check staleness: if note_index_age exceeds threshold, fall back to walk
    max_age = get_config("search", "max_index_age_seconds", None, vault=vault_path)
    if max_age is not None and float(max_age) > 0:
        age = note_index_age(vault_path)
        if age > float(max_age):
            return False

    return True


def note_index_age(vault: str | Path | None = None) -> float:
    """Return how stale ``note_index`` is, in seconds.

    Computes ``max(on-disk .md mtime) - max(note_index mtime)``. Returns
    ``0.0`` when the index is current or ahead, when the DB is absent, or
    when either source is unreadable. A positive value means on-disk notes
    exist that the index does not yet reflect -- the DB-first read path may
    return stale results, and the user should rebuild via
    ``update_index.py``.

    This is a *signal*, not a trigger: read paths never auto-rebuild, because
    a read doing surprising write work is worse than a slightly stale result.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().
    """
    resolved = vault or resolve_vault()
    db_path = get_embeddings_db_path(resolved)
    if not db_path.exists():
        return 0.0
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return 0.0
    try:
        row = conn.execute("SELECT MAX(mtime) FROM note_index").fetchone()
    except sqlite3.Error:
        conn.close()
        return 0.0
    conn.close()
    db_max = float(row[0]) if row and row[0] is not None else 0.0

    disk_max = 0.0
    for p in _walk_vault_notes(resolved):
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > disk_max:
            disk_max = m
    if disk_max <= db_max:
        return 0.0
    return disk_max - db_max


def find_notes_by_project(
    project: str,
    vault: str | Path | None = None,
    snapshot: SessionIndexSnapshot | None = None,
    include_superseded: bool = False,
) -> list[Path]:
    """Find all notes with a matching ``project`` field in frontmatter.

    Reads ``note_index`` when available and ``search.use_note_index`` is true
    (default); falls back to a filesystem walk so behaviour is unchanged on a
    vault with no embeddings.db. Superseded notes are excluded unless
    *include_superseded*.

    Args:
        project: Exact project value to match.
        vault: Optional vault path. Defaults to resolve_vault().
        snapshot: PRF-104 -- serve the query from an already-loaded snapshot
            instead of a fresh one. Honours ``search.use_note_index`` the same
            way, so the escape hatch still forces the walk.
        include_superseded: Include retired notes in the result.
    """
    if _note_index_enabled(vault):
        if snapshot is not None:
            return snapshot.paths_where(
                project=project,
                limit=_FIND_ALL_LIMIT,
                include_superseded=include_superseded,
            )
        result = query_note_index(
            project=project,
            vault=vault,
            limit=_FIND_ALL_LIMIT,
            include_superseded=include_superseded,
        )
        if result is not None:
            return result
    return _find_notes_by_project_walk(
        project, vault=vault, include_superseded=include_superseded
    )


def find_notes_by_tag(
    tag: str,
    vault: str | Path | None = None,
    include_superseded: bool = False,
) -> list[Path]:
    """Find all notes containing the given tag in their ``tags`` list.

    DB-first with walk fallback; see :func:`find_notes_by_project`.
    Superseded notes are excluded unless *include_superseded*.
    """
    if _note_index_enabled(vault):
        result = query_note_index(
            tag=tag,
            vault=vault,
            limit=_FIND_ALL_LIMIT,
            include_superseded=include_superseded,
        )
        if result is not None:
            return result
    return _find_notes_by_tag_walk(
        tag, vault=vault, include_superseded=include_superseded
    )


def find_notes_by_type(
    note_type: str,
    vault: str | Path | None = None,
    include_superseded: bool = False,
) -> list[Path]:
    """Find all notes with a matching ``type`` field in frontmatter.

    DB-first with walk fallback; see :func:`find_notes_by_project`.
    Superseded notes are excluded unless *include_superseded*.
    """
    if _note_index_enabled(vault):
        result = query_note_index(
            note_type=note_type,
            vault=vault,
            limit=_FIND_ALL_LIMIT,
            include_superseded=include_superseded,
        )
        if result is not None:
            return result
    return _find_notes_by_type_walk(
        note_type, vault=vault, include_superseded=include_superseded
    )


def find_recent_notes(
    days: int = 3,
    vault: str | Path | None = None,
    limit: int = _FIND_ALL_LIMIT,
    snapshot: SessionIndexSnapshot | None = None,
    include_superseded: bool = False,
) -> list[Path]:
    """Find notes modified within the last *days* days, sorted by mtime descending.

    DB-first with walk fallback; see :func:`find_notes_by_project`.

    Args:
        days: Look-back window in days.
        vault: Optional vault path. Defaults to resolve_vault().
        limit: PRF-104 -- cap on returned notes. The general-purpose default is
            unbounded, as before; the SessionStart context path passes an
            explicit cap because its injected-context budget is 4,000 chars and
            fetching thousands of rows serves nothing.
        snapshot: PRF-104 -- serve the query from an already-loaded snapshot.
        include_superseded: Include retired notes in the result.
    """
    if _note_index_enabled(vault):
        if snapshot is not None:
            return snapshot.paths_where(
                recent_days=days,
                limit=limit,
                include_superseded=include_superseded,
            )
        result = query_note_index(
            recent_days=days,
            vault=vault,
            limit=limit,
            include_superseded=include_superseded,
        )
        if result is not None:
            return result
    return _find_recent_notes_walk(
        days, vault=vault, include_superseded=include_superseded
    )[:limit]


def all_vault_notes(vault: str | Path | None = None) -> list[Path]:
    """Return all ``.md`` files in the vault (excluding ``EXCLUDE_DIRS``, ``CLAUDE.md``, ``TAGS.md``, ``MANIFEST.md``).

    DB-first (follow-up to ENH-004): reads ``note_index`` when available and
    falls back to the filesystem walk otherwise. ``note_index`` and
    ``note_embeddings`` share ``embeddings.db``, so callers that already depend
    on embeddings being current gain no new staleness from this.

    Callers that require the AUTHORITATIVE current filesystem set -- index
    builders (``update_index.py``, ``build_embeddings.py``) and mutation paths
    (``doctor/*``, ``vault_merge``, ``vault_export``, backlink validation in
    ``vault_links``) -- must call :func:`all_vault_notes_walk` instead: a stale
    index must never silently change what those operations see. The escape
    hatch is ``search.use_note_index: false`` in config (forces the walk).

    Args:
        vault: Optional vault path. Defaults to resolve_vault().
    """
    if _note_index_enabled(vault):
        result = query_note_index(vault=vault, limit=_FIND_ALL_LIMIT)
        if result is not None:
            return result
    return _walk_vault_notes(vault)


def all_vault_notes_walk(vault: str | Path | None = None) -> list[Path]:
    """Authoritative filesystem enumeration of every vault note.

    Always walks the tree -- never reads ``note_index``. Use this wherever the
    caller makes a write/mutation decision based on the note set, or is itself
    populating the index (a stale DB view would be a silent bug there). For
    pure read/display callers that already tolerate index staleness on par
    with ``find_notes_by_*``, prefer :func:`all_vault_notes`.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().
    """
    return _walk_vault_notes(vault)


# ---------------------------------------------------------------------------
# Context building
# ---------------------------------------------------------------------------


def read_note_summary(path: Path, max_lines: int = 5) -> str:
    """Read a note and return its title (first ``#`` heading) plus the first
    *max_lines* of body content. Used for building context blocks.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""

    body = get_body(content)
    lines = body.strip().splitlines()

    title: str = path.stem  # fallback title
    body_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            body_start = i + 1
            break

    # Collect up to max_lines of non-empty body content after the title
    summary_lines: list[str] = []
    for line in lines[body_start:]:
        stripped = line.strip()
        if not stripped:
            continue
        # Skip HTML comments
        if stripped.startswith("<!--"):
            continue
        summary_lines.append(stripped)
        if len(summary_lines) >= max_lines:
            break

    result = title
    if summary_lines:
        result += "\n" + "\n".join(summary_lines)
    return result


def build_context_block(notes: list[Path], max_chars: int = 4000) -> str:
    """Build a context string from a list of notes, respecting *max_chars* budget.

    Each note is formatted as::

        ### Note Title (folder/filename)
        [summary lines]

    Stops adding notes when approaching *max_chars*.
    """
    parts: list[str] = []
    char_count = 0

    vault_root = resolve_vault()
    for note_path in notes:
        # Determine the relative folder/filename label
        try:
            rel = note_path.relative_to(vault_root)
        except ValueError:
            rel = Path(note_path.parent.name) / note_path.name

        summary = read_note_summary(note_path)
        if not summary:
            continue

        # Extract title from first line of summary
        summary_lines = summary.splitlines()
        title = summary_lines[0] if summary_lines else note_path.stem
        body = "\n".join(summary_lines[1:]) if len(summary_lines) > 1 else ""

        block = f"### {title} ({rel})\n"
        if body:
            block += body + "\n"
        block += "\n"

        if char_count + len(block) > max_chars:
            break

        parts.append(block)
        char_count += len(block)

    return "".join(parts).rstrip("\n")


def build_compact_index(
    notes: list[Path],
    max_chars: int = 2000,
    vault: Path | None = None,
    snapshot: SessionIndexSnapshot | None = None,
) -> str:
    """Build a compact one-line-per-note index: title [tags] (folder).

    Much smaller than build_context_block -- use when vault is large or
    token budget is tight. Full note content is available via the parsidion skill.

    QA-005: Queries note_index DB first (title, tags, folder already indexed);
    falls back to file reads only when DB is absent.

    Args:
        notes: List of note paths to include.
        max_chars: Maximum total characters before truncating with a count line.
        vault: Optional vault path. Defaults to resolve_vault().
        snapshot: PRF-104 -- an already-loaded ``note_index`` snapshot to read
            the title/tags/folder map from. Omit it (the default) and one is
            loaded here, preserving the standalone contract external callers
            such as parsidion-mcp depend on.

    Returns:
        A compact index string, or empty string if notes is empty.
    """
    vault = vault or resolve_vault()
    # QA-005: Try DB-backed lookup to avoid N+1 file reads
    index_map = (
        snapshot.compact_index_map()
        if snapshot is not None
        else _load_note_index_map(vault)
    )
    lines: list[str] = []
    total = 0
    for path in notes:
        db_entry = index_map.get(path.stem) if index_map else None
        if db_entry:
            title, tags_str, folder = db_entry
            tags = (
                [t.strip() for t in tags_str.split(",") if t.strip()]
                if tags_str
                else []
            )
            folder = folder or "root"
        else:
            # Fallback: read the file
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            fm = parse_frontmatter(content)
            title = extract_title(content, path.stem)
            tags = fm.get("tags", [])
            if isinstance(tags, str):
                tags = [tags]
            folder = path.parent.name if path.parent != vault else "root"
        tag_str = " ".join(f"`{t}`" for t in tags) if tags else ""
        entry = f"- [[{path.stem}]] {title} ({folder})" + (
            " — " + tag_str if tag_str else ""
        )
        total += len(entry) + 1
        if total > max_chars:
            lines.append(
                f"- ... ({len(notes) - len(lines)} more notes, "
                "use parsidion skill to browse)"
            )
            break
        lines.append(entry)

    if not lines:
        return ""

    header = (
        "**Available vault notes** (compact index — "
        "use `parsidion` skill to load full content):\n"
    )
    return header + "\n".join(lines)


# ---------------------------------------------------------------------------
# Index-rebuild subprocess owner (ARC-004)
# ---------------------------------------------------------------------------


def run_index_rebuild(
    vault: Path | None = None,
    *,
    rebuild_graph: bool | None = None,
    graph_include_daily: bool | None = None,
    timeout: float = 300.0,
    scripts_dir: Path | None = None,
) -> tuple[str, subprocess.CompletedProcess[str] | None]:
    """Run ``update_index.py`` via ``uv run --no-project``.

    ARC-004: single owner of the index-rebuild subprocess contract shared by
    the installer, the MCP ``rebuild_index`` tool, and the summarizer queue.
    Previously three independent launchers disagreed on argv (the installer
    omitted ``--no-project``), environment (only the summarizer stripped
    ``CLAUDECODE``), script discovery, and timeout handling.

    Contract:

    - argv always starts ``["uv", "run", "--no-project", <update_index.py>]``
      so uv never discovers a ``pyproject.toml`` in the inherited cwd and
      syncs an unrelated project's dependencies;
    - the child environment is :func:`core.vault_hooks.env_without_claudecode`
      so a ``claude``-backed update does not refuse to nest;
    - timeout kills the whole process group
      (:func:`core.subproc_util.run_with_pgkill`), not just the parent.

    Script discovery: when *scripts_dir* is given it is used exclusively
    (missing script ⇒ ``("launch", None)`` — the installer relies on this to
    warn about a broken install target instead of rebuilding through some
    other copy). Otherwise the directory of this ``core`` package wins (keeps
    the subprocess consistent with the running code, ARC-021) with the
    installed ``~/.claude/skills/parsidion/scripts`` as fallback.

    Args:
        vault: Vault to rebuild; ``None`` omits ``--vault`` so
            ``resolve_vault()`` default precedence applies.
        rebuild_graph: Pass ``--rebuild-graph`` when True; ``None`` sends no
            flag.
        graph_include_daily: Pass ``--graph-include-daily`` when True (only
            meaningful with *rebuild_graph*); ``None`` sends no flag.
        timeout: Seconds before the process group is killed.
        scripts_dir: Directory containing ``update_index.py``; see discovery
            rules above.

    Returns:
        ``(reason, proc)`` as documented for
        :func:`core.subproc_util.run_with_pgkill` — ``("ok", CompletedProcess)``
        with any returncode, ``("launch", None)`` when uv or the script is
        unavailable, ``("timeout", None)``. Never raises.
    """
    if scripts_dir is not None:
        script = scripts_dir / "update_index.py"
        if not script.exists():
            return "launch", None
    else:
        source_dir = Path(__file__).resolve().parent.parent.parent
        script = next(
            (
                candidate
                for candidate in (
                    source_dir / "update_index.py",
                    SCRIPTS_DIR / "update_index.py",
                )
                if candidate.exists()
            ),
            None,
        )
        if script is None:
            return "launch", None

    argv = ["uv", "run", "--no-project", str(script)]
    if vault is not None:
        argv += ["--vault", str(vault)]
    if rebuild_graph:
        argv.append("--rebuild-graph")
    if graph_include_daily:
        argv.append("--graph-include-daily")

    return run_with_pgkill(
        argv,
        cwd=vault,
        timeout=timeout,
        env=env_without_claudecode(),
    )
