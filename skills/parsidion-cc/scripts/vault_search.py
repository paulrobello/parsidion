#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "fastembed>=0.6.0,<1.0",
#   "sqlite-vec>=0.1.6,<1.0",
#   "rich>=13.0",
# ]
# ///
"""Unified vault search: semantic (positional query) or metadata (filter flags).

Semantic mode — provide a natural language query:
    vault_search.py "sqlite vector search" --top 5
    vault_search.py "hook patterns" --json
    vault_search.py "qdrant embeddings" --min-score 0.4

Metadata mode — provide one or more filter flags (no positional query):
    vault_search.py --tag python --limit 10
    vault_search.py --folder Patterns
    vault_search.py --type debugging
    vault_search.py --project parsidion-cc
    vault_search.py --recent-days 7
    vault_search.py --tag rust --folder Patterns --text

Both modes output the same JSON structure. Semantic results include a ``score``
field (cosine similarity); metadata results set ``score`` to ``null``.
"""

import argparse
import json
import os
import re
import sqlite3
import struct
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# These scripts are not a proper package — sys.path.insert is intentional so
# each script can run standalone via ``uv run`` without requiring pip install.
sys.path.insert(0, str(Path(__file__).parent))
import vault_common  # noqa: E402

_DEFAULT_MODEL: str = vault_common.get_config(
    "embeddings", "model", "BAAI/bge-small-en-v1.5"
)


# ---------------------------------------------------------------------------
# Semantic search
# ---------------------------------------------------------------------------


def _open_db_semantic(db_path: Path) -> sqlite3.Connection:
    """Open embeddings DB with sqlite-vec extension loaded.

    Args:
        db_path: Path to the SQLite embeddings database.

    Returns:
        An open sqlite3.Connection with sqlite-vec loaded.
    """
    try:
        import sqlite_vec  # type: ignore[import-untyped]
    except ImportError:
        print(
            "sqlite-vec not installed — run: uv tool install --editable '.[tools]'",
            file=sys.stderr,
        )
        sys.exit(1)
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def _pack_vector(vec: list[float]) -> bytes:
    """Pack a float32 vector as a BLOB for sqlite-vec query parameter.

    Args:
        vec: List of float values.

    Returns:
        Packed binary representation.
    """
    return struct.pack(f"{len(vec)}f", *vec)


def search(
    query: str,
    top: int = 10,
    min_score: float = 0.45,
    model_name: str = _DEFAULT_MODEL,
    vault: Path | None = None,
) -> list[dict[str, object]]:
    """Search the vault for notes semantically similar to *query*.

    Returns an empty list gracefully when embeddings.db does not exist.

    Args:
        query: Natural language query string.
        top: Maximum number of results to return.
        min_score: Minimum cosine similarity threshold (0.0–1.0).
        model_name: fastembed model ID used when the index was built.
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        List of result dicts with keys: score, stem, title, folder, tags, path.
        Sorted by score descending.
    """
    db_path = vault_common.get_embeddings_db_path(vault)
    if not db_path.exists():
        return []

    try:
        from fastembed import TextEmbedding  # type: ignore[import-untyped]

        model = TextEmbedding(model_name=model_name)
        query_vec = list(model.embed([query]))[0]
        query_blob = _pack_vector(list(query_vec))
    except Exception:  # noqa: BLE001 — graceful fallback
        return []

    try:
        conn = _open_db_semantic(db_path)
        cursor = conn.execute(
            """
            SELECT stem, path, folder, title, tags,
                   (1.0 - vec_distance_cosine(embedding, ?)) AS score
            FROM note_embeddings
            ORDER BY score DESC
            LIMIT ?
            """,
            (query_blob, top),
        )
        rows = cursor.fetchall()
        conn.close()
    except Exception:  # noqa: BLE001 — graceful fallback
        return []

    results: list[dict[str, object]] = []
    for stem, path, folder, title, tags_str, score in rows:
        if score < min_score:
            continue
        tags_raw: str = tags_str if isinstance(tags_str, str) else ""
        tags: list[str] = [t.strip() for t in tags_raw.split(",") if t.strip()]
        results.append(
            {
                "score": round(float(score), 4),
                "stem": stem,
                "title": title,
                "folder": folder,
                "tags": tags,
                "path": path,
                "summary": "",
                "note_type": "",
                "project": "",
                "confidence": "",
                "mtime": None,
                "related": [],
                "is_stale": False,
                "incoming_links": 0,
            }
        )

    return results


