"""Registry shapes for the doctor pipeline.

Two registries live here:

* ``FixMode`` (ARC-008) binds a vault-wide CLI ``--flag`` to the runner
  that performs the fix (``--fix-tags``, ``--strip-prefixes``, ...);
  ``run_fix_modes`` iterates them so the ``--fix-all`` dispatch is data.
* ``Rule`` (QA-005) binds a per-note issue-code family to the check that
  detects it (and optionally the fix that repairs it), so ``check_note``
  iterates a registered list instead of inlining eight checks in one body
  and each rule is unit-testable on its own.

Also QA-005: ``DoctorOptions`` freezes every flag ``run_scan_and_repair``
takes (replacing the 12-parameter signature) and ``ScanContext`` carries the
mutable per-run scanning state (note lists, note map, skip count) that the
pipeline stages trade back and forth -- previously a 10-parameter /
5-tuple-return handoff.

Stdlib-only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# A runner takes (vault_path, dry_run) and performs the mode's work.  CLI
# concerns (argparse, args model, etc.) stay in doctor.cli; the runner sees
# only the resolved vault and the execute/dry-run flag.
FixModeRunner = Callable[[Path, bool], None]


@dataclass(frozen=True)
class FixMode:
    """One row in the fix-mode registry.

    Attributes:
        flag: The ``argparse`` attribute name that selects this mode
            (e.g. ``"fix_tags"`` for ``--fix-tags``).
        runner: Callable invoked as ``runner(vault_path, dry_run)``.
        label: Human-readable name used in progress / commit messages.
    """

    flag: str
    runner: FixModeRunner
    label: str


@dataclass(frozen=True)
class DoctorOptions:
    """Every flag ``run_scan_and_repair`` reads, frozen at construction.

    QA-005: replaces the 12-keyword parameter list.  Defaults mirror
    ``doctor.cli``'s argparse defaults so a bare ``DoctorOptions()`` is the
    same as invoking the CLI with no optional flags.

    Attributes:
        dry_run: Report issues but skip all writes and AI calls.
        fix_frontmatter: Invoke the AI backend to repair repairable issues.
        fix_sessions: Print the session-duplicate report and exit.
        errors_only: Suppress warnings; only report/repair errors.
        no_state: Skip the stale-state filter.
        model: AI model override (None = backend default).
        limit: Max notes to repair per run (0 = unlimited).
        jobs: Parallel repair worker count.
        timeout: Per-repair AI call timeout in seconds.
        fix_headings: Auto-promote ``##`` headings to ``#`` during repair.
    """

    dry_run: bool = False
    fix_frontmatter: bool = False
    fix_sessions: bool = False
    errors_only: bool = False
    no_state: bool = False
    model: str | None = None
    limit: int = 0
    jobs: int = 3
    timeout: int = 120
    fix_headings: bool = True


@dataclass
class ScanContext:
    """Mutable per-run scanning state threaded through the pipeline stages.

    QA-005: the prefix-cluster stage previously took 10 parameters and
    returned a 5-tuple the caller destructured back into exactly these
    fields; the stages now mutate this object in place.

    Attributes:
        vault: Resolved vault root.
        state: Loaded doctor state dict (mutated; saved by the orchestrator).
        options: The run's frozen flags.
        today_str: ``date.today().isoformat()``, computed once per run.
        explicit: True when the user named specific notes (disables the
            state-skip filter for the refresh path).
        all_notes: Every vault note (rebuilt after prefix-cluster moves).
        note_map: Wikilink target map over ``all_notes``.
        target_notes: Notes the scan will visit.
        skipped_by_state: Count filtered out by the stale-state check.
        vault_claude_md / vault_tags_md: Auto-generated root files always
            excluded from scanning (rebuilt by ``update_index.py``).
    """

    vault: Path
    state: dict
    options: DoctorOptions
    today_str: str
    explicit: bool = False
    all_notes: list[Path] = field(default_factory=list)
    note_map: dict[str, list[Path]] = field(default_factory=dict)
    target_notes: list[Path] = field(default_factory=list)
    skipped_by_state: int = 0
    vault_claude_md: Path | None = None
    vault_tags_md: Path | None = None


@dataclass(frozen=True)
class NoteCheckContext:
    """Immutable per-note context handed to every ``Rule.check``.

    Attributes:
        note_map: Wikilink target map over all vault notes.
        vault: Resolved vault root (for relative-path math).
        parts: The note's path parts relative to the vault.
        is_daily: True for daily-typed or Daily/-folder notes (several rules
            are skipped for daily notes).
    """

    note_map: dict[str, list[Path]]
    vault: Path
    parts: tuple[str, ...]
    is_daily: bool


# A rule check takes (path, raw content, parsed frontmatter, ctx) and
# returns the Issues it found. ``Any`` for the Issue type keeps protocol.py
# import-cycle-free (``Issue`` lives in ``doctor._state``).
RuleCheck = Callable[[Path, str, dict[str, Any], NoteCheckContext], list[Any]]
# A rule fix, when the code family has a mechanical one, takes the note path
# and returns whether it changed the note.
RuleFix = Callable[[Path], bool]


@dataclass(frozen=True)
class Rule:
    """One row in the per-note check registry (QA-005).

    Attributes:
        name: Issue-code family this rule detects (e.g. ``"BROKEN_WIKILINK"``).
        check: Detector invoked once per note by ``check_note``.
        fix: Optional mechanical fixer for the same code family. Detection
            and repair are intentionally decoupled -- the repair pipeline
            (``doctor.worker``) stages fixes by code across a whole note --
            so this field documents the pairing without requiring it.
    """

    name: str
    check: RuleCheck
    fix: RuleFix | None = None


def run_fix_modes(
    modes: tuple[FixMode, ...],
    args: object,
    vault_path: Path,
) -> bool:
    """Run every selected fix-mode in registry order.

    ``--fix-all`` keeps going through the whole registry; a standalone mode
    (``--fix-tags`` alone, etc.) returns after the first selected mode so the
    user's chosen mode runs without cascading into the rest of the pipeline.

    Args:
        modes: The fix-mode registry (typically the tuple built by
            ``doctor.cli._build_fix_modes``).
        args: Parsed argparse namespace; each mode's ``flag`` is read via
            ``getattr``.
        vault_path: Resolved vault root.

    Returns:
        True if a standalone (non-``--fix-all``) mode ran and the caller
        should therefore skip ``run_scan_and_repair``; False otherwise (either
        no mode was selected, or ``--fix-all`` ran every selected mode and the
        caller should continue into scan-and-repair).
    """
    fix_all = bool(getattr(args, "fix_all", False))
    execute = bool(getattr(args, "execute", False))
    dry = not execute
    for mode in modes:
        if getattr(args, mode.flag, False):
            mode.runner(vault_path, dry)
            if not fix_all:
                return True
    return False
