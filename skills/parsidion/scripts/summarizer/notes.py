"""Note parsing, frontmatter validation, and the note writer.

Extracted from ``summarize_sessions.py`` (ARC-009).

None of these functions are test-monkeypatched (tests call them directly to
verify behaviour), so they extract cleanly to a leaf submodule.  The entry
shim re-exports every symbol so ``summarize_sessions.write_note`` etc. still
resolve.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import vault_common
from vault_fs import backup_note
from vault_path import is_path_inside_vault

from summarizer._state_const import (
    _DEFAULT_FOLDER,
    _FRONTMATTER_KEY_LINE_RE,
    _RELATED_LINE_RE,
    _RELATED_STEM_RE,
    _REQUIRED_FRONTMATTER_FIELDS,
    _TYPE_FOLDERS,
    _VALID_NOTE_TYPES,
    _VALID_PROVENANCE_VALUES,
)


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


def _stamp_prompt_version(note_content: str, prompt_version: str) -> str:
    """Inject ``prompt_version: <value>`` into a note's frontmatter.

    ENH-008 Step 3: stamps the ``<id>@<semver>`` of the prompt that produced
    the note so evaluation can slice note quality by prompt version. Inserted
    right after the ``session_id`` line when present (the two travel together
    — both are provenance for an AI-generated note), otherwise appended to the
    frontmatter block. No-op when the note has no frontmatter or already
    carries a ``prompt_version`` field.

    Additive: older code and older notes ignore the field, so stamping never
    invalidates existing frontmatter.
    """
    if not prompt_version:
        return note_content
    fm = vault_common.parse_frontmatter(note_content)
    if not fm:
        return note_content  # no frontmatter — let validation handle it
    if "prompt_version" in fm:
        return note_content  # already stamped (e.g. the model emitted it)
    lines = note_content.splitlines(keepends=True)
    # Locate the closing frontmatter delimiter (second bare '---').
    delim_indices = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
    if len(delim_indices) < 2:
        return note_content
    closer = delim_indices[1]
    stamp_line = f"prompt_version: {prompt_version}\n"
    # Prefer to insert right after a session_id line so the two provenance
    # fields sit together; otherwise insert just before the closer.
    insert_at = closer
    for i in range(delim_indices[0] + 1, closer):
        if _FRONTMATTER_KEY_LINE_RE.match(lines[i]) and lines[i].startswith(
            "session_id"
        ):
            insert_at = i + 1
            break
    lines.insert(insert_at, stamp_line)
    return "".join(lines)


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

    QA-001: thin wrapper around :func:`vault_fs.backup_note` (the canonical
    shared helper). The summarizer signature ``(note_path, vault)`` already
    matches the canonical order, so this is a direct delegation.

    SEC-107: mirrors ``vault_doctor._backup_note`` so the merge path defends
    a trusted, frequently-retrieved note the same way doctor's repair path
    does. First version of the day wins (an existing backup is not replaced).
    Raises ``OSError`` on copy failure so the caller can choose to abort the
    merge — unlike doctor's "never raise" contract, the merge caller already
    has a fallback (return None and let the attempts cap dead-letter it).
    """
    backup_note(note_path, vault)


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