# ---------------------------------------------------------------------------
# Metadata search
# ---------------------------------------------------------------------------


def query(
    *,
    tag: str | None = None,
    folder: str | None = None,
    note_type: str | None = None,
    project: str | None = None,
    recent_days: int | None = None,
    limit: int = 50,
    vault: Path | None = None,
) -> list[dict[str, object]]:
    """Query the note_index table for metadata-filtered results.

    Returns an empty list (not None) if the DB is absent or table missing.

    Args:
        tag: Exact tag token to match.
        folder: Exact folder name to match.
        note_type: Exact note_type to match.
        project: Exact project name to match.
        recent_days: Notes modified within this many days.
        limit: Maximum result count.
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        List of result dicts with score set to null, sorted by mtime descending.
    """
    db_path = vault_common.get_embeddings_db_path(vault)
    if not db_path.exists():
        return []

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError:
        return []

    try:
        if (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='note_index'"
            ).fetchone()
            is None
        ):
            return []

        # SECURITY: The SQL WHERE clause is assembled from literal condition fragments
        # only — no column names are ever derived from external input.  All filter
        # values are passed as bound parameters (?).  Column names used below form a
        # static whitelist: tags, folder, note_type, project, mtime.  Any future
        # addition of a user-supplied column name must be added to this whitelist and
        # reviewed for injection risk.
        # Static whitelist (documentation only — all conditions below are literals):
        #   _ALLOWED_QUERY_COLUMNS = {"tags", "folder", "note_type", "project", "mtime"}
        conditions: list[str] = []
        params: list[object] = []

        if tag is not None:
            # Tags are stored as ", ".join(sorted(tags_list)) — canonical format
            # enforced at write time in update_index.py and build_embeddings.py.
            # See ARC-004.
            conditions.append("(tags = ? OR tags LIKE ? OR tags LIKE ? OR tags LIKE ?)")
            params.extend([tag, f"{tag},%", f"%, {tag}", f"%, {tag},%"])

        if folder is not None:
            conditions.append("folder = ?")
            params.append(folder)

        if note_type is not None:
            conditions.append("note_type = ?")
            params.append(note_type)

        if project is not None:
            conditions.append("project = ?")
            params.append(project)

        if recent_days is not None:
            cutoff = (datetime.now() - timedelta(days=recent_days)).timestamp()
            conditions.append("mtime >= ?")
            params.append(cutoff)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = (
            f"SELECT stem, path, folder, title, summary, tags, note_type, "
            f"project, confidence, mtime, related, is_stale, incoming_links "
            f"FROM note_index {where} ORDER BY mtime DESC LIMIT ?"
        )
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    results: list[dict[str, object]] = []
    for row in rows:
        d = dict(row)
        tags_str: str = d.get("tags", "") or ""
        related_str: str = d.get("related", "") or ""
        results.append(
            {
                "score": None,
                "stem": d.get("stem", ""),
                "title": d.get("title", ""),
                "folder": d.get("folder", ""),
                "tags": [t.strip() for t in tags_str.split(",") if t.strip()],
                "path": d.get("path", ""),
                "summary": d.get("summary", ""),
                "note_type": d.get("note_type", ""),
                "project": d.get("project", ""),
                "confidence": d.get("confidence", ""),
                "mtime": d.get("mtime"),
                "related": [r.strip() for r in related_str.split(",") if r.strip()],
                "is_stale": bool(d.get("is_stale", 0)),
                "incoming_links": d.get("incoming_links", 0),
            }
        )
    return results


