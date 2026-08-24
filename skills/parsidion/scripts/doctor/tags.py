"""Tag deduplication + session-duplicate detection.

Extracted from the original ``vault_doctor.py`` (ARC-008 / QA-003).

Stdlib-only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import vault_common
import vault_fs

from doctor._state import (
    SESSION_ID_PATTERN,
    _active_vault,
    _backup_note,
    _rel,
)
from doctor.graph import _run_reindex

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
                # A "-s" suffix match is a semantic guess, not a form variant:
                # distinct tags can collide (io vs ios). When the "plural" form
                # dominates its "singular" by an order of magnitude, the pair is
                # two coexisting tags, not drift onto a rare typo form — skip it.
                singular, plural = (t1, t2) if t1 + "s" == t2 else (t2, t1)
                if tag_counts.get(plural, 0) >= 10 * max(
                    1, tag_counts.get(singular, 0)
                ):
                    seen.add(pair_key)
                    continue
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
    _backup_note(_active_vault(), path)
    vault_fs.atomic_write_text(path, new_content)
    return True


def _update_graph_json_tags(
    merges: list[tuple[str, str, str]], vault_path: Path | None = None
) -> int:
    """Update graph.json to replace merged-away tags with their canonical form.

    Returns the number of substitutions made.
    """
    if vault_path is None:
        vault_path = _active_vault()
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
        # QA-017: graph.json is a wide (47.5 MB) write — route through an
        # atomic tmp+rename so an interrupt cannot leave a half-written file.
        # The visualizer streams this file and a truncated body would break
        # the SSE rebuild. The atomic JSON writer at :194 changes the file
        # suffix when computing the tmp name (graph.json → graph.json.tmp),
        # which would leave a stale .tmp residue in the vault root; writing
        # the full body (with the original trailing newline) via
        # atomic_write_text preserves byte-for-byte parity and uses the
        # conventional `<name>.tmp` sibling instead.
        vault_fs.atomic_write_text(graph_path, json.dumps(data, indent=2) + "\n")

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
        vault_path = _active_vault()

    # QA-014: one read per note. Pass 1 keeps (note, content, fm_match,
    # issues) so the rewrite pass reuses the already-read content instead
    # of re-reading and re-regex-parsing every candidate.
    found: list[tuple[Path, str, re.Match[str], list[str]]] = []
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
            found.append((note, content, fm_match, issues))

    if not found:
        return 0

    print(f"\nFound {len(found)} note(s) with underscores in tags/project:\n")
    for note, _content, _fm_match, issues in found[:20]:
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
    for note, content, fm_match, _ in found:
        fm_text = _rewrite_underscore_fields(fm_match.group(1))

        if fm_text != fm_match.group(1):
            new_content = (
                content[: fm_match.start(1)] + fm_text + content[fm_match.end(1) :]
            )
            _backup_note(vault_path, note)
            vault_fs.atomic_write_text(note, new_content)
            modified += 1

    if modified:
        print(f"  Normalized underscores → hyphens in {modified} note(s)")
    return modified


def _rewrite_underscore_fields(fm_text: str) -> str:
    """QA-014: the underscore-to-hyphen rewrite for one frontmatter block.

    Rewrites tag values (inline list, quoted inline list, or block
    sequence) and the scalar ``project`` field, leaving every other field
    untouched.
    """
    # Regex for project field: project: some_value
    project_re = re.compile(r"^(project:\s*)(.+)$", re.MULTILINE)

    # Fix tags: replace underscores with hyphens in tag values only
    # Inline: tags: [par_ai_core, foo] or tags: ["par_ai_core", "foo"]
    inline_m = _TAGS_INLINE_RE.search(fm_text)
    if inline_m:
        old_items = inline_m.group(2)
        new_items = old_items.replace("_", "-")
        if old_items != new_items:
            fm_text = (
                fm_text[: inline_m.start(2)] + new_items + fm_text[inline_m.end(2) :]
            )
        return project_re.sub(
            lambda m: m.group(1) + m.group(2).replace("_", "-"), fm_text
        )

    # Block sequence: replace underscores in "  - tag_name" lines.
    # Bound the replacement at the end of the contiguous tags block —
    # substituting through the rest of the frontmatter would corrupt
    # later block-sequence fields (e.g. sources: URLs with underscores).
    block_m = _TAGS_BLOCK_START_RE.search(fm_text)
    if block_m:
        after = fm_text[block_m.end() :]
        all_lines = after.split("\n")
        end_idx = 0
        saw_item = False
        for i, line in enumerate(all_lines):
            stripped = line.strip()
            if stripped.startswith("- "):
                saw_item = True
                end_idx = i + 1
            elif not stripped and not saw_item:
                # Leading blank line before first item — skip
                end_idx = i + 1
            else:
                break  # blank line after items, or next field
        changed = False
        for i in range(end_idx):
            line = all_lines[i]
            if line.startswith("  - ") and "_" in line:
                all_lines[i] = "  - " + line[4:].replace("_", "-")
                changed = True
        if changed:
            fm_text = fm_text[: block_m.end()] + "\n".join(all_lines)

    # Fix project field
    return project_re.sub(lambda m: m.group(1) + m.group(2).replace("_", "-"), fm_text)


def run_fix_sessions(vault_path: Path | None = None) -> None:
    """Detect and report notes sharing the same session_id."""
    if vault_path is None:
        vault_path = _active_vault()

    notes = vault_common.all_vault_notes_walk(vault=vault_path)
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
        vault_path = _active_vault()
    all_notes = list(vault_common.all_vault_notes_walk(vault_path))

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
