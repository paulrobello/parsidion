"""Shared backlink helpers for Parsidion vault note cross-referencing.

This module provides utilities for finding related vault notes (by tag overlap
or semantic similarity) and injecting bidirectional wikilinks into note
frontmatter.  It is stdlib-only and can be imported by any vault script
without introducing third-party dependencies.

Extracted from ``summarize_sessions.py`` to enable reuse by both the
summarizer and ``parsidion-mcp``.
"""

import os
import re
from collections.abc import Callable
from pathlib import Path

# ARC-001: import siblings directly — core/ must not round-trip through the
# deprecated root-shim facade.
from .vault_config import get_config
from .vault_index import _FRONTMATTER_RE, all_vault_notes_walk, parse_frontmatter
from .vault_path import get_embeddings_db_path, resolve_vault

__all__ = [
    "find_related_by_tags",
    "find_related_by_semantic",
    "inject_related_links",
    "add_backlinks_to_existing",
    "sub_wikilinks_outside_code",
    "replace_wikilinks_outside_code",
    "strip_unresolved_wikilinks",
    # QA-005 / ARC-012: stdlib modules (re, os, subprocess) are deliberately
    # NOT re-exported -- callers ``import re`` etc. directly. The single
    # private helper below is re-exported because doctor/check.py reaches
    # into it via the shim (``vault_links._iter_unprotected_spans``).
    "_iter_unprotected_spans",
]


# ---------------------------------------------------------------------------
# Code-fence-aware wikilink rewriting
# ---------------------------------------------------------------------------
#
# Several callers (vault_doctor.py, vault_merge.py) rewrite `[[stem]]`
# wikilinks vault-wide during renames/merges/migrations. Doing that with a
# plain str.replace()/re.sub() over the whole note body also rewrites
# literal wikilink examples that appear inside fenced code blocks or inline
# code spans — corrupting notes that document wikilink syntax. The helpers
# below restrict rewriting to text outside of "protected" code regions.

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def _iter_unprotected_spans(content: str) -> list[tuple[int, int]]:
    """Return (start, end) offsets of *content* that are safe to rewrite.

    Excludes text inside fenced code blocks (``` ` `` `` or ``~~~``, tracked
    line-by-line; an opening fence may have a language suffix, e.g.
    ` ```python `) and text inside single-backtick inline code spans on a
    non-fenced line. YAML frontmatter is treated as ordinary text (it is
    delimited by ``---``, not by code fences) so it is always included.

    An opening fence that is never closed protects the remainder of the
    document — under-rewriting a note is safer than corrupting a code
    example that happens to look like a closing fence.

    Limitation: inline code detection only handles non-nested, single
    backtick pairs (`` `like this` ``) on the same line. Multi-backtick
    inline spans (`` ``like this`` ``), escaped backticks, or an inline
    span that crosses a line boundary are not specially recognised — the
    stray backtick is treated as ordinary text.
    """
    spans: list[tuple[int, int]] = []
    pos = 0
    in_fence = False
    fence_char = ""
    fence_len = 0

    for line in content.splitlines(keepends=True):
        line_start = pos
        pos += len(line)
        stripped = line.rstrip("\n")

        if in_fence:
            m = _FENCE_RE.match(stripped)
            if m and m.group(1)[0] == fence_char and len(m.group(1)) >= fence_len:
                in_fence = False
            # Whole line (open, body, or close fence) stays protected.
            continue

        m = _FENCE_RE.match(stripped)
        if m:
            in_fence = True
            fence_char = m.group(1)[0]
            fence_len = len(m.group(1))
            continue  # the opening fence line itself is protected

        # Not inside a fence: protect inline code spans, keep the rest.
        last = 0
        for im in _INLINE_CODE_RE.finditer(line):
            if im.start() > last:
                spans.append((line_start + last, line_start + im.start()))
            last = im.end()
        if last < len(line):
            spans.append((line_start + last, line_start + len(line)))

    return spans