# ---------------------------------------------------------------------------
# Grep / full-text body search
# ---------------------------------------------------------------------------


def _get_all_notes_as_results(limit: int, vault: Path | None = None) -> list[dict[str, Any]]:
    """Return all vault notes as result dicts suitable for grep filtering.

    Tries the note_index DB first; falls back to a file walk via
    ``vault_common.all_vault_notes()``.

    Args:
        limit: Maximum number of notes to return.
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        List of result dicts with ``score`` set to ``None``.
    """
    vault = vault or vault_common.resolve_vault()
    db_path = vault_common.get_embeddings_db_path(vault)
    if db_path.exists():
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='note_index'"
            ).fetchone()
            if row is not None:
                sql = (
                    "SELECT stem, path, folder, title, summary, tags, note_type, "
                    "project, confidence, mtime, related, is_stale, incoming_links "
                    "FROM note_index ORDER BY mtime DESC LIMIT ?"
                )
                rows = conn.execute(sql, (limit,)).fetchall()
                conn.close()
                results: list[dict[str, Any]] = []
                for r in rows:
                    d = dict(r)
                    tags_str: str = d.get("tags", "") or ""
                    related_str: str = d.get("related", "") or ""
                    results.append(
                        {
                            "score": None,
                            "stem": d.get("stem", ""),
                            "title": d.get("title", ""),
                            "folder": d.get("folder", ""),
                            "tags": [
                                t.strip() for t in tags_str.split(",") if t.strip()
                            ],
                            "path": d.get("path", ""),
                            "summary": d.get("summary", ""),
                            "note_type": d.get("note_type", ""),
                            "project": d.get("project", ""),
                            "confidence": d.get("confidence", ""),
                            "mtime": d.get("mtime"),
                            "related": [
                                r2.strip()
                                for r2 in related_str.split(",")
                                if r2.strip()
                            ],
                            "is_stale": bool(d.get("is_stale", 0)),
                            "incoming_links": d.get("incoming_links", 0),
                        }
                    )
                return results
            conn.close()
        except sqlite3.Error:
            pass

    # Fallback: file walk
    fallback_results: list[dict[str, Any]] = []
    for path in vault_common.all_vault_notes(vault)[:limit]:
        stem = path.stem
        folder = path.parent.name if path.parent != vault else ""
        fallback_results.append(
            {
                "score": None,
                "stem": stem,
                "title": stem.replace("-", " ").title(),
                "folder": folder,
                "tags": [],
                "path": str(path),
                "summary": "",
                "note_type": "",
                "project": "",
                "confidence": "",
                "mtime": None,
                "related": [],
                "is_stale": False,
                "incoming_links": 0,
            }
        )
    return fallback_results


def _apply_grep_filter(
    results: list[dict[str, Any]],
    pattern: str,
    case_sensitive: bool,
    has_filters: bool,
    has_query: bool,
    limit: int,
    vault: Path | None = None,
) -> list[dict[str, Any]]:
    """Filter *results* (or all vault notes) by a regex pattern applied to note bodies.

    When used standalone (no metadata filters and no semantic query), fetches
    candidate notes from the DB or file walk first.

    Args:
        results: Existing results from semantic or metadata search (may be empty).
        pattern: Regular expression pattern for ``re.search``.
        case_sensitive: If True, disables ``re.IGNORECASE``.
        has_filters: Whether metadata filter flags were supplied.
        has_query: Whether a semantic query was supplied.
        limit: Max results cap when fetching all notes standalone.
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        Filtered list of result dicts whose note bodies match *pattern*.
    """
    flags = 0 if case_sensitive else re.IGNORECASE

    try:
        compiled = re.compile(pattern, flags)
    except re.error as exc:
        print(f"--grep: invalid regex pattern: {exc}", file=sys.stderr)
        sys.exit(2)

    # Standalone grep — no prior results from semantic or metadata mode
    if not has_filters and not has_query:
        results = _get_all_notes_as_results(limit, vault)

    matched: list[dict[str, Any]] = []
    for result in results:
        note_path_str = result.get("path", "")
        if not note_path_str:
            continue
        note_path = Path(note_path_str)
        if not note_path.exists():
            continue
        try:
            content = note_path.read_text(encoding="utf-8")
        except OSError:
            continue
        body = vault_common.get_body(content)
        if compiled.search(body):
            # Normalise score to None for grep-only results
            matched.append({**result, "score": result.get("score")})

    return matched


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _format_text(results: list[dict[str, Any]]) -> str:
    """Format results as human-readable one-line-per-note text.

    Args:
        results: List of result dicts.

    Returns:
        Newline-separated string.
    """
    lines: list[str] = []
    for r in results:
        score = r.get("score")
        tags = r.get("tags", [])
        tags_str = ", ".join(str(t) for t in tags) if isinstance(tags, list) else ""
        stale = " [STALE]" if r.get("is_stale") else ""
        tags_label = f" [{tags_str}]" if tags_str else ""
        score_label = f"{float(score):.4f}  " if isinstance(score, (int, float)) else ""
        lines.append(
            f"{score_label}{r['folder'] or '.'}/{r['stem']}{tags_label}{stale} — {r['title']}"
        )
    return "\n".join(lines)


