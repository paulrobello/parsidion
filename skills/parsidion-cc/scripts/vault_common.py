"""Shared utility library for the Claude Vault knowledge management system.

Provides functions for parsing frontmatter, searching notes, building context
blocks, and managing the vault directory structure. Uses only Python stdlib.
"""

from pathlib import Path
import functools
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, date, timedelta
from typing import Any

__all__: list[str] = [
    # Module-level constants
    "VAULT_ROOT",
    "TEMPLATES_DIR",
    "VAULT_DIRS",
    "EXCLUDE_DIRS",
    # Environment helpers
    "env_without_claudecode",
    # Frontmatter and content parsing
    "parse_frontmatter",
    "get_body",
    # Note search functions
    "find_notes_by_project",
    "find_notes_by_tag",
    "find_notes_by_type",
    "find_recent_notes",
    "all_vault_notes",
    "read_note_summary",
    "build_context_block",
    # Project and vault management
    "get_project_name",
    "ensure_vault_dirs",
    "today_daily_path",
    "create_daily_note_if_missing",
    # Configuration
    "load_config",
    "get_config",
    # File locking
    "flock_exclusive",
    "flock_shared",
    "funlock",
    # Transcript helpers
    "extract_text_from_content",
    "read_last_n_lines",
    # Transcript analysis and queuing (shared by session_stop and subagent_stop hooks)
    "TRANSCRIPT_CATEGORIES",
    "TRANSCRIPT_CATEGORY_LABELS",
    "parse_transcript_lines",
    "detect_categories",
    "append_to_pending",
    # Utilities
    "slugify",
    "git_commit_vault",
    "EMBEDDINGS_DB_FILENAME",
    "get_embeddings_db_path",
    "ensure_note_index_schema",
    "query_note_index",
    # Content helpers
    "extract_title",
]

# NOTE: VAULT_ROOT and TEMPLATES_DIR are intentionally patched by the installer
# (install.py) via regex substitution to point at user-chosen paths.  They are
# module-level mutable state and are NOT thread-safe — this is acceptable because
# each hook script runs as a short-lived subprocess.  See ARC-007 in AUDIT.md.
VAULT_ROOT: Path = Path.home() / "ClaudeVault"
TEMPLATES_DIR: Path = Path.home() / ".claude" / "skills" / "parsidion-cc" / "templates"
VAULT_DIRS: list[str] = [
    "Daily",
    "Projects",
    "Languages",
    "Frameworks",
    "Patterns",
    "Debugging",
    "Tools",
    "Research",
    "Templates",
    "History",
]
EXCLUDE_DIRS: set[str] = {".obsidian", "Templates", ".git", ".trash", "TagsRoutes"}

EMBEDDINGS_DB_FILENAME: str = "embeddings.db"


def get_embeddings_db_path() -> Path:
    """Return the path to the vault's embeddings database.

    Returns:
        Path to VAULT_ROOT/embeddings.db.
    """
    return VAULT_ROOT / EMBEDDINGS_DB_FILENAME


