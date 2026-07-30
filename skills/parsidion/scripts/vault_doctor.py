#!/usr/bin/env python3
"""vault_doctor.py — Scan vault notes for issues; optionally repair via Claude haiku.

Stdlib-only. Run with:
    uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py
    uv run --no-project ... --fix          # apply Claude-suggested repairs
    uv run --no-project ... --dry-run      # show issues only, no Claude calls
    uv run --no-project ... note.md ...    # scan specific notes only
    uv run --no-project ... --limit 10     # cap repairs at N notes
    uv run --no-project ... --fix --jobs 5 # repair with 5 parallel workers (default: 3)

When repairing BROKEN_WIKILINK issues, the doctor uses a Python-only two-stage
strategy — no Claude call needed:
  1. Exact case-insensitive stem match against the note map.
  2. Semantic fallback via ``vault-search --json --top=2 --min-score=0.5``.
  If a replacement is found the link is updated everywhere in the note; if not,
  the brackets are stripped (text kept in body, entry dropped from ``related``).
  If stripping empties the ``related`` field, the orphan-repair workflow kicks in
  (semantic candidates injected via ``_find_semantic_candidates``).

When repairing ORPHAN_NOTE issues (no [[wikilinks]] in 'related'), the doctor
queries ``vault-search`` semantically — using the note's H1 heading or stem as
the query — and injects the top-5 candidate stems into the Claude prompt.  This
ensures repairs pick real, existing notes rather than hallucinated links.
Degrades gracefully when ``vault-search`` is not installed or ``embeddings.db``
is absent.

Before the first execute-mode mutation of a note in a given run, a copy of the
original is saved to ``<vault>/.trash/backup/<YYYY-MM-DD>/<relative-path>``
(first version of the day wins). ``.trash`` is already excluded from indexing,
so backups never show up in search or the graph. Backups are best-effort and
never block a fix; prune ``.trash/backup/`` freely whenever you like.

# ARC-015: Concurrency model rationale
# vault_doctor.py uses ``concurrent.futures.ThreadPoolExecutor`` because it is
# a stdlib-only script.  ``ThreadPoolExecutor`` is sufficient here: the work is
# I/O-bound (prompt AI helper subprocesses + file reads/writes) and Python's
# GIL does not prevent I/O parallelism.  Adding ``anyio`` or ``asyncio`` would
# require a dependency change that violates the stdlib-only constraint.
#
# summarize_sessions.py uses ``anyio`` + ``anyio.create_task_group`` because it
# already depends on ``claude-agent-sdk`` (which is built on anyio) and benefits
# from structured concurrency guarantees (task groups propagate exceptions
# reliably, unlike ThreadPoolExecutor's ``Future`` cancellation model).
#
# Both approaches are intentional — the choice was driven by dependency
# constraints, not inconsistency.  See ARC-015.
"""

import argparse
import atexit
import concurrent.futures
import errno
import json  # noqa: F401 — used by orchestrator + re-exported for tests
import os
import re  # noqa: F401 — used by run_migrate_daily_notes; re-exported for tests
import shutil  # noqa: F401 — re-exported for test monkeypatch (vault_doctor.shutil.copy2)
import subprocess  # noqa: F401 — re-exported for test monkeypatch (vault_doctor.subprocess.run)
import sys
import threading
from datetime import date, datetime  # noqa: F401 — re-exported for tests
from pathlib import Path

import ai_backend  # noqa: F401 — re-exported for test monkeypatch (vault_doctor.ai_backend.run_ai_prompt)
import vault_common
import vault_fs  # noqa: F401 — re-exported for test monkeypatch
import vault_links  # noqa: F401 — re-exported for test monkeypatch