def _format_rich(results: list[dict[str, Any]]) -> None:
    """Print results with Rich colorized one-line-per-note output.

    Score is colored green (>=0.80), yellow (>=0.60), or red (<0.60).
    Folder is cyan, stem bold, tags dim yellow, title bright white.

    Args:
        results: List of result dicts.
    """
    from rich.console import Console
    from rich.text import Text

    console = Console()
    for r in results:
        score = r.get("score")
        tags = r.get("tags", [])
        tags_str = ", ".join(str(t) for t in tags) if isinstance(tags, list) else ""
        is_stale = bool(r.get("is_stale"))

        line = Text()

        if isinstance(score, (int, float)):
            s = float(score)
            score_style = (
                "bold green" if s >= 0.80 else "yellow" if s >= 0.60 else "red"
            )
            line.append(f"{s:.4f}  ", style=score_style)

        line.append(r.get("folder") or ".", style="cyan")
        line.append("/", style="dim white")
        line.append(str(r.get("stem", "")), style="bold white")

        if tags_str:
            line.append(" [", style="dim white")
            line.append(tags_str, style="dim yellow")
            line.append("]", style="dim white")

        if is_stale:
            line.append(" [STALE]", style="bold red")

        line.append(" — ", style="dim white")
        line.append(str(r.get("title", "")), style="bright_white")

        console.print(line, soft_wrap=True)


# ---------------------------------------------------------------------------
# Env-var helpers
# ---------------------------------------------------------------------------

_ENV_PREFIX = "VAULT_SEARCH_"


def _env_float(name: str, fallback: float) -> float:
    """Return float from env var *name* or *fallback* on missing/invalid value.

    Args:
        name: Environment variable name (without prefix).
        fallback: Value to use when the variable is absent or non-numeric.

    Returns:
        Parsed float or fallback.
    """
    raw = os.environ.get(_ENV_PREFIX + name)
    if raw is None:
        return fallback
    try:
        return float(raw)
    except ValueError:
        return fallback


def _env_int(name: str, fallback: int) -> int:
    """Return int from env var *name* or *fallback* on missing/invalid value.

    Args:
        name: Environment variable name (without prefix).
        fallback: Value to use when the variable is absent or non-integer.

    Returns:
        Parsed int or fallback.
    """
    raw = os.environ.get(_ENV_PREFIX + name)
    if raw is None:
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback


# ---------------------------------------------------------------------------
# Interactive TUI mode (#9)
# ---------------------------------------------------------------------------