def ensure_note_index_schema(conn: sqlite3.Connection) -> None:
    """Create the note_index table and indexes if they don't exist.

    Args:
        conn: An open sqlite3.Connection (caller sets WAL mode).
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS note_index (
            stem           TEXT    NOT NULL PRIMARY KEY,
            path           TEXT    NOT NULL,
            folder         TEXT    NOT NULL DEFAULT '',
            title          TEXT    NOT NULL DEFAULT '',
            summary        TEXT    NOT NULL DEFAULT '',
            tags           TEXT    NOT NULL DEFAULT '',
            note_type      TEXT    NOT NULL DEFAULT '',
            project        TEXT    NOT NULL DEFAULT '',
            confidence     TEXT    NOT NULL DEFAULT '',
            mtime          REAL    NOT NULL DEFAULT 0.0,
            related        TEXT    NOT NULL DEFAULT '',
            is_stale       INTEGER NOT NULL DEFAULT 0,
            incoming_links INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ni_folder    ON note_index(folder)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ni_note_type ON note_index(note_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ni_project   ON note_index(project)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ni_mtime     ON note_index(mtime DESC)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ni_tags      ON note_index(tags)")
    conn.commit()


def query_note_index(
    *,
    tag: str | None = None,
    folder: str | None = None,
    note_type: str | None = None,
    project: str | None = None,
    recent_days: int | None = None,
    limit: int = 200,
) -> list[Path] | None:
    """Query the note_index table in embeddings.db for fast metadata filtering.

    Returns None (not []) if the DB is absent or the table is missing,
    signalling the caller to fall back to a file walk.

    Args:
        tag: Exact tag token to match in the comma-separated tags column.
        folder: Exact folder name to match.
        note_type: Exact note_type value to match.
        project: Exact project value to match.
        recent_days: Only return notes modified within this many days.
        limit: Maximum number of results (default 200).

    Returns:
        List of existing Paths sorted by mtime descending, or None on DB error.
    """
    db_path = get_embeddings_db_path()
    if not db_path.exists():
        return None

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return None

    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='note_index'"
        ).fetchone()
        if row is None:
            return None

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
            # 4-pattern exact-token match to avoid partial hits (e.g. "python" must not
            # match "python-decorator").  Tags are stored as ", ".join(sorted(tags_list))
            # — canonical format enforced at write time in update_index.py and
            # build_embeddings.py.  See ARC-004.
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
        sql = f"SELECT path FROM note_index {where} ORDER BY mtime DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        # SEC-005: Reject any path that resolves outside VAULT_ROOT — guards
        # against a tampered embeddings.db injecting arbitrary file paths.
        vault_root_resolved = VAULT_ROOT.resolve()
        return [
            p
            for (path_str,) in rows
            if (p := Path(path_str)).exists()
            and p.resolve().is_relative_to(vault_root_resolved)
        ]
    except sqlite3.Error:
        return None
    finally:
        conn.close()


# Variables safe to pass through to child processes (avoids leaking secrets).
# SEC-006: ANTHROPIC_BASE_URL is intentionally included — environments that
# configure the Anthropic API via a proxy or custom base URL need it forwarded
# to child ``claude -p`` processes for AI features to work.
# ANTHROPIC_API_KEY is included so non-default key configurations (proxy, org,
# Bedrock) work correctly; without it all ``claude -p`` subprocess calls fail
# silently when the user has a non-standard API key configured.
_SAFE_ENV_KEYS: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "SHELL",
        "TERM",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_API_KEY",
    }
)


def env_without_claudecode() -> dict[str, str]:
    """Return a filtered copy of the current environment for child processes.

    Only includes variables listed in ``_SAFE_ENV_KEYS``, which avoids leaking
    secrets or triggering the Claude nesting guard (``CLAUDECODE``).

    Returns:
        A dict suitable for passing as ``env=`` to ``subprocess.run`` / ``Popen``.
    """
    return {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?\n)---\s*\n", re.DOTALL)
_YAML_LIST_INLINE_RE = re.compile(r"^\[(.*)]\s*$")
_SLUG_SPECIAL_RE = re.compile(r"[^a-z0-9\-]")
_SLUG_MULTI_HYPHEN_RE = re.compile(r"-{2,}")


def parse_frontmatter(content: str) -> dict[str, Any]:
    """Parse YAML frontmatter from markdown content using regex.

    **Supported YAML subset** (stdlib-only; not a full YAML 1.2 parser):

    - Scalars: bare strings, single/double-quoted strings, integers, floats,
      booleans (``true``/``false``/``yes``/``no``), ``null``/``~``, and
      date strings (``YYYY-MM-DD`` kept as strings).
    - Inline lists: ``key: [a, b, c]`` with optional quoting of items.
      Quoted items may contain commas.
    - Block sequence lists: ``key:`` followed by ``  - item`` lines.
    - Multi-line scalars: ``>`` (folded — joins continuation lines with a
      space), ``|`` (literal — joins with newlines), and strip variants
      ``>-`` / ``|-``.  Only indented continuation lines (indent > 0) are
      collected; the block ends at the next bare key or blank line.
    - Trailing inline comments (``# comment``) are stripped from scalar
      values, respecting surrounding quotes.

    **Not supported** (silently ignored or returned as bare strings):
    - Nested mappings deeper than 1 level (``key: {a: 1}`` or indented
      sub-mappings).
    - YAML anchors, aliases, and tags (``!!str``, etc.).
    - Multi-document streams (``---`` as a separator within a value).
    - Flow mappings.

    Returns an empty dict when no frontmatter block is found or when the
    opening/closing ``---`` delimiters are missing.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}

    raw = match.group(1)
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None
    # Multi-line scalar state: block_style is ">" (folded) or "|" (literal)
    block_style: str | None = None
    block_parts: list[str] = []

    def _flush_block() -> None:
        """Finalize a multi-line scalar block and store it in result."""
        nonlocal block_style, block_parts
        if current_key is not None and block_style is not None and block_parts:
            if block_style in (">", ">-"):
                result[current_key] = " ".join(block_parts)
            else:  # "|" or "|-"
                result[current_key] = "\n".join(block_parts)
        block_style = None
        block_parts = []

    for line in raw.splitlines():
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())

        # If we're collecting a multi-line scalar block, check for continuation
        if block_style is not None:
            if indent > 0 and stripped:
                block_parts.append(stripped)
                continue
            else:
                _flush_block()
                # Fall through to process this line normally

        # Continuation of a multi-line list (- item form)
        if (
            stripped.startswith("- ")
            and current_key is not None
            and current_list is not None
        ):
            current_list.append(_parse_scalar(stripped[2:].strip()))
            result[current_key] = current_list
            continue

        # If we were collecting a list and hit a non-list line, close it
        if current_list is not None:
            current_list = None

        # Skip blank lines and comments
        if not stripped or stripped.startswith("#"):
            continue

        # key: value pair
        colon_idx = line.find(":")
        if colon_idx == -1:
            continue

        key = line[:colon_idx].strip()
        value_str = line[colon_idx + 1 :].strip()

        if not key:
            continue

        current_key = key

        # Empty value -- could be start of a multi-line list
        if not value_str:
            current_list = []
            result[key] = current_list
            continue

        # Multi-line scalar block indicators: >, |, >-, |-
        if value_str in (">", "|", ">-", "|-"):
            block_style = value_str
            block_parts = []
            current_list = None
            continue

        # Inline list: [a, b, c]
        list_match = _YAML_LIST_INLINE_RE.match(value_str)
        if list_match:
            inner = list_match.group(1).strip()
            if not inner:
                result[key] = []
            else:
                items = [
                    _parse_scalar(item.strip()) for item in _split_list_items(inner)
                ]
                result[key] = items
            current_list = None
            continue

        # Scalar value
        result[key] = _parse_scalar(value_str)
        current_list = None

    # Flush any remaining block at end of frontmatter
    _flush_block()

    return result


