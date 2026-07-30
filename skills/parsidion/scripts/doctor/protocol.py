"""Registry shape for vault-wide fix-modes (``--fix-tags``, ``--strip-prefixes`` etc.).

ARC-008 / QA-003: reducing the ``--fix-all`` dispatch in ``main()`` from a
55-branch ``if args.X: …`` ladder to a loop over a ``(flag, runner)`` tuple so
the mode list is data.  Each ``FixMode`` binds a CLI ``--flag`` to the runner
that performs the fix; ``run_fix_modes`` iterates them so adding a new mode
is one tuple append instead of another branch in two places (``--fix-all``
implication + the dispatch ladder).

Stdlib-only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

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
