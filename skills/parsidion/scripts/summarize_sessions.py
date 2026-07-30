#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["anyio>=4.0.0,<5.0"]
# ///
"""On-demand AI-powered session summarizer for Parsidion vault.

Reads pending_summaries.jsonl, processes transcripts via the configured AI backend,
and writes structured vault notes to the appropriate vault folders.

Usage:
    uv run summarize_sessions.py [--sessions FILE] [--dry-run] [--model MODEL] [--persist]

ARC-015: Concurrency model rationale
This script uses ``anyio`` + ``anyio.create_task_group`` for async concurrency.
Structured concurrency guarantees from task groups (exception propagation,
automatic cancellation) are more robust than ``ThreadPoolExecutor`` futures.

vault_doctor.py uses ``concurrent.futures.ThreadPoolExecutor`` instead because
it is a stdlib-only script — adding anyio would violate that constraint.  Both
choices are intentional.  See ARC-015.

DONE(QA-018): Backlink helpers (find_related_by_tags, find_related_by_semantic,
inject_related_links, add_backlinks_to_existing) have been extracted into
vault_links.py.  This file now imports and delegates to that module.
"""

import argparse
import atexit
import contextlib
import json
import os
import re
import shutil
import string
import subprocess
import sys
import time
import traceback
from datetime import date, datetime
from functools import partial
from pathlib import Path
from typing import cast

import anyio  # type: ignore[import-untyped]
from anyio import to_thread  # type: ignore[import-untyped]

import ai_backend
import vault_common
import vault_links
from vault_path import is_path_inside_vault

# Constants, sentinels, enums, regexes, and default config values (ARC-009).
from summarizer._state_const import (  # noqa: F401 — re-exported for tests
    _ACTIVE_SESSION_GRACE_SECS,
    _DEAD,
    _DEAD_LETTER_RETENTION_DAYS,
    _DEFAULT_FOLDER,
    _DEFAULT_MAX_CLEANED_CHARS,
    _DEFAULT_MAX_PARALLEL,
    _DEFAULT_TRANSCRIPT_TAIL_BYTES,
    _DEFAULT_TRANSCRIPT_TAIL_LINES,
    _DEFERRED,
    _FAILURE_REASON_KEY,
    _FRONTMATTER_KEY_LINE_RE,
    _MAX_ATTEMPTS,
    _RELATED_LINE_RE,
    _RELATED_STEM_RE,
    _REQUIRED_FRONTMATTER_FIELDS,
    _SKIPPED,
    _STALE,
    _SUMMARIZER_STATE_FILENAME,
    _TYPE_FOLDERS,
    _VALID_NOTE_TYPES,
    _VALID_PROVENANCE_VALUES,
    FailureReason,
)

# File locking imported from vault_common (canonical implementation)
_flock_exclusive = vault_common.flock_exclusive
_flock_shared = vault_common.flock_shared
_funlock = vault_common.funlock


async def _run_summarizer_prompt(
    prompt: str,
    *,
    model: str | None,
    model_tier: ai_backend.ModelTier,
    purpose: str,
    timeout: int | float | None,
    vault: Path,
) -> str | None:
    """Run a summarizer prompt through the configured AI backend."""

    result = await to_thread.run_sync(
        partial(
            ai_backend.run_ai_prompt,
            prompt,
            model=model,
            model_tier=model_tier,
            purpose=purpose,
            timeout=timeout,
            vault=vault,
        )
    )
    return cast(str | None, result)


