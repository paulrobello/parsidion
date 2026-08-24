"""Note-level issue scanner — ``check_note``.

Extracted from the original ``vault_doctor.py`` (ARC-008 / QA-003).  Produces
the ``Issue`` list consumed by the repair pipeline.

Stdlib-only.
"""

from __future__ import annotations

import re
from pathlib import Path

import vault_common
import vault_links

from doctor._state import (
    REQUIRED_FIELDS_ALL,
    REQUIRED_FIELDS_KNOWLEDGE,
    VALID_TYPES,
    Issue,
)
from doctor.links import resolve_wikilink
from doctor.protocol import NoteCheckContext, Rule

# ---------------------------------------------------------------------------
# Frontmatter syntax checks
# ---------------------------------------------------------------------------
#
# ``parse_frontmatter`` implements a deliberately small YAML subset and never
# raises: a shape outside that subset silently yields the wrong value, so a note
# whose tags/sources/related were quietly dropped still scanned clean. These
# checks read the RAW frontmatter text, the only place the damage is still
# visible. Each mirrors a shape found in the live vault; see
# tests/test_frontmatter_syntax_checks.py.

# Mirrors vault_index._FRONTMATTER_RE. Duplicated rather than imported because
# that name is private to the parser; the shape (opening/closing bare fences) is
# part of the note format, not of the parser's internals.
_FM_BLOCK_RE = re.compile(r"\A---\s*\n(.*?\n)---\s*\n", re.DOTALL)
_FM_KEY_LINE_RE = re.compile(r"^(\s*)([A-Za-z][\w-]*)\s*:(.*)$")
_BLOCK_SCALAR_VALUES = (">", "|", ">-", "|-")
# Fields the note schema defines as lists; a bare scalar here is data loss
# (a space-separated `tags:` collapses to one string and drops every tag).
_LIST_FIELDS = ("tags", "related", "sources")


def _consume_wrapped_list_line(
    line: str, open_list_key: str, depth: int
) -> tuple[str | None, int, str | None]:
    """Consume one line of a wrapped inline list (QA-005 extraction).

    Returns ``(open_list_key, depth, terminated_key)`` — *terminated_key* is
    the key to mark unterminated when a new top-level key ended the wrapped
    list before it closed (None on ordinary consumption lines).
    """
    key_match = _FM_KEY_LINE_RE.match(line)
    if key_match and not key_match.group(1):
        return None, 0, open_list_key
    depth += line.count("[") - line.count("]")
    if depth <= 0:
        return None, depth, None
    return open_list_key, depth, None


def _scan_frontmatter_lines(
    path: Path, lines: list[str]
) -> tuple[list[Issue], dict[str, int], set[str]]:
    """Single left-to-right scan of the raw frontmatter lines.

    Returns ``(issues, seen_keys, unterminated)`` where *issues* carries the
    position-dependent NESTED_FM_KEY findings (emitted in encounter order)
    and the two maps feed the post-scan defect reporters.
    """
    issues: list[Issue] = []
    in_block_scalar = False
    in_list = False
    # Key whose inline `[` never closed, and the depth still outstanding.
    open_list_key: str | None = None
    depth = 0
    unterminated: set[str] = set()
    seen_keys: dict[str, int] = {}

    for line in lines:
        stripped = line.strip()

        if open_list_key is not None:
            # Consuming a wrapped inline list: only a new top-level key ends it.
            open_list_key, depth, terminated_key = _consume_wrapped_list_line(
                line, open_list_key, depth
            )
            if terminated_key is not None:
                unterminated.add(terminated_key)
            continue

        if in_block_scalar:
            if stripped and line[:1].isspace():
                continue
            in_block_scalar = False

        if not stripped or stripped.startswith("#"):
            continue
        if in_list and stripped.startswith("- "):
            continue

        key_match = _FM_KEY_LINE_RE.match(line)
        if key_match is None:
            continue
        indent, key, value = key_match.group(1), key_match.group(2), key_match.group(3)
        value = value.strip()

        if indent:
            issues.append(
                Issue(
                    path,
                    "warning",
                    "NESTED_FM_KEY",
                    f"Indented mapping key '{key}' is not supported by the "
                    "frontmatter parser and is read as a top-level scalar",
                )
            )
            continue

        seen_keys[key] = seen_keys.get(key, 0) + 1

        in_list = not value
        if value in _BLOCK_SCALAR_VALUES:
            in_block_scalar = True
            in_list = False
            continue
        if value.startswith("[") and value.count("[") > value.count("]"):
            open_list_key = key
            depth = value.count("[") - value.count("]")

    if open_list_key is not None:
        unterminated.add(open_list_key)

    return issues, seen_keys, unterminated


