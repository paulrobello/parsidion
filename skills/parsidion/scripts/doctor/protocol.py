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
from typing import Any, Literal

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


# ---------------------------------------------------------------------------
# Selectable rule catalog (ENH-015)
# ---------------------------------------------------------------------------

RuleKind = Literal["check", "mode", "repair"]
RuleRisk = Literal["safe", "bulk"]


@dataclass(frozen=True)
class RuleSpec:
    """One selectable doctor rule (ENH-015).

    The catalog unifies the three kinds of rule a doctor run can execute so
    ``--only``/``--skip`` name them from one namespace:

    * ``check`` — a per-note scan rule (``doctor.check`` registry).  ``target``
      is the ``Rule.name`` (issue-code family) of the registry entry.
    * ``mode`` — a vault-wide fix mode (``FixMode`` registry).  ``target`` is
      the ``FixMode.flag`` (argparse attribute name).
    * ``repair`` — the AI frontmatter-repair stage of scan-and-repair
      (``--fix-frontmatter``).  ``target`` is the ``DoctorOptions`` field.

    ``risk`` marks the rules that operate on many notes at once or let the
    AI backend rewrite note bodies — the historically regression-prone ones
    (``bulk``) — so ``--list-rules`` can flag them and a safe bulk invocation
    can exclude them via ``--skip``.
    """

    name: str
    kind: RuleKind
    risk: RuleRisk
    description: str
    target: str


RULE_SPECS: tuple[RuleSpec, ...] = (
    RuleSpec(
        "frontmatter-syntax",
        "check",
        "safe",
        "Frontmatter parses; nested/list/bracket syntax errors",
        "FRONTMATTER_SYNTAX",
    ),
    RuleSpec(
        "required-fields",
        "check",
        "safe",
        "Required frontmatter fields present (date, type, related)",
        "REQUIRED_FIELDS",
    ),
    RuleSpec(
        "valid-type",
        "check",
        "safe",
        "type field is one of the allowed note types",
        "VALID_TYPE",
    ),
    RuleSpec(
        "date-format",
        "check",
        "safe",
        "date field is YYYY-MM-DD",
        "DATE_FORMAT",
    ),
    RuleSpec(
        "related-links",
        "check",
        "safe",
        "related field links to at least one other note (no orphans)",
        "ORPHAN",
    ),
    RuleSpec(
        "self-ref",
        "check",
        "safe",
        "related field does not link to the note itself",
        "SELF_REF",
    ),
    RuleSpec(
        "headings",
        "check",
        "safe",
        "First heading matches the note title",
        "HEADING_MISMATCH",
    ),
    RuleSpec(
        "broken-wikilinks",
        "check",
        "safe",
        "Wikilinks resolve to an existing note",
        "BROKEN_WIKILINKS",
    ),
    RuleSpec(
        "flat-daily",
        "check",
        "safe",
        "Daily notes use the DD-{username}.md namespace",
        "FLAT_DAILY",
    ),
    RuleSpec(
        "frontmatter-repair",
        "repair",
        "bulk",
        "AI frontmatter repair stage of scan-and-repair",
        "fix_frontmatter",
    ),
    RuleSpec(
        "tags",
        "mode",
        "bulk",
        "Merge duplicate tags (plural/singular, hyphen variants)",
        "fix_tags",
    ),
    RuleSpec(
        "strip-prefixes",
        "mode",
        "bulk",
        "Strip redundant subfolder prefixes from filenames (vault-wide rename)",
        "strip_prefixes",
    ),
    RuleSpec(
        "subfolder-prefix",
        "mode",
        "bulk",
        "Move prefix clusters into subfolders (rewrites wikilinks)",
        "migrate_subfolders",
    ),
    RuleSpec(
        "daily-namespace",
        "mode",
        "bulk",
        "Rename legacy flat daily notes to DD-{username}.md",
        "migrate_daily_notes",
    ),
    RuleSpec(
        "permissions",
        "mode",
        "safe",
        "Tighten permissions on sensitive vault files",
        "fix_permissions",
    ),
)

