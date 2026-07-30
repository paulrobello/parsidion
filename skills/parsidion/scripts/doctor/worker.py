"""Parallel repair worker — ``_repair_one``.

Extracted from the original ``vault_doctor.py`` (ARC-008 / QA-003).

Each call repairs a single note by:

1. Promoting headings (Python-only).
2. Removing self-referencing wikilinks (Python-only).
3. Repairing broken wikilinks via exact + semantic match (Python-only).
4. Calling the prompt AI for remaining issues, then normalising its output.

State writes are guarded by a ``threading.Lock`` so multiple workers can run
in parallel via ``ThreadPoolExecutor``.

Stdlib-only.
"""

from __future__ import annotations

import threading
from pathlib import Path

import vault_fs

from doctor._state import (
    AI_TIMEOUT,
    REPAIRABLE_CODES,
    Issue,
    _active_vault,
    _backup_note,
    _rel,
)
from doctor.frontmatter import _normalize_repaired_note, _note_is_daily, repair_note
from doctor.headings import _auto_fix_headings, _auto_fix_self_refs
from doctor.links import _auto_repair_broken_wikilinks


def _repair_one(
    note_path: Path,
    note_issues: list[Issue],
    model: str | None,
    state: dict,
    today_str: str,
    lock: threading.Lock,
    timeout: int = AI_TIMEOUT,
    note_map: dict[str, list[Path]] | None = None,
    fix_headings: bool = True,
    vault_path: Path | None = None,
) -> bool:
    """Repair one note, update state under *lock*, return True on success."""
    if vault_path is None:
        vault_path = _active_vault()
    key = _rel(note_path)
    rel = note_path.relative_to(vault_path)
    repairable = [i for i in note_issues if i.code in REPAIRABLE_CODES]
    broken = [i for i in repairable if i.code == "BROKEN_WIKILINK"]
    heading_issues = [i for i in repairable if i.code == "HEADING_MISMATCH"]
    self_ref_issues = [i for i in repairable if i.code == "SELF_REF"]
    other = [
        i
        for i in repairable
        if i.code not in ("BROKEN_WIKILINK", "HEADING_MISMATCH", "SELF_REF")
    ]

    with lock:
        prev_status = state.get("notes", {}).get(key, {}).get("status", "")

    # Step 0: Python-based heading promotion (no Claude needed)
    heading_fix_made = False
    if heading_issues and fix_headings:
        heading_fix_made = _auto_fix_headings(note_path)
        if heading_fix_made:
            with lock:
                print(f"  ✓ {rel}: promoted ## heading to #", flush=True)

    # Step 0b: Python-based self-reference removal (no Claude needed)
    self_ref_fix_made = False
    if self_ref_issues:
        self_ref_fix_made = _auto_fix_self_refs(note_path)
        if self_ref_fix_made:
            with lock:
                print(f"  ✓ {rel}: removed self-referencing wikilink(s)", flush=True)

    # Step 1: Python-based broken-link repair (no Claude needed)
    link_fix_made = False
    became_orphan = False
    if broken and note_map is not None:
        fixed_content, became_orphan = _auto_repair_broken_wikilinks(
            note_path, broken, note_map
        )
        if fixed_content:
            _backup_note(vault_path, note_path)
            vault_fs.atomic_write_text(note_path, fixed_content + "\n")
            link_fix_made = True

    # Step 2: If note became orphan (all related removed, no candidates found),
    #         inject a synthetic ORPHAN_NOTE issue so Claude's orphan repair fires
    if became_orphan:
        other.append(
            Issue(
                note_path,
                "warning",
                "ORPHAN_NOTE",
                "All related links removed — no candidates found",
            )
        )

    # Step 3: Claude repair for remaining issues (MISSING_FIELD, ORPHAN_NOTE, etc.)
    fixed_content = None
    repair_status = "failed"
    if other:
        fixed_content, repair_status = repair_note(note_path, other, model, timeout)
        if fixed_content:
            # Normalize the AI output before writing: defend against malformed
            # frontmatter (missing closing ---, leaked markers, fabricated or
            # badly-nested wikilinks). Reject (don't write) if it cannot be
            # made valid, so the note is retried instead of being corrupted.
            normalized = _normalize_repaired_note(
                fixed_content, note_map, _note_is_daily(rel, fixed_content)
            )
            if normalized is None:
                fixed_content = None
                repair_status = "failed"
            else:
                _backup_note(vault_path, note_path)
                vault_fs.atomic_write_text(note_path, normalized + "\n")
    elif broken or heading_issues or self_ref_issues:
        # Only broken wikilinks / heading / self-ref fixes — no Claude call needed
        repair_status = (
            "fixed"
            if (link_fix_made or heading_fix_made or self_ref_fix_made)
            else "failed"
        )

    if fixed_content:
        icon = "✓"
    elif (link_fix_made or heading_fix_made or self_ref_fix_made) and not other:
        # Fixed by Python, no Claude needed
        icon = "✓"
    else:
        if repair_status == "timeout" and prev_status == "timeout":
            repair_status = "needs_review"
        icon = "✗" if not (link_fix_made or self_ref_fix_made) else "~"

    with lock:
        msg = f"  {rel} ({len(repairable)} issue(s)) … {icon}"
        if repair_status == "needs_review":
            msg += (
                "\n    → needs_review (timed out twice; flagged for user intervention)"
            )
        print(msg, flush=True)
        state.setdefault("notes", {})[key] = {
            "status": repair_status,
            "last_checked": today_str,
            "issues": [i.code for i in repairable],
        }

    return (
        fixed_content is not None
        or link_fix_made
        or heading_fix_made
        or self_ref_fix_made
    )
