#!/usr/bin/env python3
"""vault-merge — merge two vault notes into one.

Thin re-export shim over the ``cli.merge`` package (ARC-005). The bulk of
the original 1,179-line God module — AI-output validation, note lookup,
frontmatter field parsing, dry-run preview cache, execute-path locking,
the diff summary, embedding-based duplicate scan, and the post-merge index
rebuild — has moved into focused submodules under ``cli.merge``; the
public + private surface the original exposed remains importable from
``vault_merge`` so every ``import vault_merge`` consumer, the test suite's
attribute access (``vault_merge._ai_merge_bodies``,
``vault_merge.AIMergeOutputError``, ``vault_merge.ai_backend``,
``vault_merge._hash_content``, ``vault_merge._merge_notes``, …), and the
``vault-merge = "vault_merge:main"`` console-script entry point keep
working byte-for-byte.

What stays here and why:
    ``AIMergeOutputError``, ``_ai_merge_bodies``, ``_merge_notes``,
    ``_hash_content``, ``_build_frontmatter``, ``_write_preview``,
    ``_load_fresh_preview``, ``_update_wikilinks_in_vault``, and ``main``
    stay defined in this entry shim. ``_merge_notes`` weaves the AI body
    pipeline through ``_ai_merge_bodies`` / ``_hash_content`` /
    ``_build_frontmatter`` via bare-name calls that resolve in this
    module's globals; ``_write_preview`` and ``_load_fresh_preview`` do
    the same with ``_hash_content`` and ``_preview_cache_path``; ``main``
    orchestrates the whole shim-resident pipeline. The test suite patches
    ``vault_merge.ai_backend.run_ai_prompt`` (a module-attribute patch
    that reaches the singleton ``ai_backend`` from any caller), so
    ``ai_backend`` is re-exported here, and ``_ai_merge_bodies`` stays in
    the module the test patches for parity with the vault_search.py split.

Usage:
    vault-merge NOTE_A NOTE_B [--output OUTPUT] [--dry-run] [--execute] [--from-preview]

NOTE_A and NOTE_B can be:
  - Absolute paths to .md files
  - Stem names searched in the vault (case-insensitive)

Without --execute, prints the proposed merged content and exits. When the AI
backend produced the merged body, that body is cached to a per-pair preview
file so a later `--execute --from-preview` can apply the exact reviewed text
without a second (possibly different) AI call.

With --execute, writes the merged note, moves NOTE_B to .trash/, and
updates all wikilinks across the vault. The write/trash/backlink sequence is
guarded by an exclusive, non-blocking lock so two concurrent `--execute`
invocations against the same vault cannot interleave.
"""

import argparse
import contextlib
import hashlib
import json  # noqa: F401 — re-exported (vault_merge.json) for backwards compat
import os  # noqa: F401 — re-exported (vault_merge.os) for backwards compat
import re  # noqa: F401 — re-exported (vault_merge.re) for backwards compat
import shutil  # noqa: F401 — re-exported (vault_merge.shutil) for backwards compat
import sqlite3  # noqa: F401 — re-exported (vault_merge.sqlite3) for backwards compat
import subprocess  # noqa: F401 — re-exported (vault_merge.subprocess) for backwards compat
import sys
from collections.abc import Iterator  # noqa: F401 — re-exported for backwards compat
from pathlib import Path

import ai_backend  # noqa: F401 — re-exported (vault_merge.ai_backend) for tests
import vault_common  # noqa: F401 — re-exported (vault_merge.vault_common) for tests
import vault_config  # noqa: F401 — re-exported (vault_merge.vault_config) for tests
import vault_fs  # noqa: F401 — re-exported (vault_merge.vault_fs) for tests
import vault_links  # noqa: F401 — re-exported (vault_merge.vault_links) for tests
from vault_path import is_path_inside_vault
from prompt_templates import render  # noqa: F401 — re-exported (vault_merge.render)