def _split_list_items(text: str) -> list[str]:
    """Split a comma-separated list, respecting quoted strings."""
    items: list[str] = []
    current: list[str] = []
    in_quote: str | None = None

    for ch in text:
        if in_quote:
            current.append(ch)
            if ch == in_quote:
                in_quote = None
        elif ch in ('"', "'"):
            in_quote = ch
            current.append(ch)
        elif ch == ",":
            items.append("".join(current).strip())
            current = []
        else:
            current.append(ch)

    remaining = "".join(current).strip()
    if remaining:
        items.append(remaining)

    return items


def _parse_scalar(value: str) -> Any:
    """Parse a scalar YAML value into a Python type.

    Handles booleans, None/null, integers, floats, quoted strings, and bare
    strings. Date strings (YYYY-MM-DD) are kept as strings for simplicity.
    """
    # Strip surrounding quotes
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]

    lower = value.lower()
    if lower in ("true", "yes"):
        return True
    if lower in ("false", "no"):
        return False
    if lower in ("null", "~", ""):
        return None

    # Try integer
    try:
        return int(value)
    except ValueError:
        pass

    # Try float
    try:
        return float(value)
    except ValueError:
        pass

    return value


def get_body(content: str) -> str:
    """Return markdown content after the frontmatter block.

    If no frontmatter is found, returns the entire content.
    """
    match = _FRONTMATTER_RE.match(content)
    if not match:
        return content
    return content[match.end() :]


