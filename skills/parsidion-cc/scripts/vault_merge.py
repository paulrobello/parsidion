#!/usr/bin/env python3
"""vault-merge — merge two vault notes into one.

Usage:
    vault-merge NOTE_A NOTE_B [--output OUTPUT] [--dry-run] [--execute]

NOTE_A and NOTE_B can be:
  - Absolute paths to .md files
  - Stem names searched in the vault (case-insensitive)

Without --execute, prints the proposed merged content and exits.
With --execute, writes the merged note, moves NOTE_B to .trash/, and
updates all wikilinks across the vault.
"""

import argparse
import os
import re
import shutil
import struct
import sqlite3
import subprocess
import sys
from pathlib import Path

import vault_common

_DEFAULT_AI_MODEL: str = vault_common.get_config(
    "defaults", "haiku_model", "claude-haiku-4-5-20251001"
)
_DEFAULT_AI_TIMEOUT: int = 60


# ---------------------------------------------------------------------------
# AI merge
# ---------------------------------------------------------------------------


def _ai_merge_bodies(
    path_a: Path, path_b: Path, title: str, vault_path: Path | None = None
) -> str | None:
    """Use claude to intelligently merge two note bodies into one.

    Passes file paths to Claude so it can read the notes directly, avoiding
    prompt bloat and character limits.

    Args:
        path_a: Path to the primary note file.
        path_b: Path to the note being merged in.
        title: Title of the merged note (for context).
        vault_path: Path to the vault root.

    Returns:
        The merged body text, or None on failure (caller should fall back
        to naive concatenation).
    """
    prompt = (
        "You are a note-merging assistant. Read the two vault notes at the "
        "paths below and merge them into a SINGLE cohesive note.\n\n"
        f"Note A (primary): {path_a}\n"
        f"Note B (to merge in): {path_b}\n"
        f"Topic: {title}\n\n"
        "Rules:\n"
        "- Read both files, then combine all unique information into one unified note\n"
        "- Remove duplicate or near-duplicate content — do NOT repeat the same "
        "information in different words\n"
        "- Preserve all unique details, code snippets, and specific facts\n"
        "- Keep the structure: ## Summary, ## Key Learnings, ## Context (or "
        "whatever headings the notes use)\n"
        "- Use bullet points for Key Learnings (consolidate overlapping bullets)\n"
        "- Output ONLY the merged note body (no frontmatter, no explanation)\n"
        "- Do NOT wrap the output in markdown code fences\n"
        "- Do NOT include any preamble or commentary — output starts with the "
        "first heading"
    )

    model = vault_common.get_config("summarizer", "merge_model", _DEFAULT_AI_MODEL)
    timeout = vault_common.get_config(
        "summarizer", "merge_timeout", _DEFAULT_AI_TIMEOUT
    )

    try:
        result = subprocess.run(
            [
                "claude",
                "-p",
                prompt,
                "--model",
                model,
                "--no-session-persistence",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=vault_common.env_without_claudecode(),
        )
        if result.returncode != 0:
            return None
        merged = result.stdout.strip()
        # Sanity check: AI output should be non-trivial
        if len(merged) < 50:
            return None
        return merged
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        return None


# ---------------------------------------------------------------------------
# Note lookup
# ---------------------------------------------------------------------------


def _find_note(query: str, vault_path: Path) -> Path | None:
    """Locate a vault note by absolute path or stem name.

    If ``query`` is an absolute path that exists, return it directly.
    Otherwise walk all vault notes and return the first whose stem matches
    ``query`` (case-insensitive).

    Args:
        query: Absolute path string or stem name.
        vault_path: Path to the vault root.

    Returns:
        Matching Path, or None if not found.
    """
    candidate = Path(query)
    if candidate.is_absolute() and candidate.exists():
        return candidate

    # Relative path: try relative to vault root
    if not candidate.is_absolute():
        vault_candidate = vault_path / query
        if vault_candidate.exists():
            return vault_candidate
        # Add .md if missing
        if not query.endswith(".md"):
            vault_candidate_md = vault_path / (query + ".md")
            if vault_candidate_md.exists():
                return vault_candidate_md

    # Stem search across all vault notes
    query_lower = query.lower().removesuffix(".md")
    for path in vault_common.all_vault_notes(vault=vault_path):
        if path.stem.lower() == query_lower:
            return path
    return None


# ---------------------------------------------------------------------------
# Frontmatter helpers
# ---------------------------------------------------------------------------


def _parse_related_list(fm: dict) -> list[str]:
    """Extract the related field as a list of strings.

    Handles both list and bare-string formats.

    Args:
        fm: Parsed frontmatter dict.

    Returns:
        List of related wikilink strings.
    """
    raw = fm.get("related", [])
    if isinstance(raw, list):
        return [str(r) for r in raw]
    if isinstance(raw, str) and raw.strip():
        # May be a quoted inline list like '["[[a]]", "[[b]]"]'
        inner = raw.strip()
        if inner.startswith("[") and inner.endswith("]"):
            items = re.findall(r'"([^"]+)"', inner)
            return items if items else [inner]
        return [inner]
    return []


def _parse_tags_list(fm: dict) -> list[str]:
    """Extract the tags field as a list of strings.

    Args:
        fm: Parsed frontmatter dict.

    Returns:
        List of tag strings.
    """
    raw = fm.get("tags", [])
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t).strip()]
    if isinstance(raw, str) and raw.strip():
        # Handle "[tag1, tag2]" or "tag1, tag2"
        inner = raw.strip().strip("[]")
        return [t.strip().strip('"').strip("'") for t in inner.split(",") if t.strip()]
    return []


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
        "session_id",
    ):
        if key not in fm:
            continue
        value = fm[key]
        if value is None or value == "" or value == [] or value == {}:
            continue
        if key in ("tags", "sources", "related") and isinstance(value, list):
            # Inline quoted array format: ["[[a]]", "[[b]]"]
            items_str = ", ".join(f'"{v}"' for v in value)
            lines.append(f"{key}: [{items_str}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------


def _merge_notes(
    path_a: Path,
    content_a: str,
    path_b: Path,
    content_b: str,
    *,
    no_ai: bool = False,
    vault_path: Path | None = None,
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

    # Related: union, deduplicated
    related_a = _parse_related_list(fm_a)
    related_b = _parse_related_list(fm_b)
    # Add a backlink to note_b's stem so the merged note references it
    stem_b_link = f"[[{path_b.stem}]]"
    seen: set[str] = set()
    merged_related: list[str] = []
    for r in related_a + related_b + [stem_b_link]:
        r_norm = r.strip().lower()
        if r_norm not in seen:
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

    title_a = vault_common.extract_title(content_a, path_a.stem)
    title_b = vault_common.extract_title(content_b, path_b.stem)

    # Try AI merge for intelligent deduplication
    merged_body: str | None = None
    if not no_ai and body_b:
        merged_body = _ai_merge_bodies(path_a, path_b, title_a, vault_path=vault_path)
        if merged_body:
            # Add a comment noting the merge source
            merged_body += f"\n\n<!-- merged from: {title_b} ({path_b.name}) -->"

    # Fallback: naive concatenation
    if merged_body is None:
        merged_body = body_a
        if body_b:
            separator_comment = f"<!-- merged from: {title_b} ({path_b.name}) -->"
            merged_body += f"\n\n---\n\n{separator_comment}\n\n{body_b}"

    return _build_frontmatter(merged_fm) + "\n" + merged_body + "\n"


# ---------------------------------------------------------------------------
# Wikilink update
# ---------------------------------------------------------------------------

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+?)(?:[|#][^\]]*)?\]\]")


def _update_wikilinks_in_vault(old_stem: str, new_stem: str, vault_path: Path) -> int:
    """Replace all wikilinks referencing old_stem with new_stem across the vault.

    Only rewrites files that actually contain the old wikilink.

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
    for path in vault_common.all_vault_notes(vault=vault_path):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        new_content, n = old_pattern.subn(replacement, content)
        if n > 0:
            path.write_text(new_content, encoding="utf-8")
            updated += 1
    return updated


# ---------------------------------------------------------------------------
# Diff summary
# ---------------------------------------------------------------------------


def _print_diff_summary(
    path_a: Path,
    content_a: str,
    path_b: Path,
    content_b: str,
    vault_path: Path | None = None,
) -> None:
    """Print a human-readable diff summary of two notes.

    Args:
        path_a: Path to note A.
        content_a: Content of note A.
        path_b: Path to note B.
        content_b: Content of note B.
        vault_path: Path to the vault root.
    """
    title_a = vault_common.extract_title(content_a, path_a.stem)
    title_b = vault_common.extract_title(content_b, path_b.stem)
    fm_a = vault_common.parse_frontmatter(content_a)
    fm_b = vault_common.parse_frontmatter(content_b)
    tags_a = _parse_tags_list(fm_a)
    tags_b = _parse_tags_list(fm_b)
    body_a = vault_common.get_body(content_a).strip()
    body_b = vault_common.get_body(content_b).strip()

    print("=" * 60)
    print(f"NOTE A:  {path_a}")
    print(f"  Title:  {title_a}")
    print(f"  Tags:   {', '.join(tags_a) or '(none)'}")
    print(f"  Type:   {fm_a.get('type', '(none)')}")
    print(f"  Lines:  {len(body_a.splitlines())}")
    print()
    print(f"NOTE B:  {path_b}")
    print(f"  Title:  {title_b}")
    print(f"  Tags:   {', '.join(tags_b) or '(none)'}")
    print(f"  Type:   {fm_b.get('type', '(none)')}")
    print(f"  Lines:  {len(body_b.splitlines())}")
    print("=" * 60)
    print()
    # Preview first 5 lines of each body
    print("--- Note A preview ---")
    for line in body_a.splitlines()[:5]:
        print(f"  {line}")
    print()
    print("--- Note B preview ---")
    for line in body_b.splitlines()[:5]:
        print(f"  {line}")
    print()


# ---------------------------------------------------------------------------
# Duplicate scan
# ---------------------------------------------------------------------------

_DEFAULT_SCAN_THRESHOLD = 0.92
_DEFAULT_SCAN_TOP = 50


def _scan_duplicates(
    threshold: float = _DEFAULT_SCAN_THRESHOLD,
    top: int = _DEFAULT_SCAN_TOP,
    vault_path: Path | None = None,
) -> None:
    """Scan all vault notes for near-duplicate pairs using embedding similarity.

    Loads all embeddings from the DB, computes pairwise cosine similarity,
    and prints pairs above *threshold* sorted by score descending.

    Args:
        threshold: Minimum similarity score to report (0.0–1.0).
        top: Maximum number of pairs to report.
        vault_path: Path to the vault root.
    """
    db_path = vault_common.get_embeddings_db_path(vault=vault_path)
    if not db_path.exists():
        print(
            "No embeddings database found. Run build_embeddings.py first.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        import sqlite_vec  # type: ignore[import-untyped]
    except ImportError:
        print(
            "sqlite-vec not installed — run: uv tool install --editable '.[tools]'",
            file=sys.stderr,
        )
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    try:
        rows = conn.execute(
            "SELECT stem, path, folder, title, embedding FROM note_embeddings"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        print(f"Error reading embeddings: {exc}", file=sys.stderr)
        conn.close()
        sys.exit(1)
    conn.close()

    if not rows:
        print("No embeddings found in database.")
        return

    # Unpack vectors
    n = len(rows)
    stems = [r[0] for r in rows]
    folders = [r[2] for r in rows]
    titles = [r[3] for r in rows]
    blobs = [r[4] for r in rows]

    dim = len(blobs[0]) // 4  # float32 = 4 bytes each
    vecs: list[list[float]] = [list(struct.unpack(f"{dim}f", b)) for b in blobs]

    # Compute pairwise cosine similarity (upper triangle only)
    pairs: list[tuple[float, int, int]] = []
    for i in range(n):
        vi = vecs[i]
        norm_i = sum(x * x for x in vi) ** 0.5
        if norm_i == 0:
            continue
        for j in range(i + 1, n):
            vj = vecs[j]
            norm_j = sum(x * x for x in vj) ** 0.5
            if norm_j == 0:
                continue
            dot = sum(a * b for a, b in zip(vi, vj, strict=True))
            score = dot / (norm_i * norm_j)
            if score >= threshold:
                pairs.append((score, i, j))

    if not pairs:
        print(f"No note pairs found above similarity threshold {threshold:.2f}.")
        return

    pairs.sort(key=lambda x: x[0], reverse=True)
    pairs = pairs[:top]

    print(f"Found {len(pairs)} near-duplicate pair(s) (threshold={threshold:.2f}):\n")
    for rank, (score, i, j) in enumerate(pairs, 1):
        label_a = f"{folders[i] or '.'}/{stems[i]}"
        label_b = f"{folders[j] or '.'}/{stems[j]}"
        print(f"  {rank:>3}.  [{score:.4f}]  {label_a}")
        print(f"              {label_b}")
        print(f"         A: {titles[i]}")
        print(f"         B: {titles[j]}")
        print(f"         → vault-merge {stems[i]} {stems[j]}")
        print()


# ---------------------------------------------------------------------------
# Index rebuild
# ---------------------------------------------------------------------------


def _rebuild_index() -> None:
    """Run update_index.py to rebuild the vault index after a merge."""
    index_script = Path(__file__).parent / "update_index.py"
    if not index_script.exists():
        index_script = (
            Path.home()
            / ".claude"
            / "skills"
            / "parsidion-cc"
            / "scripts"
            / "update_index.py"
        )
    if not index_script.exists():
        print(
            "Warning: update_index.py not found, skipping index rebuild.",
            file=sys.stderr,
        )
        return
    try:
        subprocess.run(
            ["uv", "run", str(index_script)],
            check=True,
            capture_output=True,
            text=True,
        )
        print("Vault index rebuilt.")
    except subprocess.CalledProcessError as e:
        print(f"Warning: index rebuild failed: {e.stderr}", file=sys.stderr)
    except OSError as e:
        print(f"Warning: could not run update_index.py: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


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

        # Resolve notes
        path_a = _find_note(args.note_a, vault_path)
        if path_a is None:
            print(f"Error: note not found: {args.note_a}", file=sys.stderr)
            sys.exit(1)

        path_b = _find_note(args.note_b, vault_path)
        if path_b is None:
            print(f"Error: note not found: {args.note_b}", file=sys.stderr)
            sys.exit(1)

        if path_a.resolve() == path_b.resolve():
            print("Error: NOTE_A and NOTE_B are the same file.", file=sys.stderr)
            sys.exit(1)

        content_a = path_a.read_text(encoding="utf-8")
        content_b = path_b.read_text(encoding="utf-8")

        # Show diff summary
        _print_diff_summary(path_a, content_a, path_b, content_b, vault_path=vault_path)

        # Build merged content
        merged = _merge_notes(
            path_a,
            content_a,
            path_b,
            content_b,
            no_ai=args.no_ai,
            vault_path=vault_path,
        )

        if args.dry_run or not args.execute:
            print("=== Proposed merged content ===\n")
            print(merged)
            if not args.execute:
                print("(dry-run — pass --execute to apply changes)")
            return

        # --execute: write merged note
        output_path = Path(args.output) if args.output else path_a
        output_path.write_text(merged, encoding="utf-8")
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


if __name__ == "__main__":
    main()