def sub_wikilinks_outside_code(
    content: str,
    pattern: re.Pattern[str],
    repl: str | Callable[[re.Match[str]], str],
) -> tuple[str, int]:
    """Fence/inline-code-aware equivalent of ``pattern.subn(repl, content)``.

    Applies ``pattern.subn(repl, segment)`` independently to each span of
    *content* that is not inside a fenced code block or inline code span
    (see ``_iter_unprotected_spans``); protected spans are copied through
    unchanged. This lets callers keep their own regex/case-sensitivity/
    callback logic (e.g. case-insensitive stem matching, unwrap-to-display-
    text callbacks) while gaining fence-awareness for free.

    Returns:
        Tuple of (new_content, total substitution count).
    """
    spans = _iter_unprotected_spans(content)
    if not spans:
        return content, 0

    pieces: list[str] = []
    total = 0
    prev_end = 0
    for start, end in spans:
        if start > prev_end:
            pieces.append(content[prev_end:start])  # protected text, verbatim
        new_segment, n = pattern.subn(repl, content[start:end])
        pieces.append(new_segment)
        total += n
        prev_end = end
    if prev_end < len(content):
        pieces.append(content[prev_end:])

    return "".join(pieces), total


def replace_wikilinks_outside_code(content: str, replacements: dict[str, str]) -> str:
    """Rewrite ``[[old]]`` and ``[[old|alias]]`` wikilinks to their new stem.

    ``replacements`` maps old stem -> new stem. Matching is exact
    (case-sensitive) on the stem inside the brackets, mirroring the naive
    ``str.replace(f"[[{old}]]", ...)`` + alias-regex pattern this helper
    replaces; an optional ``|alias`` suffix is preserved unchanged.
    ``#anchor`` links are left untouched (the naive call sites this unifies
    never handled that form either). Text inside fenced code blocks and
    inline code spans is never modified — see ``sub_wikilinks_outside_code``.

    Args:
        content: Full note content (frontmatter + body).
        replacements: Mapping of old stem -> new stem.

    Returns:
        Rewritten content. Returns *content* unchanged if ``replacements``
        is empty.
    """
    if not replacements:
        return content

    pattern = re.compile(
        r"\[\[(" + "|".join(re.escape(s) for s in replacements) + r")(\|[^\]]*)?\]\]"
    )

    def _repl(m: re.Match[str]) -> str:
        new_stem = replacements[m.group(1)]
        suffix = m.group(2) or ""
        return f"[[{new_stem}{suffix}]]"

    new_content, _ = sub_wikilinks_outside_code(content, pattern, _repl)
    return new_content


def strip_unresolved_wikilinks(content: str, vault: Path) -> tuple[str, int]:
    """Remove ``[[link]]`` wikilinks that don't resolve to a vault note.

    Used at note-write time to drop dangling links the summarizer backend
    invents — the common ``[[<project>]]`` "hub" link that mirrors the
    ``project:`` field but points at a note that doesn't exist. Only text
    outside fenced/inline code is considered, so a code example that happens
    to contain ``[[brackets]]`` is left untouched.

    The ``related:`` frontmatter array is re-emitted with non-resolving
    entries dropped; elsewhere (body prose) a non-resolving ``[[stem]]`` /
    ``[[stem|alias]]`` is replaced by its display text so the sentence still
    reads. The ``related:`` pass runs first, so its now-all-valid entries are
    left untouched by the body pass.

    Args:
        content: Full note content (frontmatter + body).
        vault: Vault root, used to build the set of valid note stems.

    Returns:
        ``(new_content, removed_count)``.
    """
    valid = {p.stem.lower() for p in all_vault_notes_walk(vault)}

    def _resolves(target: str) -> bool:
        t = target.split("|")[0].split("#")[0].strip().split("/")[-1].lower()
        if t.endswith((".md", ".markdown")):
            t = t.rsplit(".", 1)[0]
        return bool(t) and t in valid

    removed = 0

    # 1) related: field — drop non-resolving entries, keep valid ones.
    fm_match = _FRONTMATTER_RE.match(content)
    if fm_match:
        inner_s, inner_e = fm_match.start(1), fm_match.end(1)
        fm_inner = content[inner_s:inner_e]
        rel_re = re.compile(r"^(related:\s*)(\[.*?\])\s*$", re.MULTILINE)
        rel_m = rel_re.search(fm_inner)
        if rel_m:
            entries = re.findall(r'"(\[\[[^\]]+\]\])"', rel_m.group(2))
            kept: list[str] = []
            for raw_entry in entries:
                em = re.match(r"\[\[([^\]]+)\]\]", raw_entry)
                if em and _resolves(em.group(1)):
                    kept.append(f'"{raw_entry}"')
                elif em:
                    removed += 1
                else:
                    kept.append(f'"{raw_entry}"')  # malformed entry: keep as-is
            new_line = f"{rel_m.group(1)}[{', '.join(kept)}]"
            fm_inner = rel_re.sub(lambda _: new_line, fm_inner, count=1)
            if not fm_inner.endswith("\n"):
                fm_inner += "\n"
            content = content[:inner_s] + fm_inner + content[inner_e:]

    # 2) Body prose — non-resolving [[x]] -> display text (alias or stem).
    link_re = re.compile(r"\[\[([^\]\n]+)\]\]")

    def _body_repl(m: re.Match[str]) -> str:
        nonlocal removed
        inner = m.group(1)
        target = inner.split("|")[0].split("#")[0]
        if _resolves(target):
            return m.group(0)
        removed += 1
        alias = inner.split("|", 1)[1] if "|" in inner else target
        return alias.split("#")[0].strip()

    content, _ = sub_wikilinks_outside_code(content, link_re, _body_repl)
    return content, removed


