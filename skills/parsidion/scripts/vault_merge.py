#!/usr/bin/env python3
"""vault-merge — merge two vault notes into one.

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
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import ai_backend
import vault_common
import vault_config
import vault_links

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows fallback
    _fcntl = None

_DEFAULT_AI_TIMEOUT: int = 60
_PREVIEW_DIRNAME = ".merge_previews"
_MERGE_LOCK_FILENAME = ".merge.lock"


class AIMergeOutputError(RuntimeError):
    """AI backend returned output that is not a valid merged note body.

    Raised instead of silently accepting refusal/error text: writing such
    output over the keeper note (and then trashing note B) is destructive.
    ``main()`` catches this before any file is written or trashed.
    """


# ---------------------------------------------------------------------------
# AI merge
# ---------------------------------------------------------------------------


def _is_valid_merge_body(merged: str) -> bool:
    """Return True if AI output has the shape the merge prompt demands.

    The prompt requires the backend to emit ONLY the merged note body,
    starting with the first markdown heading — no frontmatter, no code
    fences, no prose preamble. Backend refusals and error messages fail
    this shape check, so they can never be written over the keeper note.
    """
    stripped = merged.strip()
    if len(stripped) < 50:
        return False
    return stripped.startswith("#")


def _configured_merge_model(vault_path: Path | None = None) -> str | None:
    """Return an explicitly configured merge model, if any."""
    config = vault_config.load_config(vault=vault_path)
    summarizer = config.get("summarizer")
    if not isinstance(summarizer, dict) or "merge_model" not in summarizer:
        return None
    model = summarizer["merge_model"]
    if isinstance(model, str) and model.strip():
        return model.strip()
    return None


def _configured_merge_timeout(vault_path: Path | None = None) -> int | float:
    """Return the configured merge timeout or the backend-neutral default."""
    config = vault_config.load_config(vault=vault_path)
    summarizer = config.get("summarizer")
    if isinstance(summarizer, dict):
        timeout = summarizer.get("merge_timeout")
        if isinstance(timeout, bool):
            return _DEFAULT_AI_TIMEOUT
        if isinstance(timeout, (int, float)):
            return timeout
    return _DEFAULT_AI_TIMEOUT


def _ai_merge_bodies(
    path_a: Path, path_b: Path, title: str, vault_path: Path | None = None
) -> str | None:
    """Use the configured prompt AI backend to intelligently merge two note bodies.

    Passes file paths to the backend so it can read the notes directly, avoiding
    prompt bloat and character limits.

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


_WIKILINK_SPAN_RE = re.compile(r"\[\[([^\[\]]+)\]\]")


def _parse_related_list(fm: dict) -> list[str]:
    """Extract ``[[wikilink]]`` entries from the related field, robustly.

    Handles list, bare-string, and *malformed* values — e.g. a leaked template
    comment like ``[]  # inline quoted array: ["note-one", "note-two"]`` — by
    extracting only the actual ``[[wikilink]]`` spans. Never echoes raw comment
    text back into the field (which previously produced mangled ``related``
    values when a note with an unusual field was the merge keeper).
    """
    raw = fm.get("related", [])
    text = "".join(str(r) for r in raw) if isinstance(raw, list) else str(raw or "")
    seen: set[str] = set()
    out: list[str] = []
    for m in _WIKILINK_SPAN_RE.finditer(text):
        span = m.group(0)
        if span.lower() not in seen:
            seen.add(span.lower())
            out.append(span)
    return out


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


# ---------------------------------------------------------------------------
# Dry-run preview cache
# ---------------------------------------------------------------------------
#
# A dry-run merge and a later --execute each independently called the AI
# backend, so the text a user reviewed in the dry-run was never guaranteed to
# be what --execute actually wrote. When a dry-run produces an AI-merged
# body, it is cached here (keyed by the sha256 of both source notes' raw
# content) so `--execute --from-preview` can reuse the exact reviewed text
# instead of risking a different fresh AI call. The cache is a single JSON
# sidecar per (keeper, loser) pair — body and staleness hashes are always
# read/written together, so one small file is simpler than a markdown body
# plus a separate metadata file.


def _hash_content(content: str) -> str:
    """Return the sha256 hex digest of note content, for staleness checks."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _preview_dir(vault_path: Path) -> Path:
    """Return the vault's preview-cache directory, creating it if needed."""
    preview_dir = vault_path / _PREVIEW_DIRNAME
    preview_dir.mkdir(mode=0o700, exist_ok=True)
    return preview_dir


def _preview_cache_path(vault_path: Path, path_a: Path, path_b: Path) -> Path:
    """Return the JSON preview-cache path for a (keeper, loser) note pair."""
    return _preview_dir(vault_path) / f"{path_a.stem}--{path_b.stem}.json"


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
    return body if isinstance(body, str) else None


