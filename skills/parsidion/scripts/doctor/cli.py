"""CLI entry point for ``vault_doctor`` (``main()``).

Extracted from the original ``vault_doctor.py`` (ARC-008 / QA-003).

The ``--fix-all`` mode dispatch is now data-driven via the ``FIX_MODES``
registry in ``doctor.protocol``: adding a new ``--fix-X`` mode is one tuple
in the registry plus its argparse declaration, instead of a new ``if`` block
that has to be remembered in *two* places (the ``--fix-all`` implication and
the dispatch ladder).

Stdlib-only.
"""

from __future__ import annotations

import argparse
import atexit
import os
import sys
from pathlib import Path

import vault_common
import vault_fs

from doctor._state import (
    AI_TIMEOUT,
    _backed_up_this_run,
    load_state,
)
from doctor.daily import run_migrate_daily_notes
from doctor.orchestrator import run_scan_and_repair
from doctor.permissions import run_fix_permissions
from doctor.prefixes import run_strip_prefixes
from doctor.protocol import (
    RULE_NAMES,
    RULE_SPECS,
    DoctorOptions,
    FixMode,
    deselected_rules,
    run_fix_modes,
    rule_enabled,
    select_rules,
)
from doctor.subfolder import run_migrate_subfolders
from doctor.tags import run_fix_tags


def _valid_daily_username(value: str) -> str:
    """SEC-010: reject usernames that could escape the Daily/YYYY-MM dir."""
    if value and not vault_fs.is_valid_vault_username(value):
        raise argparse.ArgumentTypeError(
            f"invalid --daily-username {value!r}: allowed characters are "
            "letters, digits, '.', '_', '-' (max 64 chars)"
        )
    return value


