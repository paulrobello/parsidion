"""Core scan-and-repair pipeline (``run_scan_and_repair``).

Extracted from the original ``vault_doctor.py`` (ARC-008 / QA-003).  Originally
a 309-line function with cyclomatic complexity 58 and 6 levels of nesting; the
prefix-cluster, scan, summarise, classify, and apply-repairs stages are now
standalone helpers, leaving ``run_scan_and_repair`` as a flat sequential
pipeline with single-level branching.

Behavior is byte-identical to the original — every print, state write, git
commit, reindex, and exit path is preserved.

Stdlib-only.
"""

from __future__ import annotations

import concurrent.futures
import sys
import threading
from datetime import date
from pathlib import Path

import vault_common

from doctor._state import (
    DETECTION_ONLY_CODES,
    REPAIRABLE_CODES,
    STALE_COMMIT_MINUTES,
    Issue,
    _rel,
    save_state,
    should_skip,
)
from doctor.check import check_note
from doctor.protocol import (
    DoctorOptions,
    RuleReport,
    ScanContext,
    deselected_rules,
    rule_enabled,
)
from doctor.frontmatter import (  # noqa: F401 — _note_is_daily re-export parity
    _auto_fix_metadata_wrapper,
    _auto_fix_scalar_list_field,
    _note_is_daily,
)
from doctor.graph import _run_reindex, commit_stale_files
from doctor.links import build_note_map, dedup_related_links
from doctor.subfolder import (
    _filter_clusters_with_claude,
    find_prefix_clusters,
    fix_prefix_cluster,
)
from doctor.tags import run_fix_sessions
from doctor.worker import _repair_one


def _display_cluster_plan(
    clusters: list[tuple[Path, str, list[Path], Path | None]],
    vault: Path,
) -> None:
    """Print the prefix-cluster migration plan (no mutations)."""
    total_cluster_notes = sum(len(n) for _, _, n, _ in clusters)
    print(
        f"\nFound {len(clusters)} prefix cluster(s) "
        f"({total_cluster_notes} note(s) to reorganize):\n"
    )
    for cluster_folder, prefix, cluster_notes, base_note in clusters:
        folder_rel = cluster_folder.relative_to(vault)
        kind = "exact-stem" if base_note is not None else "first-word"
        print(f"  {folder_rel}/{prefix}/  ({len(cluster_notes)} notes, {kind})")
        for note in sorted(cluster_notes):
            note_rel = note.relative_to(vault)
            if note is base_note:
                new_name = note.name  # base note keeps its filename
            elif note.stem.startswith(f"{prefix}-"):
                new_name = note.stem[len(prefix) + 1 :] + ".md"
            else:
                new_name = note.name
            print(f"    {note_rel}  →  {folder_rel}/{prefix}/{new_name}")
    print()


def _apply_prefix_clusters(
    clusters: list[tuple[Path, str, list[Path], Path | None]], ctx: ScanContext
) -> int:
    """Execute prefix-cluster moves and refresh the scan context in place.

    QA-005: previously a 10-parameter function returning a 5-tuple the
    caller destructured back into exactly the ``ScanContext`` fields; it
    now mutates *ctx* (``all_notes``, ``note_map``, ``target_notes``,
    ``skipped_by_state``) and returns just the number of moves made.
    """
    vault = ctx.vault
    print("Reorganizing prefix clusters…\n")
    cluster_repaired = 0
    for cluster_folder, prefix, cluster_notes, base_note in clusters:
        moves = fix_prefix_cluster(
            cluster_folder, prefix, cluster_notes, ctx.all_notes, base_note
        )
        for old_path, new_path in moves:
            old_rel = old_path.relative_to(vault)
            new_rel = new_path.relative_to(vault)
            print(f"  {old_rel}  →  {new_rel}")
            cluster_repaired += 1
    if not cluster_repaired:
        return 0
    vault_common.git_commit_vault(
        f"refactor(vault): reorganize {cluster_repaired} note(s) into prefix subfolders",
        vault=vault,
    )
    print()
    # Refresh after moves
    ctx.all_notes = list(vault_common.all_vault_notes_walk(vault))
    ctx.note_map = build_note_map(ctx.all_notes)
    all_filtered = [
        p
        for p in ctx.all_notes
        if p != ctx.vault_claude_md
        and p != ctx.vault_tags_md
        and p.name != "MANIFEST.md"
    ]
    if not ctx.explicit and not ctx.options.no_state:
        new_target = [
            p for p in all_filtered if not should_skip(_rel(p, vault), ctx.state)
        ]
        ctx.target_notes = new_target
        ctx.skipped_by_state = len(all_filtered) - len(new_target)
    else:
        ctx.target_notes = all_filtered
        ctx.skipped_by_state = 0
    return cluster_repaired


