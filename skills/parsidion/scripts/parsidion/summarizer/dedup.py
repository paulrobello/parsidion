"""Dedup helpers: stem resolution, candidate search, tag/project readers.

Extracted from ``summarize_sessions.py`` (ARC-009).

These are all **test-patchable** callees of the anyio core (``summarize_one``,
``build_prompt``, ``run_all``) which stays in the entry shim.  Tests
``monkeypatch.setattr(summarize_sessions, "read_existing_tags", …)`` etc.; the
shim re-exports these so bare-name lookups in the shim's globals see the patched
version at call time (Python resolves bare names in the *caller's* module
globals).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from parsidion.core.vault_config import get_config
from parsidion.core.vault_index import (
    all_vault_notes,
    parse_frontmatter,
    read_note_summary,
)
from parsidion.core.vault_path import get_embeddings_db_path, resolve_vault


def read_project_names(
    vault_notes: list[Path] | None = None,
    vault: Path | None = None,
) -> set[str]:
    """Collect all project field values from vault note frontmatter.

    Used to filter project names out of the existing-tags list shown to the
    model, since project tags are injected deterministically post-generation.

    ARC-028: tries the ``note_index`` DB first (one ``SELECT DISTINCT project``
    — the column is already maintained by update_index.py and is what every
    other consumer of project metadata reads). Falls back to a full vault walk
    only when the DB or its ``project`` column is unavailable, so an
    embeddings-disabled vault keeps working.

    Args:
        vault_notes: Pre-collected list of vault note paths. Used only by the
            fallback path. When ``None`` and the fallback runs, calls
            ``all_vault_notes(vault)``.
        vault: Optional vault path used to locate embeddings.db. Defaults to
            ``resolve_vault()``.

    Returns:
        Set of project name strings found across all vault notes.
    """
    # ARC-028: DB-first path — the project column is maintained by update_index
    # and is already what every other code path reads. This replaces an O(N)
    # file walk + per-note frontmatter parse with one indexed SELECT.
    try:
        import sqlite3 as _sqlite3

        resolved_vault = vault or resolve_vault()
        db_path = get_embeddings_db_path(resolved_vault)
        if db_path.exists():
            conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                # Defensive: an older note_index schema lacking `project`
                # would raise OperationalError; treat that as "DB not usable"
                # and fall through to the file walk.
                rows = conn.execute(
                    "SELECT DISTINCT project FROM note_index "
                    "WHERE project IS NOT NULL AND project != ''"
                ).fetchall()
                projects = {str(row[0]) for row in rows if row and row[0]}
                if projects:
                    return projects
            except _sqlite3.Error:
                pass  # fall through to the file walk
            finally:
                conn.close()
    except (OSError, ValueError):
        pass

    notes = vault_notes if vault_notes is not None else all_vault_notes(vault)
    projects: set[str] = set()
    for note_path in notes:
        try:
            content = note_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = parse_frontmatter(content)
        proj = fm.get("project")
        if isinstance(proj, str) and proj:
            projects.add(proj)
    return projects


def read_existing_tags(vault: Path) -> list[str]:
    """Read existing tags from the vault TAGS.md file.

    Parses the '## Existing Tags' section which contains a comma-separated
    list of all tags currently in the vault. Falls back to CLAUDE.md for
    backwards compatibility with older vaults.

    Args:
        vault: Path to the vault directory.

    Returns:
        Sorted list of existing tag strings, or empty list if unavailable.
    """
    for path in (vault / "TAGS.md", vault / "CLAUDE.md"):
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        match = re.search(r"^## Existing Tags\n(.+)$", content, re.MULTILINE)
        if match:
            tags_line = match.group(1).strip()
            return [t.strip() for t in tags_line.split(",") if t.strip()]
    return []


def _resolve_note_stem(stem: str, vault: Path) -> Path | None:
    """Resolve a note stem to its vault path via the note_index DB.

    Args:
        stem: Note filename without extension (e.g. "my-note").
        vault: Path to the vault directory.

    Returns:
        Path to the note file, or None if not found.
    """
    db_path = get_embeddings_db_path(vault)
    if db_path.exists():
        try:
            import sqlite3 as _sqlite3

            conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            row = conn.execute(
                "SELECT path FROM note_index WHERE stem = ?", (stem,)
            ).fetchone()
            conn.close()
            if row:
                p = Path(row[0])
                if p.exists():
                    return p
        except Exception as exc:  # noqa: BLE001
            print(f"note_index lookup best-effort: {exc}", file=sys.stderr)
            pass
    # Fallback: walk vault notes
    for note in all_vault_notes(vault):
        if note.stem == stem:
            return note
    return None


_DEDUP_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "into",
        "when",
        "where",
        "what",
        "how",
        "why",
        "not",
        "are",
        "was",
        "were",
        "have",
        "has",
        "had",
        "but",
        "all",
        "any",
        "can",
        "use",
        "using",
        "used",
        "pattern",
        "patterns",
        "debugging",
        "research",
        "project",
        "knowledge",
        "error",
        "fix",
        "fixes",
        "session",
        "note",
        "notes",
        "config",
        "setup",
        "unknown",
    }
)


def _dedup_tokens(text: str) -> set[str]:
    """Extract meaningful lowercase tokens (>=3 chars) for lexical dedup."""
    return {
        tok
        for tok in re.findall(r"[a-z0-9]{3,}", text.lower())
        if tok not in _DEDUP_STOPWORDS
    }


def _find_lexical_dedup_candidates(
    topic_query: str,
    vault: Path,
    *,
    project: str = "",
    top_k: int = 5,
    in_flight: list[tuple[str, str, str]] | None = None,
) -> list[tuple[str, float, str]]:
    """Lexical fallback for dedup when vector embeddings are disabled or stale.

    Scores both in-flight notes written earlier in the same summarizer batch
    and indexed notes from ``note_index`` (scoped to *project* when known) by
    token overlap against *topic_query*.
    """
    q_tokens = _dedup_tokens(topic_query)
    if len(q_tokens) < 3:
        return []

    scored: dict[str, tuple[float, str]] = {}

    def _score_entry(stem: str, title: str, summary: str, boost: float = 0.0) -> None:
        if not stem:
            return
        cand_tokens = _dedup_tokens(f"{stem} {title} {summary}")
        if not cand_tokens:
            return
        common = q_tokens & cand_tokens
        if len(common) < 3:
            return
        denom = min(len(q_tokens), len(cand_tokens))
        overlap = len(common) / denom if denom else 0.0
        score = min(0.99, overlap + boost)
        if score >= 0.45:
            prev = scored.get(stem)
            if prev is None or score > prev[0]:
                scored[stem] = (score, (summary or title or stem)[:400])

    # 1. Check in-flight notes written earlier in this batch (not yet embedded/indexed).
    for stem, title, summary in in_flight or []:
        _score_entry(stem, title, summary, boost=0.10)

    # 2. Query note_index (fast SQLite read; works even when embeddings.enabled=false).
    db_path = get_embeddings_db_path(vault)
    if db_path.exists():
        try:
            import sqlite3 as _sqlite3

            conn = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                if project and project != "unknown":
                    rows = conn.execute(
                        "SELECT stem, title, summary FROM note_index "
                        "WHERE project = ? ORDER BY mtime DESC LIMIT 400",
                        (project,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT stem, title, summary FROM note_index "
                        "ORDER BY mtime DESC LIMIT 400"
                    ).fetchall()
            except _sqlite3.Error:
                rows = []
            finally:
                conn.close()
            for stem, title, summary in rows:
                _score_entry(str(stem or ""), str(title or ""), str(summary or ""))
        except (OSError, ValueError):
            pass

    ranked = sorted(
        ((stem, score, summary) for stem, (score, summary) in scored.items()),
        key=lambda x: -x[1],
    )
    return ranked[:top_k]


def _find_dedup_candidates(
    topic_query: str,
    vault: Path,
    threshold: float = 0.80,
    top_k: int = 5,
    *,
    project: str = "",
    in_flight: list[tuple[str, str, str]] | None = None,
) -> list[tuple[str, float, str]]:
    """Search for existing notes semantically or lexically similar to *topic_query*.

    Used before the final summarization call to detect near-duplicates and
    prompt the backend to merge rather than create a new note. Combines vector
    search (when enabled) with lexical/in-flight batch matching so vaults with
    ``embeddings.enabled: false`` and parallel summarizer batches still dedup.

    Args:
        topic_query: Free-text query derived from project name and categories.
        vault: Path to the vault directory.
        threshold: Minimum cosine similarity score to consider a duplicate.
        top_k: Maximum number of candidates to return.
        project: Optional project name to scope lexical dedup lookups.
        in_flight: Optional list of ``(stem, title, summary)`` tuples for notes
            written earlier in the same batch before index/embedding rebuilds.

    Returns:
        List of (stem, score, summary) tuples ordered by descending score.
    """
    candidates_by_stem: dict[str, tuple[float, str]] = {}
    db_path = get_embeddings_db_path(vault)
    embeddings_enabled = bool(get_config("embeddings", "enabled", True, vault=vault))

    if db_path.exists() and embeddings_enabled:
        # ENH-003: in-process call shares the process-cached embedding model
        # instead of spawning vault_search.py and reloading ~67 MB ONNX per dedup
        # check. Lazy + guarded; on any failure fall back to lexical candidates.
        try:
            import parsidion.vault_search as vault_search  # noqa: PLC0415

            items = vault_search.search(
                query=topic_query,
                top=top_k,
                min_score=get_config("embeddings", "min_score", 0.45, vault=vault),
                vault=vault,
            )
        except Exception:  # noqa: BLE001
            items = []

        for item in items:
            try:
                score = float(item.get("score") or 0.0)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if score < threshold:
                continue
            stem = str(item.get("stem", ""))
            if not stem:
                continue
            # Read summary from the note file
            path_str = str(item.get("path", ""))
            summary = ""
            if path_str:
                try:
                    summary_lines = read_note_summary(Path(path_str)).splitlines()
                    summary = " ".join(summary_lines[:3]).strip()[:400]
                except (OSError, UnicodeDecodeError):
                    summary = stem
            candidates_by_stem[stem] = (score, summary)

    # Always fold in in-flight batch matches, and use lexical note_index fallback
    # whenever embeddings are disabled or produced no semantic candidates.
    if not embeddings_enabled or not candidates_by_stem or in_flight:
        lexical = _find_lexical_dedup_candidates(
            topic_query,
            vault,
            project=project,
            top_k=top_k,
            in_flight=in_flight,
        )
        for stem, score, summary in lexical:
            if stem not in candidates_by_stem or score > candidates_by_stem[stem][0]:
                # When embeddings are enabled and already found semantic hits,
                # only inject in-flight batch siblings (score boosted >= 0.55).
                if embeddings_enabled and candidates_by_stem:
                    in_flight_stems = {s for s, _, _ in (in_flight or [])}
                    if stem not in in_flight_stems:
                        continue
                candidates_by_stem[stem] = (score, summary)

    ranked = sorted(
        (
            (stem, score, summary)
            for stem, (score, summary) in candidates_by_stem.items()
        ),
        key=lambda x: -x[1],
    )
    return ranked[:top_k]
