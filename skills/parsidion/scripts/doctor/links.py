"""Wikilink resolution, broken-link repair, and related-field dedup.

Extracted from the original ``vault_doctor.py`` (ARC-008 / QA-003).

Stdlib-only.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import vault_common
import vault_fs
import vault_links

from doctor._state import Issue, _active_vault, _backup_note


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

    # Derive the lookup key from the link target. Wikilink targets are bare
    # slugs with no extension, so Path.stem must NOT be used here: it strips
    # the last dotted segment, breaking version-numbered slugs like
    # "sha2-hmac-migration-0.11-0.13" (-> "...0.11-0"). Strip a trailing
    # .md/.markdown only if the author included one.
    target_name = target.split("/")[-1].lower()
    if target_name.endswith((".md", ".markdown")):
        target_name = target_name.rsplit(".", 1)[0]
    stem = target_name
    candidates = note_map.get(stem, [])
    if not candidates:
        return False

    # If a folder prefix is given, require it to appear in the path
    if "/" in target:
        folder_prefix = target.split("/")[0].lower()
        return any(folder_prefix in str(p).lower() for p in candidates)

    return True


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
        # SEC-128: insert `--` before the note-derived positional so a
        # wikilink like [[--help]] cannot parse as a vault-search flag.
        result = subprocess.run(
            [
                "vault-search",
                "--json",
                "--top=2",
                f"--min-score={min_score}",
                "--",
                link_text,
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
    if replacements:
        link_pattern = re.compile(
            r"\[\[(" + "|".join(re.escape(link) for link in replacements) + r")\]\]"
        )

        def _repl_body_link(m: re.Match[str]) -> str:
            link = m.group(1)
            replacement = replacements.get(link)
            if replacement:
                return f"[[{replacement}]]"
            return link  # strip brackets, keep text

        content, _ = vault_links.sub_wikilinks_outside_code(
            content, link_pattern, _repl_body_link
        )

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
        # SEC-128: insert `--` before the note-derived query so an H1
        # starting with `--` (e.g. '# --verbose flag') cannot parse as a
        # vault-search flag.
        result = subprocess.run(
            ["vault-search", "--json", f"--top={top_k + 1}", "--", query],
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


def dedup_related_links(dry_run: bool = False, vault_path: Path | None = None) -> int:
    """Remove duplicate wikilinks from the ``related`` frontmatter field.

    Scans all vault notes and rewrites any ``related:`` line that contains
    duplicate entries.  Returns the number of notes fixed.
    """
    if vault_path is None:
        vault_path = _active_vault()
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
            _backup_note(vault_path, note_path)
            vault_fs.atomic_write_text(note_path, updated)
            fixed += 1
        except OSError as exc:
            rel = note_path.relative_to(vault_path)
            print(f"  ⚠ failed to write {rel}: {exc}", file=sys.stderr)

    if fixed and not dry_run:
        vault_common.git_commit_vault(
            f"chore(vault): deduplicate related links in {fixed} note(s)",
            vault=vault_path,
        )
    return fixed
