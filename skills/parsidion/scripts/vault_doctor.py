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
import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import ai_backend
import vault_common

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_TYPES = frozenset(
    {
        "pattern",
        "debugging",
        "research",
        "project",
        "daily",
        "tool",
        "language",
        "framework",
        "knowledge",
    }
)
# Fields required for all notes
REQUIRED_FIELDS_ALL = ("date", "type")
# Additional fields required for knowledge notes (not daily)
REQUIRED_FIELDS_KNOWLEDGE = ("confidence", "related")
REPAIRABLE_CODES = frozenset(
    {
        "MISSING_FRONTMATTER",
        "MISSING_FIELD",
        "INVALID_TYPE",
        "INVALID_DATE",
        "ORPHAN_NOTE",
        "BROKEN_WIKILINK",
        "HEADING_MISMATCH",
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
    path: Path
    severity: str  # "error" | "warning"
    code: str
    message: str


# ---------------------------------------------------------------------------
# State file  (<resolved vault>/doctor_state.json)
# ---------------------------------------------------------------------------
# Schema:
# {
#   "last_run": "2026-03-13T14:30:00",
#   "pid": 12345,                    # PID of the currently-running doctor (if any)
#   "notes": {
#     "Research/foo.md": {
#       "status": "ok" | "fixed" | "failed" | "timeout" | "skipped",
#       "last_checked": "YYYY-MM-DD",
#       "issues": ["CODE", ...]     # issue codes found (empty = clean)
#     }
#   }
# }
# "ok"           — no issues found; skip for STATE_STALE_DAYS before re-checking
# "fixed"        — prompt AI repaired it; re-check next run to confirm
# "failed"       — prompt AI returned no output; retry next run
# "timeout"      — prompt AI timed out once; retry ONE more time
# "needs_review" — timed out on retry; skip and flag for user intervention
# "skipped"      — only non-repairable issues; skip indefinitely (manual fix needed)
# ---------------------------------------------------------------------------

# Module-level vault path, set by main() after argument parsing.
# QA-003: _rel() falls back gracefully instead of raising RuntimeError
# when _vault_path is None.
_vault_path: Path | None = None


def _get_state_file(vault_path: Path) -> Path:
    """Return the state file path for the given vault."""
    return vault_path / "doctor_state.json"


def _rel(path: Path, vault_path: Path | None = None) -> str:
    """Return path relative to vault root as a string key.

    Args:
        path: Absolute note path.
        vault_path: Explicit vault root. Falls back to module-level
            ``_vault_path``, then to ``vault_common.resolve_vault()``.
    """
    vp = vault_path or _vault_path or vault_common.resolve_vault()
    return str(path.relative_to(vp))


def load_state(vault_path: Path) -> dict:
    """Load doctor_state.json, returning empty structure if missing/corrupt."""
    try:
        return json.loads(_get_state_file(vault_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"last_run": None, "notes": {}}


def save_state(state: dict, vault_path: Path) -> None:
    """Write doctor_state.json atomically."""
    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    state_file = _get_state_file(vault_path)
    tmp = state_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(state_file)


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


def _write_pid(state: dict, vault_path: Path) -> None:
    """Write *state* (including pid) to the state file immediately."""
    state_file = _get_state_file(vault_path)
    tmp = state_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(state_file)


def _release_pid(vault_path: Path) -> None:
    """Clear our pid from the state file at process exit."""
    try:
        current = load_state(vault_path)
        if current.get("pid") == os.getpid():
            current.pop("pid", None)
            state_file = _get_state_file(vault_path)
            tmp = state_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
            tmp.replace(state_file)
    except Exception:  # noqa: BLE001
        pass  # best-effort cleanup


# ---------------------------------------------------------------------------
# Stale file auto-commit
# ---------------------------------------------------------------------------


def commit_stale_files(
    dry_run: bool = False, vault_path: Path | None = None
) -> list[Path]:
    """Stage and commit uncommitted vault files whose mtime is older than STALE_COMMIT_MINUTES.

    Skips deleted files (no mtime to check) and respects the git.auto_commit
    config flag.  Returns the list of paths that were (or would be) committed.
    Does nothing when the vault has no .git directory.
    """
    if vault_path is None:
        vault_path = _vault_path if _vault_path else vault_common.VAULT_ROOT
    git_marker = vault_path / ".git"
    if not (git_marker.is_dir() or git_marker.is_file()):
        return []

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-u"],
            cwd=str(vault_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0 or not result.stdout.strip():
        return []

    cutoff = datetime.now().timestamp() - STALE_COMMIT_MINUTES * 60
    stale: list[Path] = []

    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        xy = line[:2]
        # Skip deletions — no file on disk to check mtime
        if "D" in xy:
            continue
        filepath_part = line[3:]
        # Handle renames: "old -> new"
        if " -> " in filepath_part:
            filepath_part = filepath_part.split(" -> ", 1)[1]
        path = vault_path / filepath_part.strip()
        try:
            if path.stat().st_mtime <= cutoff:
                stale.append(path)
        except OSError:
            continue

    if not stale:
        return []

    if dry_run:
        return stale

    committed = vault_common.git_commit_vault(
        f"chore(vault): auto-commit {len(stale)} stale file(s) via vault_doctor",
        paths=stale,
        vault=vault_path,
    )
    return stale if committed else []


def dedup_related_links(dry_run: bool = False, vault_path: Path | None = None) -> int:
    """Remove duplicate wikilinks from the ``related`` frontmatter field.

    Scans all vault notes and rewrites any ``related:`` line that contains
    duplicate entries.  Returns the number of notes fixed.
    """
    if vault_path is None:
        vault_path = _vault_path if _vault_path else vault_common.VAULT_ROOT
    fixed = 0
    related_re = re.compile(r"^(related:\s*)(\[.*?\])\s*$", re.MULTILINE)
    entry_re = re.compile(r'"(\[\[[^\]]+\]\])"')

    for note_path in vault_common.all_vault_notes(vault_path):
        try:
            content = note_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        m = related_re.search(content)
        if not m:
            continue
        prefix = m.group(1)
        entries = entry_re.findall(m.group(2))
        deduped = list(dict.fromkeys(entries))
        if len(deduped) == len(entries):
            continue
        if dry_run:
            dropped = len(entries) - len(deduped)
            rel = note_path.relative_to(vault_path)
            print(f"  {rel}: {dropped} duplicate(s)")
            fixed += 1
            continue
        quoted = ", ".join(f'"{e}"' for e in deduped)
        new_line = f"{prefix}[{quoted}]"
        updated = related_re.sub(new_line, content, count=1)
        try:
            note_path.write_text(updated, encoding="utf-8")
            fixed += 1
        except OSError:
            pass

    if fixed and not dry_run:
        vault_common.git_commit_vault(
            f"chore(vault): deduplicate related links in {fixed} note(s)",
            vault=vault_path,
        )
    return fixed


# ---------------------------------------------------------------------------
# Wikilink resolution
# ---------------------------------------------------------------------------


def build_note_map(notes: list[Path]) -> dict[str, list[Path]]:
    """Return stem (lowercase) → [paths] for all vault notes."""
    note_map: dict[str, list[Path]] = {}
    for p in notes:
        note_map.setdefault(p.stem.lower(), []).append(p)
    return note_map


def find_prefix_clusters(
    all_notes: list[Path],
    vault_path: Path,
) -> list[tuple[Path, str, list[Path], Path | None]]:
    """Find groups of flat notes that should be reorganised into a subfolder.

    Two cluster types are detected:

    **Exact-stem** (base_note is not None):
        One note's stem is the exact prefix of 2+ sibling notes separated by ``-``.
        Example: ``gpu-voxel-ray-marching-optimizations``,
                 ``gpu-voxel-ray-marching-optimizations-0853``,
                 ``gpu-voxel-ray-marching-optimizations-0930``
        → subfolder ``gpu-voxel-ray-marching-optimizations/``, base note keeps its
          filename (wikilinks stay valid), variants drop the full base-stem prefix.
        These clusters bypass Claude filtering (relationship is unambiguous).

    **First-word** (base_note is None):
        3+ notes share the same first ``-``-delimited word and that word represents
        a specific named subject (project, library, OS …).  Generic words are filtered
        out by ``_filter_clusters_with_claude`` before fixes are applied.

    Returns list of ``(folder, prefix, notes, base_note | None)``.
    Only examines notes at depth-2 relative to vault root (e.g. Patterns/foo.md).
    Skips Daily/, MANIFEST.md, and cases where the subfolder already exists.
    """
    by_folder: dict[Path, list[Path]] = {}
    for note in all_notes:
        rel = note.relative_to(vault_path)
        parts = rel.parts
        if len(parts) != 2:
            continue
        if parts[0] == "Daily":
            continue
        if parts[1] in ("MANIFEST.md", "CLAUDE.md"):
            continue
        by_folder.setdefault(note.parent, []).append(note)

    clusters: list[tuple[Path, str, list[Path], Path | None]] = []
    for folder, folder_notes in sorted(by_folder.items()):
        already_claimed: set[Path] = set()

        # Pass 1 — exact-stem clusters (unambiguous; bypass Claude filter)
        for base in sorted(folder_notes, key=lambda p: len(p.stem), reverse=True):
            if base in already_claimed:
                continue
            variants = [
                n
                for n in folder_notes
                if n is not base and n.stem.startswith(f"{base.stem}-")
            ]
            if len(variants) < 2:
                continue
            subfolder = folder / base.stem
            if subfolder.exists():
                continue
            all_in_cluster = [base, *variants]
            clusters.append((folder, base.stem, all_in_cluster, base))
            already_claimed.update(all_in_cluster)

        # Pass 2 — first-word clusters (filtered by Claude)
        by_prefix: dict[str, list[Path]] = {}
        for note in folder_notes:
            if note in already_claimed:
                continue
            stem_parts = note.stem.split("-")
            if len(stem_parts) < 2:
                continue
            by_prefix.setdefault(stem_parts[0], []).append(note)

        for prefix, cluster_notes in sorted(by_prefix.items()):
            if len(cluster_notes) < PREFIX_CLUSTER_MIN:
                continue
            if (folder / prefix).exists():
                continue
            clusters.append((folder, prefix, cluster_notes, None))

    return clusters


def _filter_clusters_with_claude(
    clusters: list[tuple[Path, str, list[Path], Path | None]],
    model: str | None = DEFAULT_MODEL,
    timeout: int = AI_TIMEOUT,
) -> list[tuple[Path, str, list[Path], Path | None]]:
    """Use prompt AI to discard first-word clusters whose prefix is a generic English word.

    Exact-stem clusters (base_note is not None) are always kept — the relationship
    is unambiguous.  Only first-word clusters (base_note is None) are evaluated.

    A "meaningful" first-word prefix is a specific project/library/tool/OS name
    (e.g. 'parvitar', 'redis', 'obsidian').  Generic verbs, adjectives, and modifiers
    (e.g. 'fixing', 'missing', 'multi', 'cross') are rejected.  Falls back to keeping
    all clusters on any error so the caller is never silently blocked.
    """
    if not clusters:
        return clusters

    # Exact-stem clusters pass through unconditionally
    exact_stem: list[tuple[Path, str, list[Path], Path | None]] = [
        (f, p, n, b) for f, p, n, b in clusters if b is not None
    ]
    first_word: list[tuple[Path, str, list[Path], Path | None]] = [
        (f, p, n, b) for f, p, n, b in clusters if b is None
    ]

    if not first_word:
        return clusters

    lines = []
    for folder, prefix, notes, _ in first_word:
        stems = ", ".join(n.stem for n in sorted(notes))
        lines.append(f"  prefix='{prefix}' folder='{folder.name}' stems=[{stems}]")

    prompt = (
        "You are a vault organizer. Below are candidate prefix clusters found in a\n"
        "knowledge vault. Each cluster groups notes that share the same first word\n"
        "in their kebab-case filename.\n\n"
        "Decide which clusters represent a SPECIFIC subject worth its own subfolder:\n"
        "- KEEP: project names, library names, tool names, OS names, technology names\n"
        "  (e.g. 'parvitar', 'redis', 'obsidian', 'cctmux', 'macos', 'gitnexus')\n"
        "- REJECT: generic English words that are unrelated verbs, adjectives, or\n"
        "  modifiers that happen to share a prefix\n"
        "  (e.g. 'fixing', 'missing', 'multi', 'cross', 'harness', 'build')\n\n"
        "Candidates:\n"
        + "\n".join(lines)
        + "\n\nReturn ONLY a JSON array of prefix strings to KEEP — no explanation.\n"
        'Example: ["parvitar", "redis", "obsidian"]'
    )

    try:
        output = ai_backend.run_ai_prompt(
            prompt,
            model=model,
            model_tier="small",
            timeout=timeout,
            purpose="vault-doctor",
        )
        if not output:
            return clusters  # fallback

        m = re.search(r"\[.*?\]", output.strip(), re.DOTALL)
        if not m:
            return clusters

        accepted: set[str] = set(json.loads(m.group(0)))
        kept_first_word: list[tuple[Path, str, list[Path], Path | None]] = [
            (f, p, n, b) for f, p, n, b in first_word if p in accepted
        ]
        result_clusters: list[tuple[Path, str, list[Path], Path | None]] = (
            exact_stem + kept_first_word
        )
        return result_clusters

    except (json.JSONDecodeError, ValueError):
        return clusters  # fallback: keep all


def fix_prefix_cluster(
    folder: Path,
    prefix: str,
    cluster_notes: list[Path],
    all_notes: list[Path],
    base_note: Path | None = None,
) -> list[tuple[Path, Path]]:
    """Move *cluster_notes* into *folder*/*prefix*/ and patch wikilinks vault-wide.

    Returns list of (old_path, new_path) moves performed.

    For **first-word clusters** (base_note is None): notes whose stem starts with
    ``prefix-`` are moved and the prefix is stripped from their filename.

    For **exact-stem clusters** (base_note is the note whose stem == prefix):
    - The base note is moved into the subfolder with its **original filename**
      (stem unchanged → existing ``[[wikilinks]]`` keep resolving).
    - Variant notes have the full ``prefix-`` stripped from their stem.
    """
    subfolder = folder / prefix
    moves: list[tuple[Path, Path]] = []

    for note in cluster_notes:
        if note is base_note:
            # Exact-stem base: keep same filename, just relocate into subfolder
            moves.append((note, subfolder / note.name))
        elif note.stem.startswith(f"{prefix}-"):
            new_stem = note.stem[len(prefix) + 1 :]
            if new_stem:
                moves.append((note, subfolder / f"{new_stem}.md"))
        # else: skip notes that don't match the expected pattern

    if not moves:
        return []

    subfolder.mkdir(parents=True, exist_ok=True)

    # Only variant notes (not the base) change their stem — only those need patching
    stem_map: dict[str, str] = {
        old.stem: new.stem
        for old, new in moves
        if old is not base_note and old.stem != new.stem
    }
    old_paths = {old for old, _ in moves}

    # Move files first (skip missing files gracefully)
    failed_moves: list[tuple[Path, Path]] = []
    for old_path, new_path in moves:
        try:
            old_path.rename(new_path)
        except FileNotFoundError:
            print(
                f"  ⚠ skipped (not found): {old_path.relative_to(_vault_path if _vault_path else vault_common.VAULT_ROOT)}"
            )
            failed_moves.append((old_path, new_path))
    # Remove failed moves so wikilink patching doesn't reference nonexistent files
    if failed_moves:
        failed_set = set(failed_moves)
        moves = [m for m in moves if m not in failed_set]

    def _patch(path: Path) -> None:
        """Rewrite wikilinks in *path* according to *stem_map* renames."""
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return
        original = content
        for old_stem, new_stem in stem_map.items():
            content = content.replace(f"[[{old_stem}]]", f"[[{new_stem}]]")
            content = re.sub(
                rf"\[\[{re.escape(old_stem)}\|",
                f"[[{new_stem}|",
                content,
            )
        if content != original:
            path.write_text(content, encoding="utf-8")

    for note in all_notes:
        if note not in old_paths:
            _patch(note)
    for _, new_path in moves:
        _patch(new_path)

    return moves


def find_subfolder_candidates(
    vault_root: Path,
) -> dict[str, list[tuple[str, list[Path]]]]:
    """Find notes that could be grouped into subfolders by common prefix.

    Scans all top-level vault folders (depth-2 notes only — e.g. Patterns/foo.md).
    Groups notes within each folder by the first ``-``-delimited word in their stem.
    Returns only groups with >= PREFIX_CLUSTER_MIN (3) notes.

    Returns:
        dict mapping folder_name (relative to vault_root) to a list of
        (prefix, [note_paths]) tuples — one per qualifying prefix group.
    """
    by_folder: dict[Path, list[Path]] = {}
    for note in vault_common.all_vault_notes(vault_root):
        rel = note.relative_to(vault_root)
        parts = rel.parts
        # Only flat notes (depth 2: folder/note.md) — skip subfolders and root
        if len(parts) != 2:
            continue
        folder_name = parts[0]
        if folder_name in vault_common.EXCLUDE_DIRS:
            continue
        if folder_name == "Daily":
            continue
        if parts[1] in ("MANIFEST.md", "CLAUDE.md"):
            continue
        by_folder.setdefault(note.parent, []).append(note)

    result: dict[str, list[tuple[str, list[Path]]]] = {}
    for folder, notes in sorted(by_folder.items()):
        folder_rel = str(folder.relative_to(vault_root))
        by_prefix: dict[str, list[Path]] = {}
        for note in notes:
            stem_parts = note.stem.split("-")
            if len(stem_parts) < 2:
                continue
            prefix = stem_parts[0]
            # Skip if the subfolder already exists
            if (folder / prefix).exists():
                continue
            by_prefix.setdefault(prefix, []).append(note)

        groups = [
            (prefix, sorted(notes_in_group))
            for prefix, notes_in_group in sorted(by_prefix.items())
            if len(notes_in_group) >= PREFIX_CLUSTER_MIN
        ]
        if groups:
            result[folder_rel] = groups

    return result


def run_migrate_subfolders(vault_root: Path, dry_run: bool = True) -> None:
    """Detect prefix groups and optionally migrate notes into subfolders.

    Shows all candidate groups (folders with >= 3 notes sharing a first-word prefix).
    With ``dry_run=True`` (default): prints what would move without touching files.
    With ``dry_run=False``: moves files, updates wikilinks vault-wide, then calls
    ``update_index.py`` to rebuild the index.

    Args:
        vault_root: Root path of the vault.
        dry_run: When True, only print candidates — do not move any files.
    """
    candidates = find_subfolder_candidates(vault_root)

    if not candidates:
        print("No subfolder migration candidates found.")
        return

    total_groups = sum(len(groups) for groups in candidates.values())
    total_notes = sum(
        len(notes) for groups in candidates.values() for _, notes in groups
    )
    print(
        f"Found {total_groups} prefix group(s) across "
        f"{len(candidates)} folder(s) ({total_notes} note(s) total):\n"
    )

    for folder_rel, groups in sorted(candidates.items()):
        for prefix, notes in groups:
            subfolder_rel = f"{folder_rel}/{prefix}/"
            print(f"  {subfolder_rel}  ({len(notes)} notes)")
            for note in notes:
                note_rel = note.relative_to(vault_root)
                # Strip the prefix from the new stem (first-word migration)
                new_stem = note.stem[len(prefix) + 1 :]
                new_name = f"{new_stem}.md" if new_stem else note.name
                print(f"    {note_rel}  →  {folder_rel}/{prefix}/{new_name}")
        print()

    if dry_run:
        print(
            f"[dry-run] {total_notes} note(s) would be moved into "
            f"{total_groups} subfolder(s). Run with --execute to apply."
        )
        return

    # --- Execute migrations ---
    all_notes = list(vault_common.all_vault_notes(vault_root))
    total_moved = 0
    for folder_rel, groups in sorted(candidates.items()):
        folder = vault_root / folder_rel
        for prefix, notes in groups:
            moves = fix_prefix_cluster(folder, prefix, notes, all_notes, base_note=None)
            for old_path, new_path in moves:
                old_rel = old_path.relative_to(vault_root)
                new_rel = new_path.relative_to(vault_root)
                print(f"  Moved: {old_rel}  →  {new_rel}")
                total_moved += 1

    if total_moved:
        vault_common.git_commit_vault(
            f"refactor(vault): migrate {total_moved} note(s) into prefix subfolders via vault_doctor --migrate-subfolders",
            vault=vault_root,
        )
        print(f"\nMoved {total_moved} note(s). Running update_index.py…")
        update_index_script = Path(__file__).parent / "update_index.py"
        try:
            subprocess.run(
                ["uv", "run", "--no-project", str(update_index_script)],
                check=True,
                env=vault_common.env_without_claudecode(),
                timeout=60,
            )
            print("Index rebuilt.")
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            print(f"Warning: update_index.py failed: {exc}", file=sys.stderr)
            print("Run manually: uv run --no-project update_index.py", file=sys.stderr)
    else:
        print("No files were moved (all subfolders may already exist).")


def resolve_wikilink(raw_link: str, note_map: dict[str, list[Path]]) -> bool:
    """Return True if [[raw_link]] resolves to at least one vault note.

    Handles display aliases (``[[target|alias]]``) and section anchors
    (``[[target#heading]]``).  Folder-qualified links (``[[folder/note]]``)
    require the path to contain the given folder segment.
    """
    # Strip display alias and section anchor
    target = raw_link.split("|")[0].split("#")[0].strip()
    if not target:
        return True  # empty — ignore

    stem = Path(target.split("/")[-1]).stem.lower()
    candidates = note_map.get(stem, [])
    if not candidates:
        return False

    # If a folder prefix is given, require it to appear in the path
    if "/" in target:
        folder_prefix = target.split("/")[0].lower()
        return any(folder_prefix in str(p).lower() for p in candidates)

    return True


# ---------------------------------------------------------------------------
# Note checker
# ---------------------------------------------------------------------------


def check_note(
    path: Path, note_map: dict[str, list[Path]], vault_path: Path
) -> list[Issue]:
    """Return a list of Issues found in *path*."""
    issues: list[Issue] = []

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [Issue(path, "error", "READ_ERROR", str(exc))]

    rel = path.relative_to(vault_path)

    # Flat daily note: Daily/YYYY-MM-DD.md should be Daily/YYYY-MM/DD.md
    parts = rel.parts
    if parts[0] == "Daily" and len(parts) == 2:
        if re.match(r"^\d{4}-\d{2}-\d{2}\.md$", parts[1]):
            issues.append(
                Issue(
                    path,
                    "warning",
                    "FLAT_DAILY",
                    "Daily note is flat (YYYY-MM-DD.md) — should live in Daily/YYYY-MM/DD.md",
                )
            )

    # Parse frontmatter
    fm = vault_common.parse_frontmatter(content)
    if not fm:
        issues.append(
            Issue(
                path, "error", "MISSING_FRONTMATTER", "No YAML frontmatter block found"
            )
        )
        # Can't check field-level issues without frontmatter
        return issues

    # Required fields
    note_type_raw = fm.get("type", "")
    is_daily = note_type_raw == "daily" or parts[0] == "Daily"
    required = (
        REQUIRED_FIELDS_ALL
        if is_daily
        else REQUIRED_FIELDS_ALL + REQUIRED_FIELDS_KNOWLEDGE
    )
    for fname in required:
        val = fm.get(fname)
        if val is None or val == "" or val == [] or val == "[]":
            issues.append(
                Issue(
                    path,
                    "error",
                    "MISSING_FIELD",
                    f"Required field '{fname}' is absent or empty",
                )
            )

    # Valid type
    if note_type_raw and note_type_raw not in VALID_TYPES:
        issues.append(
            Issue(
                path,
                "error",
                "INVALID_TYPE",
                f"type '{note_type_raw}' is not one of: {', '.join(sorted(VALID_TYPES))}",
            )
        )

    # Date format
    date_val = str(fm.get("date", ""))
    if date_val and not re.match(r"^\d{4}-\d{2}-\d{2}$", date_val):
        issues.append(
            Issue(
                path, "warning", "INVALID_DATE", f"date '{date_val}' is not YYYY-MM-DD"
            )
        )

    # Orphan check — related must contain at least one [[wikilink]] (not for daily notes)
    if not is_daily:
        related = fm.get("related", [])
        related_str = str(related)
        if not re.search(r"\[\[.+?\]\]", related_str):
            issues.append(
                Issue(
                    path,
                    "warning",
                    "ORPHAN_NOTE",
                    "No [[wikilinks]] in 'related' field (orphan note)",
                )
            )

    # Heading mismatch — first heading is ## but no # heading exists (skip daily notes)
    if not is_daily:
        body = vault_common.get_body(content)
        has_h1 = False
        first_h2_line: str | None = None
        for bline in body.splitlines():
            s = bline.strip()
            if s.startswith("# ") and not s.startswith("## "):
                has_h1 = True
                break
            if (
                first_h2_line is None
                and s.startswith("## ")
                and not s.startswith("### ")
            ):
                first_h2_line = s
        if not has_h1 and first_h2_line is not None:
            issues.append(
                Issue(
                    path,
                    "warning",
                    "HEADING_MISMATCH",
                    f"No # heading found; first ## heading should be promoted to #: {first_h2_line}",
                )
            )

    # Broken wikilinks anywhere in the document.
    # Exclude newlines from the match to avoid capturing cross-line false positives
    # (e.g. truncated MANIFEST table cells in daily notes).
    # Also skip links containing shell metacharacters (bash [[ ]] conditionals).
    _SHELL_META = re.compile(r"[!$<>|&;{}\n]")
    for link in re.findall(r"\[\[([^\]\n]+)\]\]", content):
        clean = link.split("|")[0].split("#")[0].strip()
        if not clean or _SHELL_META.search(clean):
            continue
        if not resolve_wikilink(clean, note_map):
            issues.append(
                Issue(
                    path,
                    "warning",
                    "BROKEN_WIKILINK",
                    f"[[{clean}]] does not resolve to any vault note",
                )
            )

    return issues


# ---------------------------------------------------------------------------
# Claude repair
# ---------------------------------------------------------------------------


def _find_link_replacement(
    link_text: str,
    note_map: dict[str, list[Path]],
    exclude_path: Path | None = None,
    min_score: float = 0.5,
) -> str | None:
    """Return the stem to replace a broken [[link_text]] with, or None to remove it.

    Strategy:
    1. Exact case-insensitive stem match — if exactly one vault note matches,
       return its stem.
    2. Prefix-strip match — if the link is ``prefix-rest`` and ``rest`` resolves
       to a note inside a ``prefix/`` subfolder, return ``rest``.  This handles
       links that broke when notes were migrated into subfolders and the prefix
       was stripped from the filename.
    3. Semantic fallback via vault-search — take the top result above min_score
       that isn't exclude_path.
    Returns None if no match is found (caller should remove the link).
    """
    # Normalize: strip .md extension if present (some links use [[note.md]] format)
    clean = link_text.strip()
    if clean.lower().endswith(".md"):
        clean = clean[:-3]

    # 1. Exact match (case-insensitive stem)
    key = clean.lower()
    matches = note_map.get(key, [])
    if len(matches) == 1:
        return matches[0].stem
    # Multiple exact matches — ambiguous, fall through

    # 2. Prefix-strip match: try splitting at each hyphen position to find
    #    a subfolder that matches the prefix and a note that matches the rest.
    #    e.g. "claude-agent-sdk-overview" → prefix="claude-agent-sdk", rest="overview"
    segments = clean.split("-")
    for i in range(1, len(segments)):
        prefix = "-".join(segments[:i]).lower()
        rest = "-".join(segments[i:]).lower()
        rest_matches = note_map.get(rest, [])
        for m in rest_matches:
            if any(p.lower() == prefix for p in m.parent.parts):
                return m.stem

    # 3. Semantic fallback
    try:
        result = subprocess.run(
            [
                "vault-search",
                link_text,
                "--json",
                "--top=2",
                f"--min-score={min_score}",
            ],
            env=vault_common.env_without_claudecode(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        exclude_resolved = str(exclude_path.resolve()) if exclude_path else None
        for item in data:
            item_resolved = str(Path(str(item["path"])).resolve())
            if exclude_resolved and item_resolved == exclude_resolved:
                continue
            return str(item["stem"])
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired, KeyError):
        pass
    return None


def _auto_repair_broken_wikilinks(
    path: Path,
    broken_issues: list[Issue],
    note_map: dict[str, list[Path]],
) -> tuple[str | None, bool]:
    """Repair broken wikilinks in *path* using exact + semantic matching.

    Returns (new_content | None, became_orphan).

    - For each broken link: attempt to find a replacement via _find_link_replacement().
    - If a replacement is found → update the link everywhere in the note.
    - If no replacement → remove the link (strip brackets in body; drop from related).
    - If removing all related links empties the field → became_orphan = True,
      and _find_semantic_candidates() is called to inject candidates.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return None, False

    original_content = content

    # Deduplicate broken link texts from issues
    seen: set[str] = set()
    broken_links: list[str] = []
    for issue in broken_issues:
        m = re.search(r"\[\[([^\]]+)\]\]", issue.message)
        if m:
            link_text = m.group(1).strip()
            if link_text not in seen:
                seen.add(link_text)
                broken_links.append(link_text)

    if not broken_links:
        return None, False

    # Resolve replacements
    replacements: dict[str, str | None] = {}
    for link in broken_links:
        replacements[link] = _find_link_replacement(link, note_map, exclude_path=path)

    # --- Update `related` frontmatter field ---
    became_orphan = False
    related_pattern = re.compile(r"^(related:\s*)(\[.*?\])\s*$", re.MULTILINE)
    related_match = related_pattern.search(content)
    if related_match:
        prefix = related_match.group(1)
        raw_list = related_match.group(2)
        # Parse individual quoted wikilink entries: "[[stem]]"
        entries = re.findall(r'"(\[\[[^\]]+\]\])"', raw_list)
        new_entries: list[str] = []
        for entry in entries:
            m = re.match(r"\[\[([^\]]+)\]\]", entry)
            if not m:
                new_entries.append(f'"{entry}"')
                continue
            stem = m.group(1).strip()
            if stem in replacements:
                replacement = replacements[stem]
                if replacement is not None:
                    new_entries.append(f'"[[{replacement}]]"')
                # else: drop (removed)
            else:
                new_entries.append(f'"{entry}"')

        # Deduplicate entries, preserving order
        new_entries = list(dict.fromkeys(new_entries))

        if new_entries:
            new_related_line = f"{prefix}[{', '.join(new_entries)}]"
        else:
            # All links removed — check if we can inject semantic candidates
            became_orphan = True
            candidates = _find_semantic_candidates(path)
            if candidates:
                candidate_entries = [f'"[[{s}]]"' for s in candidates]
                new_related_line = f"{prefix}[{', '.join(candidate_entries)}]"
                became_orphan = False
            else:
                new_related_line = f"{prefix}[]"

        content = related_pattern.sub(new_related_line, content)

    # --- Update body text broken links ---
    for link, replacement in replacements.items():
        if replacement:
            content = content.replace(f"[[{link}]]", f"[[{replacement}]]")
        else:
            # Strip brackets, keep text
            content = content.replace(f"[[{link}]]", link)

    if content == original_content:
        return None, False

    return content, became_orphan


def _find_semantic_candidates(path: Path, top_k: int = 5) -> list[str]:
    """Return stem names of semantically similar vault notes for wikilink suggestions.

    Calls the ``vault-search`` CLI as a subprocess and returns up to *top_k* stem
    names (excluding *path* itself).  Returns [] gracefully on any failure —
    missing ``vault-search`` binary, absent ``embeddings.db``, JSON parse errors.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return []

    # Use the H1 heading as the query — most descriptive; fall back to the stem
    title_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
    query = title_match.group(1).strip() if title_match else path.stem.replace("-", " ")

    try:
        result = subprocess.run(
            ["vault-search", query, "--json", f"--top={top_k + 1}"],
            capture_output=True,
            text=True,
            timeout=30,
            env=vault_common.env_without_claudecode(),
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        self_path = str(path.resolve())
        return [
            str(item["stem"])
            for item in data
            if str(Path(str(item["path"])).resolve()) != self_path
        ][:top_k]
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired, KeyError):
        return []


def _auto_fix_headings(path: Path) -> bool:
    """Promote the first ``## `` heading to ``# `` when no ``# `` heading exists.

    Returns True if the file was modified.
    """
    content = path.read_text(encoding="utf-8")
    body = vault_common.get_body(content)

    # Check there is no existing # heading
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            return False  # already has a proper H1

    # Find and promote the first ## heading
    lines = content.split("\n")
    modified = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            # Only promote if we're past the frontmatter
            lines[i] = line.replace("## ", "# ", 1)
            modified = True
            break

    if modified:
        path.write_text("\n".join(lines), encoding="utf-8")
    return modified


def repair_note(
    path: Path,
    issues: list[Issue],
    model: str | None = DEFAULT_MODEL,
    timeout: int = AI_TIMEOUT,
    vault_path: Path | None = None,
) -> tuple[str | None, str]:
    """Call the configured prompt AI backend to fix *issues* in *path*.

    Returns (fixed_content_or_None, status) where status is one of
    "fixed", "failed", or "timeout".
    """
    if vault_path is None:
        vault_path = _vault_path if _vault_path else vault_common.VAULT_ROOT
    content = path.read_text(encoding="utf-8")
    rel = path.relative_to(vault_path)
    issue_lines = "\n".join(
        f"  - [{i.severity.upper()}] {i.code}: {i.message}" for i in issues
    )

    # For ORPHAN_NOTE issues, find semantically similar notes so Claude can
    # pick real wikilinks instead of inventing them from thin air.
    has_orphan = any(i.code == "ORPHAN_NOTE" for i in issues)
    candidates: list[str] = _find_semantic_candidates(path) if has_orphan else []
    candidate_section = ""
    if candidates:
        links = ", ".join(f"[[{s}]]" for s in candidates)
        candidate_section = (
            f"\n\nSemantically similar vault notes "
            f"(choose from these for the 'related' field — do NOT invent others):\n{links}"
        )

    prompt = f"""You are a vault note repair tool. Fix ONLY the listed issues in this Obsidian markdown note.
Do NOT rewrite, summarise, or add content beyond what is needed to resolve each issue.
Return ONLY the corrected note — no explanation, no code fences.

File: {rel}

Issues to fix:
{issue_lines}

Rules:
- Valid values for 'type': {", ".join(sorted(VALID_TYPES))}
- Valid values for 'confidence': high | medium | low
- 'date' must be YYYY-MM-DD
- 'related' must contain at least one [[wikilink]] to a related concept
- Every note needs: date, type, confidence, related in its YAML frontmatter
- 'sources' should be [] if unknown{candidate_section}

Current note:
---BEGIN---
{content}
---END---"""

    try:
        output = ai_backend.run_ai_prompt(
            prompt,
            model=model,
            model_tier="small",
            timeout=timeout,
            purpose="vault-doctor",
            vault=vault_path,
            raise_on_timeout=True,
        )
    except ai_backend.AiBackendTimeout:
        return None, "timeout"
    if output:
        output = output.strip()
        # Strip accidental markdown fences if the backend added them
        output = re.sub(r"^```[a-z]*\n?", "", output)
        output = re.sub(r"\n?```$", "", output)
        if output:
            return output, "fixed"
    return None, "failed"


# ---------------------------------------------------------------------------
# Parallel repair worker
# ---------------------------------------------------------------------------


def _repair_one(
    note_path: Path,
    note_issues: list[Issue],
    model: str | None,
    state: dict,
    today_str: str,
    lock: threading.Lock,
    timeout: int = AI_TIMEOUT,
    note_map: dict[str, list[Path]] | None = None,
    fix_headings: bool = True,
    vault_path: Path | None = None,
) -> bool:
    """Repair one note, update state under *lock*, return True on success."""
    if vault_path is None:
        vault_path = _vault_path if _vault_path else vault_common.VAULT_ROOT
    key = _rel(note_path)
    rel = note_path.relative_to(vault_path)
    repairable = [i for i in note_issues if i.code in REPAIRABLE_CODES]
    broken = [i for i in repairable if i.code == "BROKEN_WIKILINK"]
    heading_issues = [i for i in repairable if i.code == "HEADING_MISMATCH"]
    other = [
        i for i in repairable if i.code not in ("BROKEN_WIKILINK", "HEADING_MISMATCH")
    ]

    with lock:
        prev_status = state.get("notes", {}).get(key, {}).get("status", "")

    # Step 0: Python-based heading promotion (no Claude needed)
    heading_fix_made = False
    if heading_issues and fix_headings:
        heading_fix_made = _auto_fix_headings(note_path)
        if heading_fix_made:
            with lock:
                print(f"  ✓ {rel}: promoted ## heading to #", flush=True)

    # Step 1: Python-based broken-link repair (no Claude needed)
    link_fix_made = False
    became_orphan = False
    if broken and note_map is not None:
        fixed_content, became_orphan = _auto_repair_broken_wikilinks(
            note_path, broken, note_map
        )
        if fixed_content:
            note_path.write_text(fixed_content + "\n", encoding="utf-8")
            link_fix_made = True

    # Step 2: If note became orphan (all related removed, no candidates found),
    #         inject a synthetic ORPHAN_NOTE issue so Claude's orphan repair fires
    if became_orphan:
        other.append(
            Issue(
                note_path,
                "warning",
                "ORPHAN_NOTE",
                "All related links removed — no candidates found",
            )
        )

    # Step 3: Claude repair for remaining issues (MISSING_FIELD, ORPHAN_NOTE, etc.)
    fixed_content = None
    repair_status = "failed"
    if other:
        fixed_content, repair_status = repair_note(note_path, other, model, timeout)
        if fixed_content:
            note_path.write_text(fixed_content + "\n", encoding="utf-8")
    elif broken or heading_issues:
        # Only broken wikilinks / heading fixes — no Claude call needed
        repair_status = "fixed" if (link_fix_made or heading_fix_made) else "failed"

    if fixed_content:
        icon = "✓"
    elif (link_fix_made or heading_fix_made) and not other:
        # Fixed by Python, no Claude needed
        icon = "✓"
    else:
        if repair_status == "timeout" and prev_status == "timeout":
            repair_status = "needs_review"
        icon = "✗" if not link_fix_made else "~"

    with lock:
        msg = f"  {rel} ({len(repairable)} issue(s)) … {icon}"
        if repair_status == "needs_review":
            msg += (
                "\n    → needs_review (timed out twice; flagged for user intervention)"
            )
        print(msg, flush=True)
        state.setdefault("notes", {})[key] = {
            "status": repair_status,
            "last_checked": today_str,
            "issues": [i.code for i in repairable],
        }

    return fixed_content is not None or link_fix_made or heading_fix_made


# ---------------------------------------------------------------------------
# Tag deduplication
# ---------------------------------------------------------------------------

# Regex to find the tags line in frontmatter (inline or block).
# We operate on raw file text to preserve formatting of other fields.
_TAGS_INLINE_RE = re.compile(r"^(tags:\s*)\[([^\]]*)\]\s*$", re.MULTILINE)
_TAGS_BLOCK_START_RE = re.compile(r"^tags:\s*$", re.MULTILINE)


def _collect_all_tags(notes: list[Path]) -> dict[str, int]:
    """Return tag → usage count across all vault notes."""
    counts: dict[str, int] = {}
    for note in notes:
        try:
            content = note.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = vault_common.parse_frontmatter(content)
        tags = fm.get("tags", [])
        if isinstance(tags, list):
            for t in tags:
                tag = str(t).strip()
                if tag:
                    counts[tag] = counts.get(tag, 0) + 1
    return counts


def _find_session_duplicates(notes: list[Path]) -> list[tuple[str, list[Path]]]:
    """Find groups of notes that share the same session_id in frontmatter.

    Returns a list of (session_id, [paths]) for sessions with >1 note.
    """
    session_map: dict[str, list[Path]] = {}
    for path in notes:
        try:
            content = path.read_text(encoding="utf-8")
            fm = vault_common.parse_frontmatter(content)
            sid = fm.get("session_id")
            if not sid:
                tags = fm.get("tags", [])
                if isinstance(tags, str):
                    tags = [tags]
                sid = next(
                    (t for t in tags if SESSION_ID_PATTERN.match(str(t).lower())), None
                )

            if sid:
                sid_str = str(sid).lower()
                if sid_str not in session_map:
                    session_map[sid_str] = []
                session_map[sid_str].append(path)
        except OSError:
            continue

    return [(sid, paths) for sid, paths in session_map.items() if len(paths) > 1]


def _find_tag_duplicates(
    tag_counts: dict[str, int],
) -> list[tuple[str, str, str]]:
    """Find duplicate tag pairs that should be merged.

    Returns list of (keep, merge_away, reason).
    The tag with higher usage count is kept; ties prefer kebab-case.
    """
    tags = sorted(tag_counts.keys())
    pairs: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    for i, t1 in enumerate(tags):
        for t2 in tags[i + 1 :]:
            pair_key = (min(t1, t2), max(t1, t2))
            if pair_key in seen:
                continue

            reason: str | None = None

            # Hyphen vs underscore (exact match after normalization)
            if t1.replace("-", "_") == t2 or t1.replace("_", "-") == t2:
                reason = "hyphen/underscore"

            # Plural/singular (simple -s suffix)
            elif t1 + "s" == t2 or t2 + "s" == t1:
                reason = "plural/singular"

            # Exact duplicate with different casing
            elif t1.lower() == t2.lower() and t1 != t2:
                reason = "case"

            # Hyphenated vs single-word (e.g. real-time vs realtime)
            elif t1.replace("-", "") == t2 or t2.replace("-", "") == t1:
                reason = "hyphenated/collapsed"

            if reason:
                seen.add(pair_key)
                c1 = tag_counts.get(t1, 0)
                c2 = tag_counts.get(t2, 0)
                # Pick canonical form.  Vault convention: prefer short,
                # singular, kebab-case tags.  So:
                # 1. Plural/singular → always keep singular
                # 2. Hyphen/underscore → always keep kebab-case
                # 3. Hyphenated/collapsed → keep hyphenated (more readable)
                # 4. Fallback: higher count wins
                if reason == "plural/singular":
                    # Singular is the shorter one (without trailing -s)
                    if t1 + "s" == t2:
                        keep, away = t1, t2
                    else:
                        keep, away = t2, t1
                elif reason == "hyphen/underscore":
                    if "-" in t1 and "_" in t2:
                        keep, away = t1, t2
                    else:
                        keep, away = t2, t1
                elif reason == "hyphenated/collapsed":
                    # Keep the hyphenated form (more readable)
                    if "-" in t1:
                        keep, away = t1, t2
                    else:
                        keep, away = t2, t1
                elif c1 >= c2:
                    keep, away = t1, t2
                else:
                    keep, away = t2, t1
                pairs.append((keep, away, reason))

    return pairs


def _replace_tag_in_note(path: Path, old_tag: str, new_tag: str) -> bool:
    """Replace *old_tag* with *new_tag* in a note's frontmatter tags field.

    Handles inline lists ``[a, b]``, inline quoted ``["a", "b"]``, and
    block sequence (``- item``) formats.  Returns True if the file was modified.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False

    # Find frontmatter boundaries
    fm_match = re.match(r"^---\n(.*?\n)---", content, re.DOTALL)
    if not fm_match:
        return False

    fm_text = fm_match.group(1)
    original_fm = fm_text

    # Strategy: find the tags field and do targeted replacement within it.
    # This avoids corrupting other frontmatter fields.

    # Inline list: tags: [tag1, tag2]
    inline_m = _TAGS_INLINE_RE.search(fm_text)
    if inline_m:
        prefix = inline_m.group(1)
        items_str = inline_m.group(2)
        # Parse items, respecting quotes
        items: list[str] = []
        for item in re.findall(r'"([^"]*)"', items_str):
            items.append(item)
        if not items:
            # Unquoted inline: [a, b, c]
            items = [i.strip().strip('"').strip("'") for i in items_str.split(",")]

        new_items: list[str] = []
        replaced = False
        for item in items:
            if item == old_tag:
                if new_tag not in new_items:
                    new_items.append(new_tag)
                replaced = True
            elif item not in new_items:
                new_items.append(item)

        if not replaced:
            return False

        # Detect quoting style from original
        has_quotes = '"' in items_str
        if has_quotes:
            formatted = ", ".join(f'"{t}"' for t in new_items)
        else:
            formatted = ", ".join(new_items)
        new_line = f"{prefix}[{formatted}]"
        fm_text = fm_text[: inline_m.start()] + new_line + fm_text[inline_m.end() :]

    else:
        # Block sequence: tags:\n  - item\n  - item\n...
        block_m = _TAGS_BLOCK_START_RE.search(fm_text)
        if block_m:
            # Split everything after "tags:" into lines and find the
            # contiguous block of "  - ..." items.  The first line is
            # often empty (the newline right after "tags:").
            after = fm_text[block_m.end() :]
            all_lines = after.split("\n")
            tag_lines: list[str] = []  # original "  - X" lines
            end_idx = 0
            for i, line in enumerate(all_lines):
                stripped = line.strip()
                if stripped.startswith("- "):
                    tag_lines.append(line)
                    end_idx = i + 1
                elif not stripped and not tag_lines:
                    # Leading blank line before first item — skip
                    end_idx = i + 1
                    continue
                elif not stripped and tag_lines:
                    # Blank line after items — end of block
                    break
                else:
                    break  # next field

            if not tag_lines:
                return False

            # Parse old tags, build new list with replacement
            replaced = False
            seen_tags: set[str] = set()
            new_tag_lines: list[str] = []
            for line in tag_lines:
                tag_val = line.strip()[2:].strip().strip('"').strip("'")
                if tag_val == old_tag:
                    if new_tag not in seen_tags:
                        new_tag_lines.append(f"  - {new_tag}")
                        seen_tags.add(new_tag)
                    replaced = True
                elif tag_val not in seen_tags:
                    new_tag_lines.append(line)
                    seen_tags.add(tag_val)

            if not replaced:
                return False

            # Reconstruct: "tags:\n" + new tag lines + everything after the block
            rest = "\n".join(all_lines[end_idx:])
            fm_text = (
                fm_text[: block_m.end()] + "\n" + "\n".join(new_tag_lines) + "\n" + rest
            )
        else:
            return False

    if fm_text == original_fm:
        return False

    new_content = content[: fm_match.start(1)] + fm_text + content[fm_match.end(1) :]
    path.write_text(new_content, encoding="utf-8")
    return True


def _update_graph_json_tags(
    merges: list[tuple[str, str, str]], vault_path: Path | None = None
) -> int:
    """Update graph.json to replace merged-away tags with their canonical form.

    Returns the number of substitutions made.
    """
    if vault_path is None:
        vault_path = _vault_path if _vault_path else vault_common.VAULT_ROOT
    graph_path = vault_path / ".obsidian" / "graph.json"
    if not graph_path.is_file():
        return 0

    try:
        data = json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0

    subs = 0
    for keep, away, _ in merges:
        for group in data.get("colorGroups", []):
            query = group.get("query", "")
            old_ref = f"tag:#{away}"
            if old_ref in query:
                # Replace with canonical, but only add if not already present
                new_ref = f"tag:#{keep}"
                if new_ref in query:
                    # Already has canonical — just remove the old one
                    query = query.replace(f" OR {old_ref}", "")
                    query = query.replace(f"{old_ref} OR ", "")
                    query = query.replace(old_ref, "")
                else:
                    query = query.replace(old_ref, new_ref)
                group["query"] = query
                subs += 1

    if subs:
        graph_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    return subs


def _normalize_underscores_in_frontmatter(
    notes: list[Path],
    dry_run: bool = True,
    vault_path: Path | None = None,
) -> int:
    """Convert underscores to hyphens in tags and project frontmatter fields.

    Handles all YAML tag formats (inline, quoted inline, block sequence) and
    the scalar ``project`` field.  Returns the number of notes modified.
    """
    if vault_path is None:
        vault_path = _vault_path if _vault_path else vault_common.VAULT_ROOT
    # Regex for project field: project: some_value
    project_re = re.compile(r"^(project:\s*)(.+)$", re.MULTILINE)

    found: list[tuple[Path, list[str]]] = []
    for note in notes:
        try:
            content = note.read_text(encoding="utf-8")
        except OSError:
            continue
        fm_match = re.match(r"^---\n(.*?\n)---", content, re.DOTALL)
        if not fm_match:
            continue
        fm = vault_common.parse_frontmatter(content)
        issues: list[str] = []
        # Check tags
        tags = fm.get("tags", [])
        if isinstance(tags, list):
            for t in tags:
                if "_" in str(t):
                    issues.append(f"tag: {t} → {str(t).replace('_', '-')}")
        # Check project
        proj = str(fm.get("project", ""))
        if "_" in proj:
            issues.append(f"project: {proj} → {proj.replace('_', '-')}")
        if issues:
            found.append((note, issues))

    if not found:
        return 0

    print(f"\nFound {len(found)} note(s) with underscores in tags/project:\n")
    for note, issues in found[:20]:
        rel = note.relative_to(vault_path)
        print(f"  {rel}")
        for issue in issues:
            print(f"    {issue}")
    if len(found) > 20:
        print(f"  ... and {len(found) - 20} more")
    print()

    if dry_run:
        return 0

    modified = 0
    for note, _ in found:
        try:
            content = note.read_text(encoding="utf-8")
        except OSError:
            continue
        fm_match = re.match(r"^---\n(.*?\n)---", content, re.DOTALL)
        if not fm_match:
            continue
        fm_text = fm_match.group(1)
        original_fm = fm_text

        # Fix tags: replace underscores with hyphens in tag values only
        # Inline: tags: [par_ai_core, foo] or tags: ["par_ai_core", "foo"]
        inline_m = _TAGS_INLINE_RE.search(fm_text)
        if inline_m:
            old_items = inline_m.group(2)
            new_items = old_items.replace("_", "-")
            if old_items != new_items:
                fm_text = (
                    fm_text[: inline_m.start(2)]
                    + new_items
                    + fm_text[inline_m.end(2) :]
                )
        else:
            # Block sequence: replace underscores in "  - tag_name" lines
            block_m = _TAGS_BLOCK_START_RE.search(fm_text)
            if block_m:
                after = fm_text[block_m.end() :]
                new_after = re.sub(
                    r"^(  - )(.+)$",
                    lambda m: m.group(1) + m.group(2).replace("_", "-"),
                    after,
                    flags=re.MULTILINE,
                )
                if new_after != after:
                    fm_text = fm_text[: block_m.end()] + new_after

        # Fix project field
        fm_text = project_re.sub(
            lambda m: m.group(1) + m.group(2).replace("_", "-"),
            fm_text,
        )

        if fm_text != original_fm:
            new_content = (
                content[: fm_match.start(1)] + fm_text + content[fm_match.end(1) :]
            )
            note.write_text(new_content, encoding="utf-8")
            modified += 1

    if modified:
        print(f"  Normalized underscores → hyphens in {modified} note(s)")
    return modified


def _run_reindex(vault_path: Path | None = None) -> None:
    """Run update_index.py to rebuild the vault index."""
    if vault_path is None:
        vault_path = _vault_path if _vault_path else vault_common.VAULT_ROOT

    script = Path(__file__).parent / "update_index.py"
    if not script.exists():
        script = (
            Path.home()
            / ".claude"
            / "skills"
            / "parsidion"
            / "scripts"
            / "update_index.py"
        )

    if not script.exists():
        print("Warning: update_index.py not found, skipping re-index.", file=sys.stderr)
        return

    print(f"\nRebuilding vault index at {vault_path}...")
    try:
        subprocess.run(
            ["uv", "run", "--no-project", str(script), "--vault", str(vault_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        print("Index rebuilt successfully.")
    except Exception as exc:
        print(f"Warning: update_index.py failed: {exc}", file=sys.stderr)


def run_fix_sessions(vault_path: Path | None = None) -> None:
    """Detect and report notes sharing the same session_id."""
    if vault_path is None:
        vault_path = _vault_path if _vault_path else vault_common.VAULT_ROOT

    notes = vault_common.all_vault_notes(vault=vault_path)
    duplicates = _find_session_duplicates(notes)

    if not duplicates:
        print("No duplicate session IDs found.")
        return

    print(f"\nFound {len(duplicates)} session(s) with multiple notes:\n")
    for sid, paths in sorted(duplicates, key=lambda x: len(x[1]), reverse=True):
        print(f"  Session: {sid} ({len(paths)} notes)")
        for p in sorted(paths):
            print(f"    - {_rel(p, vault_path)}")

        if len(paths) >= 2:
            print(f"    → vault-merge {paths[0].stem} {paths[1].stem}")
        print()


def run_fix_tags(
    dry_run: bool = True, vault_path: Path | None = None, auto_reindex: bool = True
) -> None:
    """Detect and merge duplicate tags across the vault.

    Finds duplicate tag pairs (plural/singular, hyphen/underscore,
    collapsed hyphens) and merges them to a canonical form.  Also
    normalizes any remaining underscores in tags and project fields.

    Args:
        dry_run: When True, only report — do not modify any files.
        vault_path: Vault root path (uses resolver if None).
    """
    if vault_path is None:
        vault_path = _vault_path if _vault_path else vault_common.VAULT_ROOT
    all_notes = list(vault_common.all_vault_notes(vault_path))

    # Step 1: Normalize underscores → hyphens in tags and project fields
    underscore_fixed = _normalize_underscores_in_frontmatter(
        all_notes, dry_run=dry_run, vault_path=vault_path
    )

    # Step 2: Detect and merge duplicate tag pairs
    tag_counts = _collect_all_tags(all_notes)
    duplicates = _find_tag_duplicates(tag_counts)

    if not duplicates and not underscore_fixed:
        print("No duplicate tags found.")
        return

    total_modified = underscore_fixed

    if duplicates:
        print(f"\nFound {len(duplicates)} duplicate tag pair(s):\n")
        print(f"  {'Keep':<30} {'#':>4}  {'Merge away':<30} {'#':>4}  Reason")
        print(f"  {'─' * 80}")
        for keep, away, reason in sorted(
            duplicates, key=lambda x: -tag_counts.get(x[1], 0)
        ):
            ck = tag_counts.get(keep, 0)
            ca = tag_counts.get(away, 0)
            print(f"  {keep:<30} {ck:>4}  {away:<30} {ca:>4}  {reason}")
        print()

        total_affected = sum(tag_counts.get(away, 0) for _, away, _ in duplicates)
        print(f"Total note edits needed: ~{total_affected}")

        if dry_run:
            print("\n[dry-run] Run with --execute to apply all fixes.")
            return

        # Apply merges
        for keep, away, _reason in duplicates:
            count = 0
            for note in all_notes:
                if _replace_tag_in_note(note, away, keep):
                    count += 1
                    total_modified += 1
            if count:
                print(f"  Merged '{away}' → '{keep}' in {count} note(s)")

        # Update graph.json
        graph_subs = _update_graph_json_tags(duplicates, vault_path=vault_path)
        if graph_subs:
            print(f"  Updated {graph_subs} graph.json color group(s)")
    elif dry_run:
        return

    if total_modified:
        msg_parts: list[str] = []
        if underscore_fixed:
            msg_parts.append(f"normalize {underscore_fixed} underscore field(s)")
        if duplicates:
            msg_parts.append(f"merge {len(duplicates)} duplicate tag pair(s)")
        vault_common.git_commit_vault(
            f"refactor(vault): {', '.join(msg_parts)}",
            vault=vault_path,
        )
        print(f"\nDone: {total_modified} note(s) modified.")
        if auto_reindex:
            _run_reindex(vault_path)
    else:
        print("\nNo files were modified.")


# ---------------------------------------------------------------------------
# Redundant prefix stripping
# ---------------------------------------------------------------------------


def _find_redundant_prefixes(
    all_notes: list[Path],
    vault_path: Path,
) -> list[tuple[Path, Path]]:
    """Find notes inside subfolders whose filename redundantly starts with the subfolder name.

    For example, ``Projects/cctmux/cctmux-overview.md`` should be
    ``Projects/cctmux/overview.md`` since the subfolder already provides
    the namespace.

    Returns list of (old_path, new_path) pairs.
    """
    pairs: list[tuple[Path, Path]] = []
    for note in all_notes:
        rel = note.relative_to(vault_path)
        parts = rel.parts
        if len(parts) != 3:  # folder/subfolder/note.md
            continue
        subfolder = parts[1].lower()
        stem = note.stem.lower()
        if stem.startswith(f"{subfolder}-"):
            new_stem = note.stem[len(subfolder) + 1 :]
            if new_stem:
                new_path = note.parent / f"{new_stem}.md"
                # Don't rename if the target already exists
                if not new_path.exists():
                    pairs.append((note, new_path))
    return pairs


def run_strip_prefixes(
    dry_run: bool = True, vault_path: Path | None = None, auto_reindex: bool = True
) -> None:
    """Strip redundant subfolder prefixes from note filenames.

    Renames files and updates all wikilinks vault-wide.

    Args:
        dry_run: When True, only report — do not modify any files.
        vault_path: Vault root path (uses resolver if None).
    """
    if vault_path is None:
        vault_path = _vault_path if _vault_path else vault_common.VAULT_ROOT
    all_notes = list(vault_common.all_vault_notes(vault_path))
    pairs = _find_redundant_prefixes(all_notes, vault_path)

    if not pairs:
        print("No redundant prefixes found.")
        return

    # Group by subfolder for display
    by_folder: dict[str, list[tuple[Path, Path]]] = {}
    for old, new in pairs:
        folder_key = str(old.parent.relative_to(vault_path))
        by_folder.setdefault(folder_key, []).append((old, new))

    print(f"\nFound {len(pairs)} note(s) with redundant subfolder prefix:\n")
    for folder, folder_pairs in sorted(by_folder.items()):
        print(f"  {folder}/")
        for old, new in folder_pairs:
            print(f"    {old.name}  →  {new.name}")
    print()

    if dry_run:
        print(
            f"[dry-run] {len(pairs)} file(s) would be renamed. Run with --execute to apply."
        )
        return

    # Build stem remapping for wikilink patching
    stem_map: dict[str, str] = {old.stem: new.stem for old, new in pairs}

    # Rename files
    for old, new in pairs:
        old.rename(new)

    # Patch wikilinks vault-wide (including in the renamed files)
    patched_notes = 0
    current_notes = list(vault_common.all_vault_notes(vault_path))
    for note in current_notes:
        try:
            content = note.read_text(encoding="utf-8")
        except OSError:
            continue
        original = content
        for old_stem, new_stem in stem_map.items():
            content = content.replace(f"[[{old_stem}]]", f"[[{new_stem}]]")
            content = re.sub(
                rf"\[\[{re.escape(old_stem)}\|",
                f"[[{new_stem}|",
                content,
            )
        if content != original:
            note.write_text(content, encoding="utf-8")
            patched_notes += 1

    vault_common.git_commit_vault(
        f"refactor(vault): strip redundant subfolder prefix from {len(pairs)} note(s)",
        vault=vault_path,
    )
    print(
        f"Renamed {len(pairs)} file(s), patched wikilinks in {patched_notes} note(s)."
    )
    if auto_reindex:
        _run_reindex(vault_path)


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
    import re

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

            new_text = text
            for old, new in moved:
                if old.parent != month_dir:
                    continue
                old_stem = re.escape(old.stem)
                new_stem = new.stem
                # Match [[DD]] but not [[DD-something]] (avoid double-rename)
                new_text = re.sub(
                    rf"\[\[{old_stem}\]\]",
                    f"[[{new_stem}]]",
                    new_text,
                )

            if new_text != text:
                rollup.write_text(new_text, encoding="utf-8")
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


def main() -> None:
    """Parse CLI arguments, acquire the singleton PID lock, and dispatch to the requested repair mode."""
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
    args = parser.parse_args()

    # Resolve vault path
    global _vault_path
    _vault_path = vault_common.resolve_vault(explicit=args.vault, cwd=os.getcwd())
    vault_common.apply_configured_env_defaults(vault=_vault_path)

    # QA-001/QA-003: Restore VAULT_ROOT on exit to prevent cross-contamination
    original_vault_root = vault_common.VAULT_ROOT
    vault_common.VAULT_ROOT = _vault_path
    atexit.register(lambda: setattr(vault_common, "VAULT_ROOT", original_vault_root))

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
        run_migrate_subfolders(_vault_path, dry_run=dry)
        if not args.fix_all:
            return

    # ── --migrate-daily-notes mode ─────────────────────────────────────────
    if args.migrate_daily_notes:
        dry = not args.execute
        run_migrate_daily_notes(_vault_path, dry_run=dry, username=args.daily_username)
        if not args.fix_all:
            return

    # Auto-fix legacy pending paths (silent when nothing to fix)
    fixed_paths = vault_common.migrate_pending_paths(
        dry_run=args.dry_run, vault=_vault_path
    )
    if fixed_paths:
        action = "Would fix" if args.dry_run else "Fixed"
        print(
            f"{action} {fixed_paths} legacy transcript path(s) in pending_summaries.jsonl.\n"
        )

    # Session consolidation check
    if args.fix_sessions:
        run_fix_sessions(vault_path=_vault_path)
        sys.exit(0)

    # Auto-deduplicate related wikilinks (silent when nothing to fix)
    deduped = dedup_related_links(dry_run=args.dry_run, vault_path=_vault_path)
    if deduped:
        action = "Would deduplicate" if args.dry_run else "Deduplicated"
        print(f"{action} related links in {deduped} note(s).\n")

    # Auto-commit uncommitted vault files older than STALE_COMMIT_MINUTES
    stale = commit_stale_files(dry_run=args.dry_run, vault_path=_vault_path)
    if stale:
        rel_stale = [str(p.relative_to(_vault_path)) for p in stale]
        if args.dry_run:
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
    if args.notes:
        target_notes = [Path(n).resolve() for n in args.notes]
        explicit = True
    else:
        target_notes = list(vault_common.all_vault_notes(_vault_path))
        explicit = False

    # Always skip the auto-generated vault index and per-folder MANIFEST files —
    # both are rebuilt by update_index.py and should never be doctor-repaired.
    vault_claude_md = _vault_path / "CLAUDE.md"
    target_notes = [
        p for p in target_notes if p != vault_claude_md and p.name != "MANIFEST.md"
    ]

    # Skip notes that have already been processed and are still fresh
    if not explicit and not args.no_state:
        before = len(target_notes)
        target_notes = [p for p in target_notes if not should_skip(_rel(p), state)]
        skipped_by_state = before - len(target_notes)
    else:
        skipped_by_state = 0

    # Build note map once for wikilink resolution
    all_notes = list(vault_common.all_vault_notes(_vault_path))
    note_map = build_note_map(all_notes)

    # ── Prefix cluster detection and fixing ──────────────────────────────────
    clusters = find_prefix_clusters(all_notes, _vault_path)
    if clusters and not args.dry_run:
        # Filter out generic-word false positives using the configured prompt AI backend
        clusters = _filter_clusters_with_claude(
            clusters, model=args.model, timeout=args.timeout
        )
    cluster_repaired = 0
    if clusters:
        total_cluster_notes = sum(len(n) for _, _, n, _ in clusters)
        print(
            f"\nFound {len(clusters)} prefix cluster(s) "
            f"({total_cluster_notes} note(s) to reorganize):\n"
        )
        for cluster_folder, prefix, cluster_notes, base_note in clusters:
            folder_rel = cluster_folder.relative_to(_vault_path)
            kind = "exact-stem" if base_note is not None else "first-word"
            print(f"  {folder_rel}/{prefix}/  ({len(cluster_notes)} notes, {kind})")
            for note in sorted(cluster_notes):
                note_rel = note.relative_to(_vault_path)
                if note is base_note:
                    new_name = note.name  # base note keeps its filename
                elif note.stem.startswith(f"{prefix}-"):
                    new_name = note.stem[len(prefix) + 1 :] + ".md"
                else:
                    new_name = note.name
                print(f"    {note_rel}  →  {folder_rel}/{prefix}/{new_name}")
        print()

        if not args.dry_run and args.fix_frontmatter:
            print("Reorganizing prefix clusters…\n")
            for cluster_folder, prefix, cluster_notes, base_note in clusters:
                moves = fix_prefix_cluster(
                    cluster_folder, prefix, cluster_notes, all_notes, base_note
                )
                for old_path, new_path in moves:
                    old_rel = old_path.relative_to(_vault_path)
                    new_rel = new_path.relative_to(_vault_path)
                    print(f"  {old_rel}  →  {new_rel}")
                    cluster_repaired += 1
            if cluster_repaired:
                vault_common.git_commit_vault(
                    f"refactor(vault): reorganize {cluster_repaired} note(s) into prefix subfolders",
                    vault=_vault_path,
                )
                print()
                # Refresh after moves
                all_notes = list(vault_common.all_vault_notes(_vault_path))
                note_map = build_note_map(all_notes)
                all_filtered = [
                    p
                    for p in all_notes
                    if p != vault_claude_md and p.name != "MANIFEST.md"
                ]
                if not explicit and not args.no_state:
                    target_notes = [
                        p for p in all_filtered if not should_skip(_rel(p), state)
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
        note_issues = check_note(note, note_map, _vault_path)
        if args.errors_only:
            note_issues = [i for i in note_issues if i.severity == "error"]
        key = _rel(note)
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
        if not args.dry_run:
            save_state(state, _vault_path)
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
        rel = note_path.relative_to(_vault_path)
        print(f"  {rel}")
        for issue in note_issues:
            icon = "✗" if issue.severity == "error" else "⚠"
            print(f"    {icon} [{issue.code}] {issue.message}")
    print()

    if args.dry_run:
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
        key = _rel(p)
        state.setdefault("notes", {})[key] = {
            "status": "skipped",
            "last_checked": today_str,
            "issues": [i.code for i in issues_by_note[p]],
        }

    if not repair_candidates:
        print("No repairable issues (flat daily notes require manual fixes).")
        save_state(state, _vault_path)
        return

    if not args.fix_frontmatter:
        print(
            f"{len(repair_candidates)} note(s) have repairable issues.\n"
            "Run with --fix-frontmatter to repair them via the configured prompt AI backend."
        )
        save_state(state, _vault_path)
        return

    # Apply repairs
    limit = args.limit if args.limit > 0 else len(repair_candidates)
    jobs = max(1, args.jobs)
    repaired = 0
    failed = 0
    lock = threading.Lock()

    print(
        f"Repairing up to {limit} note(s) via prompt AI ({jobs} parallel job(s), {args.timeout}s timeout)…\n"
    )
    batch = repair_candidates[:limit]
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as executor:
        futures = {
            executor.submit(
                _repair_one,
                note_path,
                note_issues,
                args.model,
                state,
                today_str,
                lock,
                args.timeout,
                note_map,
                args.fix_headings,
                _vault_path,
            ): note_path
            for note_path, note_issues in batch
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                success = future.result()
            except Exception as exc:  # noqa: BLE001
                note_path = futures[future]
                print(f"  {_rel(note_path)} … ✗ (exception: {exc})", flush=True)
                success = False
            if success:
                repaired += 1
            else:
                failed += 1

    save_state(state, _vault_path)
    leftover = len(repair_candidates) - limit
    print(
        f"\nDone: {repaired} repaired, {failed} failed, {leftover} not yet processed."
    )

    if repaired:
        if not args.fix_all:
            _run_reindex(_vault_path)


if __name__ == "__main__":
    main()