# ---------------------------------------------------------------------------
# Constants, data model, and shared state live in doctor._state so the
# submodules and this shim share one ``_vault_path`` / ``_backed_up_this_run``
# object.  Re-exported here so existing ``vault_doctor.X`` attribute access
# (including test ``monkeypatch.setattr``) keeps working byte-for-byte.
# See doctor/_state.py for the test-patch compatibility contract.
# ---------------------------------------------------------------------------
from doctor._state import (  # noqa: F401 — re-exports
    AI_TIMEOUT,
    DEFAULT_MODEL,
    PREFIX_CLUSTER_MIN,
    REPAIRABLE_CODES,
    REQUIRED_FIELDS_ALL,
    REQUIRED_FIELDS_KNOWLEDGE,
    SESSION_ID_PATTERN,
    STATE_STALE_DAYS,
    STALE_COMMIT_MINUTES,
    VALID_TYPES,
    Issue,
    _active_vault,
    _backup_note,
    _backed_up_this_run,
    _get_state_file,
    _rel,
    _release_pid,
    _resolve_shim_vault_path,
    _vault_path,
    _write_json_atomic,
    _write_pid,
    is_process_running,
    load_state,
    save_state,
    should_skip,
)


# ---------------------------------------------------------------------------
# Stale file auto-commit
# ---------------------------------------------------------------------------


from doctor.graph import (  # noqa: E402,F401 — re-exports grouped by concern
    _run_reindex,
    commit_stale_files,
)


from doctor.links import (  # noqa: E402,F401 — re-exports grouped by concern
    _auto_repair_broken_wikilinks,
    _find_link_replacement,
    _find_semantic_candidates,
    build_note_map,
    dedup_related_links,
    resolve_wikilink,
)


# ---------------------------------------------------------------------------
# Wikilink resolution
# ---------------------------------------------------------------------------


from doctor.subfolder import (  # noqa: E402,F401 — re-exports grouped by concern
    _filter_clusters_with_claude,
    find_prefix_clusters,
    find_subfolder_candidates,
    fix_prefix_cluster,
    run_migrate_subfolders,
)


# ---------------------------------------------------------------------------
# Note checker
# ---------------------------------------------------------------------------


from doctor.check import (  # noqa: E402,F401 — re-exports grouped by concern
    check_note,
)


# ---------------------------------------------------------------------------
# Claude repair
# ---------------------------------------------------------------------------


from doctor.headings import (  # noqa: E402,F401 — re-exports grouped by concern
    _auto_fix_headings,
    _auto_fix_self_refs,
)


from doctor.frontmatter import (  # noqa: E402,F401 — re-exports grouped by concern
    _FM_DELIM_RE,
    _FM_KEY_RE,
    _FM_LEAKED_MARKER_RE,
    _FM_RELATED_RE,
    _frontmatter_stems,
    _normalize_repaired_note,
    _note_is_daily,
    repair_note,
)


# ---------------------------------------------------------------------------
# AI-output normalization (defence against malformed frontmatter)
# ---------------------------------------------------------------------------

# A bare YAML document delimiter (exactly three dashes, optional trailing space).
# ---------------------------------------------------------------------------


from doctor.worker import (  # noqa: E402,F401 — re-exports grouped by concern
    _repair_one,
)


# ---------------------------------------------------------------------------
# Tag deduplication
# ---------------------------------------------------------------------------

# Regex to find the tags line in frontmatter (inline or block).
# We operate on raw file text to preserve formatting of other fields.
from doctor.tags import (  # noqa: E402,F401 — re-exports grouped by concern
    _TAGS_BLOCK_START_RE,
    _TAGS_INLINE_RE,
    _collect_all_tags,
    _find_session_duplicates,
    _find_tag_duplicates,
    _normalize_underscores_in_frontmatter,
    _replace_tag_in_note,
    _update_graph_json_tags,
    run_fix_sessions,
    run_fix_tags,
)


# ---------------------------------------------------------------------------
# Redundant prefix stripping
# ---------------------------------------------------------------------------


