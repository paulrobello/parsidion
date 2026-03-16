#!/usr/bin/env python3
"""vault_doctor.py — Scan vault notes for issues; optionally repair via Claude haiku.

Stdlib-only. Run with:
    uv run --no-project ~/.claude/skills/claude-vault/scripts/vault_doctor.py
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
# I/O-bound (subprocess calls to ``claude -p`` + file reads/writes) and Python's
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

sys.path.insert(0, str(Path(__file__).parent))
import vault_common  # noqa: E402

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
    }
)
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
AI_TIMEOUT = 120  # seconds
STATE_FILE = vault_common.VAULT_ROOT / "doctor_state.json"
STATE_STALE_DAYS = 7  # re-check "ok" notes after this many days
STALE_COMMIT_MINUTES = 15  # auto-commit uncommitted files older than this


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
# State file  (~ClaudeVault/doctor_state.json)
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
# "fixed"        — Claude repaired it; re-check next run to confirm
# "failed"       — Claude returned no output; retry next run
# "timeout"      — claude -p timed out once; retry ONE more time
# "needs_review" — timed out on retry; skip and flag for user intervention
# "skipped"      — only non-repairable issues; skip indefinitely (manual fix needed)
# ---------------------------------------------------------------------------


def _rel(path: Path) -> str:
    """Return path relative to VAULT_ROOT as a string key."""
    return str(path.relative_to(vault_common.VAULT_ROOT))


def load_state() -> dict:
    """Load doctor_state.json, returning empty structure if missing/corrupt."""
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"last_run": None, "notes": {}}


def save_state(state: dict) -> None:
    """Write doctor_state.json atomically."""
    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


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


def is_process_running(pid: int) -> bool:
    """Return True if a process with *pid* is currently running."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # process exists; we lack permission to signal it


def _write_pid(state: dict) -> None:
    """Write *state* (including pid) to the state file immediately."""
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _release_pid() -> None:
    """Clear our pid from the state file at process exit."""
    try:
        current = load_state()
        if current.get("pid") == os.getpid():
            current.pop("pid", None)
            tmp = STATE_FILE.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
            tmp.replace(STATE_FILE)
    except Exception:  # noqa: BLE001
        pass  # best-effort cleanup


# ---------------------------------------------------------------------------
# Stale file auto-commit
# ---------------------------------------------------------------------------


def commit_stale_files(dry_run: bool = False) -> list[Path]:
    """Stage and commit uncommitted vault files whose mtime is older than STALE_COMMIT_MINUTES.

    Skips deleted files (no mtime to check) and respects the git.auto_commit
    config flag.  Returns the list of paths that were (or would be) committed.
    Does nothing when the vault has no .git directory.
    """
    git_marker = vault_common.VAULT_ROOT / ".git"
    if not (git_marker.is_dir() or git_marker.is_file()):
        return []

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-u"],
            cwd=str(vault_common.VAULT_ROOT),
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
        path = vault_common.VAULT_ROOT / filepath_part.strip()
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
    )
    return stale if committed else []


# ---------------------------------------------------------------------------
# Wikilink resolution
# ---------------------------------------------------------------------------


def build_note_map(notes: list[Path]) -> dict[str, list[Path]]:
    """Return stem (lowercase) → [paths] for all vault notes."""
    note_map: dict[str, list[Path]] = {}
    for p in notes:
        note_map.setdefault(p.stem.lower(), []).append(p)
    return note_map


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


