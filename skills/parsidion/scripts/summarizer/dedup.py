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
import subprocess
from pathlib import Path

import vault_common


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
            ``vault_common.all_vault_notes(vault)``.
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

        resolved_vault = vault or vault_common.resolve_vault()
        db_path = vault_common.get_embeddings_db_path(resolved_vault)
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

    notes = (
        vault_notes if vault_notes is not None else vault_common.all_vault_notes(vault)
    )
    projects: set[str] = set()
    for note_path in notes:
        try:
            content = note_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = vault_common.parse_frontmatter(content)
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
    db_path = vault_common.get_embeddings_db_path(vault)
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
        except Exception:  # noqa: BLE001
            pass
    # Fallback: walk vault notes
    for note in vault_common.all_vault_notes(vault):
        if note.stem == stem:
            return note
    return None


def _find_dedup_candidates(
    topic_query: str,
    vault: Path,
    threshold: float = 0.80,
    top_k: int = 5,
) -> list[tuple[str, float, str]]:
    """Search for existing notes semantically similar to *topic_query*.

    Used before the final summarization call to detect near-duplicates and
    prompt the backend to merge rather than create a new note.

    Args:
        topic_query: Free-text query derived from project name and categories.
        vault: Path to the vault directory.
        threshold: Minimum cosine similarity score to consider a duplicate.
        top_k: Maximum number of candidates to return.

    Returns:
        List of (stem, score, summary) tuples for notes above *threshold*,
        ordered by descending score.  Returns empty list when vault_search.py
        or embeddings.db is absent, or when the subprocess fails.
    """
    import json as _json

    # scripts/ is the parent of this submodule's directory (summarizer/).
    vault_search_script = Path(__file__).resolve().parent.parent / "vault_search.py"
    db_path = vault_common.get_embeddings_db_path(vault)
    if not vault_search_script.exists() or not db_path.exists():
        return []

    try:
        result = subprocess.run(
            [
                "uv",
                "run",
                "--no-project",
                str(vault_search_script),
                "--top",
                str(top_k),
                "--json",
                "--vault",
                str(vault),
                # SEC-128: ``--`` separates flags from the note-derived
                # positional so a topic_query beginning with "--" cannot
                # parse as a vault-search flag.
                "--",
                topic_query,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            env=vault_common.env_without_claudecode(),
        )
        if result.returncode != 0:
            return []
        items: list[dict[str, object]] = _json.loads(result.stdout)
    except (
        subprocess.TimeoutExpired,
        FileNotFoundError,
        OSError,
        _json.JSONDecodeError,
    ):
        return []

    candidates: list[tuple[str, float, str]] = []
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
                summary_lines = vault_common.read_note_summary(
                    Path(path_str)
                ).splitlines()
                summary = " ".join(summary_lines[:3]).strip()[:400]
            except (OSError, UnicodeDecodeError):
                summary = stem
        candidates.append((stem, score, summary))

    return candidates