def _report_frontmatter_defects(
    path: Path,
    seen_keys: dict[str, int],
    unterminated: set[str],
    content: str,
    fm: dict,
) -> list[Issue]:
    """Post-scan defect reports: duplicates, unterminated lists, orphans, scalars."""
    issues: list[Issue] = []

    for key, count in sorted(seen_keys.items()):
        if count > 1:
            issues.append(
                Issue(
                    path,
                    "warning",
                    "DUPLICATE_FM_KEY",
                    f"Key '{key}' appears {count} times; the parser is "
                    "last-wins, so the earlier value is silently discarded",
                )
            )

    for key in sorted(unterminated):
        issues.append(
            Issue(
                path,
                "error",
                "UNTERMINATED_FM_LIST",
                f"Inline list '{key}' opens with '[' but never closes on the "
                "same line; the parser stores it as the scalar '[' and "
                "mis-reads every following line",
            )
        )

    # An orphan close bracket left in the body by a collapsed inline list.
    for bline in vault_common.get_body(content).splitlines():
        if not bline.strip():
            continue
        if bline.strip() in ("]", "],"):
            issues.append(
                Issue(
                    path,
                    "warning",
                    "ORPHAN_FM_BRACKET",
                    "Body starts with an orphan ']' — residue of an inline "
                    "frontmatter list whose opening line was rewritten",
                )
            )
        break

    # A list-typed field holding a bare scalar. Skipped for a field already
    # reported as unterminated, where the scalar is that defect's symptom.
    for key in _LIST_FIELDS:
        if key in unterminated:
            continue
        val = fm.get(key)
        if isinstance(val, str) and val.strip():
            issues.append(
                Issue(
                    path,
                    "warning",
                    "SCALAR_LIST_FIELD",
                    f"Field '{key}' must be a list but holds the scalar "
                    f"{val.strip()!r}; its entries are lost",
                )
            )

    return issues


def _check_frontmatter_syntax(
    path: Path,
    content: str,
    fm: dict,
    ctx: NoteCheckContext,  # noqa: ARG001 — registry signature
) -> list[Issue]:
    """Detect malformed frontmatter shapes ``parse_frontmatter`` swallows."""
    match = _FM_BLOCK_RE.match(content)
    if match is None:
        return []

    lines = match.group(1).split("\n")
    scan_issues, seen_keys, unterminated = _scan_frontmatter_lines(path, lines)
    scan_issues.extend(
        _report_frontmatter_defects(path, seen_keys, unterminated, content, fm)
    )
    return scan_issues


def _check_flat_daily(
    path: Path, content: str, fm: dict, ctx: NoteCheckContext
) -> list[Issue]:
    """FLAT_DAILY: Daily/YYYY-MM-DD.md should be Daily/YYYY-MM/DD.md."""
    parts = ctx.parts
    if parts[0] == "Daily" and len(parts) == 2:
        if re.match(r"^\d{4}-\d{2}-\d{2}\.md$", parts[1]):
            return [
                Issue(
                    path,
                    "warning",
                    "FLAT_DAILY",
                    "Daily note is flat (YYYY-MM-DD.md) — should live in Daily/YYYY-MM/DD.md",
                )
            ]
    return []