def check_note(path: Path, note_map: dict[str, list[Path]]) -> list[Issue]:
    """Return a list of Issues found in *path*."""
    issues: list[Issue] = []

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [Issue(path, "error", "READ_ERROR", str(exc))]

    rel = path.relative_to(vault_common.VAULT_ROOT)

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

    # Broken wikilinks anywhere in the document
    for link in re.findall(r"\[\[([^\]]+)\]\]", content):
        clean = link.split("|")[0].split("#")[0].strip()
        if clean and not resolve_wikilink(clean, note_map):
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
    2. Semantic fallback via vault-search — take the top result above min_score
       that isn't exclude_path.
    Returns None if no match is found (caller should remove the link).
    """
    # 1. Exact match (case-insensitive stem)
    key = link_text.lower().strip()
    matches = note_map.get(key, [])
    if len(matches) == 1:
        return matches[0].stem
    # Multiple exact matches — ambiguous, fall through to semantic search

    # 2. Semantic fallback
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    try:
        result = subprocess.run(
            [
                "vault-search",
                link_text,
                "--json",
                "--top=2",
                f"--min-score={min_score}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
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
    related_pattern = re.compile(
        r'^(related:\s*)(\[.*?\])\s*$', re.MULTILINE
    )
    related_match = related_pattern.search(content)
    if related_match:
        prefix = related_match.group(1)
        raw_list = related_match.group(2)
        # Parse individual quoted wikilink entries: "[[stem]]"
        entries = re.findall(r'"(\[\[[^\]]+\]\])"', raw_list)
        new_entries: list[str] = []
        for entry in entries:
            m = re.match(r'\[\[([^\]]+)\]\]', entry)
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

        if new_entries:
            new_related_line = f'{prefix}[{", ".join(new_entries)}]'
        else:
            # All links removed — check if we can inject semantic candidates
            became_orphan = True
            candidates = _find_semantic_candidates(path)
            if candidates:
                candidate_entries = [f'"[[{s}]]"' for s in candidates]
                new_related_line = f'{prefix}[{", ".join(candidate_entries)}]'
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

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)  # permit nested invocation

    try:
        result = subprocess.run(
            ["vault-search", query, "--json", f"--top={top_k + 1}"],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
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


def repair_note(
    path: Path,
    issues: list[Issue],
    model: str = DEFAULT_MODEL,
    timeout: int = AI_TIMEOUT,
) -> tuple[str | None, str]:
    """Call Claude *model* to fix *issues* in *path*.

    Returns (fixed_content_or_None, status) where status is one of
    "fixed", "failed", or "timeout".
    """
    content = path.read_text(encoding="utf-8")
    rel = path.relative_to(vault_common.VAULT_ROOT)
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

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)  # permit nested claude invocation

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", model],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            # Strip accidental markdown fences if Claude added them
            output = re.sub(r"^```[a-z]*\n?", "", output)
            output = re.sub(r"\n?```$", "", output)
            if output:
                return output, "fixed"
        return None, "failed"
    except subprocess.TimeoutExpired:
        print("  (timeout)", flush=True)
        return None, "timeout"
    except FileNotFoundError:
        print("  (claude CLI not found)", flush=True)
        return None, "failed"


# ---------------------------------------------------------------------------
# Parallel repair worker
# ---------------------------------------------------------------------------


def _repair_one(
    note_path: Path,
    note_issues: list[Issue],
    model: str,
    state: dict,
    today_str: str,
    lock: threading.Lock,
    timeout: int = AI_TIMEOUT,
    note_map: dict[str, list[Path]] | None = None,
) -> bool:
    """Repair one note, update state under *lock*, return True on success."""
    key = _rel(note_path)
    rel = note_path.relative_to(vault_common.VAULT_ROOT)
    repairable = [i for i in note_issues if i.code in REPAIRABLE_CODES]
    broken = [i for i in repairable if i.code == "BROKEN_WIKILINK"]
    other = [i for i in repairable if i.code != "BROKEN_WIKILINK"]

    with lock:
        prev_status = state.get("notes", {}).get(key, {}).get("status", "")

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
    elif broken:
        # Only broken wikilinks — no Claude call needed
        repair_status = "fixed" if link_fix_made else "failed"

    if fixed_content:
        icon = "✓"
    elif link_fix_made and not other:
        # Broken links fixed by Python, no Claude needed
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

    return fixed_content is not None or link_fix_made


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Vault Doctor — find and optionally repair vault note issues",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "notes",
        nargs="*",
        type=Path,
        help="Specific notes to check (default: all vault notes)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Apply Claude-suggested repairs (writes files)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report issues only; do not call Claude",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        metavar="MODEL",
        help=f"Claude model for repairs (default: {DEFAULT_MODEL})",
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
    args = parser.parse_args()

    # Load persistent state
    state = load_state() if not args.no_state else {"last_run": None, "notes": {}}

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
    _write_pid(state)  # claim the lock immediately
    atexit.register(_release_pid)  # release on any exit path

    # Auto-commit uncommitted vault files older than STALE_COMMIT_MINUTES
    stale = commit_stale_files(dry_run=args.dry_run)
    if stale:
        rel_stale = [str(p.relative_to(vault_common.VAULT_ROOT)) for p in stale]
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
        target_notes = list(vault_common.all_vault_notes())
        explicit = False

    # Always skip the auto-generated vault index — rebuilt by update_index.py
    vault_claude_md = vault_common.VAULT_ROOT / "CLAUDE.md"
    target_notes = [p for p in target_notes if p != vault_claude_md]

    # Skip notes that have already been processed and are still fresh
    if not explicit and not args.no_state:
        before = len(target_notes)
        target_notes = [p for p in target_notes if not should_skip(_rel(p), state)]
        skipped_by_state = before - len(target_notes)
    else:
        skipped_by_state = 0

    print(
        f"Scanning {len(target_notes)} vault notes"
        + (f" ({skipped_by_state} skipped — already OK)" if skipped_by_state else "")
        + "…"
    )

    # Build note map once for wikilink resolution
    all_notes = list(vault_common.all_vault_notes())
    note_map = build_note_map(all_notes)

    # Scan — also record clean notes in state
    issues_by_note: dict[Path, list[Issue]] = {}
    for note in target_notes:
        note_issues = check_note(note, note_map)
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
            save_state(state)
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
        rel = note_path.relative_to(vault_common.VAULT_ROOT)
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
        print(
            "No repairable issues (flat daily notes require manual fixes)."
        )
        save_state(state)
        return

    if not args.fix:
        print(
            f"{len(repair_candidates)} note(s) have repairable issues.\n"
            f"Run with --fix to repair them via Claude ({args.model})."
        )
        save_state(state)
        return

    # Apply repairs
    limit = args.limit if args.limit > 0 else len(repair_candidates)
    jobs = max(1, args.jobs)
    repaired = 0
    failed = 0
    lock = threading.Lock()

    print(
        f"Repairing up to {limit} note(s) via {args.model} ({jobs} parallel job(s), {args.timeout}s timeout)…\n"
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

    save_state(state)
    leftover = len(repair_candidates) - limit
    print(
        f"\nDone: {repaired} repaired, {failed} failed, {leftover} not yet processed."
    )

    if repaired:
        print("\nRun update_index.py to rebuild the vault index after repairs.")


if __name__ == "__main__":
    main()