def find_related_by_tags(
    new_note_path: Path,
    new_tags: list[str],
    max_links: int = 5,
    vault_notes: list[Path] | None = None,
    vault: Path | None = None,
) -> list[str]:
    """Find existing vault notes that share tags with a new note.

    Args:
        new_note_path: Path to the newly written note (excluded from results).
        new_tags: Tags from the new note's frontmatter.
        max_links: Maximum number of related note wikilinks to return.
        vault_notes: Pre-collected list of vault note paths.  When ``None``
            (default), calls ``all_vault_notes_walk()``.  Callers
            that already have the list should pass it to avoid a redundant
            vault walk.  See ARC-010.
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        List of ``"[[stem]]"`` wikilink strings for the top matching notes,
        sorted by tag-overlap score descending.
    """
    if not new_tags:
        return []

    new_tag_set = set(new_tags)
    candidates: list[tuple[int, Path]] = []
    notes = vault_notes if vault_notes is not None else all_vault_notes_walk(vault)

    for note_path in notes:
        # Skip the note itself and daily notes
        if note_path == new_note_path:
            continue
        if note_path.parts and "Daily" in note_path.parts:
            continue

        try:
            content = note_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        fm = parse_frontmatter(content)
        existing_tags = fm.get("tags")
        if not isinstance(existing_tags, list):
            continue

        overlap = len(new_tag_set & {str(t) for t in existing_tags})
        if overlap >= 1:
            candidates.append((overlap, note_path))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [f"[[{p.stem}]]" for _, p in candidates[:max_links]]