def extract_title(content: str, stem: str) -> str:
    """Extract the display title from a vault note.

    Searches the note body for the first top-level ``# `` heading (a single
    hash followed by a space, never ``##`` or deeper).  Falls back to the
    filename *stem* converted to title-case if no heading is found.

    This is the canonical title-extraction function for the vault.  All
    scripts that need a note title should call this instead of duplicating
    the logic.  See ARC-009.

    Args:
        content: Full note content (frontmatter + body).
        stem: Filename stem (without extension) used as fallback title.

    Returns:
        Title string — either the heading text or the humanized stem.
    """
    body = get_body(content)
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return stem.replace("-", " ").title()


def _walk_vault_notes() -> list[Path]:
    """Walk the vault tree and return all .md files, excluding EXCLUDE_DIRS and CLAUDE.md."""
    notes: list[Path] = []
    if not VAULT_ROOT.is_dir():
        return notes

    for dirpath, dirnames, filenames in os.walk(VAULT_ROOT):
        # Prune excluded directories in-place so os.walk skips them
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            if fname == "CLAUDE.md" and Path(dirpath) == VAULT_ROOT:
                continue
            notes.append(Path(dirpath) / fname)

    return notes


def _find_notes_by_field(field: str, value: str) -> list[Path]:
    """Find all notes where a frontmatter *field* matches *value* (case-insensitive).

    For scalar fields (``project``, ``type``), matches the value directly.
    For list fields (``tags``), matches if any element equals *value*.

    Args:
        field: The frontmatter field name to search (e.g. ``"project"``).
        value: The target value to match (compared case-insensitively).

    Returns:
        List of matching note paths.
    """
    matches: list[Path] = []
    target = value.lower()
    for note_path in _walk_vault_notes():
        try:
            content = note_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = parse_frontmatter(content)
        field_val = fm.get(field)
        if isinstance(field_val, str) and field_val.lower() == target:
            matches.append(note_path)
        elif isinstance(field_val, list):
            if any(
                isinstance(item, str) and item.lower() == target for item in field_val
            ):
                matches.append(note_path)
    return matches


def find_notes_by_project(project: str) -> list[Path]:
    """Find all notes with a matching ``project`` field in frontmatter."""
    result = query_note_index(project=project)
    if result is not None:
        return result
    return _find_notes_by_field("project", project)


def find_notes_by_tag(tag: str) -> list[Path]:
    """Find all notes containing the given tag in their ``tags`` list."""
    result = query_note_index(tag=tag)
    if result is not None:
        return result
    return _find_notes_by_field("tags", tag)


def find_notes_by_type(note_type: str) -> list[Path]:
    """Find all notes with a matching ``type`` field in frontmatter."""
    result = query_note_index(note_type=note_type)
    if result is not None:
        return result
    return _find_notes_by_field("type", note_type)


def find_recent_notes(days: int = 3) -> list[Path]:
    """Find notes modified within the last *days* days, sorted by mtime descending."""
    result = query_note_index(recent_days=days)
    if result is not None:
        return result
    cutoff = datetime.now() - timedelta(days=days)
    cutoff_ts = cutoff.timestamp()
    recent: list[tuple[float, Path]] = []

    for note_path in _walk_vault_notes():
        try:
            mtime = note_path.stat().st_mtime
        except OSError:
            continue
        if mtime >= cutoff_ts:
            recent.append((mtime, note_path))

    recent.sort(key=lambda x: x[0], reverse=True)
    return [path for _, path in recent]


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

    for note_path in notes:
        # Determine the relative folder/filename label
        try:
            rel = note_path.relative_to(VAULT_ROOT)
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