from doctor.prefixes import (  # noqa: E402,F401 — re-exports grouped by concern
    _find_redundant_prefixes,
    run_strip_prefixes,
)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_migrate_daily_notes(
    vault_root: Path, dry_run: bool = True, username: str = ""
) -> None:
    """Rename legacy ``Daily/YYYY-MM/DD.md`` notes to ``DD-{username}.md``.

    The un-namespaced ``DD.md`` format causes git merge conflicts when a team
    shares a vault — multiple users write the same filename on the same day.
    This migration renames existing notes once so future writes use the new
    ``DD-{username}.md`` format.

    After renaming, wikilinks inside rollup notes (``week-NN.md``,
    ``monthly.md``) that reference the old stem are updated automatically.

    Args:
        vault_root: Root path of the vault.
        dry_run: When True, only print candidates — do not rename any files.
        username: Username suffix to append.  Resolved from vault config /
            ``$USER`` environment variable when empty.
    """
    if not username:
        username = vault_common.get_vault_username()

    daily_root = vault_root / "Daily"
    if not daily_root.exists():
        print("No Daily/ directory found — nothing to migrate.")
        return

    # Pattern for un-namespaced day files: exactly two digits, no hyphen suffix
    stem_re = re.compile(r"^\d{2}$")

    candidates: list[tuple[Path, Path]] = []  # (old_path, new_path)

    for month_dir in sorted(daily_root.iterdir()):
        if not month_dir.is_dir():
            continue
        for note in sorted(month_dir.glob("[0-9][0-9].md")):
            if stem_re.match(note.stem):
                new_name = f"{note.stem}-{username}.md"
                new_path = note.parent / new_name
                candidates.append((note, new_path))

    if not candidates:
        print(
            f"No legacy daily notes found to migrate (already using DD-{username}.md format or vault is empty)."
        )
        return

    print(f"Found {len(candidates)} legacy daily note(s) to rename:\n")
    for old, new in candidates:
        old_rel = old.relative_to(vault_root)
        new_rel = new.relative_to(vault_root)
        status = ""
        if new.exists():
            status = "  [SKIP — target already exists]"
        print(f"  {old_rel}  →  {new_rel}{status}")

    if dry_run:
        print(
            f"\n[dry-run] {len(candidates)} note(s) would be renamed. "
            "Run with --execute to apply."
        )
        return

    # --- Execute renames ---
    moved: list[tuple[Path, Path]] = []
    skipped = 0
    for old, new in candidates:
        if new.exists():
            print(f"  Skipped (target exists): {old.relative_to(vault_root)}")
            skipped += 1
            continue
        _backup_note(vault_root, old)
        old.rename(new)
        print(
            f"  Renamed: {old.relative_to(vault_root)}  →  {new.relative_to(vault_root)}"
        )
        moved.append((old, new))

    if not moved:
        print("No files renamed.")
        return

    # --- Update wikilinks in rollup notes ---
    # Rollup notes (week-NN.md, monthly.md) contain [[DD]] wikilinks.
    # Update them to [[DD-username]].
    rollup_pattern = re.compile(r"week-\d+\.md|monthly\.md")
    updated_rollups: list[Path] = []

    for month_dir in sorted(daily_root.iterdir()):
        if not month_dir.is_dir():
            continue
        for rollup in month_dir.iterdir():
            if not rollup_pattern.match(rollup.name):
                continue
            try:
                text = rollup.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            # Match [[DD]] but not [[DD-something]] (avoid double-rename)
            stem_map = {
                old.stem: new.stem for old, new in moved if old.parent == month_dir
            }
            new_text = vault_links.replace_wikilinks_outside_code(text, stem_map)

            if new_text != text:
                _backup_note(vault_root, rollup)
                vault_fs.atomic_write_text(rollup, new_text)
                updated_rollups.append(rollup)
                print(f"  Updated wikilinks: {rollup.relative_to(vault_root)}")

    # --- Commit and rebuild index ---
    all_changed = [new for _, new in moved] + updated_rollups
    vault_common.git_commit_vault(
        f"refactor(vault): migrate {len(moved)} daily note(s) to DD-{username}.md format",
        paths=all_changed,
    )
    print(f"\nMigrated {len(moved)} note(s). Running update_index.py…")
    update_index_script = Path(__file__).parent / "update_index.py"
    try:
        subprocess.run(
            ["uv", "run", "--no-project", str(update_index_script)],
            check=True,
            env=vault_common.env_without_claudecode(),
            timeout=60,
        )
        print("Index rebuilt.")
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        print(f"Warning: update_index.py failed: {exc}", file=sys.stderr)
        print("Run manually: uv run --no-project update_index.py", file=sys.stderr)
    if skipped:
        print(f"Note: {skipped} file(s) skipped because target already existed.")


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
        target_notes = list(vault_common.all_vault_notes(vault))
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
    all_notes = list(vault_common.all_vault_notes(vault))
    note_map = build_note_map(all_notes)

    # ── Prefix cluster detection and fixing ──────────────────────────────────
    clusters = find_prefix_clusters(all_notes, vault)
    if clusters and not dry_run:
        # Filter out generic-word false positives using the configured prompt AI backend
        clusters = _filter_clusters_with_claude(clusters, model=model, timeout=timeout)
    cluster_repaired = 0
    if clusters:
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

        if not dry_run and fix_frontmatter:
            print("Reorganizing prefix clusters…\n")
            for cluster_folder, prefix, cluster_notes, base_note in clusters:
                moves = fix_prefix_cluster(
                    cluster_folder, prefix, cluster_notes, all_notes, base_note
                )
                for old_path, new_path in moves:
                    old_rel = old_path.relative_to(vault)
                    new_rel = new_path.relative_to(vault)
                    print(f"  {old_rel}  →  {new_rel}")
                    cluster_repaired += 1
            if cluster_repaired:
                vault_common.git_commit_vault(
                    f"refactor(vault): reorganize {cluster_repaired} note(s) into prefix subfolders",
                    vault=vault,
                )
                print()
                # Refresh after moves
                all_notes = list(vault_common.all_vault_notes(vault))
                note_map = build_note_map(all_notes)
                all_filtered = [
                    p
                    for p in all_notes
                    if p != vault_claude_md
                    and p != vault_tags_md
                    and p.name != "MANIFEST.md"
                ]
                if not explicit and not no_state:
                    target_notes = [
                        p
                        for p in all_filtered
                        if not should_skip(_rel(p, vault), state)
                    ]
                    skipped_by_state = len(all_filtered) - len(target_notes)
                else:
                    target_notes = all_filtered
                    skipped_by_state = 0

    print(
        f"Scanning {len(target_notes)} vault notes"
        + (f" ({skipped_by_state} skipped — already OK)" if skipped_by_state else "")
        + "…"
    )

    # Scan — also record clean notes in state
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

    if not issues_by_note:
        print("✓ No issues found.")
        if not dry_run:
            save_state(state, vault)
        return

    # Summarise
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

    if dry_run:
        return

    # Classify repair candidates
    repair_candidates = []
    manual_only: list[Path] = []
    for p, iv in issues_by_note.items():
        if any(i.code in REPAIRABLE_CODES for i in iv):
            repair_candidates.append((p, iv))
        else:
            manual_only.append(p)

    # Mark manual-only notes as "skipped" in state
    for p in manual_only:
        key = _rel(p, vault)
        state.setdefault("notes", {})[key] = {
            "status": "skipped",
            "last_checked": today_str,
            "issues": [i.code for i in issues_by_note[p]],
        }

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

    # Apply repairs
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

    save_state(state, vault)
    leftover = len(repair_candidates) - effective_limit
    print(
        f"\nDone: {repaired} repaired, {failed} failed, {leftover} not yet processed."
    )

    # Scan-and-repair is the LAST stage of the --fix-all pipeline; earlier
    # stages reindex only their own changes, so repairs must reindex here too.
    if repaired:
        _run_reindex(vault)