def find_related_by_semantic(
    new_note_path: Path,
    vault_root: Path | None = None,
    max_links: int = 5,
    tag_strs: list[str] | None = None,
    vault: Path | None = None,
) -> list[str]:
    """Find related vault notes using semantic search (in-process, cached model).

    Returns an empty list when embeddings.db is missing or the in-process
    search fails for any reason.

    Args:
        new_note_path: Path to the newly written note (excluded from results).
        vault_root: Deprecated alias for vault. Use vault instead.
        max_links: Maximum number of related note wikilinks to return.
        tag_strs: Already-parsed tag strings from the note's frontmatter. When
            provided, avoids re-reading the note file.
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        List of ``"[[stem]]"`` wikilink strings, sorted by semantic similarity.
    """
    # Support legacy vault_root parameter
    vault = vault or vault_root or resolve_vault()

    db_path = get_embeddings_db_path(vault)
    if not db_path.exists():
        return []

    # Build query from stem and tags of the new note.
    # Use caller-supplied tag_strs when available to avoid re-reading the file.
    if tag_strs is None:
        try:
            content = new_note_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
        fm = parse_frontmatter(content)
        note_tags = fm.get("tags") or []
        if not isinstance(note_tags, list):
            note_tags = []
        tag_strs = [str(t) for t in note_tags]

    tag_part = " ".join(tag_strs)
    query = f"{new_note_path.stem.replace('-', ' ')} {tag_part}".strip()

    # ENH-003: in-process call shares the process-cached embedding model
    # instead of spawning vault_search.py and reloading ~67 MB ONNX per note.
    # Lazy + guarded so this stdlib-only module's import surface is unchanged;
    # on any failure fall back to no semantic backlinks (the prior subprocess
    # error path). ARC-027(b): vault= is forwarded so multi-vault setups
    # compute backlinks against the owning vault.
    try:
        import vault_search  # noqa: PLC0415

        items = vault_search.search(
            query=query,
            top=max_links + 1,
            min_score=get_config("embeddings", "min_score", 0.45),
            vault=vault,
        )
    except Exception:  # noqa: BLE001
        return []

    links: list[str] = []
    for item in items:
        stem = str(item.get("stem", ""))
        if not stem or stem == new_note_path.stem:
            continue
        links.append(f"[[{stem}]]")
        if len(links) >= max_links:
            break

    return links


# A top-level frontmatter key line (`key:` / `key: value` at indent 0). Used as
# the hard stop when consuming a malformed `related` field so it can never
# swallow the fields below it.
_FM_TOP_LEVEL_KEY_RE = re.compile(r"^[A-Za-z][\w-]*\s*:")
_RELATED_KEY_RE = re.compile(r"^related\s*:(.*)$")


def _related_field_spans(fm_lines: list[str]) -> list[tuple[int, int]]:
    """Return the ``[start, end)`` line span of every top-level ``related`` field.

    A field spans its ``related:`` line plus whatever continuation the stdlib
    parser would associate with it: indented block-sequence ``- item`` lines, or
    the remainder of an inline ``[...]`` list wrapped across several lines.

    This replaces a regex that required the line to end right after an optional
    ``[...]``.  Two shapes in the live vault defeated it — a trailing ``#``
    comment (the daily-note template placeholder) and a bare scalar — and the
    caller's fallback was to *append* a second ``related:`` key, which is how 35
    notes ended up with duplicate top-level keys.  Scanning lines matches every
    shape and, by returning all spans, lets the caller collapse existing
    duplicates instead of adding to them.
    """
    spans: list[tuple[int, int]] = []
    i = 0
    n = len(fm_lines)
    while i < n:
        match = _RELATED_KEY_RE.match(fm_lines[i])
        if not match:
            i += 1
            continue
        value = match.group(1).strip()
        j = i + 1
        if value.startswith("[") and value.count("[") > value.count("]"):
            # Inline list opened but not closed on this line. Consume until the
            # brackets balance -- wikilinks are themselves balanced, so they do
            # not perturb the count.
            depth = value.count("[") - value.count("]")
            while j < n and depth > 0:
                line = fm_lines[j]
                if not line.strip() or _FM_TOP_LEVEL_KEY_RE.match(line):
                    break  # never closed -- stop before the next field
                depth += line.count("[") - line.count("]")
                j += 1
        elif not value:
            while (
                j < n
                and fm_lines[j][:1] in " \t"
                and fm_lines[j].strip().startswith("- ")
            ):
                j += 1
        spans.append((i, j))
        i = j
    return spans


def _replace_related_field(fm_inner: str, new_line: str) -> str:
    """Return *fm_inner* with a single ``related`` field set to *new_line*.

    The first existing field is replaced in place (preserving field order); any
    further ``related`` fields are dropped, so a note that already carries
    duplicates is healed rather than grown.  When no field exists the new line
    is appended.
    """
    fm_lines = fm_inner.split("\n")
    # `fm_inner` always ends with a newline, so split() leaves a trailing "".
    had_trailing_newline = bool(fm_lines) and fm_lines[-1] == ""
    if had_trailing_newline:
        fm_lines = fm_lines[:-1]

    spans = _related_field_spans(fm_lines)
    if not spans:
        out = [*fm_lines, new_line]
    else:
        out = []
        prev = 0
        for idx, (start, end) in enumerate(spans):
            out.extend(fm_lines[prev:start])
            if idx == 0:
                out.append(new_line)
            prev = end
        out.extend(fm_lines[prev:])

    text = "\n".join(out)
    return f"{text}\n" if had_trailing_newline else text