def get_project_name(cwd: str | None = None) -> str:
    """Extract a project name from *cwd* or the current directory.

    Uses the basename of the directory. If the directory is inside a git
    repository, uses the repository root basename instead.
    """
    if cwd is None:
        cwd = os.getcwd()

    path = Path(cwd).resolve()

    # Walk up to find a .git directory
    check = path
    while check != check.parent:
        if (check / ".git").exists():
            return check.name
        check = check.parent

    # Fallback: basename of the given directory
    return path.name


def ensure_vault_dirs() -> None:
    """Create any missing vault directories under ``VAULT_ROOT``."""
    VAULT_ROOT.mkdir(parents=True, exist_ok=True)
    for dirname in VAULT_DIRS:
        (VAULT_ROOT / dirname).mkdir(exist_ok=True)

    # Ensure Templates symlink points to the skill templates
    templates_link = VAULT_ROOT / "Templates"
    if templates_link.is_dir() and not templates_link.is_symlink():
        # Only create symlink if the directory is empty (freshly created by us)
        try:
            if not any(templates_link.iterdir()):
                templates_link.rmdir()
                templates_link.symlink_to(TEMPLATES_DIR)
        except OSError:
            pass
    elif not templates_link.exists():
        try:
            templates_link.symlink_to(TEMPLATES_DIR)
        except OSError:
            # Fall back to a plain directory if symlink fails
            templates_link.mkdir(exist_ok=True)


def today_daily_path() -> Path:
    """Return the path to today's daily note: ``Daily/YYYY-MM/DD.md``."""
    today = date.today()
    month_dir = f"{today.year:04d}-{today.month:02d}"
    day_file = f"{today.day:02d}.md"
    return VAULT_ROOT / "Daily" / month_dir / day_file


def create_daily_note_if_missing() -> Path:
    """Create today's daily note from the template if it doesn't exist.

    Replaces ``{{date}}`` in the template with today's date. Returns the
    path to the daily note (whether newly created or already existing).
    """
    daily_path = today_daily_path()

    if daily_path.exists():
        return daily_path

    # Ensure the Daily directory exists
    daily_path.parent.mkdir(parents=True, exist_ok=True)

    template_path = TEMPLATES_DIR / "daily.md"
    today_str = date.today().isoformat()

    if template_path.is_file():
        template_content = template_path.read_text(encoding="utf-8")
        content = template_content.replace("{{date}}", today_str)
    else:
        # Minimal fallback if template is missing
        content = (
            f"---\ndate: {today_str}\ntype: daily\ntags: [daily]\n---\n\n"
            f"## Sessions\n\n## Key Decisions\n\n## Problems Solved\n\n## Open Questions\n"
        )

    daily_path.write_text(content, encoding="utf-8")
    return daily_path


def all_vault_notes() -> list[Path]:
    """Return all ``.md`` files in the vault, excluding ``EXCLUDE_DIRS`` and ``CLAUDE.md``."""
    return _walk_vault_notes()


# ---------------------------------------------------------------------------
# Configuration loader (reads VAULT_ROOT/config.yaml)
# ---------------------------------------------------------------------------


def _strip_inline_comment(value: str) -> str:
    """Strip a trailing ``# comment`` from a YAML value, respecting quotes."""
    in_quote: str | None = None
    for i, ch in enumerate(value):
        if in_quote:
            if ch == in_quote:
                in_quote = None
        elif ch in ('"', "'"):
            in_quote = ch
        elif ch == "#" and i > 0 and value[i - 1] in (" ", "\t"):
            return value[:i].rstrip()
    return value