def _delete_preview(vault_path: Path, path_a: Path, path_b: Path) -> None:
    """Remove a pair's cached preview after a successful --execute."""
    _preview_cache_path(vault_path, path_a, path_b).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Execute-path locking
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _merge_lock(vault_path: Path) -> Iterator[None]:
    """Hold an exclusive, non-blocking lock around the merge mutation sequence.

    Guards read A/B -> write keeper -> trash loser -> rewrite backlinks so two
    concurrent ``--execute`` invocations against the same vault cannot
    interleave. A second invocation that cannot acquire the lock fails
    immediately with ``SystemExit`` instead of blocking, so a stuck or
    crashed holder can never wedge unrelated merges.

    ``vault_fs.flock_exclusive`` is not used here because it blocks
    indefinitely (no ``LOCK_NB``); a blocked second invocation would look
    like a hang rather than the clean, immediate failure this needs.
    """
    lock_path = _preview_dir(vault_path) / _MERGE_LOCK_FILENAME
    lock_file = open(lock_path, "a+", encoding="utf-8")
    if _fcntl is not None:
        try:
            _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            print(
                "Error: another vault-merge --execute is already running "
                f"against this vault (lock: {lock_path}). Try again shortly.",
                file=sys.stderr,
            )
            sys.exit(1)
    try:
        yield
    finally:
        if _fcntl is not None:
            _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_UN)
        lock_file.close()


# ---------------------------------------------------------------------------
# Wikilink update
# ---------------------------------------------------------------------------

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

    for path in vault_common.all_vault_notes(vault=vault_path):
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


def _is_excluded_from_scan(path: str, folder: str) -> bool:  # noqa: ARG001
    """Return True for notes excluded from duplicate scanning.

    Daily notes share a templated session-list structure and the ``NN-probello``
    slug pattern, so they hit the cosine threshold against each other despite
    being semantically distinct (different days). Merging them destroys the
    per-day structure, so they are excluded entirely.

    The embeddings store the path as absolute (``/…/Daily/YYYY-MM/…``) and the
    folder as the month (``YYYY-MM``), so match the ``Daily/`` segment anywhere
    in the normalized path (covers absolute and relative forms).
    """
    norm = str(path).replace("\\", "/")
    return "/Daily/" in norm or norm.lstrip("./").startswith("Daily/")


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
            "SELECT stem, path, folder, title, tags, embedding FROM note_embeddings"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        print(f"Error reading embeddings: {exc}", file=sys.stderr)
        conn.close()
        sys.exit(1)

    rows = [r for r in rows if not _is_excluded_from_scan(str(r[1]), str(r[2]))]

    if not rows:
        conn.close()
        print("No embeddings found in database.")
        return

    stems = [r[0] for r in rows]
    folders = [r[2] for r in rows]
    titles = [r[3] for r in rows]
    tags_list = [r[4] for r in rows]
    stem_to_idx = {s: i for i, s in enumerate(stems)}

    # Pairwise cosine via sqlite-vec's C-level vec_distance_cosine (fast at
    # scale; the pure-Python O(n^2) scan took minutes on thousands of notes).
    # similarity >= threshold  <=>  vec_distance_cosine <= (1 - threshold).
    # Restrict to non-excluded stems via a temp table so _is_excluded_from_scan
    # stays the single source of truth for the Daily-note filter.
    max_dist = 1.0 - threshold
    try:
        conn.execute("CREATE TEMP TABLE _kept (stem TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO _kept (stem) VALUES (?)", [(s,) for s in stems])
        pair_rows = conn.execute(
            """
            SELECT a.stem, b.stem,
                   (1.0 - vec_distance_cosine(a.embedding, b.embedding)) AS score
            FROM note_embeddings a
            JOIN note_embeddings b ON a.rowid < b.rowid
            WHERE a.stem IN (SELECT stem FROM _kept)
              AND b.stem IN (SELECT stem FROM _kept)
              AND vec_distance_cosine(a.embedding, b.embedding) <= ?
            ORDER BY score DESC
            LIMIT ?
            """,
            (max_dist, top),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        print(f"Error computing similarities: {exc}", file=sys.stderr)
        conn.close()
        sys.exit(1)
    conn.close()

    pairs: list[tuple[float, int, int]] = []
    for a, b, score in pair_rows:
        ia = stem_to_idx.get(a)
        ib = stem_to_idx.get(b)
        if ia is not None and ib is not None:
            pairs.append((score, ia, ib))

    if not pairs:
        print(f"No note pairs found above similarity threshold {threshold:.2f}.")
        return

    pairs.sort(key=lambda x: x[0], reverse=True)
    pairs = pairs[:top]

    print(f"Found {len(pairs)} near-duplicate pair(s) (threshold={threshold:.2f}):\n")
    for rank, (score, i, j) in enumerate(pairs, 1):
        label_a = f"{folders[i] or '.'}/{stems[i]}"
        label_b = f"{folders[j] or '.'}/{stems[j]}"

        # ARC-011: Enhancement - session_id matching logic
        match_note = ""
        tags_a = [t.strip() for t in tags_list[i].split(",") if t.strip()]
        tags_b = [t.strip() for t in tags_list[j].split(",") if t.strip()]

        sid_a = next(
            (
                t
                for t in tags_a
                if len(t) == 16 and all(c in "0123456789abcdef" for c in t.lower())
            ),
            None,
        )
        sid_b = next(
            (
                t
                for t in tags_b
                if len(t) == 16 and all(c in "0123456789abcdef" for c in t.lower())
            ),
            None,
        )

        if sid_a and sid_b and sid_a == sid_b:
            match_note = f" [SAME SESSION: {sid_a}]"

        print(f"  {rank:>3}.  [{score:.4f}]  {label_a}")
        print(f"              {label_b}{match_note}")
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
            / "parsidion"
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
            output_path = Path(args.output) if args.output else path_a
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


if __name__ == "__main__":
    main()
