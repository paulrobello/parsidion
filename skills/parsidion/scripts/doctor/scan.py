"""Read-only vault scan — ``scan_notes_readonly``.

ENH-007 / Step 3: exposes the metadata + wikilink scan that ``vault_doctor``
already performs as an importable, side-effect-free function so the
``vault_health`` reporting layer can consume frontmatter/broken-wikilink
counts without re-implementing validation (the repo already has three
drifted copies of similar resolvers; one validator is enough).

This is a small extraction from ``doctor.orchestrator._scan_notes_for_issues``
that drops the state-file writes — same ``check_note`` call, same
``build_note_map`` shape, no AI calls, no mutations. The orchestrator keeps
its state-recording version because the repair pipeline depends on the clean
notes being recorded between runs; the health report only needs the counts.

Stdlib-only.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import vault_common

from doctor.check import check_note
from doctor.links import build_note_map


@dataclass(frozen=True)
class ScanSummary:
    """Aggregate result of a read-only scan over the vault.

    Attributes:
        total_notes: Notes scanned (auto-generated files excluded).
        notes_with_issues: Notes with at least one Issue (error or warning).
        errors: Total error-severity issues across all notes.
        warnings: Total warning-severity issues across all notes.
        by_code: Issue code → count of notes carrying that code at least once.
            Keyed by note (not by issue instance) so a note with two
            BROKEN_WIKILINK issues counts once — the health report cares
            about how many notes are affected, not the raw issue count.
    """

    total_notes: int
    notes_with_issues: int
    errors: int
    warnings: int
    by_code: dict[str, int] = field(default_factory=dict)


def _git_tracked_gitignored(vault: Path) -> list[str]:
    """Return vault-relative paths that are both tracked and gitignored.

    Uses ``git ls-files`` against the gitignore-excluded set so a file the
    user committed before gitignoring shows up here. Degrades to an empty
    list when git is unavailable or the vault is not a git repo (the common
    case for freshly-created vaults before the first commit).
    """
    if not (vault / ".git").exists():
        return []
    try:
        result = subprocess.run(
            ["git", "-C", str(vault), "ls-files", "-i", "-c", "--"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def scan_notes_readonly(vault: Path) -> ScanSummary:
    """Run ``check_note`` over every vault note and return aggregated counts.

    Read-only: no state writes, no AI calls, no mutations. Mirrors what
    ``vault_doctor --dry-run`` reports, minus the prefix-cluster detection
    and the related-link dedup (both of which are write paths).

    Auto-generated files (``CLAUDE.md``, ``TAGS.md``, ``MANIFEST.md``) are
    excluded so the count matches what the doctor's own scan pipeline sees.

    Args:
        vault: Resolved vault root.

    Returns:
        Aggregated issue counts. Always returns a ``ScanSummary`` — never
        raises; an unreadable note becomes a READ_ERROR issue on that note.
    """
    notes = list(vault_common.all_vault_notes_walk(vault))
    vault_claude_md = vault / "CLAUDE.md"
    vault_tags_md = vault / "TAGS.md"
    notes = [
        p
        for p in notes
        if p != vault_claude_md and p != vault_tags_md and p.name != "MANIFEST.md"
    ]
    note_map = build_note_map(notes)

    by_code: dict[str, int] = {}
    notes_with_issues = 0
    errors = 0
    warnings = 0
    for note in notes:
        issues = check_note(note, note_map, vault)
        if not issues:
            continue
        notes_with_issues += 1
        # Count each code at most once per note (health dimension cares about
        # affected-note counts, not raw issue multiplicity).
        seen_codes: set[str] = set()
        for issue in issues:
            if issue.severity == "error":
                errors += 1
            else:
                warnings += 1
            if issue.code not in seen_codes:
                seen_codes.add(issue.code)
                by_code[issue.code] = by_code.get(issue.code, 0) + 1

    return ScanSummary(
        total_notes=len(notes),
        notes_with_issues=notes_with_issues,
        errors=errors,
        warnings=warnings,
        by_code=by_code,
    )