def _parse_config_yaml(text: str) -> dict[str, Any]:
    """Parse a simple YAML config with at most one level of nesting.

    Handles top-level scalars and single-level section dicts::

        top_key: value
        section:
          nested_key: value
    """
    result: dict[str, Any] = {}
    current_section: str | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        colon_idx = stripped.find(":")
        if colon_idx == -1:
            print(
                f"vault_common: ignoring unparsable config line: {stripped!r}",
                file=sys.stderr,
            )
            continue

        key = stripped[:colon_idx].strip()
        value_str = stripped[colon_idx + 1 :].strip()

        if not key:
            print(
                f"vault_common: ignoring config line with empty key: {stripped!r}",
                file=sys.stderr,
            )
            continue

        if indent == 0:
            if not value_str:
                # Section header — start collecting nested keys
                current_section = key
                result[key] = {}
            else:
                value_str = _strip_inline_comment(value_str)
                result[key] = _parse_scalar(value_str)
                current_section = None
        elif current_section is not None and indent > 0:
            value_str = _strip_inline_comment(value_str)
            section = result.get(current_section)
            if isinstance(section, dict):
                section[key] = _parse_scalar(value_str)
        elif indent > 0:
            # Indented line outside any section -- likely a typo
            print(
                f"vault_common: ignoring indented config line outside any section: {stripped!r}",
                file=sys.stderr,
            )

    return result


@functools.lru_cache(maxsize=1)
def _load_config_cached() -> dict[str, Any]:
    """Internal cached implementation — call ``load_config()`` instead.

    Wrapped with ``functools.lru_cache(maxsize=1)`` so results survive for
    the lifetime of the process (each hook is a single-threaded subprocess).
    Use ``load_config.cache_clear()`` in tests to reset between cases.
    """
    config_path = VAULT_ROOT / "config.yaml"
    if not config_path.is_file():
        return {}

    try:
        content = config_path.read_text(encoding="utf-8")
        return _parse_config_yaml(content)
    except (OSError, UnicodeDecodeError):
        return {}


def load_config() -> dict[str, Any]:
    """Load ``config.yaml`` from *VAULT_ROOT*.

    Results are cached per-process via ``functools.lru_cache``.  Call
    ``load_config.cache_clear()`` to invalidate the cache in tests when
    ``VAULT_ROOT`` has been patched to point at a different directory.

    Returns an empty dict when the file is missing or unreadable.
    """
    return _load_config_cached()


def _clear_config_cache() -> None:
    """Invalidate the ``load_config`` cache.

    Provided as a named helper so tests don't need to reach into the
    ``_load_config_cached`` internal name.  Equivalent to calling
    ``_load_config_cached.cache_clear()`` directly.
    """
    _load_config_cached.cache_clear()


def get_config(section: str, key: str, default: Any = None) -> Any:
    """Look up a config value with fallback to *default*.

    Distinguishes between a key that is absent (returns *default*) and a key
    that is explicitly set to ``null`` in config.yaml (returns ``None``).  This
    allows users to disable optional features by setting e.g. ``ai_model: null``.

    Args:
        section: Top-level section name (e.g. ``"session_start_hook"``).
        key: Key within the section (e.g. ``"max_chars"``).
        default: Value returned when the key is absent from the config file.

    Returns:
        The configured value (which may be ``None`` if explicitly set), or
        *default* when the key is absent.
    """
    config = load_config()
    section_dict = config.get(section)
    if isinstance(section_dict, dict):
        if key in section_dict:
            return section_dict[key]
    return default


# ---------------------------------------------------------------------------
# Cross-platform file locking
# ---------------------------------------------------------------------------

try:
    import fcntl as _fcntl

    def flock_exclusive(f) -> None:
        """Acquire an exclusive (write) lock on an open file descriptor."""
        _fcntl.flock(f, _fcntl.LOCK_EX)

    def flock_shared(f) -> None:
        """Acquire a shared (read) lock on an open file descriptor."""
        _fcntl.flock(f, _fcntl.LOCK_SH)

    def funlock(f) -> None:
        """Release a lock on an open file descriptor."""
        _fcntl.flock(f, _fcntl.LOCK_UN)

