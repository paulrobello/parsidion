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
    clusters: list[tuple[Path, str, list[Path], Path | None]],
    all_notes: list[Path],
    vault: Path,
    state: dict,
    explicit: bool,
    no_state: bool,
    vault_claude_md: Path,
    vault_tags_md: Path,
    target_notes: list[Path],
    skipped_by_state: int,
) -> tuple[int, list[Path], dict[str, list[Path]], list[Path], int]:
    """Execute prefix-cluster moves and refresh the note map + target list.

    Returns ``(cluster_repaired, all_notes, note_map, target_notes,
    skipped_by_state)``.  When no moves happened the caller's ``target_notes``
    and ``skipped_by_state`` are returned unchanged; the note map is rebuilt
    only if at least one move succeeded.
    """
    print("Reorganizing prefix clusters…\n")
    cluster_repaired = 0
    for cluster_folder, prefix, cluster_notes, base_note in clusters:
        moves = fix_prefix_cluster(
            cluster_folder, prefix, cluster_notes, all_notes, base_note
        )
        for old_path, new_path in moves:
            old_rel = old_path.relative_to(vault)
            new_rel = new_path.relative_to(vault)
            print(f"  {old_rel}  →  {new_rel}")
            cluster_repaired += 1
    if not cluster_repaired:
        return 0, all_notes, build_note_map(all_notes), target_notes, skipped_by_state
    vault_common.git_commit_vault(
        f"refactor(vault): reorganize {cluster_repaired} note(s) into prefix subfolders",
        vault=vault,
    )
    print()
    # Refresh after moves
    all_notes = list(vault_common.all_vault_notes_walk(vault))
    note_map = build_note_map(all_notes)
    all_filtered = [
        p
        for p in all_notes
        if p != vault_claude_md and p != vault_tags_md and p.name != "MANIFEST.md"
    ]
    if not explicit and not no_state:
        new_target = [p for p in all_filtered if not should_skip(_rel(p, vault), state)]
        new_skipped = len(all_filtered) - len(new_target)
    else:
        new_target = all_filtered
        new_skipped = 0
    return cluster_repaired, all_notes, note_map, new_target, new_skipped