# ---------------------------------------------------------------------------
# Re-exports from cli.merge.* — every symbol the original vault_merge.py
# exposed remains importable from ``vault_merge``. Function objects are
# immutable so ``from cli.merge.X import f`` + ``vault_merge.f(...)`` is a
# stable binding for external callers and the test suite.
# ---------------------------------------------------------------------------
from cli.merge.ai_helpers import (  # noqa: F401 — re-exports
    _DEFAULT_AI_TIMEOUT,
    _configured_merge_model,
    _configured_merge_timeout,
    _is_valid_merge_body,
)
from cli.merge.frontmatter import (  # noqa: F401 — re-exports
    _WIKILINK_SPAN_RE,
    _parse_related_list,
    _parse_tags_list,
)
from cli.merge.index import _rebuild_index  # noqa: F401 — re-export
from cli.merge.lookup import _find_note  # noqa: F401 — re-export
from cli.merge.preview import (  # noqa: F401 — re-exports
    _MERGE_LOCK_FILENAME,
    _PREVIEW_DIRNAME,
    _delete_preview,
    _merge_lock,
    _preview_cache_path,
    _preview_dir,
)
from cli.merge.scan import (  # noqa: F401 — re-exports
    _DEFAULT_SCAN_THRESHOLD,
    _DEFAULT_SCAN_TOP,
    _is_excluded_from_scan,
    _scan_duplicates,
)
from cli.merge.display import _print_diff_summary  # noqa: F401 — re-export


class AIMergeOutputError(RuntimeError):
    """AI backend returned output that is not a valid merged note body.

    Raised instead of silently accepting refusal/error text: writing such
    output over the keeper note (and then trashing note B) is destructive.
    ``main()`` catches this before any file is written or trashed.
    """