# ---------------------------------------------------------------------------
# SEC-109/110/112/114 migration: tighten permissions on sensitive vault files
# ---------------------------------------------------------------------------

# Files inside the vault that may carry secrets or session-derived PII.
# Chmod'd to 0600 (owner read/write only) by run_fix_permissions so a shared
# vault (rare but supported) cannot leak them to other accounts.
_SECRET_FILES: tuple[str, ...] = (
    "pending_summaries.jsonl",
    "dead_letters.jsonl",
    "config.yaml",
    "config.local.yaml",
)

# Glob patterns matching backup variants of the secret files (atomic-write
# leftovers, manual backups, the rotate-on-size copies vault_fs produces).
_SECRET_FILE_GLOBS: tuple[str, ...] = (
    "pending_summaries.jsonl.bak*",
    "pending_summaries.jsonl.tmp",
    "dead_letters.jsonl.bak*",
    "dead_letters.jsonl.tmp",
)

_FILE_MODE = 0o600
_DIR_MODE = 0o700


def _chmod_if_exists(path: Path, mode: int) -> bool:
    """Chmod *path* to *mode* when it exists. Best-effort; never raises.

    Returns True when the mode was applied, False otherwise (missing file,
    permission error, etc.). Errors are reported once via stderr so an
    unattended ``--fix-all`` run still surfaces them.
    """
    try:
        path.chmod(mode)
        return True
    except OSError as exc:
        # File-not-found is expected — many of the glob targets only exist
        # transiently. Anything else is a real environment problem worth a
        # stderr line.
        if exc.errno != errno.ENOENT:
            print(
                f"  permission repair: could not chmod {path}: {exc}",
                file=sys.stderr,
            )
        return False