def _check_required_fields(
    path: Path, content: str, fm: dict, ctx: NoteCheckContext
) -> list[Issue]:
    """MISSING_FIELD: every required frontmatter field must be non-empty."""
    required = (
        REQUIRED_FIELDS_ALL
        if ctx.is_daily
        else REQUIRED_FIELDS_ALL + REQUIRED_FIELDS_KNOWLEDGE
    )
    issues: list[Issue] = []
    for fname in required:
        val = fm.get(fname)
        if val is None or val == "" or val == [] or val == "[]":
            issues.append(
                Issue(
                    path,
                    "error",
                    "MISSING_FIELD",
                    f"Required field '{fname}' is absent or empty",
                )
            )
    return issues


def _check_valid_type(
    path: Path, content: str, fm: dict, ctx: NoteCheckContext
) -> list[Issue]:
    """INVALID_TYPE: ``type`` must be one of the schema's values."""
    note_type_raw = fm.get("type", "")
    if note_type_raw and note_type_raw not in VALID_TYPES:
        return [
            Issue(
                path,
                "error",
                "INVALID_TYPE",
                f"type '{note_type_raw}' is not one of: "
                f"{', '.join(sorted(VALID_TYPES))}",
            )
        ]
    return []


def _check_date_format(
    path: Path, content: str, fm: dict, ctx: NoteCheckContext
) -> list[Issue]:
    """INVALID_DATE: ``date`` must be YYYY-MM-DD."""
    date_val = str(fm.get("date", ""))
    if date_val and not re.match(r"^\d{4}-\d{2}-\d{2}$", date_val):
        return [
            Issue(
                path, "warning", "INVALID_DATE", f"date '{date_val}' is not YYYY-MM-DD"
            )
        ]
    return []


def _check_orphan(
    path: Path, content: str, fm: dict, ctx: NoteCheckContext
) -> list[Issue]:
    """ORPHAN_NOTE: ``related`` must contain at least one wikilink (not daily)."""
    if ctx.is_daily:
        return []
    related_str = str(fm.get("related", []))
    if not re.search(r"\[\[.+?\]\]", related_str):
        return [
            Issue(
                path,
                "warning",
                "ORPHAN_NOTE",
                "No [[wikilinks]] in 'related' field (orphan note)",
            )
        ]
    return []


def _check_self_ref(
    path: Path, content: str, fm: dict, ctx: NoteCheckContext
) -> list[Issue]:
    """SELF_REF: ``related`` must not link back to the note itself (not daily)."""
    if ctx.is_daily:
        return []
    related_str = str(fm.get("related", []))
    self_ref_pattern = f"[[{path.stem}]]"
    if self_ref_pattern in related_str:
        return [
            Issue(
                path,
                "warning",
                "SELF_REF",
                f"Self-referencing wikilink {self_ref_pattern} in 'related'",
            )
        ]
    return []


def _check_heading_mismatch(
    path: Path, content: str, fm: dict, ctx: NoteCheckContext
) -> list[Issue]:
    """HEADING_MISMATCH: ## present but no # heading (not daily)."""
    if ctx.is_daily:
        return []
    body = vault_common.get_body(content)
    has_h1 = False
    first_h2_line: str | None = None
    for bline in body.splitlines():
        s = bline.strip()
        if s.startswith("# ") and not s.startswith("## "):
            has_h1 = True
            break
        if first_h2_line is None and s.startswith("## ") and not s.startswith("### "):
            first_h2_line = s
    if not has_h1 and first_h2_line is not None:
        return [
            Issue(
                path,
                "warning",
                "HEADING_MISMATCH",
                f"No # heading found; first ## heading should be promoted to #: {first_h2_line}",
            )
        ]
    return []