def _scan_notes_for_issues(
    target_notes: list[Path],
    note_map: dict[str, list[Path]],
    vault: Path,
    errors_only: bool,
    state: dict,
    today_str: str,
) -> dict[Path, list[Issue]]:
    """Run check_note over every target, recording clean notes in state.

    Returns the dict of notes with at least one issue.
    """
    issues_by_note: dict[Path, list[Issue]] = {}
    for note in target_notes:
        note_issues = check_note(note, note_map, vault)
        if errors_only:
            note_issues = [i for i in note_issues if i.severity == "error"]
        key = _rel(note, vault)
        if note_issues:
            issues_by_note[note] = note_issues
        else:
            # Record as clean so it can be skipped next run
            state.setdefault("notes", {})[key] = {
                "status": "ok",
                "last_checked": today_str,
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
    repair_candidates: list[tuple[Path, list[Issue]]],
    model: str | None,
    state: dict,
    today_str: str,
    jobs: int,
    timeout: int,
    note_map: dict[str, list[Path]],
    fix_headings: bool,
    vault: Path,
    limit: int,
) -> tuple[int, int, int]:
    """Run _repair_one over the candidate batch via ThreadPoolExecutor.

    Returns (repaired, failed, leftover).
    """
    effective_limit = limit if limit > 0 else len(repair_candidates)
    effective_jobs = max(1, jobs)
    repaired = 0
    failed = 0
    lock = threading.Lock()

    print(
        f"Repairing up to {effective_limit} note(s) via prompt AI "
        f"({effective_jobs} parallel job(s), {timeout}s timeout)…\n"
    )
    batch = repair_candidates[:effective_limit]
    with concurrent.futures.ThreadPoolExecutor(max_workers=effective_jobs) as executor:
        futures = {
            executor.submit(
                _repair_one,
                note_path,
                note_issues,
                model,
                state,
                today_str,
                lock,
                timeout,
                note_map,
                fix_headings,
                vault,
            ): note_path
            for note_path, note_issues in batch
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                success = future.result()
            except Exception as exc:  # noqa: BLE001
                note_path = futures[future]
                print(f"  {_rel(note_path, vault)} … ✗ (exception: {exc})", flush=True)
                success = False
            if success:
                repaired += 1
            else:
                failed += 1

    leftover = len(repair_candidates) - effective_limit
    return repaired, failed, leftover


def run_scan_and_repair(
    vault: Path,
    state: dict,
    *,
    notes: list[Path],
    dry_run: bool,
    fix_frontmatter: bool,
    fix_sessions: bool,
    errors_only: bool,
    no_state: bool,
    model: str | None,
    limit: int,
    jobs: int,
    timeout: int,
    fix_headings: bool,
) -> None:
    """Run the core scan-and-repair pipeline.

    Handles: legacy pending-path migration, session-consolidation check,
    related-link dedup, stale-file auto-commit, prefix-cluster detection,
    note scanning, issue reporting, and parallel AI-assisted repair.

    All parameters are passed explicitly — this function does not read the
    module-level ``_vault_path`` global.

    Args:
        vault: Resolved vault root path.
        state: Loaded doctor state dict (may be mutated and saved).
        notes: Explicit note paths to scan (empty list = all vault notes).
        dry_run: When True, report issues but skip all writes and AI calls.
        fix_frontmatter: When True, invoke the AI backend to repair issues.
        fix_sessions: When True, print session-duplicate report and exit.
        errors_only: When True, suppress warnings and only report/repair errors.
        no_state: When True, skip the stale-state filter.
        model: AI model override (None = backend default).
        limit: Max notes to repair per run (0 = unlimited).
        jobs: Parallel repair worker count.
        timeout: Per-repair AI call timeout in seconds.
        fix_headings: When True, auto-promote ## headings to #.
    """
    # Auto-fix legacy pending paths (silent when nothing to fix)
    fixed_paths = vault_common.migrate_pending_paths(dry_run=dry_run, vault=vault)
    if fixed_paths:
        action = "Would fix" if dry_run else "Fixed"
        print(
            f"{action} {fixed_paths} legacy transcript path(s) in pending_summaries.jsonl.\n"
        )

    # Session consolidation check
    if fix_sessions:
        run_fix_sessions(vault_path=vault)
        sys.exit(0)

    # Auto-deduplicate related wikilinks (silent when nothing to fix)
    deduped = dedup_related_links(dry_run=dry_run, vault_path=vault)
    if deduped:
        action = "Would deduplicate" if dry_run else "Deduplicated"
        print(f"{action} related links in {deduped} note(s).\n")

    # Auto-commit uncommitted vault files older than STALE_COMMIT_MINUTES
    stale = commit_stale_files(dry_run=dry_run, vault_path=vault)
    if stale:
        rel_stale = [str(p.relative_to(vault)) for p in stale]
        if dry_run:
            print(
                f"[dry-run] Would commit {len(stale)} stale file(s) "
                f"(>= {STALE_COMMIT_MINUTES} min old):"
            )
        else:
            print(
                f"Committed {len(stale)} stale file(s) (>= {STALE_COMMIT_MINUTES} min old):"
            )
        for name in rel_stale:
            print(f"  {name}")
        print()

    today_str = date.today().isoformat()

    # Resolve target notes
    if notes:
        target_notes = [Path(n).resolve() for n in notes]
        explicit = True
    else:
        target_notes = list(vault_common.all_vault_notes_walk(vault))
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
    if not explicit and not no_state:
        before = len(target_notes)
        target_notes = [
            p for p in target_notes if not should_skip(_rel(p, vault), state)
        ]
        skipped_by_state = before - len(target_notes)
    else:
        skipped_by_state = 0

    # Build note map once for wikilink resolution
    all_notes = list(vault_common.all_vault_notes_walk(vault))
    note_map = build_note_map(all_notes)

    # ── Prefix cluster detection and fixing ──────────────────────────────────
    clusters = find_prefix_clusters(all_notes, vault)
    if clusters and not dry_run:
        # Filter out generic-word false positives using the configured prompt AI backend
        clusters = _filter_clusters_with_claude(clusters, model=model, timeout=timeout)
    if clusters:
        _display_cluster_plan(clusters, vault)
        if not dry_run and fix_frontmatter:
            (
                _cluster_repaired,
                all_notes,
                note_map,
                target_notes,
                skipped_by_state,
            ) = _apply_prefix_clusters(
                clusters,
                all_notes,
                vault,
                state,
                explicit,
                no_state,
                vault_claude_md,
                vault_tags_md,
                target_notes,
                skipped_by_state,
            )

    print(
        f"Scanning {len(target_notes)} vault notes"
        + (f" ({skipped_by_state} skipped — already OK)" if skipped_by_state else "")
        + "…"
    )

    # Scan — also records clean notes in state
    issues_by_note = _scan_notes_for_issues(
        target_notes, note_map, vault, errors_only, state, today_str
    )

    if not issues_by_note:
        print("✓ No issues found.")
        if not dry_run:
            save_state(state, vault)
        return

    _summarise_issues(issues_by_note, vault)

    if dry_run:
        return

    # Deterministic (Python-only) frontmatter repairs for the two detection-only
    # codes that have a safe mechanical fix (metadata: wrapper, scalar list
    # field). Runs before classification so fixed notes drop out of the AI-repair
    # candidate set; remaining codes flow through the normal repair path.
    if fix_frontmatter:
        _run_deterministic_frontmatter_fixes(
            issues_by_note, vault, state, note_map, today_str
        )
        if not issues_by_note:
            print("✓ No issues remaining after deterministic frontmatter repairs.")
            save_state(state, vault)
            return

    # Classify repair candidates
    repair_candidates, manual_only = _classify_repair_candidates(
        issues_by_note, state, today_str, vault
    )

    if not repair_candidates:
        print("No repairable issues (flat daily notes require manual fixes).")
        save_state(state, vault)
        return

    if not fix_frontmatter:
        print(
            f"{len(repair_candidates)} note(s) have repairable issues.\n"
            "Run with --fix-frontmatter to repair them via the configured prompt AI backend."
        )
        save_state(state, vault)
        return

    # Apply repairs in parallel
    repaired, failed, leftover = _apply_repairs_parallel(
        repair_candidates,
        model,
        state,
        today_str,
        jobs,
        timeout,
        note_map,
        fix_headings,
        vault,
        limit,
    )

    save_state(state, vault)
    print(
        f"\nDone: {repaired} repaired, {failed} failed, {leftover} not yet processed."
    )

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