def _build_fix_modes(args: argparse.Namespace) -> tuple[FixMode, ...]:
    """Build the per-invocation fix-mode registry.

    Modes whose runner needs CLI-only options (``--model``, ``--timeout``,
    ``--daily-username``) close over them here, so the registry shape stays
    ``(flag, (vault, dry_run) -> None, label)`` and ``run_fix_modes`` can
    iterate it without knowing each mode's argument shape.
    """

    def _run_tags(vault: Path, dry: bool) -> None:
        run_fix_tags(dry_run=dry, vault_path=vault)

    def _run_prefixes(vault: Path, dry: bool) -> None:
        run_strip_prefixes(dry_run=dry, vault_path=vault)

    def _run_subfolders(vault: Path, dry: bool) -> None:
        run_migrate_subfolders(
            vault, dry_run=dry, model=args.model, timeout=args.timeout
        )

    def _run_daily(vault: Path, dry: bool) -> None:
        run_migrate_daily_notes(vault, dry_run=dry, username=args.daily_username)

    def _run_permissions(vault: Path, dry: bool) -> None:
        run_fix_permissions(vault, dry_run=dry)

    return (
        FixMode("fix_tags", _run_tags, "tag dedup"),
        FixMode("strip_prefixes", _run_prefixes, "redundant-prefix strip"),
        FixMode("migrate_subfolders", _run_subfolders, "subfolder migration"),
        FixMode("migrate_daily_notes", _run_daily, "daily-note migration"),
        FixMode("fix_permissions", _run_permissions, "permission repair"),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Vault Doctor — find and optionally repair vault note issues",
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
        type=_valid_daily_username,
        help=(
            "Username suffix for --migrate-daily-notes "
            "(default: vault config vault.username, then $USER). "
            "Must be letters, digits, '.', '_', '-' (max 64 chars)."
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
    # ENH-015: per-rule selection. --only/--skip name rules from the
    # RULE_SPECS catalog (see --list-rules) and gate both the fix-mode
    # dispatch and the scan-and-repair checks, so a bulk --fix-all can
    # exclude the historically risky rules without giving up the safe ones.
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--only",
        action="append",
        metavar="RULE",
        choices=RULE_NAMES,
        help=(
            "Run only the named rule(s) (repeatable). "
            "See --list-rules for the rule names."
        ),
    )
    selection.add_argument(
        "--skip",
        action="append",
        metavar="RULE",
        choices=RULE_NAMES,
        help=(
            "Run everything except the named rule(s) (repeatable). "
            "Safe bulk invocation: --fix-all --skip strip-prefixes "
            "--skip subfolder-prefix."
        ),
    )
    parser.add_argument(
        "--list-rules",
        action="store_true",
        help="Print the selectable rule catalog (name, kind, risk, description) and exit.",
    )
    return parser


def _print_rule_catalog() -> None:
    """Print the RULE_SPECS catalog with a risk column (ENH-015)."""
    width = max(len(spec.name) for spec in RULE_SPECS)
    print(f"{'rule':<{width}} | kind   | risk | description")
    print(f"{'-' * width}-+--------+------+-------------")
    for spec in RULE_SPECS:
        print(
            f"{spec.name:<{width}} | {spec.kind:<6} | {spec.risk:<4} | "
            f"{spec.description}"
        )
    print("\nSelect with --only RULE / --skip RULE (repeatable, mutually exclusive).")


def main() -> None:
    """Parse CLI arguments, acquire the singleton PID lock, and dispatch to the requested repair mode."""
    _backed_up_this_run.clear()  # defensive: fresh dedup set for this run
    parser = _build_parser()
    args = parser.parse_args()

    # ENH-015: --list-rules needs no vault, lock, or state — answer and exit.
    if args.list_rules:
        _print_rule_catalog()
        return

    # Resolve vault path.  Mutates the module-level _vault_path in doctor._state
    # (re-exported here) so submodules that consult it via _active_vault() see
    # the resolved value, and so tests that patch vault_doctor._vault_path
    # observe a single source of truth.
    import doctor._state as _state

    _state._vault_path = vault_common.resolve_vault(
        explicit=args.vault, cwd=os.getcwd()
    )
    # Mirror onto this shim too: main()'s callers read vault_doctor._vault_path.
    globals()["_vault_path"] = _state._vault_path
    vault_common.apply_configured_env_defaults(vault=_state._vault_path)

    # QA-001/QA-003: Restore VAULT_ROOT on exit to prevent cross-contamination
    original_vault_root = vault_common.VAULT_ROOT
    vault_common.VAULT_ROOT = _state._vault_path
    # ARC-001: clear caches so lru_cache-memoized load_config() and
    # resolve_vault() observe the new VAULT_ROOT instead of stale values.
    vault_common.clear_config_cache()
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]

    def _restore_vault_root() -> None:
        vault_common.VAULT_ROOT = original_vault_root
        # ARC-001: flush caches on restore so subsequent code sees the original vault.
        vault_common.clear_config_cache()
        vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]

    atexit.register(_restore_vault_root)

    # Load persistent state
    state = (
        load_state(_state._vault_path)
        if not args.no_state
        else {"last_run": None, "notes": {}}
    )

    # Singleton guard — only one doctor may run at a time.
    # SEC-016: the old PID-JSON read-check-write was unlocked (two doctors
    # could both pass the check before either wrote its pid), and
    # is_process_running's True-on-PermissionError let a stale `pid: 1`
    # block doctor runs forever. flock is released by the kernel when the
    # holder dies, so there is no stale-PID state to recover from at all.
    doctor_lock_fd = vault_fs.try_singleton_lock(_state._vault_path / ".doctor.lock")
    if doctor_lock_fd is None:
        print("vault_doctor is already running. Exiting.", file=sys.stderr)
        sys.exit(1)
    atexit.register(vault_fs.release_singleton_lock, doctor_lock_fd)

    # --fix-all implies every fix-mode flag + execute.  Adding a new mode is
    # one line here + its argparse declaration + its entry in _build_fix_modes;
    # the dispatch loop in run_fix_modes picks it up automatically.
    if args.fix_all:
        args.fix_frontmatter = True
        args.fix_tags = True
        args.strip_prefixes = True
        args.migrate_subfolders = True
        args.migrate_daily_notes = True
        args.fix_permissions = True
        args.execute = True

    # Per-mode dispatch via the registry.  Standalone modes (selected without
    # --fix-all) run once and skip scan-and-repair; --fix-all runs every
    # selected mode in sequence and then falls through to scan-and-repair.
    # ENH-015: --only/--skip filter the registry by rule name, and deselecting
    # frontmatter-repair switches off the AI repair stage below.
    enabled = select_rules(args.only, args.skip)
    modes = _build_fix_modes(args)
    if enabled is not None:
        rule_for_flag = {
            spec.target: spec.name for spec in RULE_SPECS if spec.kind == "mode"
        }
        modes = tuple(m for m in modes if rule_enabled(enabled, rule_for_flag[m.flag]))
    standalone_ran = run_fix_modes(modes, args, _state._vault_path)
    if standalone_ran:
        # Standalone-mode runs never reach scan-and-repair, so the
        # end-of-run report (and its deselected line) is printed here.
        deselected = deselected_rules(enabled)
        if deselected:
            print(f"\nDeselected by --only/--skip: {', '.join(deselected)}")
        return

    run_scan_and_repair(
        _state._vault_path,
        state,
        notes=list(args.notes),
        options=DoctorOptions(
            dry_run=args.dry_run,
            fix_frontmatter=(
                args.fix_frontmatter and rule_enabled(enabled, "frontmatter-repair")
            ),
            fix_sessions=args.fix_sessions,
            errors_only=args.errors_only,
            no_state=args.no_state,
            model=args.model,
            limit=args.limit,
            jobs=args.jobs,
            timeout=args.timeout,
            fix_headings=args.fix_headings,
            enabled_rules=enabled,
        ),
    )
