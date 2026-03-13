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
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
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
AI_TIMEOUT = 30  # seconds


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


def repair_note(path: Path, issues: list[Issue], model: str = DEFAULT_MODEL) -> str | None:
    """Call Claude *model* to fix *issues* in *path*.  Returns fixed content or None."""
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
                return output
    except subprocess.TimeoutExpired:
        print("  (timeout)", flush=True)
    except FileNotFoundError:
        print("  (claude CLI not found)", flush=True)

    return None


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
    args = parser.parse_args()

    # Resolve target notes
    if args.notes:
        target_notes = [Path(n).resolve() for n in args.notes]
    else:
        target_notes = list(vault_common.all_vault_notes())
        print(f"Scanning {len(target_notes)} vault notes…")

    # Build note map once for wikilink resolution
    all_notes = list(vault_common.all_vault_notes())
    note_map = build_note_map(all_notes)

    # Scan
    issues_by_note: dict[Path, list[Issue]] = {}
    for note in target_notes:
        note_issues = check_note(note, note_map)
        if args.errors_only:
            note_issues = [i for i in note_issues if i.severity == "error"]
        if note_issues:
            issues_by_note[note] = note_issues

    if not issues_by_note:
        print("✓ No issues found.")
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

    # Determine repair candidates (only codes Claude can meaningfully fix)
    repair_candidates = [
        (p, iv)
        for p, iv in issues_by_note.items()
        if any(i.code in REPAIRABLE_CODES for i in iv)
    ]

    if not repair_candidates:
        print("No repairable issues (broken wikilinks and flat daily notes require manual fixes).")
        return

    if not args.fix:
        print(
            f"{len(repair_candidates)} note(s) have repairable issues.\n"
            f"Run with --fix to repair them via Claude ({args.model})."
        )
        return

    # Apply repairs
    limit = args.limit if args.limit > 0 else len(repair_candidates)
    repaired = 0
    failed = 0

    print(f"Repairing up to {limit} note(s) via {args.model}…\n")
    for note_path, note_issues in repair_candidates[:limit]:
        rel = note_path.relative_to(vault_common.VAULT_ROOT)
        repairable = [i for i in note_issues if i.code in REPAIRABLE_CODES]
        print(f"  {rel} ({len(repairable)} issue(s))… ", end="", flush=True)
        fixed = repair_note(note_path, repairable, args.model)
        if fixed:
            note_path.write_text(fixed + "\n", encoding="utf-8")
            print("✓")
            repaired += 1
        else:
            print("✗")
            failed += 1

    print(f"\nDone: {repaired} repaired, {failed} failed, {len(repair_candidates) - limit} skipped.")

    if repaired:
        print("\nRun update_index.py to rebuild the vault index after repairs.")


if __name__ == "__main__":
    main()
