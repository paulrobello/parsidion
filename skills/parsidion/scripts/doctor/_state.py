"""Shared constants, module-level state, and state-file helpers for the doctor package.

All definitions that were module-level in the original ``vault_doctor.py`` and
are referenced from multiple submodules live here so the shim and submodules
share the *same* objects (e.g. ``_backed_up_this_run`` must be one set, not a
copy per submodule).

Test compatibility — two ``monkeypatch`` patterns must keep working:

* ``monkeypatch.setattr(vault_doctor, "_vault_path", tmp_vault)`` — three test
  files do this.  The patch sets an attribute on the *shim's* ``__dict__``.
  ``_active_vault()`` and ``_rel()`` therefore read ``_vault_path`` dynamically
  via ``sys.modules["vault_doctor"].__dict__`` first, then fall back to this
  module's own binding, then to ``vault_common``.
* ``monkeypatch.setattr(vault_doctor.shutil, "copy2", boom)`` (and the
  ``subprocess`` / ``ai_backend`` equivalents) — these mutate the shared
  module objects, so any submodule that does ``import shutil`` sees the patch.

Stdlib-only.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import vault_common
import vault_fs

# ENH-008 Step 2: VALID_TYPES is now an alias over the single source in
# ``note_schema``. Kept under this name so every existing doctor call site and
# the ARC-010 parity test continue to work unchanged.
import note_schema as _note_schema

# ---------------------------------------------------------------------------
# Constants (formerly module-level in vault_doctor.py)
# ---------------------------------------------------------------------------

# Absolute path to skills/parsidion/scripts/ — the sibling-script directory
# the original vault_doctor.py reached via ``Path(__file__).parent``.  From
# any submodule under doctor/ that resolves to ``doctor/`` * `.parent`, so
# expose it once here instead of recomputing per call site.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent

VALID_TYPES = _note_schema.VALID_NOTE_TYPES
# Fields required for all notes
REQUIRED_FIELDS_ALL = ("date", "type")
# Additional fields required for knowledge notes (not daily)
REQUIRED_FIELDS_KNOWLEDGE = ("confidence", "related", "tags")
REPAIRABLE_CODES = frozenset(
    {
        "MISSING_FRONTMATTER",
        "MISSING_FIELD",
        "INVALID_TYPE",
        "INVALID_DATE",
        "ORPHAN_NOTE",
        "BROKEN_WIKILINK",
        "HEADING_MISMATCH",
        "SELF_REF",
    }
)
# Malformed-frontmatter shapes the scanner reports but deliberately does not
# hand to the AI repair path. They must keep re-reporting until a human fixes
# them: a note whose only issues are these lands in `manual_only`, and
# `should_skip` treats the resulting "skipped" status as permanent (unlike
# "ok", which expires after STATE_STALE_DAYS). Without an exemption the defect
# would be announced once and then stay invisible forever.
DETECTION_ONLY_CODES = frozenset(
    {
        "NESTED_FM_KEY",
        "UNTERMINATED_FM_LIST",
        "ORPHAN_FM_BRACKET",
        "SCALAR_LIST_FIELD",
        "STRAY_FM_LIST_ITEM",
        "DUPLICATE_FM_KEY",
    }
)
DEFAULT_MODEL: str | None = None
AI_TIMEOUT = 120  # seconds
STATE_STALE_DAYS = 7  # re-check "ok" notes after this many days
STALE_COMMIT_MINUTES = 15
SESSION_ID_PATTERN = re.compile(
    r"^[0-9a-f]{16}$"
)  # auto-commit uncommitted files older than this
PREFIX_CLUSTER_MIN = (
    3  # minimum flat notes sharing a prefix to trigger subfolder grouping
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Issue:
    """One doctor finding: a detected defect on a note, pre-repair."""

    path: Path
    severity: str  # "error" | "warning"
    code: str
    message: str
    # ENH-015: kebab-case slug of the rule that produced this issue (set by
    # check_note); drives the per-rule found/fixed/skipped report. Empty for
    # infrastructure errors with no owning rule (READ_ERROR).
    rule: str = ""


# ---------------------------------------------------------------------------
# Module-level state (single source of truth for the package)
# ---------------------------------------------------------------------------
#
# Schema for doctor_state.json:
# {
#   "last_run": "2026-03-13T14:30:00",
#   "notes": {
#     "Research/foo.md": {
#       "status": "ok" | "fixed" | "failed" | "timeout" | "skipped",
#       "last_checked": "YYYY-MM-DD",
#       "issues": ["CODE", ...]     # issue codes found (empty = clean)
#     }
#   }
# }
# SEC-016: the singleton "pid" field is gone — doctor exclusion now uses the
# flock on <vault>/.doctor.lock (see doctor/cli.py), released by the kernel on
# process death. A stale "pid" key in an old state file is inert residue.
# "ok"           — no issues found; skip for STATE_STALE_DAYS before re-checking
# "fixed"        — prompt AI repaired it; re-check next run to confirm
# "failed"       — prompt AI returned no output; retry next run
# "timeout"      — prompt AI timed out once; retry ONE more time
# "needs_review" — timed out on retry; skip and flag for user intervention
# "skipped"      — only non-repairable issues; skip indefinitely (manual fix needed)

# Module-level vault path, set by main() after argument parsing.
# Tests patch this via monkeypatch.setattr(vault_doctor, "_vault_path", ...)
# so this name MUST remain importable from the vault_doctor shim.
_vault_path: Path | None = None


def _resolve_shim_vault_path() -> Path | None:
    """Return ``_vault_path`` as seen on the ``vault_doctor`` shim, if set there.

    Tests do ``monkeypatch.setattr(vault_doctor, "_vault_path", ...)`` which
    writes to ``vault_doctor.__dict__`` directly.  That binding is *separate*
    from this module's ``_vault_path`` global, so ``_active_vault()`` and
    ``_rel()`` consult the shim first to honour the patch.
    """
    shim = sys.modules.get("vault_doctor")
    if shim is None:
        return None
    vp = shim.__dict__.get("_vault_path")
    if vp is None:
        return None
    return vp  # type: ignore[no-any-return]


def _active_vault() -> Path:
    """Return the currently active vault root.

    Single resolution point: prefers the module-level ``_vault_path`` set by
    ``main()`` (or by tests via monkeypatch on the vault_doctor shim), falling
    back to ``vault_common.VAULT_ROOT``.  Replaces the repeated inline ternary
    ``_vault_path if _vault_path else vault_common.VAULT_ROOT``.
    """
    shim_vp = _resolve_shim_vault_path()
    if shim_vp is not None:
        return shim_vp
    if _vault_path is not None:
        return _vault_path
    return vault_common.VAULT_ROOT  # type: ignore[no-any-return]


def _get_state_file(vault_path: Path) -> Path:
    """Return the state file path for the given vault."""
    return vault_path / "doctor_state.json"


def _rel(path: Path, vault_path: Path | None = None) -> str:
    """Return path relative to vault root as a string key.

    Args:
        path: Absolute note path.
        vault_path: Explicit vault root. Falls back to the shim's
            ``_vault_path`` (so test monkeypatches apply), then this module's
            ``_vault_path``, then ``vault_common.resolve_vault()``.
    """
    if vault_path is not None:
        return str(path.relative_to(vault_path))
    shim_vp = _resolve_shim_vault_path()
    if shim_vp is not None:
        return str(path.relative_to(shim_vp))
    if _vault_path is not None:
        return str(path.relative_to(_vault_path))
    return str(path.relative_to(vault_common.resolve_vault()))


def load_state(vault_path: Path) -> dict:
    """Load doctor_state.json, returning empty structure if missing/corrupt."""
    try:
        return json.loads(_get_state_file(vault_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"last_run": None, "notes": {}}


def _write_json_atomic(data: dict, dest: Path) -> None:
    """Write *data* as JSON to *dest* atomically via a sibling .tmp file."""
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(dest)


def save_state(state: dict, vault_path: Path) -> None:
    """Write doctor_state.json atomically."""
    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    _write_json_atomic(state, _get_state_file(vault_path))


def should_skip(key: str, state: dict) -> bool:
    """Return True if this note should be skipped based on its state entry."""
    entry = state.get("notes", {}).get(key)
    if not entry:
        return False
    status = entry.get("status", "")
    if status in ("skipped", "needs_review"):
        return True
    if status == "ok":
        last = entry.get("last_checked", "")
        try:
            checked = date.fromisoformat(last)
            return (date.today() - checked).days < STATE_STALE_DAYS
        except ValueError:
            return False
    return False  # "fixed", "failed", "timeout" — always retry


# QA-007: is_process_running moved to vault_common.py (canonical implementation).
# Local alias preserves all existing call sites unchanged.
is_process_running = vault_common.is_process_running


# ---------------------------------------------------------------------------
# Pre-mutation backups
# ---------------------------------------------------------------------------
# Every execute-mode content mutation and rename below is preceded by a call
# to _backup_note() so an operator can recover the pre-fix version of a note
# from an unattended --fix-all run. ".trash" is already in
# vault_common.EXCLUDE_DIRS so backups are invisible to search/indexing.
#
# QA-001: the implementation now lives in vault_fs.backup_note (canonical
# signature ``(note_path, vault)``). This wrapper keeps doctor's existing
# ``(vault, note_path)`` call signature so every submodule call site stays
# unchanged, plus doctor's "never raise" contract (the shared helper raises
# OSError on copy failure) and the per-run dedup set that lets a long
# --fix-all run skip re-stat'ing a note it has already backed up.

_backed_up_this_run: set[Path] = set()


def _backup_note(vault: Path, note_path: Path) -> None:
    """Copy *note_path* to today's pre-mutation backup dir, best-effort.

    Doctor's "never raise" wrapper around :func:`vault_fs.backup_note`.
    No-ops if this note was already backed up during the current process
    (tracked in ``_backed_up_this_run`` to avoid re-stat'ing) or if a backup
    for today already exists on disk (first version of the day wins).
    A backup failure warns on stderr but must not block the fix itself,
    since this runs unattended nightly via cron.
    """
    if note_path in _backed_up_this_run:
        return
    _backed_up_this_run.add(note_path)
    try:
        vault_fs.backup_note(note_path, vault)
    except OSError as exc:
        try:
            rel = note_path.relative_to(vault)
        except ValueError:
            rel = note_path
        print(f"  ⚠ backup failed for {rel}: {exc}", file=sys.stderr)
