#!/usr/bin/env python3
"""vault-supersede — retire or restore a note via the supersession contract.

Marks NOTE as superseded by REPLACEMENT: writes ``status: superseded`` and
``superseded_by: ["[[replacement]]"]`` into the note's frontmatter and appends
a quotable body line ("> Superseded by [[X]] on YYYY-MM-DD: <reason>") for
human readers. Every retrieval surface (note_index queries, walks, semantic
search, session-start and prompt-submit recall, backlink suggestions,
analytics) excludes retired notes by default; ``vault-search
--include-superseded`` reads them back for explicit history queries.
``--revert`` removes both fields and the body line, restoring retrieval —
non-destructive and reversible by construction.

Dry-run is the default; ``--execute`` applies. After applying, the vault is
committed (``git.auto_commit`` aware) and the note_index rebuilt so the
retirement is live everywhere at once.

Frontmatter is re-serialized canonically (same tradeoff the doctor's
frontmatter repair accepts): YAML comments inside the frontmatter block are
not preserved; every field value is.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import vault_common
from cli.merge.index import _rebuild_index
from cli.merge.lookup import _find_note
from core.vault_index import get_body, parse_frontmatter, serialize_frontmatter
from note_schema import STATUS_SUPERSEDED, validate_status_fields
from vault_path import resolve_vault

_SUPERSEDED_BY_FIELD = "superseded_by"


def _require_resolvable(raw: str, vault: Path) -> Path:
    """Resolve NOTE/REPLACEMENT to a vault note; exit 2 when unresolvable."""
    try:
        resolved = _find_note(raw, vault)
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    if resolved is None:
        got = raw if raw.strip() else "(empty)"
        print(
            f"error: cannot resolve {got!r} to a vault note "
            "(pass a stem or a path inside the vault)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return resolved


def _supersede(
    note: Path,
    replacement: Path,
    reason: str,
    dry_run: bool,
) -> None:
    """Write the retirement fields + body line into *note*."""
    content = note.read_text(encoding="utf-8")
    fm = parse_frontmatter(content)
    fm["status"] = STATUS_SUPERSEDED
    fm[_SUPERSEDED_BY_FIELD] = [f"[[{replacement.stem}]]"]
    errors = validate_status_fields(fm)
    if errors:
        print(
            "error: refusing to write an inconsistent pair: " + "; ".join(errors),
            file=sys.stderr,
        )
        raise SystemExit(2)

    body_line = (
        f"> Superseded by [[{replacement.stem}]] on "
        f"{datetime.now().strftime('%Y-%m-%d')}: {reason}".rstrip()
    )
    print(
        f"would edit frontmatter of {note.name} (status: superseded, "
        f"superseded_by: [[{replacement.stem}]])"
        if dry_run
        else f"editing frontmatter of {note.name} (status: superseded, "
        f"superseded_by: [[{replacement.stem}]])"
    )
    print(f"would append body line: {body_line}" if dry_run else f"append: {body_line}")
    if dry_run:
        return

    new_content = (
        serialize_frontmatter(fm)
        + _strip_supersede_lines(get_body(content))
        + body_line
        + "\n"
    )
    vault_common.atomic_write_text(note, new_content)


def _strip_supersede_lines(body: str) -> str:
    """Drop prior supersession body lines so re-runs do not accumulate them."""
    kept = [
        line for line in body.splitlines() if not line.startswith("> Superseded by [[")
    ]
    return "\n".join(kept).rstrip("\n") + "\n"


def _revert(note: Path, dry_run: bool) -> None:
    """Remove the retirement fields + body lines from *note*."""
    content = note.read_text(encoding="utf-8")
    fm = parse_frontmatter(content)
    fm.pop("status", None)
    fm.pop(_SUPERSEDED_BY_FIELD, None)
    print(
        f"would remove status/superseded_by from {note.name}"
        if dry_run
        else f"removing status/superseded_by from {note.name}"
    )
    if dry_run:
        return
    body = _strip_supersede_lines(get_body(content))
    new_content = serialize_frontmatter(fm) + body
    vault_common.atomic_write_text(note, new_content)


def main() -> None:
    """CLI entry: parse args, apply or preview the retirement/restore."""
    parser = argparse.ArgumentParser(
        prog="vault-supersede",
        description=(
            "Retire a note via the supersession contract "
            "(status: superseded + superseded_by), or restore it with --revert."
        ),
    )
    parser.add_argument(
        "--vault",
        "-V",
        default=None,
        help="Vault path (or registered name). Defaults to resolve_vault().",
    )
    parser.add_argument(
        "note",
        help="The note to retire (or restore with --revert); stem or path.",
    )
    parser.add_argument(
        "replacement",
        nargs="?",
        default=None,
        help="The replacement note the retired note points at (required to retire).",
    )
    parser.add_argument(
        "--reason",
        default="",
        help="Why the note is retired; recorded in the body line.",
    )
    parser.add_argument(
        "--revert",
        action="store_true",
        help="Un-retire: remove status/superseded_by and the body line.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply the change (without this flag the run is a preview).",
    )
    args = parser.parse_args()

    vault = resolve_vault(args.vault)
    note = _require_resolvable(args.note, vault)

    if args.revert:
        if args.replacement:
            print(
                "error: --revert does not take a replacement note",
                file=sys.stderr,
            )
            raise SystemExit(2)
        _revert(note, dry_run=not args.execute)
        if not args.execute:
            return
    else:
        if not args.replacement:
            print(
                "error: a REPLACEMENT note is required (or pass --revert)",
                file=sys.stderr,
            )
            raise SystemExit(2)
        replacement = _require_resolvable(args.replacement, vault)
        if replacement == note:
            print("error: a note cannot supersede itself", file=sys.stderr)
            raise SystemExit(2)
        _supersede(note, replacement, args.reason, dry_run=not args.execute)
        if not args.execute:
            return

    vault_common.git_commit_vault(
        (
            f"docs(vault): revert supersession of {note.stem}"
            if args.revert
            else f"docs(vault): supersede {note.stem} by {args.replacement}"
        ),
        vault=vault,
    )
    _rebuild_index()
    print("done.")


if __name__ == "__main__":
    main()