def _ai_merge_bodies(
    path_a: Path, path_b: Path, title: str, vault_path: Path | None = None
) -> str | None:
    """Use the configured prompt AI backend to intelligently merge two note bodies.

    SEC-115: both note bodies are inlined in the prompt itself, delimited
    as ``<note_a>`` / ``<note_b>`` and labelled untrusted. The previous
    implementation told the tool-enabled child agent to "Read both files",
    handing it filesystem access over content that is itself AI-generated
    from transcripts. Inlining (matching ``vault_conflicts.py``) closes
    that surface: the child has no need for filesystem tools at all.

    Args:
        path_a: Path to the primary note file.
        path_b: Path to the note being merged in.
        title: Title of the merged note (for context).
        vault_path: Path to the vault root.

    Returns:
        The merged body text, or None when the backend is unavailable
        (caller should fall back to naive concatenation).

    Raises:
        AIMergeOutputError: The backend returned output that is not a valid
            note body (e.g. a refusal or error message). The merge must be
            aborted — not concatenated silently — so the caller can leave
            both notes untouched.
    """
    # SEC-115: inline both bodies so no filesystem access is needed.
    try:
        body_a = vault_common.get_body(path_a.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        body_a = ""
    try:
        body_b = vault_common.get_body(path_b.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        body_b = ""

    prompt = render(
        "merge-notes",
        title=title,
        body_a=body_a,
        body_b=body_b,
    )

    merged = ai_backend.run_ai_prompt(
        prompt,
        model=_configured_merge_model(vault_path),
        model_tier="large",
        timeout=_configured_merge_timeout(vault_path),
        purpose="vault-merge",
        vault=vault_path,
    )
    if not merged:
        return None

    if not _is_valid_merge_body(merged):
        preview = merged.strip()[:200]
        print(
            "Error: AI merge output does not look like a note body "
            f"(refusal or error text?). First 200 chars:\n{preview}",
            file=sys.stderr,
        )
        raise AIMergeOutputError(
            "AI backend returned invalid merge output; merge aborted"
        )
    return merged


def _merge_notes(
    path_a: Path,
    content_a: str,
    path_b: Path,
    content_b: str,
    *,
    no_ai: bool = False,
    vault_path: Path | None = None,
    precomputed_ai_body: str | None = None,
    ai_body_out: dict[str, str] | None = None,
) -> str:
    """Produce merged note content from two vault notes.

    Merge strategy:
    - Tags: union of both tag lists (sorted, deduplicated)
    - Type: NOTE_A's type
    - Project: NOTE_A's project if set, else NOTE_B's
    - Related: union, deduplicated
    - Body: AI-merged (intelligently deduplicated), with naive concatenation
      fallback if AI is unavailable or ``no_ai`` is True
    - Title: NOTE_A's title (from heading or stem)

    Args:
        path_a: Path to note A (used for stem/title fallback).
        content_a: Full content of note A.
        path_b: Path to note B (used for stem/title fallback).
        content_b: Full content of note B.
        no_ai: Skip AI merge and use naive concatenation.
        vault_path: Path to the vault root.
        precomputed_ai_body: Reuse this text instead of calling the AI
            backend (e.g. a cached dry-run preview). Only takes effect when
            ``no_ai`` is False and NOTE_B has a body — same condition under
            which a fresh AI call would otherwise be made.
        ai_body_out: Optional out-param. When the merge body comes from the
            AI backend (fresh or via ``precomputed_ai_body``), the raw body
            (before the "merged from" comment is appended) is stored at
            ``ai_body_out["body"]`` so callers can cache it for later reuse.

    Returns:
        Full merged note content including frontmatter and body.
    """
    fm_a = vault_common.parse_frontmatter(content_a)
    fm_b = vault_common.parse_frontmatter(content_b)
    body_a = vault_common.get_body(content_a).strip()
    body_b = vault_common.get_body(content_b).strip()

    # Tags: union, sorted
    tags_a = _parse_tags_list(fm_a)
    tags_b = _parse_tags_list(fm_b)
    merged_tags = sorted(set(tags_a) | set(tags_b))

    # Related: union, deduplicated. Do NOT add a [[B]] backlink — B is about to
    # be trashed, so [[B]] would be a broken link, and the vault-wide
    # [[B]]→[[A]] rewrite would turn it into a self-reference inside A. The
    # body's "merged from" comment already records provenance. Also drop any
    # entry that self-references A's own stem.
    self_link = f"[[{path_a.stem}]]".lower()
    trash_link = f"[[{path_b.stem}]]".lower()
    related_a = _parse_related_list(fm_a)
    related_b = _parse_related_list(fm_b)
    seen: set[str] = set()
    merged_related: list[str] = []
    for r in related_a + related_b:
        r_norm = r.strip().lower()
        # Drop self-references (to A) and references to the trashed note (B),
        # which would be broken once B is removed.
        if not r_norm or r_norm == self_link or r_norm == trash_link or r_norm in seen:
            continue
        seen.add(r_norm)
        merged_related.append(r)

    merged_fm: dict = {}
    merged_fm["date"] = fm_a.get("date") or fm_b.get("date") or ""
    merged_fm["type"] = fm_a.get("type") or fm_b.get("type") or ""
    merged_fm["tags"] = merged_tags
    project_a = fm_a.get("project", "")
    project_b = fm_b.get("project", "")
    merged_fm["project"] = project_a if project_a else project_b
    merged_fm["confidence"] = (
        fm_a.get("confidence") or fm_b.get("confidence") or "medium"
    )
    merged_fm["sources"] = fm_a.get("sources", [])
    merged_fm["related"] = merged_related
    merged_fm["provenance"] = (
        fm_a.get("provenance") or fm_b.get("provenance") or "inferred"
    )

    title_a = vault_common.extract_title(content_a, path_a.stem)
    title_b = vault_common.extract_title(content_b, path_b.stem)

    # Try AI merge for intelligent deduplication
    merged_body: str | None = None
    if not no_ai and body_b:
        merged_body = (
            precomputed_ai_body
            if precomputed_ai_body is not None
            else _ai_merge_bodies(path_a, path_b, title_a, vault_path=vault_path)
        )
        if merged_body:
            if ai_body_out is not None:
                ai_body_out["body"] = merged_body
            # Add a comment noting the merge source
            merged_body += f"\n\n<!-- merged from: {title_b} ({path_b.name}) -->"

    # Fallback: naive concatenation
    if merged_body is None:
        merged_body = body_a
        if body_b:
            separator_comment = f"<!-- merged from: {title_b} ({path_b.name}) -->"
            merged_body += f"\n\n---\n\n{separator_comment}\n\n{body_b}"

    return _build_frontmatter(merged_fm) + "\n" + merged_body + "\n"


def _build_frontmatter(fm: dict) -> str:
    """Serialise a frontmatter dict back to a YAML block string.

    Args:
        fm: Dict with frontmatter fields.

    Returns:
        ``---\\n...\\n---\\n`` YAML frontmatter block.
    """
    lines: list[str] = ["---"]
    for key in (
        "date",
        "type",
        "tags",
        "project",
        "confidence",
        "sources",
        "related",
        "provenance",
        "session_id",
    ):
        if key not in fm:
            continue
        value = fm[key]
        if value is None or value == "" or value == [] or value == {}:
            continue
        if key in ("tags", "sources", "related") and isinstance(value, list):
            # Inline quoted array format: ["[[a]]", "[[b]]"]
            # SEC-033: escape embedded quotes/backslashes so a value like
            # the 'who said "x"' note cannot break the YAML line.
            items_str = ", ".join(
                f'"{str(v).replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
                for v in value
            )
            lines.append(f"{key}: [{items_str}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _hash_content(content: str) -> str:
    """Return the sha256 hex digest of note content, for staleness checks."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _write_preview(
    vault_path: Path,
    path_a: Path,
    content_a: str,
    path_b: Path,
    content_b: str,
    ai_body: str,
) -> Path:
    """Persist a dry-run's AI-merged body for later ``--execute --from-preview`` reuse."""
    cache_path = _preview_cache_path(vault_path, path_a, path_b)
    payload = {
        "hash_a": _hash_content(content_a),
        "hash_b": _hash_content(content_b),
        "body": ai_body,
    }
    tmp_path = cache_path.with_name(cache_path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload), encoding="utf-8")
    tmp_path.replace(cache_path)
    return cache_path


def _load_fresh_preview(
    vault_path: Path,
    path_a: Path,
    content_a: str,
    path_b: Path,
    content_b: str,
) -> str | None:
    """Return the cached AI body for (path_a, path_b) if both hashes still match.

    Returns None when no preview exists, it is unreadable, or either source
    note has changed since the preview was generated.
    """
    cache_path = _preview_cache_path(vault_path, path_a, path_b)
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("hash_a") != _hash_content(content_a) or payload.get(
        "hash_b"
    ) != _hash_content(content_b):
        return None
    body = payload.get("body")
    if not isinstance(body, str):
        return None
    # SEC-013: the preview cache is a file on disk (.merge_previews/); a
    # tampered or corrupt cache must not bypass the validation the fresh AI
    # path enforces. An invalid cached body is discarded (None) so the merge
    # falls back to a fresh AI call or naive concatenation.
    if not _is_valid_merge_body(body):
        return None
    return body


# ---------------------------------------------------------------------------
# Wikilink update
# ---------------------------------------------------------------------------

# Note: this regex was defined at the original module top but unused there
# (``_update_wikilinks_in_vault`` builds its pattern inline). Preserved
# verbatim for backwards-compat with any external reader that imported it.
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:[|#][^\]]*)?\]\]")


def _update_wikilinks_in_vault(old_stem: str, new_stem: str, vault_path: Path) -> int:
    """Replace all wikilinks referencing old_stem with new_stem across the vault.

    Only rewrites files that actually contain the old wikilink.

    In the keeper note itself (stem == new_stem), rewriting [[old]]→[[new]]
    would create a self-referencing wikilink, but skipping the file would
    leave a dangling link to the trashed note. Instead the link is unwrapped
    to its display text (the alias if present, else old_stem) so the prose
    stays readable with no broken link.

    Text inside fenced code blocks and inline code spans is left untouched
    (see ``vault_links.sub_wikilinks_outside_code``), so notes documenting
    wikilink syntax in an example are not corrupted by the rewrite.

    Args:
        old_stem: Stem name being replaced.
        new_stem: Stem name to use instead.
        vault_path: Path to the vault root.

    Returns:
        Number of files updated.
    """
    updated = 0
    old_pattern = re.compile(
        r"\[\[" + re.escape(old_stem) + r"((?:[|#][^\]]*)?)\]\]",
        re.IGNORECASE,
    )
    replacement = f"[[{new_stem}\\1]]"
    new_stem_lower = new_stem.lower()

    def _unwrap_to_display_text(m: re.Match[str]) -> str:
        suffix = m.group(1)
        if suffix.startswith("|"):
            return suffix[1:]
        return old_stem

    for path in vault_common.all_vault_notes_walk(vault=vault_path):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(
                f"Warning: skipping unreadable file during wikilink update: {path} ({exc})",
                file=sys.stderr,
            )
            continue
        if path.stem.lower() == new_stem_lower:
            new_content, n = vault_links.sub_wikilinks_outside_code(
                content, old_pattern, _unwrap_to_display_text
            )
        else:
            new_content, n = vault_links.sub_wikilinks_outside_code(
                content, old_pattern, replacement
            )
        if n > 0:
            # QA-010: atomic_write so an interrupt during backlink rewrite
            # cannot truncate a note that is merely a link target of the
            # merge (collateral damage the user never asked for). Matches the
            # repo's otherwise-consistent atomic-write discipline.
            vault_fs.atomic_write_text(path, new_content)
            updated += 1
    return updated


def main() -> None:
    """Parse CLI arguments and perform (or preview) the note merge.

    Raises:
        SystemExit: On invalid arguments or after completion.
    """
    parser = argparse.ArgumentParser(
        prog="vault-merge",
        description="Merge two vault notes into one, or scan for near-duplicate pairs.",
    )
    parser.add_argument(
        "--vault",
        "-V",
        metavar="VAULT",
        default=None,
        help="Use a specific vault (path or named vault).",
    )
    parser.add_argument(
        "note_a",
        metavar="NOTE_A",
        nargs="?",
        help="Path or stem of the primary note (kept after merge). Omit when using --scan.",
    )
    parser.add_argument(
        "note_b",
        metavar="NOTE_B",
        nargs="?",
        help="Path or stem of the note to merge into NOTE_A (moved to .trash/). Omit when using --scan.",
    )
    parser.add_argument(
        "--output",
        metavar="OUTPUT",
        default=None,
        help="Write merged note here (default: NOTE_A's path).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print merged content without writing anything.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write merged note, move NOTE_B to .trash/, update wikilinks.",
    )
    parser.add_argument(
        "--from-preview",
        action="store_true",
        help=(
            "With --execute, reuse the cached AI-merged body from a prior "
            "dry-run if both source notes are unchanged, instead of calling "
            "the AI backend again."
        ),
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan all vault notes for near-duplicate pairs using embedding similarity.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=_DEFAULT_SCAN_THRESHOLD,
        metavar="SCORE",
        help=f"Minimum similarity score for --scan (default: {_DEFAULT_SCAN_THRESHOLD}).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=_DEFAULT_SCAN_TOP,
        metavar="N",
        help=f"Maximum number of pairs to report in --scan (default: {_DEFAULT_SCAN_TOP}).",
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="Skip rebuilding the vault index after a successful merge.",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Skip AI-powered content deduplication; use naive concatenation.",
    )
    args = parser.parse_args()

    # Resolve vault path
    vault_path = vault_common.resolve_vault(explicit=args.vault, cwd=os.getcwd())
    vault_common.apply_configured_env_defaults(vault=vault_path)

    # QA-001: Replace module-level VAULT_ROOT with try/finally restore pattern
    original_vault_root = vault_common.VAULT_ROOT
    vault_common.VAULT_ROOT = vault_path
    # ARC-001: clear caches so lru_cache-memoized load_config() and
    # resolve_vault() observe the new VAULT_ROOT instead of stale values.
    vault_common.load_config.cache_clear()  # type: ignore[attr-defined]
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]

    try:
        # --scan mode: find near-duplicate pairs across the whole vault
        if args.scan:
            _scan_duplicates(
                threshold=args.threshold, top=args.top, vault_path=vault_path
            )
            return

        # Require NOTE_A and NOTE_B when not scanning
        if not args.note_a or not args.note_b:
            parser.error("NOTE_A and NOTE_B are required unless --scan is used.")

        # Resolve notes. SEC-011: LookupError means the query resolved to an
        # existing path outside the vault — refuse rather than merge it.
        try:
            path_a = _find_note(args.note_a, vault_path)
        except LookupError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        if path_a is None:
            print(f"Error: note not found: {args.note_a}", file=sys.stderr)
            sys.exit(1)

        try:
            path_b = _find_note(args.note_b, vault_path)
        except LookupError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        if path_b is None:
            print(f"Error: note not found: {args.note_b}", file=sys.stderr)
            sys.exit(1)

        if path_a.resolve() == path_b.resolve():
            print("Error: NOTE_A and NOTE_B are the same file.", file=sys.stderr)
            sys.exit(1)

        # Only the mutating (--execute, non-dry-run) path needs to serialize
        # against other invocations; a preview is read-only w.r.t. the vault
        # notes themselves (it only ever writes to its own cache file).
        is_execute = args.execute and not args.dry_run
        lock_cm: contextlib.AbstractContextManager[None] = (
            _merge_lock(vault_path) if is_execute else contextlib.nullcontext()
        )
        with lock_cm:
            content_a = path_a.read_text(encoding="utf-8")
            content_b = path_b.read_text(encoding="utf-8")

            # Show diff summary
            _print_diff_summary(
                path_a, content_a, path_b, content_b, vault_path=vault_path
            )

            precomputed_ai_body: str | None = None
            if is_execute and args.from_preview:
                precomputed_ai_body = _load_fresh_preview(
                    vault_path, path_a, content_a, path_b, content_b
                )
                if precomputed_ai_body is not None:
                    print(
                        "Reusing cached preview merge from a prior dry-run "
                        "(source notes unchanged) — skipping AI call."
                    )
                else:
                    print(
                        "No matching cached preview for this pair (or a "
                        "source note changed since the preview) — falling "
                        "back to a fresh AI merge.",
                        file=sys.stderr,
                    )

            # Build merged content
            ai_body_out: dict[str, str] = {}
            try:
                merged = _merge_notes(
                    path_a,
                    content_a,
                    path_b,
                    content_b,
                    no_ai=args.no_ai,
                    vault_path=vault_path,
                    precomputed_ai_body=precomputed_ai_body,
                    ai_body_out=ai_body_out,
                )
            except AIMergeOutputError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                print(
                    "Merge aborted — no files were modified. "
                    "Re-run with --no-ai to merge by concatenation.",
                    file=sys.stderr,
                )
                sys.exit(1)

            if args.dry_run or not args.execute:
                print("=== Proposed merged content ===\n")
                print(merged)
                if "body" in ai_body_out:
                    _write_preview(
                        vault_path,
                        path_a,
                        content_a,
                        path_b,
                        content_b,
                        ai_body_out["body"],
                    )
                    print(
                        "\nPreview cached — re-run with "
                        "--execute --from-preview to apply this exact merge "
                        "without another AI call."
                    )
                if not args.execute:
                    print("(dry-run — pass --execute to apply changes)")
                return

            # --execute: write merged note via sibling tmp + atomic replace so a
            # kill mid-write can never leave the keeper truncated. NOTE_B is only
            # trashed after the replace succeeds.
            # SEC-011: --output must land inside the vault.
            output_path = Path(args.output).expanduser() if args.output else path_a
            if not is_path_inside_vault(output_path, vault_path):
                print(
                    f"Error: --output path is outside the vault: {args.output}",
                    file=sys.stderr,
                )
                sys.exit(1)
            tmp_path = output_path.with_name(output_path.name + ".tmp")
            try:
                tmp_path.write_text(merged, encoding="utf-8")
                tmp_path.replace(output_path)
            except OSError:
                tmp_path.unlink(missing_ok=True)
                raise
            print(f"Merged note written to: {output_path}")

            # Move NOTE_B to .trash/
            trash_dir = vault_path / ".trash"
            trash_dir.mkdir(exist_ok=True)
            trash_dest = trash_dir / path_b.name
            # Avoid clobbering existing trash file
            if trash_dest.exists():
                suffix = 1
                while (trash_dir / f"{path_b.stem}.{suffix}{path_b.suffix}").exists():
                    suffix += 1
                trash_dest = trash_dir / f"{path_b.stem}.{suffix}{path_b.suffix}"
            shutil.move(str(path_b), str(trash_dest))
            print(f"Moved {path_b.name} to .trash/")

            # Update wikilinks
            n_updated = _update_wikilinks_in_vault(
                path_b.stem, output_path.stem, vault_path
            )
            if n_updated:
                print(
                    f"Updated wikilinks in {n_updated} file(s): {path_b.stem} → {output_path.stem}"
                )

            # This pair's cached preview (if any) has now been applied.
            _delete_preview(vault_path, path_a, path_b)

            # Commit
            vault_common.git_commit_vault(
                f"refactor(vault): merge {path_b.stem} into {output_path.stem}",
                vault=vault_path,
            )

            # Rebuild index
            if not args.no_index:
                _rebuild_index()

    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(0)
    finally:
        vault_common.VAULT_ROOT = original_vault_root
        # ARC-001: flush caches on restore so subsequent code sees the original vault.
        vault_common.load_config.cache_clear()  # type: ignore[attr-defined]
        vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]


if __name__ == "__main__":  # pragma: no cover — entry shim
    main()