def inject_related_links(note_path: Path, new_links: list[str]) -> None:
    """Merge new wikilinks into the ``related`` frontmatter field of a note.

    Replaces the entire ``related`` field (inline or block-style) with a clean
    inline quoted array: ``related: ["[[a]]", "[[b]]"]``.

    Self-referencing wikilinks (links back to the note itself) are filtered out.

    Args:
        note_path: Path to the note to update.
        new_links: Wikilinks to add (e.g. ``["[[note-a]]", "[[note-b]]"]``).
    """
    try:
        content = note_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return

    fm = parse_frontmatter(content)
    existing_related = fm.get("related") or []
    if not isinstance(existing_related, list):
        existing_related = []
    # Normalise existing entries to strings
    existing_strs: list[str] = [str(r) for r in existing_related]

    # Deduplicate existing + new, preserving order
    merged = list(dict.fromkeys(existing_strs + new_links))

    # Remove self-references
    self_ref = f"[[{note_path.stem}]]"
    merged = [m for m in merged if m != self_ref]

    if merged == existing_strs:
        # Nothing new to add
        return

    # Build the replacement line using inline quoted array format
    quoted_items = ", ".join(f'"{lnk}"' for lnk in merged)
    new_related_line = f"related: [{quoted_items}]"

    # Operate only on the frontmatter block: scanning the whole file would
    # clobber a body line starting with `related:` (e.g. a note quoting the
    # frontmatter schema).
    match = _FRONTMATTER_RE.match(content)
    if match is None:
        # No frontmatter block -- nowhere safe to inject
        return
    inner_start, inner_end = match.start(1), match.end(1)
    fm_inner = _replace_related_field(content[inner_start:inner_end], new_related_line)

    updated = content[:inner_start] + fm_inner + content[inner_end:]

    # Crash-safe write: tmp file in the same directory, then atomic replace
    tmp_path = note_path.parent / f".{note_path.name}.{os.getpid()}.tmp"
    try:
        tmp_path.write_text(updated, encoding="utf-8")
        tmp_path.replace(note_path)
    except OSError:
        try:
            tmp_path.unlink()
        except OSError:
            pass


def add_backlinks_to_existing(
    new_note_path: Path,
    related_notes: list[str],
    vault_notes: list[Path] | None = None,
    vault: Path | None = None,
) -> list[Path]:
    """Add a backlink to ``new_note_path`` in each of the ``related_notes``.

    For each wikilink in ``related_notes``, locates the corresponding note
    file in the vault and calls ``inject_related_links()`` to add a
    back-reference to ``new_note_path``.

    Args:
        new_note_path: Path to the newly written note.
        related_notes: List of ``"[[stem]]"`` wikilinks for existing notes.
        vault_notes: Pre-collected list of vault note paths.  When ``None``
            (default), calls ``all_vault_notes_walk()``.  Callers
            that already have the list should pass it to avoid a redundant
            vault walk.  See ARC-010.
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        List of Paths that were modified.
    """
    new_link = f"[[{new_note_path.stem}]]"
    modified: list[Path] = []

    # Build a stem -> path index from all vault notes once
    notes = vault_notes if vault_notes is not None else all_vault_notes_walk(vault)
    stem_index: dict[str, Path] = {}
    for note_path in notes:
        stem_index[note_path.stem] = note_path

    for wikilink in related_notes:
        # Extract stem from [[stem]]
        stem_match = re.match(r"^\[\[(.+)\]\]$", wikilink)
        if not stem_match:
            continue
        stem = stem_match.group(1)
        target_path = stem_index.get(stem)
        if target_path is None or target_path == new_note_path:
            continue
        inject_related_links(target_path, [new_link])
        modified.append(target_path)

    return modified
