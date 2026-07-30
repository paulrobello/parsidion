"""SEC-109/110/112/114 permission-repair fix-mode.

Extracted from the original ``vault_doctor.py`` (ARC-008 / QA-003).

Tightens filesystem modes on sensitive vault files (``pending_summaries.jsonl``,
``dead_letters.jsonl``, ``config.yaml``, ``config.local.yaml`` and their
backup variants) and key directories (vault root, ``~/.claude/logs``) so a
shared host cannot leak them.  Closes the SEC gaps for files created before
the tighter creation paths landed.

Stdlib-only.
"""

from __future__ import annotations

import errno
import sys
from pathlib import Path

from doctor._state import _active_vault

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