def _scan_notes_for_issues(ctx: ScanContext) -> dict[Path, list[Issue]]:
    """Run check_note over every target, recording clean notes in state.

    Returns the dict of notes with at least one issue. ENH-015: the run's
    rule selection filters the checks, and every detected issue is credited
    to its rule in ``ctx.report``.
    """
    issues_by_note: dict[Path, list[Issue]] = {}
    for note in ctx.target_notes:
        note_issues = check_note(
            note, ctx.note_map, ctx.vault, ctx.options.enabled_rules
        )
        if ctx.options.errors_only:
            note_issues = [i for i in note_issues if i.severity == "error"]
        for issue in note_issues:
            ctx.report.record_found(issue.rule)
        key = _rel(note, ctx.vault)
        if note_issues:
            issues_by_note[note] = note_issues
        else:
            # Record as clean so it can be skipped next run
            ctx.state.setdefault("notes", {})[key] = {
                "status": "ok",
                "last_checked": ctx.today_str,
                "issues": [],
            }
    return issues_by_note


def _summarise_issues(
    issues_by_note: dict[Path, list[Issue]], vault: Path
) -> tuple[int, int]:
    """Print the issue summary. Returns (total_errors, total_warnings)."""
    total_errors = sum(
        1 for iv in issues_by_note.values() for i in iv if i.severity == "error"
    )
    total_warnings = sum(
        1 for iv in issues_by_note.values() for i in iv if i.severity == "warning"
    )
    print(
        f"\nFound issues in {len(issues_by_note)} notes — "
        f"{total_errors} error(s), {total_warnings} warning(s)\n"
    )
    for note_path, note_issues in sorted(issues_by_note.items()):
        rel = note_path.relative_to(vault)
        print(f"  {rel}")
        for issue in note_issues:
            icon = "✗" if issue.severity == "error" else "⚠"
            print(f"    {icon} [{issue.code}] {issue.message}")
    print()
    return total_errors, total_warnings


def _classify_repair_candidates(
    issues_by_note: dict[Path, list[Issue]],
    state: dict,
    today_str: str,
    vault: Path,
) -> tuple[list[tuple[Path, list[Issue]]], list[Path]]:
    """Split issues into (repair_candidates, manual_only) and mark manual-only in state."""
    repair_candidates: list[tuple[Path, list[Issue]]] = []
    manual_only: list[Path] = []
    for p, iv in issues_by_note.items():
        if any(i.code in REPAIRABLE_CODES for i in iv):
            repair_candidates.append((p, iv))
        else:
            manual_only.append(p)
    for p in manual_only:
        codes = [i.code for i in issues_by_note[p]]
        # A "skipped" status is permanent as far as should_skip is concerned,
        # so a note carrying a detection-only defect must NOT be recorded --
        # otherwise the scanner announces it once and never mentions it again.
        # Leaving it stateless means every run re-reports it until it is fixed.
        if any(c in DETECTION_ONLY_CODES for c in codes):
            continue
        key = _rel(p, vault)
        state.setdefault("notes", {})[key] = {
            "status": "skipped",
            "last_checked": today_str,
            "issues": codes,
        }
    return repair_candidates, manual_only


