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
from doctor.frontmatter import (
    _normalize_repaired_note,
    _note_is_daily,
    repair_note,
    splice_frontmatter_onto_original,
)
from doctor.headings import _auto_fix_headings, _auto_fix_self_refs
from doctor.links import _auto_repair_broken_wikilinks


def _classify_repair_outcome(
    *,
    fixed_content: str | None,
    other: list[Issue],
    link_fix_made: bool,
    heading_fix_made: bool,
    self_ref_fix_made: bool,
    repair_status: str,
    prev_status: str,
) -> tuple[str, str]:
    """Compute ``(icon, final_repair_status)`` for one repaired note.

    QA-004: lifts the status-icon + ``repair_status`` bookkeeping out of
    :func:`_repair_one` so the worker body reads as a sequence of repair
    stages instead of an interleaved series of fix / status / print
    concerns. Pure reorganisation — every branch mirrors the prior inline
    logic verbatim.

    * ``icon`` is one of ``"✓"`` (success — AI fix landed, or a Python-only
      fix landed without any Claude call), ``"✗"`` (no fix landed at all),
      or ``"~"`` (partial — the AI call failed but a Python-only fix
      earlier in the pipeline did land).
    * A ``repair_status`` of ``"timeout"`` upgrades to ``"needs_review"``
      when the note's previous run also timed out, so a transient hang
      becomes a flag for human intervention on the second consecutive
      occurrence. All other statuses pass through unchanged.
    """
    if fixed_content:
        icon = "✓"
    elif (link_fix_made or heading_fix_made or self_ref_fix_made) and not other:
        # Fixed by Python, no Claude needed
        icon = "✓"
    else:
        if repair_status == "timeout" and prev_status == "timeout":
            repair_status = "needs_review"
        icon = "✗" if not (link_fix_made or self_ref_fix_made) else "~"
    return icon, repair_status


def _python_fix_stages(
    note_path: Path,
    repairable: list[Issue],
    note_map: dict[str, list[Path]] | None,
    fix_headings: bool,
    vault_path: Path,
    lock: threading.Lock,
    rel: Path,
) -> tuple[list[Issue], bool, bool, bool]:
    """Run the Python-only repair stages for one note (QA-005 extraction).

    Steps 0-1 of the worker: heading promotion, self-reference removal,
    and broken-link repair — none of which call the AI backend. Returns
    ``(other, heading_fix_made, self_ref_fix_made, link_fix_made)`` where
    *other* is the remaining repairable issues (plus a synthetic
    ORPHAN_NOTE when the link repair stripped every related entry).
    """
    broken = [i for i in repairable if i.code == "BROKEN_WIKILINK"]
    heading_issues = [i for i in repairable if i.code == "HEADING_MISMATCH"]
    self_ref_issues = [i for i in repairable if i.code == "SELF_REF"]
    other = [
        i
        for i in repairable
        if i.code not in ("BROKEN_WIKILINK", "HEADING_MISMATCH", "SELF_REF")
    ]

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
    if broken and note_map is not None:
        fixed_content, became_orphan = _auto_repair_broken_wikilinks(
            note_path, broken, note_map
        )
        if fixed_content:
            _backup_note(vault_path, note_path)
            vault_fs.atomic_write_text(note_path, fixed_content + "\n")
            link_fix_made = True

        # Step 2: If note became orphan (all related removed, no candidates
        # found), inject a synthetic ORPHAN_NOTE issue so the AI's orphan
        # repair fires.
        if became_orphan:
            other.append(
                Issue(
                    note_path,
                    "warning",
                    "ORPHAN_NOTE",
                    "All related links removed — no candidates found",
                )
            )

    return other, heading_fix_made, self_ref_fix_made, link_fix_made


def _ai_repair_stage(
    note_path: Path,
    other: list[Issue],
    model: str | None,
    timeout: int,
    note_map: dict[str, list[Path]] | None,
    vault_path: Path,
    rel: Path,
) -> tuple[str | None, str]:
    """Run the AI repair for the remaining issues (QA-005 extraction).

    Step 3 of the worker: call the backend, normalise its output, and write
    only when the result can be made valid. Returns
    ``(fixed_content, repair_status)``.
    """
    # SEC-033(d): the note as it stands right before the AI call — the
    # deterministic passes above may already have rewritten it, and this
    # is the body the AI repair must preserve.
    original_content = note_path.read_text(encoding="utf-8")
    fixed_content, repair_status = repair_note(note_path, other, model, timeout)
    if not fixed_content:
        return None, repair_status

    # Normalize the AI output before writing: defend against malformed
    # frontmatter (missing closing ---, leaked markers, fabricated or
    # badly-nested wikilinks). Reject (don't write) if it cannot be
    # made valid, so the note is retried instead of being corrupted.
    normalized = _normalize_repaired_note(
        fixed_content, note_map, _note_is_daily(rel, fixed_content)
    )
    if normalized is None:
        return None, "failed"

    # SEC-033(d): only the frontmatter block comes from the AI;
    # the body is the original's, byte-for-byte.
    normalized = splice_frontmatter_onto_original(normalized, original_content)
    _backup_note(vault_path, note_path)
    vault_fs.atomic_write_text(note_path, normalized.rstrip("\n") + "\n")
    return fixed_content, repair_status


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

    with lock:
        prev_status = state.get("notes", {}).get(key, {}).get("status", "")

    # QA-005: steps 0-2 (heading promotion, self-ref removal, broken-link
    # repair) lifted to _python_fix_stages; step 3 (AI repair for the
    # remaining issues) to _ai_repair_stage.
    other, heading_fix_made, self_ref_fix_made, link_fix_made = _python_fix_stages(
        note_path, repairable, note_map, fix_headings, vault_path, lock, rel
    )

    fixed_content = None
    repair_status = "failed"
    if other:
        fixed_content, repair_status = _ai_repair_stage(
            note_path, other, model, timeout, note_map, vault_path, rel
        )
    elif broken or heading_issues or self_ref_issues:
        # Only broken wikilinks / heading / self-ref fixes — no Claude call needed
        repair_status = (
            "fixed"
            if (link_fix_made or heading_fix_made or self_ref_fix_made)
            else "failed"
        )

    # QA-004: status-icon + repair_status bookkeeping lifted to
    # _classify_repair_outcome so the worker body reads as a sequence of
    # repair stages rather than interleaved fix / status / print concerns.
    icon, repair_status = _classify_repair_outcome(
        fixed_content=fixed_content,
        other=other,
        link_fix_made=link_fix_made,
        heading_fix_made=heading_fix_made,
        self_ref_fix_made=self_ref_fix_made,
        repair_status=repair_status,
        prev_status=prev_status,
    )

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