except ImportError:
    # Windows: fcntl is not available. File operations proceed without locking.
    # Race conditions between simultaneous Claude instances are acceptably rare
    # on Windows.
    def flock_exclusive(f) -> None:
        """Acquire an exclusive (write) lock on an open file descriptor (no-op on Windows)."""
        pass

    def flock_shared(f) -> None:
        """Acquire a shared (read) lock on an open file descriptor (no-op on Windows)."""
        pass

    def funlock(f) -> None:
        """Release a lock on an open file descriptor (no-op on Windows)."""
        pass


# ---------------------------------------------------------------------------
# Transcript helpers
# ---------------------------------------------------------------------------


def extract_text_from_content(content: str | list[dict]) -> str:
    """Extract plain text from a transcript message content field.

    Content can be a plain string or an array of content blocks (each with
    a ``type`` and ``text`` field for text blocks).

    Args:
        content: The message content -- either a string or list of blocks.

    Returns:
        Concatenated text from all text blocks.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def read_last_n_lines(filepath: Path, n: int) -> list[str]:
    """Read the last *n* lines of a file.

    Uses ``collections.deque(maxlen=n)`` to avoid loading the entire file
    into memory -- only the last *n* lines are retained.  See ARC-014.

    Args:
        filepath: Path to the file.
        n: Number of trailing lines to return.

    Returns:
        A list of the last n lines (or fewer if the file is shorter).
    """
    from collections import deque

    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            tail = deque(f, maxlen=n)
        return list(tail)
    except (OSError, UnicodeDecodeError):
        return []


def slugify(text: str) -> str:
    """Convert text to a kebab-case filename slug.

    Lowercases the text, replaces spaces and underscores with hyphens,
    removes special characters, and collapses multiple consecutive hyphens.
    """
    slug = text.lower().strip()
    slug = slug.replace(" ", "-").replace("_", "-")
    slug = _SLUG_SPECIAL_RE.sub("", slug)
    slug = _SLUG_MULTI_HYPHEN_RE.sub("-", slug)
    slug = slug.strip("-")
    return slug


# ---------------------------------------------------------------------------
# Transcript analysis helpers (shared by session_stop and subagent_stop hooks)
# ---------------------------------------------------------------------------

TRANSCRIPT_CATEGORIES: dict[str, list[str]] = {
    "error_fix": [
        "fixed",
        "the issue was",
        "root cause",
        "the error",
        "resolved by",
        "the fix",
        "bug was",
        "problem was",
        "workaround",
    ],
    "research": [
        "found that",
        "documentation says",
        "according to",
        "turns out",
        "discovered that",
        "learned that",
        "it appears",
        "the docs say",
        "the spec says",
    ],
    "pattern": [
        "pattern",
        "approach",
        "technique",
        "best practice",
        "convention",
        "idiom",
        "architecture",
        "design decision",
    ],
    "config_setup": [
        "configured",
        "installed",
        "set up",
        "added to",
        "created",
        "initialized",
        "migrated",
        "deployed",
    ],
}

TRANSCRIPT_CATEGORY_LABELS: dict[str, str] = {
    "error_fix": "Error Resolution",
    "research": "Research Findings",
    "pattern": "Pattern Discovery",
    "config_setup": "Config/Setup",
}


def parse_transcript_lines(lines: list[str]) -> list[str]:
    """Parse JSONL transcript lines and extract assistant message text.

    Args:
        lines: Raw JSONL lines from the transcript file.

    Returns:
        A list of text strings from assistant messages.
    """
    assistant_texts: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        if entry.get("type") != "assistant":
            continue

        message = entry.get("message", entry)
        content = message.get("content")
        if content is None:
            continue

        text = extract_text_from_content(content)
        if text.strip():
            assistant_texts.append(text)

    return assistant_texts


def detect_categories(texts: list[str]) -> dict[str, list[str]]:
    """Scan assistant texts for learnable content using keyword heuristics.

    Args:
        texts: List of assistant message texts.

    Returns:
        Dict mapping category keys to lists of matching text excerpts
        (each truncated to 500 chars).
    """
    found: dict[str, list[str]] = {}

    for text in texts:
        text_lower = text.lower()
        for category, keywords in TRANSCRIPT_CATEGORIES.items():
            for keyword in keywords:
                if keyword in text_lower:
                    if category not in found:
                        found[category] = []
                    excerpt = text[:500].strip()
                    if excerpt and excerpt not in found[category]:
                        found[category].append(excerpt)
                    break

    return found


def append_to_pending(
    transcript_path: Path,
    project: str,
    categories: dict[str, list[str]],
    force: bool = False,
    source: str = "session",
    agent_type: str | None = None,
) -> None:
    """Append a session entry to the pending summaries queue.

    Only appends when at least one significant category is detected,
    unless *force* is True (used when the AI gate has already decided).
    Guards against duplicates by session ID (transcript filename stem).

    Args:
        transcript_path: Path to the transcript JSONL file.
        project: The project name.
        categories: Detected categories mapping keys to excerpt lists.
        force: Skip the significance filter; queue unconditionally.
        source: Origin of the transcript — ``"session"`` or ``"subagent"``.
        agent_type: Subagent type (e.g. ``"Explore"``); only meaningful when
            *source* is ``"subagent"``.
    """
    all_keys = set(categories.keys())
    if not force:
        significant = {"error_fix", "research", "pattern"}
        if not (significant & all_keys):
            return

    pending_path = VAULT_ROOT / "pending_summaries.jsonl"
    session_id = transcript_path.stem

    entry: dict[str, object] = {
        "session_id": session_id,
        "transcript_path": str(transcript_path),
        "project": project,
        "categories": sorted(all_keys),
        "timestamp": datetime.now().isoformat(),
        "source": source,
    }
    if agent_type is not None:
        entry["agent_type"] = agent_type

    # SEC-010: On Windows, fcntl is unavailable so flock_exclusive() is a no-op
    # (see the ImportError fallback in the locking section above).  Concurrent writes
    # from multiple Claude instances on Windows may therefore produce duplicate entries
    # or interleaved JSON lines.  The deduplication check below provides a best-effort
    # guard, but is not race-free without OS-level locking.
    # If Windows atomic locking becomes critical, add a lock-file sidecar using:
    #   import msvcrt; msvcrt.locking(lf.fileno(), msvcrt.LK_LOCK, 1)
    try:
        with open(pending_path, "a+", encoding="utf-8") as f:
            flock_exclusive(f)
            try:
                f.seek(0)
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        existing = json.loads(line)
                        existing_id = (
                            existing.get("session_id")
                            or Path(existing.get("transcript_path", "")).stem
                        )
                        if existing_id == session_id:
                            return  # Already queued
                    except (json.JSONDecodeError, ValueError):
                        continue
                f.seek(0, 2)
                f.write(json.dumps(entry) + "\n")
            finally:
                funlock(f)
    except OSError:
        pass


def git_commit_vault(message: str, paths: list[Path] | None = None) -> bool:
    """Stage and commit changes to the vault git repository.

    Does nothing and returns False if VAULT_ROOT is not a git repository,
    if git is not available, or if ``git.auto_commit`` is ``false`` in config.
    Never raises exceptions.

    Args:
        message: Commit message.
        paths: Specific paths to stage. If None, stages all changes (``git add -A``).

    Returns:
        True if the commit succeeded, False otherwise.
    """
    if not get_config("git", "auto_commit", True):
        return False

    git_marker = VAULT_ROOT / ".git"
    if not (git_marker.is_dir() or git_marker.is_file()):
        return False

    try:
        # Stage files
        if paths:
            add_args = ["git", "add"] + [str(p) for p in paths]
        else:
            add_args = ["git", "add", "-A"]

        result = subprocess.run(
            add_args,
            cwd=str(VAULT_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False

        # Commit — exit code 1 with "nothing to commit" is not an error
        # SEC-002: message is caller-controlled but project names embedded in it
        # are sanitized by callers using safe_project (see git_commit_vault usages).
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(VAULT_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