def run_fix_permissions(
    vault_path: Path | None = None, *, dry_run: bool = False
) -> int:
    """Tighten permissions on sensitive vault files and key directories.

    Migrates older installs where the files below were created with the
    process umask default (typically 0644 for files / 0755 for dirs), making
    them readable to other accounts on a shared host. The current code paths
    create them at the tighter modes (SEC-109/110/112/114 closed the
    creation gaps); this function repairs pre-existing files to match.

    Targets:
      Files (chmod 0600): ``pending_summaries.jsonl``, ``dead_letters.jsonl``,
        their ``.bak*`` / ``.tmp`` variants, ``config.yaml`` and
        ``config.local.yaml`` (which may carry ANTHROPIC_API_KEY).
      Dirs (chmod 0700): the vault root and ``~/.claude/logs``.

    Args:
        vault_path: Vault root. Defaults to the active vault.
        dry_run: When True, report what would change without chmod'ing.

    Returns:
        Number of files/dirs repaired (0 in dry-run mode even if work exists).
    """
    if vault_path is None:
        vault_path = _active_vault()

    targets: list[tuple[Path, int]] = []

    # Vault secret files + glob variants
    for name in _SECRET_FILES:
        targets.append((vault_path / name, _FILE_MODE))
    for pattern in _SECRET_FILE_GLOBS:
        for match in vault_path.glob(pattern):
            targets.append((match, _FILE_MODE))

    # ~/.claude/logs is created by the hooks (parsidion-hook-errors.log,
    # parsidion-embed.log) and by the embedding-rebuild spawn. Pre-SEC-114
    # installs may have it at 0755.
    logs_dir = Path.home() / ".claude" / "logs"
    targets.append((logs_dir, _DIR_MODE))

    # The vault root itself — pre-SEC-109 installs created it at 0755.
    targets.append((vault_path, _DIR_MODE))

    repaired = 0
    print("\nPermission repair:")
    for target, mode in targets:
        if not target.exists():
            continue
        if dry_run:
            print(f"  would chmod {target} → {oct(mode)[2:]}")
            continue
        if _chmod_if_exists(target, mode):
            print(f"  chmod {target} → {oct(mode)[2:]}")
            repaired += 1
    if dry_run:
        print(f"  (dry-run: 0 of {len(targets)} targets chmod'd)")
    else:
        print(f"  Done: {repaired} path(s) repaired.")
    return repaired