def _run_deterministic_frontmatter_fixes(
    issues_by_note: dict[Path, list[Issue]],
    vault: Path,
    state: dict,
    note_map: dict[str, list[Path]],
    today_str: str,
    report: RuleReport | None = None,
) -> None:
    """Deterministic (Python-only) repair for the two detection-only codes that
    have a safe mechanical fix: ``NESTED_FM_KEY`` (a ``metadata:`` wrapper) and
    ``SCALAR_LIST_FIELD``.

    Runs before issue classification so fixed notes drop out of the AI-repair
    candidate set. A fixed note is re-scanned: if any codes remain they stay in
    ``issues_by_note`` and flow through the normal (possibly AI) repair path; if
    it is now clean it is removed and recorded ``fixed`` in state. Never calls
    the AI backend.
    """
    for note_path in list(issues_by_note.keys()):
        codes = {i.code for i in issues_by_note[note_path]}
        if not (codes & {"NESTED_FM_KEY", "SCALAR_LIST_FIELD"}):
            continue
        changed = False
        # metadata-wrapper first: it lifts nested fields to top level, which
        # the scalar-list fixer then sees as ordinary top-level lines.
        if "NESTED_FM_KEY" in codes:
            changed |= _auto_fix_metadata_wrapper(note_path)
        if "SCALAR_LIST_FIELD" in codes:
            changed |= _auto_fix_scalar_list_field(note_path)
        if not changed:
            continue
        # ENH-015: both codes belong to the frontmatter-syntax rule.
        if report is not None:
            report.record_fixed(
                "frontmatter-syntax",
                sum(
                    1
                    for i in issues_by_note[note_path]
                    if i.code in {"NESTED_FM_KEY", "SCALAR_LIST_FIELD"}
                ),
            )
        rel = note_path.relative_to(vault)
        new_issues = check_note(note_path, note_map, vault)
        if new_issues:
            issues_by_note[note_path] = new_issues
        else:
            del issues_by_note[note_path]
            state.setdefault("notes", {})[_rel(note_path, vault)] = {
                "status": "fixed",
                "last_checked": today_str,
                "issues": [],
            }
        print(
            f"  ✓ {rel}: flattened metadata/scalar frontmatter (deterministic)",
            flush=True,
        )


