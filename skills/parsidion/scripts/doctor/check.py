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


def check_note(
    path: Path, note_map: dict[str, list[Path]], vault_path: Path
) -> list[Issue]:
    """Return a list of Issues found in *path*."""
    issues: list[Issue] = []

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [Issue(path, "error", "READ_ERROR", str(exc))]

    rel = path.relative_to(vault_path)

    # Flat daily note: Daily/YYYY-MM-DD.md should be Daily/YYYY-MM/DD.md
    parts = rel.parts
    if parts[0] == "Daily" and len(parts) == 2:
        if re.match(r"^\d{4}-\d{2}-\d{2}\.md$", parts[1]):
            issues.append(
                Issue(
                    path,
                    "warning",
                    "FLAT_DAILY",
                    "Daily note is flat (YYYY-MM-DD.md) — should live in Daily/YYYY-MM/DD.md",
                )
            )

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

    # Required fields
    note_type_raw = fm.get("type", "")
    is_daily = note_type_raw == "daily" or parts[0] == "Daily"
    required = (
        REQUIRED_FIELDS_ALL
        if is_daily
        else REQUIRED_FIELDS_ALL + REQUIRED_FIELDS_KNOWLEDGE
    )
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

    # Valid type
    if note_type_raw and note_type_raw not in VALID_TYPES:
        issues.append(
            Issue(
                path,
                "error",
                "INVALID_TYPE",
                f"type '{note_type_raw}' is not one of: {', '.join(sorted(VALID_TYPES))}",
            )
        )

    # Date format
    date_val = str(fm.get("date", ""))
    if date_val and not re.match(r"^\d{4}-\d{2}-\d{2}$", date_val):
        issues.append(
            Issue(
                path, "warning", "INVALID_DATE", f"date '{date_val}' is not YYYY-MM-DD"
            )
        )

    # Compute related/related_str once for both orphan and self-ref checks (QA-010)
    related: object = []
    related_str: str = ""
    if not is_daily:
        related = fm.get("related", [])
        related_str = str(related)

    # Orphan check — related must contain at least one [[wikilink]] (not for daily notes)
    if not is_daily:
        if not re.search(r"\[\[.+?\]\]", related_str):
            issues.append(
                Issue(
                    path,
                    "warning",
                    "ORPHAN_NOTE",
                    "No [[wikilinks]] in 'related' field (orphan note)",
                )
            )

    # Self-referencing wikilinks in related field (skip daily notes)
    if not is_daily:
        self_ref_pattern = f"[[{path.stem}]]"
        if self_ref_pattern in related_str:
            issues.append(
                Issue(
                    path,
                    "warning",
                    "SELF_REF",
                    f"Self-referencing wikilink {self_ref_pattern} in 'related'",
                )
            )

    # Heading mismatch — first heading is ## but no # heading exists (skip daily notes)
    if not is_daily:
        body = vault_common.get_body(content)
        has_h1 = False
        first_h2_line: str | None = None
        for bline in body.splitlines():
            s = bline.strip()
            if s.startswith("# ") and not s.startswith("## "):
                has_h1 = True
                break
            if (
                first_h2_line is None
                and s.startswith("## ")
                and not s.startswith("### ")
            ):
                first_h2_line = s
        if not has_h1 and first_h2_line is not None:
            issues.append(
                Issue(
                    path,
                    "warning",
                    "HEADING_MISMATCH",
                    f"No # heading found; first ## heading should be promoted to #: {first_h2_line}",
                )
            )

    # Broken wikilinks anywhere in the document, EXCEPT inside fenced code
    # blocks or inline code. Code examples routinely contain [[...]] tokens
    # that are legitimate config syntax (TOML array-of-tables like [[bin]] or
    # [[licenses.exceptions]], not wikilinks)); scanning the raw document flagged
    # those as broken links. Only the text outside protected code regions is
    # scanned, reusing the same fence/inline-code tracker as the migration
    # rewriter so the two never disagree about what counts as a link.
    # Newlines are excluded from the match to avoid cross-line false positives
    # (e.g. truncated MANIFEST table cells in daily notes), and links containing
    # shell metacharacters are skipped (bash [[ ]] conditionals).
    _SHELL_META = re.compile(r"[!$<>|&;{}\n]")
    _scannable = "".join(
        content[start:end]
        for start, end in vault_links._iter_unprotected_spans(content)
    )
    for link in re.findall(r"\[\[([^\]\n]+)\]\]", _scannable):
        clean = link.split("|")[0].split("#")[0].strip()
        if not clean or _SHELL_META.search(clean):
            continue
        if not resolve_wikilink(clean, note_map):
            issues.append(
                Issue(
                    path,
                    "warning",
                    "BROKEN_WIKILINK",
                    f"[[{clean}]] does not resolve to any vault note",
                )
            )

    return issues