def main() -> None:
    """Parse CLI arguments, acquire the singleton PID lock, and dispatch to the requested repair mode."""
    _backed_up_this_run.clear()  # defensive: fresh dedup set for this run
    parser = argparse.ArgumentParser(
        description="Vault Doctor — find and optionally repair vault note issues",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--fix-sessions",
        action="store_true",
        help=(
            "Detect notes that share the same session_id and suggest consolidation. "
            "Consolidation must be performed manually or via vault-deduplicator agent."
        ),
    )
    parser.add_argument(
        "notes",
        nargs="*",
        type=Path,
        help="Specific notes to check (default: all vault notes)",
    )
    parser.add_argument(
        "--vault",
        "-V",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to vault root (default: VAULT_ROOT env, ~/ParsidionVault, or legacy ~/ClaudeVault if it exists)",
    )
    parser.add_argument(
        "--fix-frontmatter",
        action="store_true",
        help="Apply Claude-suggested frontmatter repairs (writes files)",
    )
    # Legacy alias preserved for backwards compatibility
    parser.add_argument(
        "--fix",
        action="store_true",
        dest="fix_frontmatter",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--fix-all",
        action="store_true",
        help=(
            "Run all fix steps: frontmatter repair, tag dedup, subfolder migration, "
            "and daily note migration. Equivalent to --fix-frontmatter --fix-tags "
            "--migrate-daily-notes --execute."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report issues only; do not call Claude",
    )
    parser.add_argument(
        "--model",
        default=None,
        metavar="MODEL",
        help="AI model for repairs (default: backend-specific small model)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Maximum number of notes to repair (0 = unlimited)",
    )
    parser.add_argument(
        "--errors-only",
        action="store_true",
        help="Only report/repair notes with errors (skip warnings)",
    )
    parser.add_argument(
        "--no-state",
        action="store_true",
        help="Ignore state file and scan all notes regardless of prior results",
    )
    parser.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=3,
        metavar="N",
        help="Number of parallel repair jobs (default: 3)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=AI_TIMEOUT,
        metavar="SECS",
        help=f"Seconds to wait for each Claude repair call (default: {AI_TIMEOUT})",
    )
    parser.add_argument(
        "--migrate-subfolders",
        action="store_true",
        help=(
            "Detect notes that share a common filename prefix (>= 3 per folder) "
            "and show candidates for subfolder migration. "
            "Use --execute to actually move the files."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "With --migrate-subfolders, --fix-tags, or --migrate-daily-notes: apply changes. "
            "Implied by --fix-all."
        ),
    )
    parser.add_argument(
        "--migrate-daily-notes",
        action="store_true",
        help=(
            "Rename legacy Daily/YYYY-MM/DD.md notes to DD-{username}.md format "
            "to prevent git merge conflicts in shared team vaults. "
            "Shows candidates by default; use --execute to apply. "
            "Included in --fix-all."
        ),
    )
    parser.add_argument(
        "--daily-username",
        default="",
        metavar="NAME",
        help=(
            "Username suffix for --migrate-daily-notes "
            "(default: vault config vault.username, then $USER)."
        ),
    )
    parser.add_argument(
        "--fix-tags",
        action="store_true",
        help=(
            "Detect and merge duplicate tags (plural/singular, hyphen/underscore, "
            "collapsed hyphens). Shows candidates by default; use --execute to apply."
        ),
    )
    parser.add_argument(
        "--fix-headings",
        action="store_true",
        default=True,
        help=(
            "Promote first ## heading to # when no # heading exists (enabled by default). "
            "Disable with --no-fix-headings."
        ),
    )
    parser.add_argument(
        "--no-fix-headings",
        action="store_false",
        dest="fix_headings",
        help="Disable heading promotion repair.",
    )
    parser.add_argument(
        "--strip-prefixes",
        action="store_true",
        help=(
            "Strip redundant subfolder prefixes from filenames "
            "(e.g. cctmux/cctmux-overview.md → cctmux/overview.md). "
            "Shows candidates by default; use --execute to apply."
        ),
    )
    parser.add_argument(
        "--fix-permissions",
        action="store_true",
        help=(
            "Tighten permissions on sensitive vault files: chmod 0600 "
            "pending_summaries.jsonl, dead_letters.jsonl, their .bak/.tmp "
            "variants, config.yaml, config.local.yaml; chmod 0700 the vault "
            "root and ~/.claude/logs. Closes SEC-109/110/112/114 for "
            "pre-existing files left at the umask default. Included in "
            "--fix-all."
        ),
    )
    args = parser.parse_args()

    # Resolve vault path
    global _vault_path
    _vault_path = vault_common.resolve_vault(explicit=args.vault, cwd=os.getcwd())
    vault_common.apply_configured_env_defaults(vault=_vault_path)

    # QA-001/QA-003: Restore VAULT_ROOT on exit to prevent cross-contamination
    original_vault_root = vault_common.VAULT_ROOT
    vault_common.VAULT_ROOT = _vault_path
    # ARC-001: clear caches so lru_cache-memoized load_config() and
    # resolve_vault() observe the new VAULT_ROOT instead of stale values.
    vault_common.load_config.cache_clear()  # type: ignore[attr-defined]
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]

    def _restore_vault_root() -> None:
        vault_common.VAULT_ROOT = original_vault_root
        # ARC-001: flush caches on restore so subsequent code sees the original vault.
        vault_common.load_config.cache_clear()  # type: ignore[attr-defined]
        vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]

    atexit.register(_restore_vault_root)

    # Load persistent state
    state = (
        load_state(_vault_path)
        if not args.no_state
        else {"last_run": None, "notes": {}}
    )

    # Singleton guard — only one doctor may run at a time
    existing_pid = state.get("pid")
    if (
        existing_pid
        and existing_pid != os.getpid()
        and is_process_running(existing_pid)
    ):
        print(
            f"vault_doctor is already running (PID {existing_pid}). Exiting.",
            file=sys.stderr,
        )
        sys.exit(1)
    state["pid"] = os.getpid()
    _write_pid(state, _vault_path)  # claim the lock immediately

    def _release_pid_wrapper() -> None:
        """Release the singleton PID lock on process exit via atexit."""
        if _vault_path is not None:
            _release_pid(_vault_path)

    atexit.register(_release_pid_wrapper)  # release on any exit path

    # --fix-all implies all fix flags + execute
    if args.fix_all:
        args.fix_frontmatter = True
        args.fix_tags = True
        args.strip_prefixes = True
        args.migrate_subfolders = True
        args.migrate_daily_notes = True
        args.fix_permissions = True
        args.execute = True

    # ── --fix-tags mode ────────────────────────────────────────────────────
    if args.fix_tags:
        dry = not args.execute
        run_fix_tags(dry_run=dry, vault_path=_vault_path)
        if not args.fix_all:
            return

    # ── --strip-prefixes mode ──────────────────────────────────────────────
    if args.strip_prefixes:
        dry = not args.execute
        run_strip_prefixes(dry_run=dry, vault_path=_vault_path)
        if not args.fix_all:
            return

    # ── --migrate-subfolders mode ──────────────────────────────────────────
    if args.migrate_subfolders:
        dry = not args.execute
        run_migrate_subfolders(
            _vault_path, dry_run=dry, model=args.model, timeout=args.timeout
        )
        if not args.fix_all:
            return

    # ── --migrate-daily-notes mode ─────────────────────────────────────────
    if args.migrate_daily_notes:
        dry = not args.execute
        run_migrate_daily_notes(_vault_path, dry_run=dry, username=args.daily_username)
        if not args.fix_all:
            return

    # ── --fix-permissions mode ─────────────────────────────────────────────
    # SEC-109/110/112/114 migration: chmod sensitive files to 0600, vault
    # root and ~/.claude/logs to 0700. Runs as part of --fix-all (unattended
    # nightly) and standalone via --fix-permissions.
    if args.fix_permissions:
        dry = not args.execute
        run_fix_permissions(_vault_path, dry_run=dry)
        if not args.fix_all:
            return

    run_scan_and_repair(
        _vault_path,
        state,
        notes=list(args.notes),
        dry_run=args.dry_run,
        fix_frontmatter=args.fix_frontmatter,
        fix_sessions=args.fix_sessions,
        errors_only=args.errors_only,
        no_state=args.no_state,
        model=args.model,
        limit=args.limit,
        jobs=args.jobs,
        timeout=args.timeout,
        fix_headings=args.fix_headings,
    )


if __name__ == "__main__":
    main()