def _strip_code_fence(text: str) -> str:
    """Strip a single surrounding markdown code fence, if present.

    The summarizer backend occasionally wraps a JSON write-gate decision in a
    ```` ```json ```` fence. Without stripping, the ``startswith("{")`` check
    misses it and a "skip"/"merge" decision falls through to ``write_note``,
    which fails frontmatter validation and reports a false "failed" result.
    Only one outer fence is removed so a genuinely fenced note body is intact.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    newline = stripped.find("\n")
    if newline == -1:
        return stripped
    inner = stripped[newline + 1 :]
    end = inner.rfind("```")
    if end != -1:
        inner = inner[:end]
    return inner.strip()


from summarizer.failure import (  # noqa: E402,F401 — re-exported for tests
    _failure_record_retryable,
    _format_failure_record,
    _mark_failure,
)

from summarizer.progress import (  # noqa: E402,F401 — re-exported for tests
    _PROGRESS_FILE,
    _clear_progress,
    _write_progress,
)


def read_pending(pending_path: Path) -> list[dict[str, object]]:
    """Read all entries from the pending summaries file.

    Args:
        pending_path: Path to the JSONL pending file.

    Returns:
        List of entry dicts.
    """
    if not pending_path.exists():
        return []
    entries: list[dict[str, object]] = []
    try:
        with open(pending_path, encoding="utf-8") as f:
            _flock_shared(f)
            try:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except (json.JSONDecodeError, ValueError):
                        continue
            finally:
                _funlock(f)
    except OSError:
        pass
    return entries


def preprocess_transcript(
    transcript_path_str: str,
    tail_lines: int = _DEFAULT_TRANSCRIPT_TAIL_LINES,
    max_chars: int | None = _DEFAULT_MAX_CLEANED_CHARS,
    tail_bytes: int | None = _DEFAULT_TRANSCRIPT_TAIL_BYTES,
) -> str:
    """Pre-process a transcript JSONL file into a cleaned human/assistant dialogue.

    Reads last N lines, keeps only human and assistant text blocks,
    strips tool calls and tool results, and optionally truncates to a character limit.

    Args:
        transcript_path_str: String path to the transcript JSONL file.
        tail_lines: Number of trailing transcript lines to read.
        max_chars: Maximum output characters, or ``None`` to return all cleaned text.
        tail_bytes: Byte ceiling on the raw tail (see ``read_last_n_lines``); bounds
            transcripts with few-but-huge lines before cleaning.

    Returns:
        Cleaned dialogue string, truncated to *max_chars* when provided.
    """
    transcript_path = Path(transcript_path_str)
    if not transcript_path.is_file():
        return ""

    try:
        tail = vault_common.read_last_n_lines(transcript_path, tail_lines, tail_bytes)
    except OSError:
        return ""

    pairs: list[str] = []

    for raw_line in tail:
        line = raw_line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        role: str | None = None
        content: object = None

        message = entry.get("message")
        if isinstance(message, dict):
            role_raw = message.get("role")
            if isinstance(role_raw, str):
                role = role_raw
            content = message.get("content")

        if role is None:
            msg_type = entry.get("type")
            if isinstance(msg_type, str) and msg_type in {"user", "assistant"}:
                role = msg_type
                content = entry.get("content")

        # Codex format: type="response_item", payload.type="message",
        # payload.role="user"/"assistant", payload.content=[{type:"input_text"/"output_text"}]
        if role is None:
            payload = entry.get("payload")
            if isinstance(payload, dict) and payload.get("type") == "message":
                role_raw = payload.get("role")
                if isinstance(role_raw, str) and role_raw in {"user", "assistant"}:
                    role = role_raw
                    content = payload.get("content")

        if role not in {"user", "assistant"} or not content:
            continue

        # Extract text blocks only
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                # For user messages, skip tool result blocks.
                # For assistant messages, skip tool call/use blocks.
                block_type = block.get("type", "")
                if role == "user" and block_type in {"tool_result", "toolResult"}:
                    continue
                if role == "assistant" and block_type in {"tool_use", "toolCall"}:
                    continue
                if block_type in {"text", "input_text", "output_text"}:
                    t = block.get("text", "")
                    if isinstance(t, str) and t.strip():
                        parts.append(t.strip())
            text = "\n".join(parts).strip()
        else:
            continue

        if not text:
            continue

        label = "Human" if role == "user" else "Assistant"
        pairs.append(f"{label}: {text}")

    cleaned = "\n\n".join(pairs)
    if max_chars is None:
        return cleaned
    return cleaned[:max_chars]


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


def build_prompt(
    project: str,
    categories: list[str],
    cleaned_transcript: str,
    existing_tags: list[str],
    session_id: str,
    similar_notes: list[tuple[str, float, str]] | None = None,
) -> str:
    """Build the backend prompt for generating a vault note.

    Args:
        project: Project name.
        categories: Detected topic categories.
        cleaned_transcript: Pre-processed transcript text.
        existing_tags: All tags currently in the vault (for reuse preference).
        session_id: Runtime session ID to embed in frontmatter.
        similar_notes: Optional list of (stem, score, summary) tuples for
            near-duplicate notes found by semantic search.  When provided and
            non-empty, instructs the backend to merge rather than create a new note.

    Returns:
        Complete prompt string.
    """
    today = date.today().isoformat()
    cats_str = ", ".join(categories) if categories else "general"
    # ARC-029: tag-rules instruction and dedup-block construction are
    # extracted (the tag rule text was duplicated inline in the two branches
    # and had drifted in whitespace).
    tags_instruction = _render_tags_instruction(existing_tags)
    dedup_block = _render_dedup_block(similar_notes)
    valid_types = ", ".join(sorted(_VALID_NOTE_TYPES))
    template = _load_prompt_template("note_writing.txt")
    # SEC-004: the SYSTEM preamble (now in the template) instructs the model
    # to treat the transcript as passive data, not as instructions.
    return template.substitute(
        project=project,
        cats_str=cats_str,
        today=today,
        dedup_block=dedup_block,
        cleaned_transcript=cleaned_transcript,
        tags_instruction=tags_instruction,
        valid_types=valid_types,
        session_id=session_id,
    )


# ARC-029: shared kebab-case / short-singular tag rule used by both branches
# of _render_tags_instruction so a single edit updates both.
_TAG_RULES_COMMON = (
    "  NEVER use underscores — always kebab-case (hyphens);\n"
    "  prefer short singular tags: 'voxel' not 'voxel-engine', 'hook' not 'hooks')"
)


def _render_tags_instruction(existing_tags: list[str]) -> str:
    """Render the frontmatter ``tags`` instruction line for the note prompt.

    When the vault has existing tags, instruct the model to STRONGLY prefer
    them (the canonical source for tag reuse). When no tags exist (fresh
    vault), instruct generic tag generation. Both branches share the same
    kebab-case / short-singular rule via :data:`_TAG_RULES_COMMON`.
    """
    if existing_tags:
        tags_str = ", ".join(existing_tags)
        return (
            f"  tags (2-4 tags — STRONGLY prefer existing tags: {tags_str};\n"
            "  only introduce a new tag if none of the existing ones fit;\n"
            + _TAG_RULES_COMMON
        )
    return "  tags (2-4 relevant tags;\n" + _TAG_RULES_COMMON


def _render_dedup_block(
    similar_notes: list[tuple[str, float, str]] | None,
) -> str:
    """Render the optional ``IMPORTANT: similar notes found`` dedup block.

    Empty string when no similar notes were found. The JSON example uses
    literal ``{ }`` because the block is substituted into a
    ``string.Template`` (not an f-string) and needs no escaping.
    """
    if not similar_notes:
        return ""
    note_lines: list[str] = []
    for stem, score, summary in similar_notes[:3]:
        note_lines.append(f"  - [[{stem}]] (similarity {score:.2f}): {summary or stem}")
    notes_str = "\n".join(note_lines)
    return (
        "\n"
        "IMPORTANT: The following existing vault notes are highly similar to this session\n"
        "(semantic similarity >= threshold). Prefer MERGING new insights into one of them\n"
        "rather than creating a duplicate note. Only create a new note if the new insights\n"
        "are genuinely distinct from all of these:\n"
        "\n"
        f"{notes_str}\n"
        "\n"
        "If you decide to merge, output ONLY this JSON (no other text):\n"
        '{{"decision": "merge", "target": "[[stem-of-note-to-update]]", "new_content": "<full updated note markdown>"}}\n'
    )


# Cache loaded prompt templates so repeated calls in a summarizer run read
# each file once. ``string.Template`` is immutable so caching the parsed
# object is safe.
_PROMPT_TEMPLATE_CACHE: dict[str, string.Template] = {}


def _load_prompt_template(name: str) -> string.Template:
    """Load and cache ``templates/prompts/<name>`` as a string.Template.

    Resolution order mirrors resolve_templates_dir(): sibling ``templates/``
    dir next to this script (repo source layout) → installed
    ``~/.claude/skills/parsidion/templates``. Falls back to an empty
    Template on any read error so the caller's ``.substitute`` returns its
    placeholders verbatim — better than crashing a summarizer run because
    a prompt file is missing.
    """
    if name in _PROMPT_TEMPLATE_CACHE:
        return _PROMPT_TEMPLATE_CACHE[name]
    template_path = vault_common.resolve_templates_dir() / "prompts" / name
    try:
        content = template_path.read_text(encoding="utf-8")
    except OSError:
        content = ""
    template = string.Template(content)
    _PROMPT_TEMPLATE_CACHE[name] = template
    return template


def parse_note_type(note_content: str) -> str:
    """Extract the type field from note YAML frontmatter.

    Args:
        note_content: Full markdown note content.

    Returns:
        The type value, or 'research' as fallback.
    """
    match = re.search(r"^type:\s*(\S+)", note_content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return "research"


def parse_note_title_slug(note_content: str) -> str:
    """Extract the first ## heading from note content and slugify it.

    Args:
        note_content: Full markdown note content.

    Returns:
        Kebab-case slug, or 'session-note' as fallback.
    """
    # Prefer H1 (#) first; fall back to first H2 (##) for legacy notes
    match = re.search(r"^#(?!#)\s+(.+)$", note_content, re.MULTILINE)
    if not match:
        match = re.search(r"^##\s+(.+)$", note_content, re.MULTILINE)
    if match:
        heading = match.group(1).strip()
        slug = vault_common.slugify(heading)
        if slug:
            return slug
    return "session-note"


def inject_project_tag(note_content: str, project: str) -> str:
    """Ensure the project name appears in the tags frontmatter field.

    Parses the YAML tags block (list or inline) and appends the project tag
    if not already present. Leaves the note unchanged if no tags field exists
    or the project tag is already there.

    Args:
        note_content: Full markdown note content.
        project: Project name to inject as a tag.

    Returns:
        Updated note content with project tag present.
    """
    if not project or project == "unknown":
        return note_content

    # Match YAML list tags block:  tags:\n  - a\n  - b
    list_match = re.search(r"^(tags:\n(?:  - .+\n)+)", note_content, re.MULTILINE)
    if list_match:
        block = list_match.group(1)
        if f"  - {project}\n" not in block:
            new_block = block.rstrip("\n") + f"\n  - {project}\n"
            return note_content.replace(block, new_block, 1)
        return note_content

    # Match inline tags:  tags: [a, b, c]
    inline_match = re.search(r"^(tags:\s*\[)([^\]]*?)(\])", note_content, re.MULTILINE)
    if inline_match:
        existing = inline_match.group(2)
        existing_tags = [t.strip() for t in existing.split(",") if t.strip()]
        if project not in existing_tags:
            existing_tags.append(project)
            new_tags = ", ".join(existing_tags)
            new_line = f"{inline_match.group(1)}{new_tags}{inline_match.group(3)}"
            return (
                note_content[: inline_match.start()]
                + new_line
                + note_content[inline_match.end() :]
            )
        return note_content

    return note_content


def _validate_frontmatter(note_content: str) -> str | None:
    """Validate that AI-generated note content has required YAML frontmatter fields.

    SEC-004: Ensures adversarial transcript content cannot produce a note that
    bypasses the expected schema (e.g. a note with no frontmatter at all, or a
    malformed type that routes the note to an unexpected folder).

    Args:
        note_content: Full markdown note content to validate.

    Returns:
        None when the note is valid, or an error string describing the violation.
    """
    fm = vault_common.parse_frontmatter(note_content)
    if not fm:
        return "Note has no YAML frontmatter block"

    for field in _REQUIRED_FRONTMATTER_FIELDS:
        if field not in fm or fm[field] is None:
            return f"Frontmatter missing required field: '{field}'"

    note_type = str(fm.get("type", ""))
    if note_type not in _VALID_NOTE_TYPES:
        return f"Frontmatter 'type' has invalid value: {note_type!r}"

    tags = fm.get("tags")
    if not isinstance(tags, list) or len(tags) == 0:
        return "Frontmatter 'tags' must be a non-empty list"

    provenance = fm.get("provenance")
    if provenance is not None and provenance not in _VALID_PROVENANCE_VALUES:
        return f"Frontmatter 'provenance' has invalid value: {provenance!r}"

    return None


def _ensure_closing_frontmatter_delimiter(note_content: str) -> str:
    """Insert a missing closing ``---`` delimiter in AI-generated frontmatter.

    The note generator sometimes emits the opening ``---`` and every frontmatter
    field but forgets the closing ``---``. ``parse_frontmatter`` (used by the
    write-gate) requires both delimiters, so without repair the note is rejected
    as "Note has no YAML frontmatter block" even though the frontmatter is
    otherwise complete. This salvages that common failure mode by inserting the
    closing delimiter at the boundary between the frontmatter fields and the
    note body (first blank line, heading, or non-frontmatter line).

    No-op when the content has no opening ``---`` or already has a well-formed
    frontmatter block.
    """
    if not note_content.lstrip().startswith("---"):
        return note_content
    if vault_common.parse_frontmatter(note_content):
        return note_content

    lines = note_content.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.strip() == "---"), None)
    if start is None:
        return note_content

    insert_at: int | None = None
    for i in range(start + 1, len(lines)):
        line = lines[i]
        is_frontmatter_line = bool(
            _FRONTMATTER_KEY_LINE_RE.match(line) or line[:1] in (" ", "\t")
        )
        if (
            line.strip() == ""
            or line.lstrip().startswith("#")
            or not is_frontmatter_line
        ):
            insert_at = i
            break

    # No body boundary found, or frontmatter is empty (boundary right after the
    # opening delimiter) — leave unchanged and let validation report it.
    if insert_at is None or insert_at <= start + 1:
        return note_content

    return "".join(lines[:insert_at] + ["---\n"] + lines[insert_at:])


def _strip_leading_preamble(note_content: str) -> str:
    """Strip prose preamble the model emits before the opening ``---``.

    A third salvage defense for AI-generated notes, complementing the
    code-fence unwrap and the missing-closing-delimiter repair. The note
    prompt asks for "ONLY the raw markdown note … no preamble", but large
    models on the hierarchical summarization path sometimes preface the note
    with a line like "Here is the note:". Because ``parse_frontmatter``
    requires the content to start with ``---``, such otherwise-valid notes
    are rejected as "Note has no YAML frontmatter block" — and the other two
    salvages are no-ops because they also assume a leading ``---``.

    Drops everything before the first line that is exactly ``---``, but only
    when the remainder actually parses as frontmatter (after the standard
    closing-delimiter repair), so a body horizontal rule is never mistaken
    for a frontmatter delimiter. No-op when the content already starts with
    ``---`` or has no salvageable frontmatter at all.
    """
    if note_content.lstrip().startswith("---"):
        return note_content
    lines = note_content.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip() != "---":
            continue
        candidate = "".join(lines[i:])
        if vault_common.parse_frontmatter(
            _ensure_closing_frontmatter_delimiter(candidate)
        ):
            return candidate
    return note_content


def _note_body(note_content: str) -> str:
    """Return the markdown body of a note — everything after the YAML frontmatter.

    Used when merging a new note into an existing one on a slug collision: the
    existing note keeps its frontmatter and only the new note's body is appended.
    """
    text = note_content.lstrip()
    if not text.startswith("---"):
        return note_content.strip()
    lines = text.splitlines()
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1 :]).strip()
    return note_content.strip()


def _normalize_related_field(note_content: str) -> str:
    """Normalize the ``related:`` frontmatter field to a clean inline array of
    ``[[wikilinks]]``.

    Repairs common AI malformations — ``[stem]`` (single bracket), ``[["stem"]]``
    (quoted inside double brackets), ``"[[stem]]"`` (quoted wikilink) — by
    extracting every ``[[...]]`` span (de-aliasing, de-quoting) and rebuilding a
    single-line ``related: ["[[a]]", "[[b]]"]``. Tokens that aren't real
    ``[[wikilinks]]`` are dropped rather than echoed verbatim (which previously
    left malformed entries in written notes).
    """
    m = _RELATED_LINE_RE.search(note_content)
    if not m:
        return note_content
    stems: list[str] = []
    seen: set[str] = set()
    for sm in _RELATED_STEM_RE.finditer(m.group(1)):
        stem = sm.group(1)
        if stem.lower() not in seen:
            seen.add(stem.lower())
            stems.append(stem)
    new_line = (
        "related: [" + ", ".join(f'"[[{s}]]"' for s in stems) + "]"
        if stems
        else "related: []"
    )
    return note_content[: m.start()] + new_line + note_content[m.end() :]


def _clean_tag(value: object) -> str:
    """Normalize a raw tag candidate to vault form.

    Lowercases, strips leading dots (``.claude`` -> ``claude``), converts
    underscores to hyphens (the vault bans underscores in tags), and keeps
    only ``[a-z0-9-]``.
    """
    s = str(value).strip().lower().lstrip(".")
    s = s.replace("_", "-")
    s = re.sub(r"[^a-z0-9-]+", "", s)
    return s.strip("-")


def _backfill_tags_if_empty(
    note_content: str, project: str, categories: list[str]
) -> str:
    """Backfill the ``tags`` field when the model left it empty or absent.

    A recurring model failure mode on long, dense transcripts (notably
    read-only audit/review subagents) is valid frontmatter with ``tags: []``
    or no ``tags`` line at all. ``inject_project_tag`` only repairs the
    inline ``tags: []`` case when a usable project is known; this catches
    the rest by deriving tags from the note ``type`` (always present), the
    session ``project``, and ``categories``. Without it the note is refused
    at validation, re-queued, and eventually dead-lettered — even though
    the content was fine.

    No-op when ``tags`` is already a non-empty list (never clobbers valid
    tags). Mirrors the other pre-validation salvage functions.
    """
    fm = vault_common.parse_frontmatter(note_content)
    existing = fm.get("tags") if fm else None
    if isinstance(existing, list) and existing:
        return note_content

    candidates: list[str] = []
    if fm:
        note_type = fm.get("type")
        if isinstance(note_type, str) and note_type.strip():
            candidates.append(note_type)
    if project:
        candidates.append(project)
    candidates.extend(categories)

    tags: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        tag = _clean_tag(raw)
        if tag and tag != "unknown" and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    if not tags:
        tags = ["general"]  # last resort — field must be non-empty to validate

    new_line = "tags: [" + ", ".join(tags) + "]"

    # Replace an existing tags construct (inline list, YAML-list block, or
    # bare scalar) if present; otherwise insert after the opening '---'.
    inline = re.search(r"^tags:\s*\[[^\]]*\]", note_content, re.MULTILINE)
    if inline:
        return note_content[: inline.start()] + new_line + note_content[inline.end() :]
    block = re.search(r"^tags:\n(?:[ \t]+-.+\n)*", note_content, re.MULTILINE)
    if block:
        return (
            note_content[: block.start()]
            + new_line
            + "\n"
            + note_content[block.end() :]
        )
    bare = re.search(r"^tags:.*$", note_content, re.MULTILINE)
    if bare:
        return note_content[: bare.start()] + new_line + note_content[bare.end() :]
    first_nl = note_content.find("\n")
    if first_nl != -1 and note_content[:first_nl].strip() == "---":
        return (
            note_content[: first_nl + 1]
            + new_line
            + "\n"
            + note_content[first_nl + 1 :]
        )
    return note_content


def _backup_note(note_path: Path, vault: Path) -> None:
    """Copy *note_path* to today's pre-mutation backup dir, best-effort.

    SEC-107: mirrors ``vault_doctor._backup_note`` so the merge path defends
    a trusted, frequently-retrieved note the same way doctor's repair path
    does. First version of the day wins (an existing backup is not replaced).
    Raises ``OSError`` on copy failure so the caller can choose to abort the
    merge — unlike doctor's "never raise" contract, the merge caller already
    has a fallback (return None and let the attempts cap dead-letter it).
    """
    try:
        rel = note_path.relative_to(vault)
    except ValueError:
        return  # outside the vault -- nothing to back up
    dest = vault / ".trash" / "backup" / date.today().isoformat() / rel
    if dest.exists():
        return  # first version of the day already saved
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(note_path, dest)


def write_note(
    note_content: str,
    dry_run: bool,
    vault: Path,
    project: str = "",
    categories: list[str] | None = None,
) -> Path | None:
    """Write a generated vault note to the appropriate folder.

    Args:
        note_content: Full markdown note content.
        dry_run: If True, print without writing.
        vault: Path to the vault directory.

    Returns:
        Path where the note was written, or None on dry-run/error.
    """
    # Strip outer code fence if the model wrapped the entire note.
    # Only strip when the content after the opening fence starts with "---"
    # (YAML frontmatter), so inner ```python fences are left untouched.
    stripped = note_content.strip()
    if stripped.startswith("```"):
        first_newline = stripped.index("\n")
        inner = stripped[first_newline + 1 :]
        if inner.lstrip().startswith("---"):
            if inner.rstrip().endswith("```"):
                inner = inner.rstrip()[:-3].rstrip()
            note_content = inner

    # Salvage AI notes where the model prefaced the note with prose ("Here is
    # the note:") before the opening '---' — a third model failure mode. The
    # fence unwrap above and the closing-delimiter repair below both assume a
    # leading '---', so without this the note is rejected as having no
    # frontmatter at all.
    note_content = _strip_leading_preamble(note_content)

    # Salvage AI notes where the model emitted the opening '---' and all
    # frontmatter fields but omitted the closing '---' delimiter — a common
    # model failure mode. parse_frontmatter (used by the write-gate) requires
    # both delimiters, so without this otherwise-valid frontmatter is rejected.
    note_content = _ensure_closing_frontmatter_delimiter(note_content)
    # Normalize 'related' to clean [[wikilinks]] — repairs AI malformations
    # ([stem], [["stem"]], quoted wikilinks) before the note is written.
    note_content = _normalize_related_field(note_content)

    # Salvage notes whose model omitted an empty/absent tags field (common on
    # dense audit/review transcripts) — derive one from type/project/categories
    # so the note passes validation instead of being refused and dead-lettered.
    note_content = _backfill_tags_if_empty(note_content, project, categories or [])

    # SEC-004: Validate YAML frontmatter conformance before writing.  Rejects notes
    # that lack required fields or have an invalid type — guards against adversarial
    # transcript content producing malformed notes that bypass folder routing.
    fm_error = _validate_frontmatter(note_content)
    if fm_error:
        print(f"  Refusing to write note: {fm_error}", file=sys.stderr)
        return None

    note_type = parse_note_type(note_content)
    folder_name = _TYPE_FOLDERS.get(note_type, _DEFAULT_FOLDER)
    slug = parse_note_title_slug(note_content)

    # Never write to Daily/ for today — the stop hook manages today's daily note
    if folder_name == "Daily":
        today = date.today().isoformat()
        fm = re.search(r"^date:\s*(\S+)", note_content, re.MULTILINE)
        note_date = fm.group(1).strip() if fm else ""
        if note_date == today:
            print(
                f"  Skipping Daily note for today ({today}) — still being built.",
                file=sys.stderr,
            )
            return None

    # SEC-001: Guard against empty slug and path traversal outside vault root.
    if not slug:
        slug = "session-note"
    target_dir = vault / folder_name
    resolved = (target_dir / f"{slug}.md").resolve()
    # SEC-130: route through vault_path.is_path_inside_vault so this check
    # cannot drift from the other three containment sites.
    if not is_path_inside_vault(resolved, vault):
        raise ValueError(f"Refusing to write outside vault: {resolved}")
    # SEC-125: assign target_path := resolved so the validated path is the one
    # written. The previous code kept the un-resolved ``target_dir / f"{slug}.md"``
    # and validated ``resolved``, leaving a TOCTOU window between the check and
    # the write (a symlink or .. component inserted at write time escaped containment).
    target_path = resolved

    if dry_run:
        print(f"[dry-run] Would write: {target_path}")
        print("---")
        print(note_content[:500])
        print("...")
        return None

    target_dir.mkdir(parents=True, exist_ok=True)

    if target_path.exists():
        # A note with this slug already exists. Previously this stamped a -HHMM
        # suffix and wrote a sibling file, which accumulated hundreds of
        # near-duplicate timestamped notes. Merge the new note's body into the
        # existing note instead so no duplicate file is ever created.
        try:
            existing = target_path.read_text(encoding="utf-8")
        except OSError:
            existing = ""
        merged = (
            existing.rstrip()
            + f"\n\n## Session update {date.today().isoformat()}\n\n"
            + _note_body(note_content)
            + "\n"
        )
        # SEC-127: route through vault_fs.atomic_write_text so the rewrite is
        # crash-atomic and preserves the existing file's mode.
        try:
            vault_common.atomic_write_text(target_path, merged)
            print(
                f"  [dedup] Slug collision: merged into existing "
                f"{target_path.name} (no duplicate created)",
                file=sys.stderr,
            )
            return target_path
        except OSError as e:
            print(f"Error merging {target_path}: {e}", file=sys.stderr)
            return None

    # SEC-127: route through vault_fs.atomic_write_text (the create path was a
    # bare write_text; the merge path now uses the same primitive).
    try:
        vault_common.atomic_write_text(target_path, note_content)
        return target_path
    except OSError as e:
        print(f"Error writing {target_path}: {e}", file=sys.stderr)
        return None


async def _summarize_chunk(
    chunk_text: str,
    chunk_num: int,
    total_chunks: int,
    model: str | None,
    vault: Path,
) -> str:
    """Summarize one chunk of a long transcript using a cheaper model.

    Args:
        chunk_text: The transcript chunk to summarize.
        chunk_num: 1-based index of this chunk.
        total_chunks: Total number of chunks.
        model: Model ID to use for summarization.
        vault: Vault path used for backend configuration and execution context.

    Returns:
        A summary string (3-5 sentences). Falls back to a truncated version of
        chunk_text on failure.
    """
    # ARC-029: chunk-summarizer prompt lives in templates/prompts/chunk_summary.txt.
    prompt = _load_prompt_template("chunk_summary.txt").substitute(
        chunk_num=chunk_num,
        total_chunks=total_chunks,
        chunk_text=chunk_text,
    )
    try:
        result_text = await _run_summarizer_prompt(
            prompt,
            model=model,
            model_tier="small",
            purpose="summarizer-chunk",
            timeout=vault_common.get_config("summarizer", "ai_timeout", None),
            vault=vault,
        )
    except Exception:  # noqa: BLE001
        print(
            f"  [chunk-summarizer] Unexpected error on chunk {chunk_num}/{total_chunks}:\n"
            + traceback.format_exc(),
            file=sys.stderr,
        )
        result_text = None

    if result_text:
        return result_text
    # Fallback: return truncated raw chunk
    return chunk_text[:500]


async def preprocess_transcript_hierarchical(
    transcript_path_str: str,
    tail_lines: int,
    max_cleaned_chars: int,
    cluster_model: str | None,
    vault: Path,
    tail_bytes: int | None = None,
) -> str:
    """Pre-process a transcript, using hierarchical summarization for long ones.

    For transcripts within the character limit, returns the cleaned text
    unchanged. For transcripts exceeding the limit, splits into chunks,
    summarizes each chunk with a cheaper model, and returns the combined
    chunk summaries.

    Args:
        transcript_path_str: String path to the transcript JSONL file.
        tail_lines: Number of trailing transcript lines to read.
        max_cleaned_chars: Maximum characters threshold.
        tail_bytes: Byte ceiling on the raw tail, bounding huge-line transcripts.
        cluster_model: Model ID to use for chunk summarization.
        vault: Vault path used for chunk summarization backend calls.

    Returns:
        Cleaned dialogue string, or hierarchical summary string for long sessions.
    """
    cleaned = preprocess_transcript(transcript_path_str, tail_lines, None, tail_bytes)
    if len(cleaned) <= max_cleaned_chars:
        return cleaned

    # Split into chunks at newline boundaries
    chunk_size = max_cleaned_chars // 3
    chunks: list[str] = []
    remaining = cleaned
    while remaining:
        if len(remaining) <= chunk_size:
            chunks.append(remaining)
            break
        # Find a newline near the chunk boundary to avoid mid-sentence cuts
        split_pos = remaining.rfind("\n", 0, chunk_size)
        if split_pos == -1:
            split_pos = chunk_size
        chunks.append(remaining[:split_pos])
        remaining = remaining[split_pos:].lstrip("\n")

    total = len(chunks)
    print(
        f"  [hierarchical] Session too long ({len(cleaned)} chars), "
        f"summarizing {total} chunks..."
    )

    summaries: list[str] = []
    for i, chunk in enumerate(chunks):
        summary = await _summarize_chunk(chunk, i + 1, total, cluster_model, vault)
        summaries.append(summary)

    header = f"[Hierarchical summary from {total} transcript segments]"
    body = "\n\n".join(f"Segment {i + 1}:\n{s}" for i, s in enumerate(summaries))
    return f"{header}\n\n{body}"


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

    vault_search_script = Path(__file__).parent / "vault_search.py"
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


async def summarize_one(
    entry: dict[str, object],
    model: str | None,
    dry_run: bool,
    semaphore: anyio.Semaphore | None,
    existing_tags: list[str],
    persist: bool,
    vault: Path,
    tail_lines: int = _DEFAULT_TRANSCRIPT_TAIL_LINES,
    tail_bytes: int | None = _DEFAULT_TRANSCRIPT_TAIL_BYTES,
    max_cleaned_chars: int = _DEFAULT_MAX_CLEANED_CHARS,
    cluster_model: str | None = None,
    vault_notes: list[Path] | None = None,
    dead_lettered_ids: set[str] | None = None,
) -> tuple[dict[str, object], Path | str | None]:
    """Summarize one pending session entry.

    Args:
        entry: Pending entry dict with transcript_path, project, categories.
        model: Model ID to use, or ``None`` for the backend large-model default.
        dry_run: If True, print without writing.
        semaphore: Concurrency limiter.
        existing_tags: All tags currently in the vault.
        persist: Backwards-compatible no-op accepted from legacy CLI usage.
        vault: Path to the vault directory.
        tail_lines: Number of transcript lines to read.
        max_cleaned_chars: Maximum characters after cleaning.
        tail_bytes: Byte ceiling on the raw tail, bounding huge-line transcripts.
        cluster_model: Model ID for hierarchical chunk summarization, or ``None``
            for the backend small-model default.
        vault_notes: Pre-collected list of all vault note paths.  Passed
            through to backlink helpers to avoid redundant vault walks.
            When ``None``, each helper calls ``all_vault_notes()`` on its
            own.  See ARC-010.

    Returns:
        Tuple of (entry, written_path). written_path is None on dry-run,
        skip decision, or error.  written_path is ``_STALE`` when the
        transcript file no longer exists (entry should be purged).
    """
    del persist

    # ARC-048(c): semaphore may be ``None`` when the caller has already
    # acquired it (run_all's _run_one wrapper does this so it can write the
    # progress ``current`` field AFTER acquisition — see _run_one). Use a
    # nullcontext so this function still works either way.
    semaphore_cm = semaphore if semaphore is not None else contextlib.nullcontext()
    async with semaphore_cm:
        transcript_path_str = str(entry.get("transcript_path", ""))
        project = str(entry.get("project", "unknown"))
        raw_cats = entry.get("categories") or []
        categories = [str(c) for c in (raw_cats if isinstance(raw_cats, list) else [])]
        session_id = str(entry.get("session_id") or Path(transcript_path_str).stem)

        # Check for missing transcript before expensive preprocessing.
        # Subagent transcripts are ephemeral — Claude Code may rename or
        # delete them between hook fire time and summarizer run.  Mark
        # these as stale so they get purged from the pending queue.
        if not Path(transcript_path_str).is_file():
            print(
                f"  Purging stale entry (transcript missing): {transcript_path_str}",
                file=sys.stderr,
            )
            return entry, _STALE

        # A session already recorded in dead_letters (prior failure or
        # write-gate skip) must not be re-processed even if a stop hook
        # re-queued it — that would re-bill an AI call for a session already
        # judged not worth a note. Purge on sight.
        # ARC-028: callers fan out many summarize_one calls in parallel; the
        # dead-letter set is read ONCE per run (see run_all) and passed in to
        # avoid re-reading the file once per entry. Tests and one-shot callers
        # that omit the parameter fall back to a single read here.
        dead_ids = (
            dead_lettered_ids
            if dead_lettered_ids is not None
            else _dead_lettered_ids(vault)
        )
        if session_id in dead_ids:
            print(
                f"  Purging re-queued dead-lettered session {session_id[:8]}",
                file=sys.stderr,
            )
            return entry, _DEAD

        # Active-session guard: a transcript still being written (this very
        # session, or one whose process is still flushing) is mutating under us.
        # Summarizing it mid-flight is racy and yields partial notes, so defer
        # it — leave it in the queue for a later run once it's genuinely idle.
        try:
            _transcript_age = time.time() - Path(transcript_path_str).stat().st_mtime
        except OSError:
            _transcript_age = float("inf")
        if _transcript_age < _ACTIVE_SESSION_GRACE_SECS:
            print(
                f"  Deferring active session {session_id[:8]} "
                f"(transcript modified {int(_transcript_age)}s ago)",
                file=sys.stderr,
            )
            return entry, _DEFERRED

        cleaned = await preprocess_transcript_hierarchical(
            transcript_path_str,
            tail_lines,
            max_cleaned_chars,
            cluster_model,
            vault,
            tail_bytes,
        )
        if not cleaned:
            print(
                f"  Skipping {transcript_path_str}: could not read transcript",
                file=sys.stderr,
            )
            _mark_failure(entry, FailureReason.TRANSCRIPT_READ, transcript_path_str)
            return entry, None

        # Semantic dedup: find near-duplicate notes before calling the backend
        dedup_threshold: float = vault_common.get_config(
            "summarizer", "dedup_threshold", 0.80
        )
        # Content-rich query: include a slice of the cleaned transcript so
        # semantic dedup can match the SPECIFIC existing note. The coarse
        # project+categories query was too generic and missed near-duplicates,
        # causing duplicate notes to be written.
        query_seed = (cleaned or "")[:400].replace("\n", " ").strip()
        topic_query = f"{project} {' '.join(categories)} {query_seed}".strip()
        similar_notes = _find_dedup_candidates(
            topic_query, vault, threshold=dedup_threshold
        )

        prompt = build_prompt(
            project, categories, cleaned, existing_tags, session_id, similar_notes
        )

        try:
            result_text = await _run_summarizer_prompt(
                prompt,
                model=model,
                model_tier="large",
                purpose="summarizer-note",
                timeout=vault_common.get_config("summarizer", "ai_timeout", None),
                vault=vault,
            )
        except Exception as e:  # noqa: BLE001
            print(
                f"  Error querying AI backend for {transcript_path_str}: {e}\n"
                + traceback.format_exc(),
                file=sys.stderr,
            )
            # QA-009: return None (not _STALE/_SKIPPED) so the queue entry is
            # preserved and retried on the next run. Only purge for known-stale
            # or write-gate-skipped cases.
            _mark_failure(entry, FailureReason.AI_BACKEND_ERROR, str(e))
            return entry, None

        if not result_text:
            print(
                f"  No result from AI backend for {transcript_path_str}",
                file=sys.stderr,
            )
            _mark_failure(entry, FailureReason.NO_RESULT, transcript_path_str)
            return entry, None

        # Write-gate: check if the backend decided this session is not worth
        # saving or should merge. Strip a wrapping ```json code fence first —
        # otherwise a fenced skip/merge decision starts with a backtick, misses
        # this JSON branch, falls through to write_note, and fails frontmatter
        # validation (false "failed" result).
        candidate = _strip_code_fence(result_text)
        if candidate.startswith("{"):
            try:
                decision = json.loads(candidate)
                if isinstance(decision, dict):
                    if decision.get("decision") == "skip":
                        reason = decision.get("reason", "no reason given")
                        short_id = str(entry.get("session_id", "?"))[:8]
                        print(f"  [write-gate] Skipping session {short_id}: {reason}")
                        return entry, _SKIPPED
                    if decision.get("decision") == "merge":
                        # The backend chose to merge into an existing note.
                        # A malformed or unresolvable merge must NOT fall
                        # through to the generic write path — result_text is
                        # still raw decision JSON there and always fails
                        # frontmatter validation with a misleading error.
                        # Fail with the real reason instead; the attempts cap
                        # in remove_processed() bounds retries.
                        target_wikilink = str(decision.get("target", ""))
                        new_content = str(decision.get("new_content", ""))
                        if not new_content or not target_wikilink:
                            reason = "merge decision missing target or new_content"
                            print(f"  {reason}", file=sys.stderr)
                            _mark_failure(entry, FailureReason.MERGE_MALFORMED, reason)
                            return entry, None
                        # Extract stem from [[stem]] wikilink
                        target_stem = target_wikilink.strip("[]")
                        target_path = _resolve_note_stem(target_stem, vault)
                        if dry_run:
                            print(f"  [dry-run] Would merge into [[{target_stem}]]")
                            return entry, None
                        if target_path is None:
                            reason = (
                                f"merge target [[{target_stem}]] could not be resolved"
                            )
                            print(f"  {reason}", file=sys.stderr)
                            _mark_failure(
                                entry, FailureReason.MERGE_UNRESOLVABLE, reason
                            )
                            return entry, None
                        new_content = _normalize_related_field(new_content)
                        new_content, _stripped = vault_links.strip_unresolved_wikilinks(
                            new_content, vault
                        )
                        # SEC-107: validate AI-generated merge content the same
                        # way write_note validates a freshly created note. A
                        # crafted transcript could otherwise steer the model
                        # into emitting decision JSON whose ``new_content``
                        # overwrites a trusted, frequently-retrieved note with
                        # arbitrary/invalid frontmatter. Abort the merge (return
                        # the failure sentinel) when validation fails so the
                        # attempts cap in remove_processed bounds retries.
                        merge_fm_error = _validate_frontmatter(new_content)
                        if merge_fm_error:
                            print(
                                f"  Refusing to merge into [[{target_stem}]]: "
                                f"{merge_fm_error}",
                                file=sys.stderr,
                            )
                            _mark_failure(
                                entry,
                                FailureReason.MERGE_VALIDATION,
                                merge_fm_error,
                            )
                            return entry, None
                        # SEC-107 / SEC-125: containment re-check on the resolved
                        # target so a symlinked or path-traversal target cannot
                        # escape the vault at write time. ``_resolve_note_stem``
                        # currently returns indexed paths so containment holds
                        # today, but the model output (and therefore the target
                        # wikilink) is attacker-influenced — check anyway.
                        resolved_target = target_path.resolve()
                        if not is_path_inside_vault(resolved_target, vault):
                            reason = (
                                f"merge target [[{target_stem}]] resolves "
                                f"outside vault: {resolved_target}"
                            )
                            print(f"  {reason}", file=sys.stderr)
                            _mark_failure(
                                entry, FailureReason.MERGE_CONTAINMENT, reason
                            )
                            return entry, None
                        # SEC-107: back up the existing note before overwriting,
                        # mirroring vault_doctor._backup_note. A failed merge
                        # must never destroy the only copy of a trusted note.
                        try:
                            _backup_note(target_path, vault)
                        except OSError as backup_err:
                            print(
                                f"  Warning: merge backup failed for "
                                f"[[{target_stem}]]: {backup_err}",
                                file=sys.stderr,
                            )
                            _mark_failure(
                                entry, FailureReason.BACKUP_FAILED, str(backup_err)
                            )
                            return entry, None
                        # SEC-127: atomic write preserves the existing mode and
                        # is crash-safe (the create path uses the same primitive).
                        vault_common.atomic_write_text(resolved_target, new_content)
                        if _stripped:
                            print(
                                f"  [links] Stripped {_stripped} "
                                f"non-resolving wikilink(s)",
                                file=sys.stderr,
                            )
                        print(
                            f"  [dedup-merge] Updated [[{target_stem}]] "
                            f"instead of creating new note"
                        )
                        return entry, resolved_target
            except (json.JSONDecodeError, ValueError):
                pass  # Not a structured decision — treat as normal note

        result_text = inject_project_tag(result_text, project)
        written = write_note(result_text, dry_run, vault, project, categories)
        if written is None and not dry_run:
            # write_note already printed the specific refusal (frontmatter
            # validation, daily-note skip, ...) to stderr.
            _mark_failure(
                entry, FailureReason.NOTE_VALIDATION, "write_note returned None"
            )

        # Automated backlink suggestion
        if written is not None:
            # Strip wikilinks the backend invented that resolve to no vault note
            # — the recurring [[<project>]] "hub" link that mirrors the project
            # field but points at nothing. Runs before backlinks so the note only
            # ever holds real, resolving links; write_note stays a pure writer.
            try:
                _written_text = written.read_text(encoding="utf-8")
                _written_text, _stripped = vault_links.strip_unresolved_wikilinks(
                    _written_text, vault
                )
                if _stripped:
                    # SEC-127: route through atomic_write_text so the link
                    # rewrite is crash-atomic and preserves the note's mode.
                    vault_common.atomic_write_text(written, _written_text)
                    print(
                        f"  [links] Stripped {_stripped} non-resolving wikilink(s)",
                        file=sys.stderr,
                    )
            except (OSError, UnicodeDecodeError):
                pass  # best-effort; never fail the main flow
            try:
                new_fm = vault_common.parse_frontmatter(
                    written.read_text(encoding="utf-8")
                )
                note_tags = new_fm.get("tags") or []
                if not isinstance(note_tags, list):
                    note_tags = []
                tag_strs = [str(t) for t in note_tags]
                related_links = vault_links.find_related_by_semantic(
                    written, vault, max_links=5, tag_strs=tag_strs
                )
                if not related_links:
                    related_links = vault_links.find_related_by_tags(
                        written, tag_strs, vault_notes=vault_notes
                    )
                if related_links:
                    vault_links.inject_related_links(written, related_links)
                    vault_links.add_backlinks_to_existing(
                        written, related_links, vault_notes=vault_notes
                    )
                    print(
                        f"  [backlinks] Added {len(related_links)} related links "
                        f"to {written.name}"
                    )
            except (OSError, UnicodeDecodeError):
                pass  # Backlink step is best-effort; never fail the main flow

        return entry, written


async def run_all(
    entries: list[dict[str, object]],
    model: str | None,
    dry_run: bool,
    persist: bool,
    vault: Path,
    max_parallel: int = _DEFAULT_MAX_PARALLEL,
    tail_lines: int = _DEFAULT_TRANSCRIPT_TAIL_LINES,
    tail_bytes: int | None = _DEFAULT_TRANSCRIPT_TAIL_BYTES,
    max_cleaned_chars: int = _DEFAULT_MAX_CLEANED_CHARS,
    cluster_model: str | None = None,
) -> list[tuple[dict[str, object], Path | str | None]]:
    """Run all summarization tasks in parallel.

    Args:
        entries: List of pending entries.
        model: Model ID, or ``None`` for the backend large-model default.
        dry_run: If True, print without writing.
        persist: Backwards-compatible no-op accepted from legacy CLI usage.
        vault: Path to the vault directory.
        max_parallel: Maximum concurrent summarization tasks.
        tail_lines: Transcript tail lines per entry.
        tail_bytes: Byte ceiling on the raw tail per entry.
        max_cleaned_chars: Max cleaned chars per entry.
        cluster_model: Model ID for hierarchical chunk summarization, or ``None``
            for the backend small-model default.

    Returns:
        List of (entry, written_path) tuples.
    """
    # ARC-010: collect vault notes once per run and pass to every per-entry
    # function so we don't call all_vault_notes() up to 3x per entry.
    vault_notes: list[Path] = vault_common.all_vault_notes(vault)
    existing_tags = read_existing_tags(vault)
    project_names = read_project_names(vault_notes=vault_notes, vault=vault)
    # Filter project names out -- they're injected post-generation, not chosen by the model
    semantic_tags = [t for t in existing_tags if t not in project_names]
    # ARC-028: read the dead-letter set ONCE per run instead of once per entry.
    # At max_parallel=5 with 50 entries the previous code re-parsed
    # dead_letters.jsonl 50 times; the file grows monotonically so the cost
    # compounded across a long run.
    dead_lettered = _dead_lettered_ids(vault)
    semaphore = anyio.Semaphore(max_parallel)
    results: list[tuple[dict[str, object], Path | str | None]] = []
    total = len(entries)

    # Initialize progress (#13)
    _write_progress(total=total, processed=0, written=0, skipped=0, errors=0)

    # Counters for progress tracking (shared across async tasks via list trick)
    _progress_counters: list[int] = [
        0,
        0,
        0,
        0,
    ]  # [processed, written, skipped, errors]

    async def _run_one(entry: dict[str, object]) -> None:
        """Wrapper that collects the result of summarize_one into *results*.

        ARC-012: catches every unhandled exception so one malformed session
        cannot cancel its siblings through ``anyio.create_task_group()``'s
        default cancellation semantics. Cancellation (Ctrl-C) is still
        propagated by re-raising ``anyio.get_cancelled_exc_class()``.
        """
        project = str(entry.get("project", "?"))
        session_id = str(entry.get("session_id", ""))[:8]
        current = f"{project} [{session_id}]"

        # ARC-048(c): acquire the semaphore HERE and write the progress
        # ``current`` field only after acquisition. Previously the progress
        # write happened before summarize_one awaited the semaphore, so
        # ``vault-stats --summarizer-progress`` named the last-*queued*
        # session rather than the one actually being processed — at
        # max_parallel=5 every queued entry showed as "current" until the
        # semaphore drained. summarize_one now accepts ``semaphore=None``
        # and uses a nullcontext when called this way.
        async with semaphore:
            _write_progress(
                total=total,
                processed=_progress_counters[0],
                written=_progress_counters[1],
                skipped=_progress_counters[2],
                errors=_progress_counters[3],
                current=current,
            )

            try:
                result = await summarize_one(
                    entry,
                    model,
                    dry_run,
                    None,  # semaphore already acquired above
                    semantic_tags,
                    persist,
                    vault,
                    tail_lines,
                    tail_bytes,
                    max_cleaned_chars,
                    cluster_model,
                    vault_notes=vault_notes,
                    dead_lettered_ids=dead_lettered,
                )
            except anyio.get_cancelled_exc_class():
                # Ctrl-C / task cancellation must propagate so the user can
                # abort a run. Do NOT swallow it.
                raise
            except Exception as exc:  # noqa: BLE001 — task-group boundary
                # Catch every unhandled exception: an unguarded write path
                # inside summarize_one would otherwise cancel all siblings
                # via anyio.create_task_group()'s cancel-on-raise semantics,
                # leaving the queue uncleaned and the index not rebuilt.
                print(
                    f" Unhandled failure for session {session_id} "
                    f"(project {project}): {exc}",
                    file=sys.stderr,
                )
                traceback.print_exc()
                _mark_failure(entry, FailureReason.UNHANDLED, str(exc))
                result = (entry, None)

            results.append(result)
            _progress_counters[0] += 1  # processed
            _, written_path = result
            if written_path in (_STALE, _SKIPPED, _DEAD):
                _progress_counters[2] += 1  # skipped/purged (stale, write-gate, dead)
            elif written_path == _DEFERRED:
                pass  # deferred active session — left in queue, not an error
            elif written_path is not None:
                _progress_counters[1] += 1  # written
            else:
                _progress_counters[3] += 1  # errors
            _write_progress(
                total=total,
                processed=_progress_counters[0],
                written=_progress_counters[1],
                skipped=_progress_counters[2],
                errors=_progress_counters[3],
            )

    async with anyio.create_task_group() as tg:
        for entry in entries:
            tg.start_soon(_run_one, entry)

    return results


from summarizer.dead_letter import (  # noqa: E402,F401 — re-exported for tests
    _append_dead_letter,
    _dead_lettered_ids,
    _prune_dead_letters,
)


def remove_processed(
    pending_path: Path,
    processed_entries: list[dict[str, object]],
    failed: dict[str, object] | None = None,
) -> None:
    """Remove successfully processed entries from the pending file.

    Entries keyed in *failed* get their optional ``attempts`` counter
    incremented (absent = 0); an entry reaching ``_MAX_ATTEMPTS`` is purged
    (dead-lettered) with a stderr warning so a deterministic failure cannot
    retry — and re-bill an AI call — on every run forever.

    ARC-030: when a failed entry's record carries ``retryable: False`` (a
    :class:`FailureReason` member marked non-retryable), the entry is
    dead-lettered on the FIRST failed attempt rather than after _MAX_ATTEMPTS
    retries — a deterministic model-output failure (MERGE_VALIDATION,
    NOTE_VALIDATION, MERGE_CONTAINMENT, ...) would re-bill an AI call and
    re-touch the same target note on every retry, so it should surface
    immediately as a dead-letter warning instead.

    Args:
        pending_path: Path to the pending JSONL file.
        processed_entries: Entries that were successfully processed.
        failed: Map of session_id/transcript_path key -> failure record. The
            record is the structured dict produced by :func:`_mark_failure`
            (``{"kind", "retryable", "detail"}``). A legacy plain-string value
            is still accepted for backward compatibility and treated as
            retryable.
    """
    if not pending_path.exists():
        return

    failed = failed or {}
    # Prefer session_id for matching; fall back to transcript_path for entries
    # written by older versions of the hook that lack session_id.
    processed_ids = {
        str(e.get("session_id") or e.get("transcript_path", ""))
        for e in processed_entries
    }

    try:
        # Hold the exclusive lock on the REAL file for the whole read+swap so
        # concurrent append_to_pending() calls (vault_fs.py flocks the same
        # file) cannot interleave between the read and the replace.
        with open(pending_path, "r+", encoding="utf-8") as f:
            _flock_exclusive(f)
            try:
                remaining: list[str] = []
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        remaining.append(line)  # Keep malformed lines
                        continue
                    key = str(
                        entry.get("session_id") or entry.get("transcript_path", "")
                    )
                    if key in processed_ids:
                        continue
                    if key in failed:
                        record = failed[key]
                        retryable = _failure_record_retryable(record)
                        raw_attempts = entry.get("attempts")
                        attempts = (
                            raw_attempts if isinstance(raw_attempts, int) else 0
                        ) + 1
                        # ARC-030: non-retryable failures dead-letter on the
                        # first attempt; retryable ones wait for _MAX_ATTEMPTS.
                        dead_letter_now = (not retryable) or attempts >= _MAX_ATTEMPTS
                        if dead_letter_now:
                            label = _format_failure_record(record)
                            print(
                                f"Warning: dead-letter purge of session "
                                f"{entry.get('session_id') or entry.get('transcript_path', '?')} "
                                f"(project: {entry.get('project', 'unknown')}) "
                                f"{'(non-retryable) ' if not retryable else f'after {attempts} failed attempts '}"
                                f"last failure: {label}",
                                file=sys.stderr,
                            )
                            _append_dead_letter(pending_path, entry, attempts, label)
                            continue
                        entry["attempts"] = attempts
                        remaining.append(json.dumps(entry))
                        continue
                    remaining.append(line)
                # Crash-atomic rewrite: write survivors to a sibling .tmp and
                # swap it over the original (same pattern as
                # _write_summarizer_state / vault_fs.migrate_pending_paths).
                # SEC-109: create the tmp with mode 0o600 via os.open+os.fdopen
                # so the queue's owner-only protection survives the replace
                # (a plain ``tmp.write_text`` honours the process umask and
                # leaves the file world-readable, silently undoing the
                # 0o600 set on first creation by vault_fs.append_to_pending).
                tmp = pending_path.with_suffix(".jsonl.tmp")
                tmp_fd = os.open(
                    str(tmp),
                    os.O_CREAT | os.O_WRONLY | os.O_TRUNC,
                    0o600,
                )
                with open(tmp_fd, "w", encoding="utf-8") as out:
                    out.write("".join(line + "\n" for line in remaining))
                tmp.replace(pending_path)
            finally:
                _funlock(f)
    except OSError as e:
        print(f"Warning: could not update pending file: {e}", file=sys.stderr)


def _resolve(
    cli_value: bool | None,
    section: str,
    key: str,
    default: bool,
) -> bool:
    """Resolve a tri-state CLI bool against config and a default.

    ARC-042: centralises the "CLI overrides config overrides default" pattern
    that ``main`` previously inlined three times with subtly different shapes.
    Used for ``--rebuild-graph``, ``--graph-include-daily``, and ``--persist``
    so a YAML ``true`` can be overridden off from the CLI via ``--no-<flag>``
    (previously impossible: the ``or`` short-circuit meant CLI ``False`` was
    treated the same as "absent").

    Args:
        cli_value: The CLI-provided value, or ``None`` when the flag was not
            given (i.e. argparse ``default=None`` with ``BooleanOptionalAction``).
        section: Config section name (e.g. ``"summarizer"``).
        key: Key within the section (e.g. ``"rebuild_graph"``).
        default: Final fallback when neither CLI nor config provides a value.

    Returns:
        The resolved bool. Config values that are not bools fall back to
        *default* (defensive against a misconfigured YAML scalar).
    """
    if cli_value is not None:
        return cli_value
    configured = vault_common.get_config(section, key, default)
    if isinstance(configured, bool):
        return configured
    return default


def rebuild_index(
    vault: Path,
    rebuild_graph: bool | None = None,
    graph_include_daily: bool | None = None,
) -> None:
    """Run update_index.py to rebuild the vault index.

    ARC-027(a): the ``uv run`` invocation now passes ``--no-project`` so
    ``uv`` does not walk up from the inherited cwd (the user's project
    directory, for the auto-launch path) looking for a ``pyproject.toml``
    and syncing an unrelated project's dependencies. Without ``--no-project``
    the index rebuild fails when launched from inside a project whose own
    deps conflict; the failure was swallowed into a warning at the caller so
    the index silently went stale while the run reported success.

    Args:
        vault: Path to the vault directory.
        rebuild_graph: When True, pass ``--rebuild-graph`` to update_index.py
            so the visualizer graph.json is regenerated after indexing.
            ``None`` means "no flag" (leave update_index's own default).
        graph_include_daily: When True, also pass ``--graph-include-daily``
            (only meaningful when ``rebuild_graph`` is True). ``None`` means
            "no flag".
    """
    index_script = Path(__file__).parent / "update_index.py"
    if not index_script.exists():
        # Try installed location
        index_script = (
            Path.home()
            / ".claude"
            / "skills"
            / "parsidion"
            / "scripts"
            / "update_index.py"
        )
    if not index_script.exists():
        print(
            "Warning: update_index.py not found, skipping index rebuild",
            file=sys.stderr,
        )
        return
    # ARC-027(a): --no-project prevents uv from discovering a pyproject.toml
    # in the inherited cwd and syncing an unrelated project's dependencies.
    cmd = ["uv", "run", "--no-project", str(index_script), "--vault", str(vault)]
    if rebuild_graph:
        cmd.append("--rebuild-graph")
    if graph_include_daily:
        cmd.append("--graph-include-daily")
    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
            env=vault_common.env_without_claudecode(),
        )
        print("Vault index rebuilt.")
    except subprocess.CalledProcessError as e:
        print(f"Warning: index rebuild failed: {e.stderr}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        # QA-005: a hung update_index/build_graph child would otherwise stall
        # the summarizer mid-run and leave the index stale with no error.
        # 300 s mirrors the bound the graph rebuild applies to its own child.
        print(
            "Warning: index rebuild timed out after 300 s; index may be stale.",
            file=sys.stderr,
        )
    except OSError as e:
        print(f"Warning: could not run update_index.py: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Singleton guard — only one summarizer may run at a time per vault.
# Mirrors vault_doctor.py's doctor_state.json PID lock: claim on start,
# release via atexit, and detect stale PIDs (killed/crashed runs) so a dead
# lock never blocks the next run. Prevents the auto-summarizer launched by
# the stop hook from racing a manual `--run-doctor` invocation.
# ---------------------------------------------------------------------------


def _summarizer_state_file(vault_path: Path) -> Path:
    """Return the singleton-guard state file path for *vault_path*."""
    return vault_path / _SUMMARIZER_STATE_FILENAME


def _load_summarizer_state(vault_path: Path) -> dict:
    """Load summarizer_state.json, returning {} if missing/corrupt."""
    try:
        return json.loads(
            _summarizer_state_file(vault_path).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {}


def _write_summarizer_state(state: dict, vault_path: Path) -> None:
    """Write summarizer_state.json atomically via a sibling .tmp file."""
    dest = _summarizer_state_file(vault_path)
    dest.parent.mkdir(parents=True, exist_ok=True)  # vault dir may not exist yet
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(dest)


def _summarizer_claim_lock_file(vault_path: Path) -> Path:
    """Return the flock guard path serializing summarizer_state.json updates."""
    return _summarizer_state_file(vault_path).with_suffix(".lock")


def claim_summarizer_lock(vault_path: Path) -> bool:
    """Claim the singleton summarizer lock for *vault_path*.

    Returns True if this process now holds the lock, False if another
    summarizer is already running. A stale PID (dead process) is reclaimed.

    The read-check-write on summarizer_state.json is serialized under an
    exclusive flock on a sibling .lock file so two near-simultaneous
    SessionEnd hooks cannot both read the pre-claim state and both "win".
    """
    lock_path = _summarizer_claim_lock_file(vault_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)  # vault dir may not exist yet
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        _flock_exclusive(lock_file)
        try:
            state = _load_summarizer_state(vault_path)
            existing_pid = state.get("pid")
            if (
                existing_pid
                and existing_pid != os.getpid()
                and vault_common.is_process_running(existing_pid)
            ):
                print(
                    f"summarize_sessions is already running (PID {existing_pid}). Skipping.",
                    file=sys.stderr,
                )
                return False
            _write_summarizer_state(
                {
                    "pid": os.getpid(),
                    "last_run": datetime.now().isoformat(timespec="seconds"),
                },
                vault_path,
            )
            return True
        finally:
            _funlock(lock_file)


def release_summarizer_lock(vault_path: Path) -> None:
    """Clear our PID from summarizer_state.json (best-effort, idempotent)."""
    try:
        with open(
            _summarizer_claim_lock_file(vault_path), "a+", encoding="utf-8"
        ) as lock_file:
            _flock_exclusive(lock_file)
            try:
                state = _load_summarizer_state(vault_path)
                if state.get("pid") == os.getpid():
                    state.pop("pid", None)
                    _write_summarizer_state(state, vault_path)
            finally:
                _funlock(lock_file)
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    """Parse arguments and run the summarizer."""
    parser = argparse.ArgumentParser(
        description="AI-powered session summarizer for Parsidion vault",
    )
    parser.add_argument(
        "--sessions",
        metavar="FILE",
        help="Process an explicit JSONL file (same format as pending file)",
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        default=False,
        help="Preview what would be created without writing",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override large model (default: backend large default)",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        default=None,
        help="Accepted for backwards compatibility; currently unused.",
    )
    parser.add_argument(
        "--run-doctor",
        action="store_true",
        default=False,
        help="Run vault_doctor before summarizing to fix legacy pending paths and stale files.",
    )
    parser.add_argument(
        "--rebuild-graph",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Rebuild visualizer graph.json after indexing (passed to update_index.py "
            "--rebuild-graph). Tri-state: --rebuild-graph forces on, --no-rebuild-graph "
            "forces off (overrides a config 'true'), unset reads "
            "summarizer.rebuild_graph from config (default false)."
        ),
    )
    parser.add_argument(
        "--graph-include-daily",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Include Daily folder notes in the graph (only used with --rebuild-graph). "
            "Same tri-state semantics as --rebuild-graph; reads "
            "summarizer.graph_include_daily from config when unset."
        ),
    )
    parser.add_argument(
        "--vault",
        "-V",
        metavar="PATH|NAME",
        default=None,
        help="Vault path or named vault (default: ~/ParsidionVault, or legacy ~/ClaudeVault if it exists)",
    )
    args = parser.parse_args()

    # Resolve options: defaults → config → CLI args
    configured_model = vault_common.get_config("summarizer", "model", None)
    if args.model is not None:
        model: str | None = args.model
    elif isinstance(configured_model, str) and configured_model.strip():
        model = configured_model
    else:
        model = None
    # ARC-042: route tri-state bools through _resolve so a config 'true' can be
    # overridden off via --no-<flag>; the previous `or` short-circuit treated
    # an absent CLI flag the same as `--flag False`, so once config was true
    # there was no way to disable from the CLI for a single run.
    persist: bool = _resolve(args.persist, "summarizer", "persist", False)
    max_parallel: int = vault_common.get_config(
        "summarizer",
        "max_parallel",
        _DEFAULT_MAX_PARALLEL,
    )
    tail_lines: int = vault_common.get_config(
        "summarizer",
        "transcript_tail_lines",
        _DEFAULT_TRANSCRIPT_TAIL_LINES,
    )
    tail_bytes: int | None = vault_common.get_config(
        "summarizer",
        "transcript_tail_bytes",
        _DEFAULT_TRANSCRIPT_TAIL_BYTES,
    )
    max_cleaned_chars: int = vault_common.get_config(
        "summarizer",
        "max_cleaned_chars",
        _DEFAULT_MAX_CLEANED_CHARS,
    )
    configured_cluster_model = vault_common.get_config(
        "summarizer",
        "cluster_model",
        None,
    )
    cluster_model: str | None = (
        configured_cluster_model
        if isinstance(configured_cluster_model, str)
        and configured_cluster_model.strip()
        else None
    )

    rebuild_graph: bool | None = _resolve(
        args.rebuild_graph, "summarizer", "rebuild_graph", False
    )
    graph_include_daily: bool | None = _resolve(
        args.graph_include_daily, "summarizer", "graph_include_daily", False
    )

    # Resolve vault
    vault_path = vault_common.resolve_vault(explicit=args.vault, cwd=os.getcwd())
    vault_common.apply_configured_env_defaults(vault=vault_path)

    # Singleton guard — only one summarizer may run at a time per vault.
    if not claim_summarizer_lock(vault_path):
        sys.exit(1)
    atexit.register(release_summarizer_lock, vault_path)

    # Retention: prune dead-letter records older than the configured window so
    # dead_letters.jsonl (which accumulates every sticky write-gate skip) stays
    # bounded. Runs every invocation regardless of pending work.
    _retention_days = int(
        vault_common.get_config(
            "summarizer",
            "dead_letter_retention_days",
            _DEAD_LETTER_RETENTION_DAYS,
        )
    )
    _pruned_dl = _prune_dead_letters(vault_path, _retention_days)
    if _pruned_dl:
        print(
            f"Pruned {_pruned_dl} dead-letter record(s) older than "
            f"{_retention_days} day(s)"
        )

    # Optionally run vault_doctor first (--fix-all: frontmatter, tags, subfolders)
    if args.run_doctor:
        import subprocess as _sp
        import sys as _sys

        _doctor = Path(__file__).parent / "vault_doctor.py"
        print("Running vault_doctor --fix-all before summarizing…")
        # QA-005: bound the run so a hung vault_doctor cannot stall the
        # summarizer indefinitely. vault_doctor --fix-all is bounded work
        # (a few seconds on a small vault, ~1 min on a large one); 10
        # minutes is a generous ceiling for the rare AI-driven repair.
        try:
            _sp.run(
                [_sys.executable, str(_doctor), "--fix-all"],
                check=False,
                timeout=600,
            )
        except _sp.TimeoutExpired:
            print(
                "Warning: vault_doctor --fix-all timed out after 600s; "
                "continuing with summarization.",
                file=sys.stderr,
            )

    # Determine source file
    if args.sessions:
        source_path = Path(args.sessions).expanduser()
    else:
        # Default: pending file in resolved vault
        source_path = vault_path / "pending_summaries.jsonl"

    entries = read_pending(source_path)
    if not entries:
        print(f"No pending sessions in {source_path}")
        return

    model_label = model or "backend large default"
    print(f"Processing {len(entries)} session(s) with model {model_label}...")
    if args.dry_run:
        print("[dry-run mode — nothing will be written]")

    results: list[tuple[dict[str, object], Path | str | None]] = cast(
        list[tuple[dict[str, object], Path | str | None]],
        anyio.run(
            run_all,
            entries,
            model,
            args.dry_run,
            persist,
            vault_path,
            max_parallel,
            tail_lines,
            tail_bytes,
            max_cleaned_chars,
            cluster_model,
        ),
    )

    # Categorise results: written notes, stale (missing transcript), write-gate
    # skips, and hard failures. Stale entries are purged from the queue since
    # the transcript can never be recovered; write-gate skips are purged because
    # the backend already decided they are transient and retrying would loop.
    successful_entries: list[dict[str, object]] = []
    stale_entries: list[dict[str, object]] = []
    skipped_entries: list[dict[str, object]] = []
    failed_entries: list[dict[str, object]] = []
    deferred_entries: list[dict[str, object]] = []
    for entry, written_path in results:
        if written_path == _STALE:
            stale_entries.append(entry)
        elif written_path == _SKIPPED:
            skipped_entries.append(entry)
        elif written_path == _DEAD:
            # Re-queued dead-lettered session — purge like stale.
            stale_entries.append(entry)
        elif written_path == _DEFERRED:
            # Active session — leave in queue for a later run.
            deferred_entries.append(entry)
        elif written_path is not None:
            print(f"  Written: {written_path}")
            successful_entries.append(entry)
        elif not args.dry_run:
            failed_entries.append(entry)

    skipped_count = len(skipped_entries)
    failed_count = len(failed_entries)
    deferred_count = len(deferred_entries)

    if not args.dry_run:
        # Remove processed, stale, and write-gate skipped entries from pending
        # file; failed entries get their attempts counter bumped (and are
        # dead-lettered at _MAX_ATTEMPTS, or on attempt 1 when the failure is
        # classified non-retryable — see ARC-030).
        removable = successful_entries + stale_entries + skipped_entries
        # ARC-030: pass the structured failure record (dict) through to
        # remove_processed so it can honor the retryable flag. Fall back to
        # the legacy plain-string shape for entries queued by older code.
        failed_reasons: dict[str, object] = {
            str(e.get("session_id") or e.get("transcript_path", "")): (
                e[_FAILURE_REASON_KEY]
                if isinstance(e.get(_FAILURE_REASON_KEY), dict)
                else _format_failure_record(e.get(_FAILURE_REASON_KEY))
            )
            for e in failed_entries
        }
        if not args.sessions:
            # Make write-gate skips sticky: record them in dead_letters so a
            # future stop-hook re-queue is caught by the _DEAD guard instead of
            # re-billing an AI call to re-evaluate a session already judged
            # transient. (Skips are also dequeued below via `removable`.)
            for entry in skipped_entries:
                _raw_attempts = entry.get("attempts")
                _attempts = _raw_attempts if isinstance(_raw_attempts, int) else 0
                _append_dead_letter(
                    source_path,
                    entry,
                    _attempts,
                    "write-gate skip (transient)",
                )
        # ARC-048(d): always honor the dequeue lifecycle (queue OR --sessions
        # FILE). Previously --sessions skipped this block entirely, so a re-run
        # of the same FILE re-processed every entry, re-billed an AI call for
        # each, and (because write_note merges on slug collision) appended a
        # fresh ``## Session update`` block to each note — quietly compounding
        # duplicate content on every invocation. The sticky dead-letter write
        # above remains queue-only (it writes a sibling dead_letters.jsonl and
        # would litter an arbitrary source directory); --sessions mode still
        # dequeues via ``removable`` without that side effect.
        if failed_reasons:
            remove_processed(source_path, removable, failed=failed_reasons)
        elif removable:
            remove_processed(source_path, removable)

        # Rebuild vault index and commit all new notes + updated index
        if successful_entries:
            rebuild_index(
                vault_path,
                rebuild_graph=rebuild_graph,
                graph_include_daily=graph_include_daily,
            )
            # SEC-002: sanitize project names to prevent embedded newlines in commit messages
            projects = {
                str(e.get("project", "unknown"))
                .replace("\n", " ")
                .replace("\r", "")
                .strip()
                for e in successful_entries
            }
            project_str = ", ".join(sorted(projects))
            vault_common.git_commit_vault(
                f"chore(vault): add session notes [{project_str}]",
                vault=vault_path,
            )

    summary_parts = [f"{len(successful_entries)} written"]
    if stale_entries:
        summary_parts.append(f"{len(stale_entries)} purged (stale/dead-lettered)")
    if skipped_count:
        summary_parts.append(f"{skipped_count} skipped by write-gate")
    if deferred_count:
        summary_parts.append(f"{deferred_count} deferred (active)")
    if failed_count:
        summary_parts.append(f"{failed_count} failed")
    print(f"Done. {len(entries)} session(s) processed: {', '.join(summary_parts)}.")
    _clear_progress()  # Remove progress file when done (#13)


if __name__ == "__main__":
    main()
