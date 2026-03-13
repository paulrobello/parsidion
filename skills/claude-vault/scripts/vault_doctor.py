#!/usr/bin/env python3
"""vault_doctor.py — Scan vault notes for issues; optionally repair via Claude haiku.

Stdlib-only. Run with:
    uv run --no-project ~/.claude/skills/claude-vault/scripts/vault_doctor.py
    uv run --no-project ... --fix          # apply Claude-suggested repairs
    uv run --no-project ... --dry-run      # show issues only, no Claude calls
    uv run --no-project ... note.md ...    # scan specific notes only
    uv run --no-project ... --limit 10     # cap repairs at N notes
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import vault_common  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_TYPES = frozenset(
    {"pattern", "debugging", "research", "project", "daily", "tool", "language", "framework"}
)
# Fields required for all notes
REQUIRED_FIELDS_ALL = ("date", "type")
# Additional fields required for knowledge notes (not daily)
REQUIRED_FIELDS_KNOWLEDGE = ("confidence", "related")
REPAIRABLE_CODES = frozenset(
    {"MISSING_FRONTMATTER", "MISSING_FIELD", "INVALID_TYPE", "INVALID_DATE", "ORPHAN_NOTE"}
)
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
AI_TIMEOUT = 60  # seconds
STATE_FILE = vault_common.VAULT_ROOT / "doctor_state.json"
STATE_STALE_DAYS = 7  # re-check "ok" notes after this many days


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
#   "notes": {
#     "Research/foo.md": {
#       "status": "ok" | "fixed" | "failed" | "timeout" | "skipped",
#       "last_checked": "YYYY-MM-DD",
#       "issues": ["CODE", ...]     # issue codes found (empty = clean)
#     }
#   }
# }
# "ok"      — no issues found; skip for STATE_STALE_DAYS before re-checking
# "fixed"   — Claude repaired it; re-check next run to confirm
# "failed"  — Claude returned no output; retry next run
# "timeout" — claude -p timed out; retry next run
# "skipped" — only non-repairable issues; skip indefinitely (manual fix needed)
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
    if status == "skipped":
        return True
    if status == "ok":
        last = entry.get("last_checked", "")
        try:
            checked = date.fromisoformat(last)
            return (date.today() - checked).days < STATE_STALE_DAYS
        except ValueError:
            return False
    return False  # "fixed", "failed", "timeout" — always retry


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
            Issue(path, "error", "MISSING_FRONTMATTER", "No YAML frontmatter block found")
        )
        # Can't check field-level issues without frontmatter
        return issues

    # Required fields
    note_type_raw = fm.get("type", "")
    is_daily = note_type_raw == "daily" or parts[0] == "Daily"
    required = REQUIRED_FIELDS_ALL if is_daily else REQUIRED_FIELDS_ALL + REQUIRED_FIELDS_KNOWLEDGE
    for fname in required:
        val = fm.get(fname)
        if val is None or val == "" or val == [] or val == "[]":
            issues.append(
                Issue(path, "error", "MISSING_FIELD", f"Required field '{fname}' is absent or empty")
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
            Issue(path, "warning", "INVALID_DATE", f"date '{date_val}' is not YYYY-MM-DD")
        )

    # Orphan check — related must contain at least one [[wikilink]] (not for daily notes)
    if not is_daily:
        related = fm.get("related", [])
        related_str = str(related)
        if not re.search(r"\[\[.+?\]\]", related_str):
            issues.append(
                Issue(
                    path, "warning", "ORPHAN_NOTE", "No [[wikilinks]] in 'related' field (orphan note)"
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


def repair_note(path: Path, issues: list[Issue], model: str = DEFAULT_MODEL) -> tuple[str | None, str]:
    """Call Claude *model* to fix *issues* in *path*.

    Returns (fixed_content_or_None, status) where status is one of
    "fixed", "failed", or "timeout".
    """
    content = path.read_text(encoding="utf-8")
    rel = path.relative_to(vault_common.VAULT_ROOT)
    issue_lines = "\n".join(f"  - [{i.severity.upper()}] {i.code}: {i.message}" for i in issues)

    prompt = f"""You are a vault note repair tool. Fix ONLY the listed issues in this Obsidian markdown note.
Do NOT rewrite, summarise, or add content beyond what is needed to resolve each issue.
Return ONLY the corrected note — no explanation, no code fences.

File: {rel}

Issues to fix:
{issue_lines}

Rules:
- Valid values for 'type': {', '.join(sorted(VALID_TYPES))}
- Valid values for 'confidence': high | medium | low
- 'date' must be YYYY-MM-DD
- 'related' must contain at least one [[wikilink]] to a related concept
- Every note needs: date, type, confidence, related in its YAML frontmatter
- 'sources' should be [] if unknown

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
            timeout=AI_TIMEOUT,
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
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Vault Doctor — find and optionally repair vault note issues",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "notes", nargs="*", type=Path, help="Specific notes to check (default: all vault notes)"
    )
    parser.add_argument(
        "--fix", action="store_true", help="Apply Claude-suggested repairs (writes files)"
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
    args = parser.parse_args()

    # Load persistent state
    state = load_state() if not args.no_state else {"last_run": None, "notes": {}}
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
    total_errors = sum(1 for iv in issues_by_note.values() for i in iv if i.severity == "error")
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
        print("No repairable issues (broken wikilinks and flat daily notes require manual fixes).")
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
    repaired = 0
    failed = 0

    print(f"Repairing up to {limit} note(s) via {args.model}…\n")
    for note_path, note_issues in repair_candidates[:limit]:
        key = _rel(note_path)
        rel = note_path.relative_to(vault_common.VAULT_ROOT)
        repairable = [i for i in note_issues if i.code in REPAIRABLE_CODES]
        print(f"  {rel} ({len(repairable)} issue(s))… ", end="", flush=True)
        fixed_content, repair_status = repair_note(note_path, repairable, args.model)
        if fixed_content:
            note_path.write_text(fixed_content + "\n", encoding="utf-8")
            print("✓")
            repaired += 1
        else:
            print("✗")
            failed += 1
        state.setdefault("notes", {})[key] = {
            "status": repair_status,
            "last_checked": today_str,
            "issues": [i.code for i in repairable],
        }

    save_state(state)
    leftover = len(repair_candidates) - limit
    print(f"\nDone: {repaired} repaired, {failed} failed, {leftover} not yet processed.")

    if repaired:
        print("\nRun update_index.py to rebuild the vault index after repairs.")


if __name__ == "__main__":
    main()