def _interactive_search(vault: Path | None = None) -> None:
    """Launch a curses-based interactive vault search TUI.

    Real-time search as you type. Arrow keys navigate results.
    Enter opens the selected note in $EDITOR. 'q' or Ctrl+C quits.
    Falls back to a simple line-input loop when curses is unavailable.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().
    """
    import curses
    import subprocess as _sp

    vault = vault or vault_common.resolve_vault()

    def _search_notes(q: str) -> list[dict[str, object]]:
        """Run a search and return results."""
        if not q.strip():
            return []
        db_path = vault_common.get_embeddings_db_path(vault)
        if db_path.exists():
            try:
                return search(query=q, top=10, min_score=0.45, vault=vault)
            except Exception:  # noqa: BLE001
                pass
        # Fallback: metadata title search via grep over all notes
        matched: list[dict[str, object]] = []
        q_lower = q.lower()
        for note_path in vault_common.all_vault_notes(vault)[:200]:
            if q_lower in note_path.stem.lower():
                try:
                    content = note_path.read_text(encoding="utf-8")
                except OSError:
                    continue
                fm = vault_common.parse_frontmatter(content)
                title = vault_common.extract_title(content, note_path.stem)
                tags = fm.get("tags", [])
                if isinstance(tags, str):
                    tags = [tags]
                matched.append(
                    {
                        "score": None,
                        "stem": note_path.stem,
                        "title": title,
                        "folder": note_path.parent.name
                        if note_path.parent != vault
                        else "",
                        "tags": tags,
                        "path": str(note_path),
                    }
                )
        return matched[:10]

    def _open_note(path_str: str) -> None:
        """Open a note in $EDITOR."""
        editor = os.environ.get("EDITOR", "nano")
        try:
            _sp.run([editor, path_str])
        except (OSError, KeyboardInterrupt):
            pass

    def _run_tui(stdscr: Any) -> None:
        curses.curs_set(1)
        curses.use_default_colors()
        if curses.has_colors():
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            curses.init_pair(3, curses.COLOR_YELLOW, -1)

        query_buf: list[str] = []
        results: list[dict[str, object]] = []
        selected = 0
        last_query = ""

        while True:
            stdscr.clear()
            h, w = stdscr.getmaxyx()

            # Header
            header = " vault-search interactive  [↑↓ navigate]  [Enter open]  [q quit] "
            stdscr.addstr(
                0,
                0,
                header[: w - 1],
                curses.A_REVERSE if curses.has_colors() else curses.A_BOLD,
            )

            # Query line
            prompt = "Search: "
            q_str = "".join(query_buf)
            stdscr.addstr(1, 0, f"{prompt}{q_str[: w - len(prompt) - 1]}")

            # Results
            max_results = h - 4
            for i, r in enumerate(results[:max_results]):
                y = i + 3
                if y >= h - 1:
                    break
                stem = str(r.get("stem", ""))
                title = str(r.get("title", ""))
                folder = str(r.get("folder", ""))
                score = r.get("score")
                score_str = (
                    f"{float(score):.3f} "
                    if isinstance(score, (int, float))
                    else "      "
                )
                line = f"{score_str}{folder}/{stem} — {title}"
                attr = curses.A_REVERSE if i == selected else curses.A_NORMAL
                if curses.has_colors() and i != selected:
                    attr = curses.color_pair(1) if i % 2 == 0 else curses.A_NORMAL
                stdscr.addstr(y, 0, line[: w - 1], attr)

            if not results and q_str:
                stdscr.addstr(3, 2, "No results found.", curses.A_DIM)

            # Status bar
            status = f" {len(results)} result(s) " if results else " Type to search... "
            stdscr.addstr(h - 1, 0, status[: w - 1], curses.A_DIM)

            # Reposition cursor
            cursor_col = min(len(prompt) + len(q_str), w - 1)
            stdscr.move(1, cursor_col)
            stdscr.refresh()

            # Re-search if query changed
            if q_str != last_query:
                last_query = q_str
                results = _search_notes(q_str)
                selected = 0

            # Input handling
            ch = stdscr.getch()

            if ch in (ord("q"), 27):  # q or ESC
                break
            elif ch in (curses.KEY_UP, ord("k")):
                selected = max(0, selected - 1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                selected = min(len(results) - 1, selected + 1) if results else 0
            elif ch in (curses.KEY_ENTER, 10, 13):
                if results and 0 <= selected < len(results):
                    path = str(results[selected].get("path", ""))
                    if path:
                        curses.endwin()
                        _open_note(path)
                        stdscr = curses.initscr()
                        curses.curs_set(1)
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                if query_buf:
                    query_buf.pop()
            elif 32 <= ch < 127:
                query_buf.append(chr(ch))

    try:
        curses.wrapper(_run_tui)
    except curses.error:
        # Terminal doesn't support curses — fall back to simple loop
        print(
            "Interactive mode (non-curses fallback — type query, Enter to search, blank to quit)"
        )
        while True:
            try:
                q = input("Search: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q:
                break
            results = _search_notes(q)
            if not results:
                print("  No results.")
                continue
            for i, r in enumerate(results):
                stem = r.get("stem", "")
                title = r.get("title", "")
                folder = r.get("folder", "")
                print(f"  [{i}] {folder}/{stem} — {title}")
            try:
                choice = input("Open [number] or Enter to continue: ").strip()
                if choice.isdigit() and int(choice) < len(results):
                    _open_note(str(results[int(choice)].get("path", "")))
            except (EOFError, KeyboardInterrupt):
                break


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point: semantic search or metadata filter depending on args."""
    parser = argparse.ArgumentParser(
        prog="vault-search",
        description=(
            "Search Claude Vault notes by meaning (semantic) or by metadata filters.\n\n"
            "Semantic mode: provide a QUERY string.\n"
            "Metadata mode: provide one or more filter flags (--tag, --folder, etc.).\n\n"
            "Environment variables (VAULT_SEARCH_*):\n"
            "  FORMAT=json|text|rich   default output format\n"
            "  MIN_SCORE=0.0–1.0       minimum cosine similarity threshold\n"
            "  TOP=N                   max semantic results\n"
            "  LIMIT=N                 max metadata results\n"
            "  MODEL=<id>              fastembed model ID\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Vault selection flag
    parser.add_argument(
        "--vault",
        "-V",
        metavar="PATH|NAME",
        default=None,
        help="Vault path or named vault (default: ~/ClaudeVault)",
    )

    # Positional — optional; triggers semantic mode when present
    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="Natural language query for semantic search. Omit to use metadata filters.",
    )

    # Semantic-only flags
    _cfg_top_k: int = vault_common.get_config("embeddings", "top_k", 10)
    _cfg_min_score: float = vault_common.get_config("embeddings", "min_score", 0.45)
    _eff_top_k = _env_int("TOP", _cfg_top_k)
    _eff_min_score = _env_float("MIN_SCORE", _cfg_min_score)
    _eff_model = os.environ.get(_ENV_PREFIX + "MODEL", _DEFAULT_MODEL)
    parser.add_argument(
        "--top",
        "-n",
        type=int,
        default=_eff_top_k,
        metavar="N",
        help=f"Semantic: max results (default {_eff_top_k}, env: VAULT_SEARCH_TOP).",
    )
    parser.add_argument(
        "--min-score",
        "-s",
        type=float,
        default=_eff_min_score,
        metavar="FLOAT",
        help=(
            f"Semantic: minimum cosine similarity 0.0–1.0 "
            f"(default {_eff_min_score}, env: VAULT_SEARCH_MIN_SCORE)."
        ),
    )
    parser.add_argument(
        "--model",
        "-m",
        default=_eff_model,
        metavar="MODEL",
        help=f"Semantic: fastembed model ID (default: {_eff_model}, env: VAULT_SEARCH_MODEL).",
    )

    # Metadata filter flags
    parser.add_argument(
        "--tag", "-T", metavar="TAG", help="Metadata: filter by exact tag token."
    )
    parser.add_argument(
        "--folder",
        "-f",
        metavar="FOLDER",
        help="Metadata: filter by exact folder name.",
    )
    parser.add_argument(
        "--type",
        "-k",
        metavar="TYPE",
        dest="note_type",
        help="Metadata: filter by note type.",
    )
    parser.add_argument(
        "--project", "-p", metavar="PROJECT", help="Metadata: filter by project name."
    )
    parser.add_argument(
        "--recent-days",
        "-d",
        metavar="N",
        type=int,
        help="Metadata: notes modified within the last N days.",
    )

    # Grep / full-text body search flags
    parser.add_argument(
        "--grep",
        "-G",
        metavar="PATTERN",
        default=None,
        help=(
            "Full-text: filter notes whose body matches PATTERN (re.search). "
            "Case-insensitive by default; use --grep-case to make it case-sensitive. "
            "Can be combined with metadata filters or used standalone."
        ),
    )
    parser.add_argument(
        "--grep-case",
        action="store_true",
        default=False,
        help="Full-text: disable case-insensitive matching for --grep.",
    )

    _eff_limit = _env_int("LIMIT", 50)
    parser.add_argument(
        "--limit",
        "-l",
        metavar="N",
        type=int,
        default=_eff_limit,
        help=f"Metadata: maximum number of results (default: {_eff_limit}, env: VAULT_SEARCH_LIMIT).",
    )

    # Output format — VAULT_SEARCH_FORMAT=json|text|rich sets the default
    _eff_format = os.environ.get(_ENV_PREFIX + "FORMAT", "json").lower()
    if _eff_format not in {"json", "text", "rich"}:
        _eff_format = "json"
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--json",
        "-j",
        dest="output_format",
        action="store_const",
        const="json",
        help="JSON array output.",
    )
    output_group.add_argument(
        "--text",
        "-t",
        dest="output_format",
        action="store_const",
        const="text",
        help="Human-readable one-line-per-note output.",
    )
    output_group.add_argument(
        "--rich",
        "-r",
        dest="output_format",
        action="store_const",
        const="rich",
        help="Rich colorized one-line-per-note output.",
    )
    parser.set_defaults(output_format=_eff_format)

    # Interactive mode flag
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        default=False,
        help="Launch interactive curses TUI for real-time search.",
    )

    args = parser.parse_args()

    # Resolve vault path
    vault_path = vault_common.resolve_vault(explicit=args.vault, cwd=os.getcwd())

    # Interactive mode — runs before the normal search logic
    if args.interactive:
        _interactive_search(vault_path)
        return

    _filter_flags = (
        args.tag,
        args.folder,
        args.note_type,
        args.project,
        args.recent_days,
    )
    has_query = args.query is not None
    has_filters = any(f is not None for f in _filter_flags)
    has_grep = args.grep is not None

    if not has_query and not has_filters and not has_grep:
        parser.error(
            "Provide a search QUERY for semantic search, or at least one filter flag "
            "(--tag, --folder, --type, --project, --recent-days, --grep) for metadata/grep search."
        )

    if has_query and has_filters:
        parser.error(
            "Semantic search (QUERY) and metadata filters are mutually exclusive. "
            "Use one mode at a time."
        )

    if has_query:
        db_path = vault_common.get_embeddings_db_path(vault_path)
        if not db_path.exists():
            print(
                "embeddings.db not found — run build_embeddings.py first",
                file=sys.stderr,
            )
            sys.exit(0)
        results = search(
            query=args.query,
            top=args.top,
            min_score=args.min_score,
            model_name=args.model,
            vault=vault_path,
        )
    else:
        results = query(
            tag=args.tag,
            folder=args.folder,
            note_type=args.note_type,
            project=args.project,
            recent_days=args.recent_days,
            limit=args.limit,
            vault=vault_path,
        )

    # --grep post-filter: applied after semantic or metadata results, or standalone
    if has_grep:
        results = _apply_grep_filter(
            results=results,
            pattern=args.grep,
            case_sensitive=args.grep_case,
            has_filters=has_filters,
            has_query=has_query,
            limit=args.limit,
            vault=vault_path,
        )

    if args.output_format == "text":
        print(_format_text(results))
    elif args.output_format == "rich":
        _format_rich(results)
    else:
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