_SHELL_META_RE = re.compile(r"[!$<>|&;{}\n]")


def _check_broken_wikilinks(
    path: Path, content: str, fm: dict, ctx: NoteCheckContext
) -> list[Issue]:
    """BROKEN_WIKILINK: every [[link]] outside code must resolve to a note.

    Code examples routinely contain [[...]] tokens that are legitimate
    config syntax (TOML array-of-tables like [[bin]] or
    [[licenses.exceptions]], not wikilinks); only the text outside
    protected code regions is scanned, reusing the same fence/inline-code
    tracker as the migration rewriter so the two never disagree about what
    counts as a link. Newlines are excluded from the match to avoid
    cross-line false positives (e.g. truncated MANIFEST table cells in
    daily notes), and links containing shell metacharacters are skipped
    (bash [[ ]] conditionals).
    """
    issues: list[Issue] = []
    scannable = "".join(
        content[start:end]
        for start, end in vault_links._iter_unprotected_spans(content)
    )
    for link in re.findall(r"\[\[([^\]\n]+)\]\]", scannable):
        clean = link.split("|")[0].split("#")[0].strip()
        if not clean or _SHELL_META_RE.search(clean):
            continue
        if not resolve_wikilink(clean, ctx.note_map):
            issues.append(
                Issue(
                    path,
                    "warning",
                    "BROKEN_WIKILINK",
                    f"[[{clean}]] does not resolve to any vault note",
                )
            )
    return issues


# QA-005: the per-note checks registered as data. PRE_FM_RULES run before
# the frontmatter gate (a flat daily note with broken frontmatter must
# still be reported); RULES run after it, in the order the original
# check_note body used -- frontmatter syntax first so the root cause is
# reported alongside whatever symptom it produces.
PRE_FM_RULES: tuple[Rule, ...] = (Rule("FLAT_DAILY", _check_flat_daily),)

RULES: tuple[Rule, ...] = (
    Rule("FRONTMATTER_SYNTAX", _check_frontmatter_syntax),
    Rule("REQUIRED_FIELDS", _check_required_fields),
    Rule("VALID_TYPE", _check_valid_type),
    Rule("DATE_FORMAT", _check_date_format),
    Rule("ORPHAN", _check_orphan),
    Rule("SELF_REF", _check_self_ref),
    Rule("HEADING_MISMATCH", _check_heading_mismatch),
    Rule("BROKEN_WIKILINKS", _check_broken_wikilinks),
)


def check_note(
    path: Path, note_map: dict[str, list[Path]], vault_path: Path
) -> list[Issue]:
    """Return a list of Issues found in *path*.

    QA-005: the checks live in the ``PRE_FM_RULES``/``RULES`` registries
    above; this function owns only the shared control flow -- read the
    note, run the path-based rules, gate on parseable frontmatter, then
    iterate the registered rules with a per-note context.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [Issue(path, "error", "READ_ERROR", str(exc))]

    rel = path.relative_to(vault_path)
    parts = rel.parts

    issues: list[Issue] = []
    pre_ctx = NoteCheckContext(
        note_map=note_map, vault=vault_path, parts=parts, is_daily=False
    )
    for rule in PRE_FM_RULES:
        issues.extend(rule.check(path, content, {}, pre_ctx))

    # Parse frontmatter
    fm = vault_common.parse_frontmatter(content)
    if not fm:
        issues.append(
            Issue(
                path, "error", "MISSING_FRONTMATTER", "No YAML frontmatter block found"
            )
        )
        # Can't check field-level issues without frontmatter
        return issues

    note_type_raw = fm.get("type", "")
    is_daily = note_type_raw == "daily" or parts[0] == "Daily"
    ctx = NoteCheckContext(
        note_map=note_map, vault=vault_path, parts=parts, is_daily=is_daily
    )
    for rule in RULES:
        issues.extend(rule.check(path, content, fm, ctx))

    return issues