def _apply_repairs_parallel(
    repair_candidates: list[tuple[Path, list[Issue]]], ctx: ScanContext
) -> tuple[int, int, int]:
    """Run _repair_one over the candidate batch via ThreadPoolExecutor.

    Returns (repaired, failed, leftover). QA-005: model/jobs/timeout/
    note_map/fix_headings/vault/limit arrive on the ScanContext instead of
    as nine separate parameters.
    """
    options = ctx.options
    effective_limit = options.limit if options.limit > 0 else len(repair_candidates)
    effective_jobs = max(1, options.jobs)
    repaired = 0
    failed = 0
    lock = threading.Lock()

    print(
        f"Repairing up to {effective_limit} note(s) via prompt AI "
        f"({effective_jobs} parallel job(s), {options.timeout}s timeout)…\n"
    )
    batch = repair_candidates[:effective_limit]
    issues_for = dict(repair_candidates)
    with concurrent.futures.ThreadPoolExecutor(max_workers=effective_jobs) as executor:
        futures = {
            executor.submit(
                _repair_one,
                note_path,
                note_issues,
                options.model,
                ctx.state,
                ctx.today_str,
                lock,
                options.timeout,
                ctx.note_map,
                options.fix_headings,
                ctx.vault,
            ): note_path
            for note_path, note_issues in batch
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                success = future.result()
            except Exception as exc:  # noqa: BLE001
                note_path = futures[future]
                print(
                    f"  {_rel(note_path, ctx.vault)} … ✗ (exception: {exc})",
                    flush=True,
                )
                success = False
            if success:
                repaired += 1
                # ENH-015: repair is staged per note, so every issue the
                # repaired note carried is credited to its rule.
                for issue in issues_for.get(futures[future], []):
                    ctx.report.record_fixed(issue.rule)
            else:
                failed += 1

    leftover = len(repair_candidates) - effective_limit
    return repaired, failed, leftover


def _pre_scan_housekeeping(vault: Path, options: DoctorOptions) -> None:
    """Best-effort vault housekeeping before the scan (QA-005 extraction).

    Legacy pending-path migration, related-link dedup, and the stale-file
    auto-commit — each silent when there is nothing to do.
    """
    fixed_paths = vault_common.migrate_pending_paths(
        dry_run=options.dry_run, vault=vault
    )
    if fixed_paths:
        action = "Would fix" if options.dry_run else "Fixed"
        print(
            f"{action} {fixed_paths} legacy transcript path(s) in "
            "pending_summaries.jsonl.\n"
        )

    deduped = dedup_related_links(dry_run=options.dry_run, vault_path=vault)
    if deduped:
        action = "Would deduplicate" if options.dry_run else "Deduplicated"
        print(f"{action} related links in {deduped} note(s).\n")

    stale = commit_stale_files(dry_run=options.dry_run, vault_path=vault)
    if stale:
        rel_stale = [str(p.relative_to(vault)) for p in stale]
        if options.dry_run:
            print(
                f"[dry-run] Would commit {len(stale)} stale file(s) "
                f"(>= {STALE_COMMIT_MINUTES} min old):"
            )
        else:
            print(
                f"Committed {len(stale)} stale file(s) "
                f"(>= {STALE_COMMIT_MINUTES} min old):"
            )
        for name in rel_stale:
            print(f"  {name}")
        print()


def _build_scan_context(
    vault: Path,
    state: dict,
    notes: list[Path],
    options: DoctorOptions,
    today_str: str,
) -> ScanContext:
    """Resolve scan targets and build the ScanContext (QA-005 extraction)."""
    # QA-107: one vault walk per run. The non-explicit path derives the scan
    # targets from the same walk that feeds note_map; only an explicit-notes
    # run uses a walk for all_notes alone. (The post-subfolder-move re-walk
    # in _apply_subfolder_migration is separate and deliberately kept.)
    all_notes = list(vault_common.all_vault_notes_walk(vault))
    if notes:
        target_notes = [Path(n).resolve() for n in notes]
        explicit = True
    else:
        target_notes = list(all_notes)
        explicit = False

    # Always skip auto-generated files (rebuilt by update_index.py, never doctor-repaired).
    vault_claude_md = vault / "CLAUDE.md"
    vault_tags_md = vault / "TAGS.md"
    target_notes = [
        p
        for p in target_notes
        if p != vault_claude_md and p != vault_tags_md and p.name != "MANIFEST.md"
    ]

    # Skip notes that have already been processed and are still fresh
    if not explicit and not options.no_state:
        before = len(target_notes)
        target_notes = [
            p for p in target_notes if not should_skip(_rel(p, vault), state)
        ]
        skipped_by_state = before - len(target_notes)
    else:
        skipped_by_state = 0

    # Build note map once for wikilink resolution
    note_map = build_note_map(all_notes)

    return ScanContext(
        vault=vault,
        state=state,
        options=options,
        today_str=today_str,
        explicit=explicit,
        all_notes=all_notes,
        note_map=note_map,
        target_notes=target_notes,
        skipped_by_state=skipped_by_state,
        vault_claude_md=vault_claude_md,
        vault_tags_md=vault_tags_md,
    )


def _print_rule_report(ctx: ScanContext) -> None:
    """Print the per-rule found/fixed/skipped table (ENH-015).

    Silent when no registered rule produced a finding (clean vault) — the
    table exists to attribute issues, not to enumerate zero rows. Rules
    deselected via ``--only``/``--skip`` are named once, after the table,
    so a thin run explains itself.
    """
    table = ctx.report.render()
    if table:
        print(f"\nRule report:\n{table}")
    deselected = deselected_rules(ctx.options.enabled_rules)
    if deselected:
        print(f"\nDeselected by --only/--skip: {', '.join(deselected)}")


def _detect_and_apply_prefix_clusters(ctx: ScanContext) -> None:
    """Detect prefix clusters, display the plan, apply on request (QA-005).

    ENH-015: gated on the ``subfolder-prefix`` rule so ``--skip
    subfolder-prefix`` suppresses cluster reorganization from this in-scan
    stage too, not just from the ``--migrate-subfolders`` fix mode.
    """
    if not rule_enabled(ctx.options.enabled_rules, "subfolder-prefix"):
        return
    clusters = find_prefix_clusters(ctx.all_notes, ctx.vault)
    if clusters and not ctx.options.dry_run:
        # Filter out generic-word false positives using the configured prompt AI backend
        clusters = _filter_clusters_with_claude(
            clusters, model=ctx.options.model, timeout=ctx.options.timeout
        )
    if clusters:
        _display_cluster_plan(clusters, ctx.vault)
        if not ctx.options.dry_run and ctx.options.fix_frontmatter:
            _apply_prefix_clusters(clusters, ctx)


def run_scan_and_repair(
    vault: Path,
    state: dict,
    notes: list[Path],
    options: DoctorOptions,
) -> None:
    """Run the core scan-and-repair pipeline.

    Handles: legacy pending-path migration, session-consolidation check,
    related-link dedup, stale-file auto-commit, prefix-cluster detection,
    note scanning, issue reporting, and parallel AI-assisted repair.

    QA-005: the twelve keyword flags previously threaded through every
    stage now arrive as one frozen ``DoctorOptions``; the mutable scanning
    state the stages trade back and forth (note lists, note map, skip
    count) lives on a ``ScanContext`` instead of a 10-parameter /
    5-tuple-return handoff.

    Args:
        vault: Resolved vault root path.
        state: Loaded doctor state dict (may be mutated and saved).
        notes: Explicit note paths to scan (empty list = all vault notes).
        options: Frozen run flags (see ``DoctorOptions``).
    """
    _pre_scan_housekeeping(vault, options)

    # Session consolidation check
    if options.fix_sessions:
        run_fix_sessions(vault_path=vault)
        sys.exit(0)

    today_str = date.today().isoformat()
    ctx = _build_scan_context(vault, state, notes, options, today_str)

    # ── Prefix cluster detection and fixing ──────────────────────────────────
    _detect_and_apply_prefix_clusters(ctx)

    print(
        f"Scanning {len(ctx.target_notes)} vault notes"
        + (
            f" ({ctx.skipped_by_state} skipped — already OK)"
            if ctx.skipped_by_state
            else ""
        )
        + "…"
    )

    # Scan — also records clean notes in state
    issues_by_note = _scan_notes_for_issues(ctx)

    if not issues_by_note:
        print("✓ No issues found.")
        if not options.dry_run:
            save_state(state, vault)
        return

    _summarise_issues(issues_by_note, vault)

    if options.dry_run:
        _print_rule_report(ctx)
        return

    # Deterministic (Python-only) frontmatter repairs for the two detection-only
    # codes that have a safe mechanical fix (metadata: wrapper, scalar list
    # field). Runs before classification so fixed notes drop out of the AI-repair
    # candidate set; remaining codes flow through the normal repair path.
    if options.fix_frontmatter:
        _run_deterministic_frontmatter_fixes(
            issues_by_note,
            vault,
            state,
            ctx.note_map,
            ctx.today_str,
            report=ctx.report,
        )
        if not issues_by_note:
            print("✓ No issues remaining after deterministic frontmatter repairs.")
            save_state(state, vault)
            _print_rule_report(ctx)
            return

    # Classify repair candidates
    repair_candidates, manual_only = _classify_repair_candidates(
        issues_by_note, state, today_str, vault
    )

    if not repair_candidates:
        print("No repairable issues (flat daily notes require manual fixes).")
        save_state(state, vault)
        _print_rule_report(ctx)
        return

    if not options.fix_frontmatter:
        print(
            f"{len(repair_candidates)} note(s) have repairable issues.\n"
            "Run with --fix-frontmatter to repair them via the configured "
            "prompt AI backend."
        )
        save_state(state, vault)
        _print_rule_report(ctx)
        return

    # Apply repairs in parallel
    repaired, failed, leftover = _apply_repairs_parallel(repair_candidates, ctx)

    save_state(state, vault)
    print(
        f"\nDone: {repaired} repaired, {failed} failed, {leftover} not yet processed."
    )
    _print_rule_report(ctx)

    # Commit the repaired notes here, under a message that names them. The
    # reindex below stages only CLAUDE.md/TAGS.md/MANIFEST.md, so without this
    # the AI's edits sat dirty in the worktree until an unrelated later hook
    # swept them into a "chore(vault): session notes" commit — attributing
    # model-authored changes to a commit that does not mention them, and
    # leaving no reviewable diff in between.
    if repaired:
        vault_common.git_commit_vault(
            f"fix(vault): repair frontmatter in {repaired} note(s) via vault_doctor",
            vault=vault,
        )

    # Scan-and-repair is the LAST stage of the --fix-all pipeline; earlier
    # stages reindex only their own changes, so repairs must reindex here too.
    if repaired:
        _run_reindex(vault)