RULE_NAMES: tuple[str, ...] = tuple(spec.name for spec in RULE_SPECS)


def select_rules(
    only: list[str] | None, skip: list[str] | None
) -> frozenset[str] | None:
    """Resolve ``--only``/``--skip`` into the enabled rule-name set.

    Returns ``None`` when no selection was made (every rule enabled) so the
    default pipeline is untouched; an explicit ``--only``/``--skip`` returns
    the effective set even when empty.
    """
    if only:
        return frozenset(only)
    if skip:
        return frozenset(RULE_NAMES) - frozenset(skip)
    return None


def rule_enabled(enabled: frozenset[str] | None, name: str) -> bool:
    """True when *name* runs under the selection (``None`` = all enabled)."""
    return enabled is None or name in enabled


def deselected_rules(enabled: frozenset[str] | None) -> list[str]:
    """Rule names excluded by the selection, in catalog order."""
    if enabled is None:
        return []
    return [name for name in RULE_NAMES if name not in enabled]


@dataclass
class RuleReport:
    """Per-rule found/fixed counters behind the end-of-run table.

    ENH-015: ``found`` counts detected issues; ``fixed`` counts issues whose
    note was repaired this run (deterministic or AI — repair is staged per
    note, so every issue a repaired note carried is credited to its rule).
    ``skipped`` in the rendered table is ``found - fixed``: issues left
    unprocessed (all of them under ``--dry-run``; manual-only and
    beyond-``--limit`` notes otherwise). Issues without an owning rule
    (``READ_ERROR``) are not counted.
    """

    found: dict[str, int] = field(default_factory=dict)
    fixed: dict[str, int] = field(default_factory=dict)

    def _bump(self, bucket: dict[str, int], slug: str, n: int) -> None:
        if n > 0 and slug:
            bucket[slug] = bucket.get(slug, 0) + n

    def record_found(self, slug: str, n: int = 1) -> None:
        self._bump(self.found, slug, n)

    def record_fixed(self, slug: str, n: int = 1) -> None:
        self._bump(self.fixed, slug, n)

    def rows(self) -> list[tuple[str, int, int, int]]:
        """(rule, found, fixed, skipped) in catalog order, active rules only."""
        active = set(self.found) | set(self.fixed)
        return [
            (
                name,
                self.found.get(name, 0),
                self.fixed.get(name, 0),
                self.found.get(name, 0) - self.fixed.get(name, 0),
            )
            for name in RULE_NAMES
            if name in active
        ]

    def render(self) -> str:
        """The ``rule | found | fixed | skipped`` table (empty when inactive)."""
        rows = self.rows()
        if not rows:
            return ""
        width = max(len(name) for name, *_ in rows)
        header = f"{'rule':<{width}} | found | fixed | skipped"
        sep = f"{'-' * width}-+-------+-------+--------"
        lines = [header, sep]
        for name, found, fixed, skipped in rows:
            lines.append(f"{name:<{width}} | {found:>5} | {fixed:>5} | {skipped:>7}")
        return "\n".join(lines)


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
        enabled_rules: ENH-015 selection from ``--only``/``--skip``;
            ``None`` runs every rule, otherwise only the named rules run.
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
    enabled_rules: frozenset[str] | None = None


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
        report: Per-rule found/fixed counters (ENH-015) rendered as the
            end-of-run table.
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
    report: RuleReport = field(default_factory=RuleReport)


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
        slug: Kebab-case CLI name (ENH-015) matching a ``RuleSpec`` in the
            ``RULE_SPECS`` catalog; the ``--only``/``--skip`` selection and
            the per-rule report key on it.
    """

    name: str
    check: RuleCheck
    fix: RuleFix | None = None
    slug: str = ""


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
