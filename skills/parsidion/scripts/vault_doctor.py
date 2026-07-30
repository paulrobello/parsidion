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

Before the first execute-mode mutation of a note in a given run, a copy of the
original is saved to ``<vault>/.trash/backup/<YYYY-MM-DD>/<relative-path>``
(first version of the day wins). ``.trash`` is already excluded from indexing,
so backups never show up in search or the graph. Backups are best-effort and
never block a fix; prune ``.trash/backup/`` freely whenever you like.

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
import errno
import json
import os
import re
import shutil  # noqa: F401 — re-exported for test monkeypatch (vault_doctor.shutil.copy2)
import subprocess  # noqa: F401 — re-exported for test monkeypatch (vault_doctor.subprocess.run)
import sys
import threading
from datetime import date, datetime
from pathlib import Path

import ai_backend
import vault_common
import vault_fs
import vault_links

# ---------------------------------------------------------------------------
# Constants, data model, and shared state live in doctor._state so the
# submodules and this shim share one ``_vault_path`` / ``_backed_up_this_run``
# object.  Re-exported here so existing ``vault_doctor.X`` attribute access
# (including test ``monkeypatch.setattr``) keeps working byte-for-byte.
# See doctor/_state.py for the test-patch compatibility contract.
# ---------------------------------------------------------------------------
from doctor._state import (  # noqa: F401 — re-exports
    AI_TIMEOUT,
    DEFAULT_MODEL,
    PREFIX_CLUSTER_MIN,
    REPAIRABLE_CODES,
    REQUIRED_FIELDS_ALL,
    REQUIRED_FIELDS_KNOWLEDGE,
    SESSION_ID_PATTERN,
    STATE_STALE_DAYS,
    STALE_COMMIT_MINUTES,
    VALID_TYPES,
    Issue,
    _active_vault,
    _backup_note,
    _backed_up_this_run,
    _get_state_file,
    _rel,
    _release_pid,
    _resolve_shim_vault_path,
    _vault_path,
    _write_json_atomic,
    _write_pid,
    is_process_running,
    load_state,
    save_state,
    should_skip,
)


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
        vault_path = _active_vault()
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


from doctor.links import (  # noqa: E402,F401 — re-exports grouped by concern
    _auto_repair_broken_wikilinks,
    _find_link_replacement,
    _find_semantic_candidates,
    build_note_map,
    dedup_related_links,
    resolve_wikilink,
)


# ---------------------------------------------------------------------------
# Wikilink resolution
# ---------------------------------------------------------------------------


from doctor.subfolder import (  # noqa: E402,F401 — re-exports grouped by concern
    _filter_clusters_with_claude,
    find_prefix_clusters,
    find_subfolder_candidates,
    fix_prefix_cluster,
    run_migrate_subfolders,
)


# ---------------------------------------------------------------------------
# Note checker
# ---------------------------------------------------------------------------


from doctor.check import (  # noqa: E402,F401 — re-exports grouped by concern
    check_note,
)


# ---------------------------------------------------------------------------
# Claude repair
# ---------------------------------------------------------------------------


def _auto_fix_self_refs(path: Path) -> bool:
    """Remove self-referencing wikilinks from the ``related`` frontmatter field.

    Returns True if the file was modified.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False

    stem = path.stem
    self_ref = f"[[{stem}]]"
    related_re = re.compile(r"^(related:\s*)(\[.*?\])\s*$", re.MULTILINE)
    m = related_re.search(content)
    if not m:
        return False

    prefix = m.group(1)
    raw_list = m.group(2)
    entries = re.findall(r'"(\[\[[^\]]+\]\])"', raw_list)
    if not entries:
        return False

    filtered = [e for e in entries if e != self_ref]
    if len(filtered) == len(entries):
        return False

    if filtered:
        quoted = ", ".join(f'"{e}"' for e in filtered)
        new_related_line = f"{prefix}[{quoted}]"
    else:
        new_related_line = f"{prefix}[]"

    updated = related_re.sub(new_related_line, content)
    if updated == content:
        return False

    _backup_note(_active_vault(), path)
    vault_fs.atomic_write_text(path, updated)
    return True


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
        _backup_note(_active_vault(), path)
        vault_fs.atomic_write_text(path, "\n".join(lines))
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
        vault_path = _active_vault()
    content = path.read_text(encoding="utf-8")
    rel = path.relative_to(vault_path)
    issue_lines = "\n".join(
        f"  - [{i.severity.upper()}] {i.code}: {i.message}" for i in issues
    )

    # Daily notes are exempt from the confidence/related requirements, so do
    # not force the model to fabricate a 'related' link for them.
    is_daily = _note_is_daily(rel, content)

    # For any non-daily note that must produce/fix a 'related' field, surface
    # real semantically-similar notes so the model picks actual wikilinks
    # instead of inventing targets that don't resolve to anything.
    needs_related = any(
        i.code in ("ORPHAN_NOTE", "MISSING_FIELD", "MISSING_FRONTMATTER")
        for i in issues
    )
    candidates: list[str] = (
        _find_semantic_candidates(path) if (not is_daily and needs_related) else []
    )
    candidate_section = ""
    if candidates:
        links = ", ".join(f"[[{s}]]" for s in candidates)
        candidate_section = (
            f"\n\nReal vault notes related to this one (choose the 'related' "
            f"links ONLY from this list — do NOT invent other targets):\n{links}"
        )

    if is_daily:
        related_rule = "- Daily notes: set 'related: []' (they are exempt from linking)"
    else:
        related_rule = (
            "- 'related' MUST be a single-line inline YAML array of [[wikilinks]], "
            "exactly like:\n"
            '      related: ["[[some-note]]", "[[another-note]]"]\n'
            "  Use only the real targets listed below; every [[link]] must resolve."
        )

    prompt = f"""You are a vault note repair tool. Fix ONLY the listed issues in this Obsidian markdown note.
Do NOT rewrite, summarise, or add content beyond what is needed to resolve each issue.
Return ONLY the corrected note as raw markdown. No explanation, no code fences, and
do NOT echo the ---BEGIN--- / ---END--- markers shown below.

File: {rel}

Issues to fix:
{issue_lines}

Rules:
- Valid values for 'type': {", ".join(sorted(VALID_TYPES))}
- Valid values for 'confidence': high | medium | low
- 'date' must be YYYY-MM-DD
- Emit exactly ONE YAML frontmatter block: a '---' line, the fields, then a '---' line.
- Every non-daily note needs: date, type, confidence, related in its frontmatter
- 'sources' should be [] if unknown
{related_rule}{candidate_section}

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
        # Strip echoed BEGIN/END markers from the prompt wrapper
        output = re.sub(r"^---BEGIN---\s*\n?", "", output)
        output = re.sub(r"\n?---END---\s*$", "", output)
        if output:
            return output, "fixed"
    return None, "failed"


# ---------------------------------------------------------------------------
# AI-output normalization (defence against malformed frontmatter)
# ---------------------------------------------------------------------------

# A bare YAML document delimiter (exactly three dashes, optional trailing space).
_FM_DELIM_RE = re.compile(r"^---\s*$")
# A leaked prompt-wrapper / fence marker the small model sometimes echoes back:
# ---BEGIN---, ---END---, ---yaml, ---YAML (any case). Bare '---' is NOT matched.
_FM_LEAKED_MARKER_RE = re.compile(r"^---(?:BEGIN|END|YAML)-*\s*$", re.IGNORECASE)
# A frontmatter field line: 'key: value' or 'key:'.
_FM_KEY_RE = re.compile(r"^[A-Za-z][\w-]*\s*:")
# The 'related:' line (matches regardless of the value that follows).
_FM_RELATED_RE = re.compile(r"^related:\s*(.*)$")


def _note_is_daily(rel: Path, content: str) -> bool:
    """A note is daily if it lives under ``Daily/`` or its frontmatter type is daily."""
    if rel.parts and rel.parts[0] == "Daily":
        return True
    fm = vault_common.parse_frontmatter(content)
    return bool(fm and fm.get("type") == "daily")


def _frontmatter_stems(raw: str, note_map: dict[str, list[Path]] | None) -> list[str]:
    """Extract ``[[wikilink]]`` stems from an arbitrary ``related`` value.

    Tolerates the malformed shapes the small model emits — double-nested
    brackets (``[["[[x]]"]]``), missing outer brackets, surrounding quotes, and
    markdown links (``[label](url)``) — by pulling the innermost ``[[...]]``
    spans. Stems are de-aliased, de-duplicated (first-seen order), and filtered
    to those that resolve via *note_map* (when provided).
    """
    stems: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"\[\[([^\[\]]+)\]\]", raw):
        stem = m.group(1).split("|")[0].split("#")[0].strip()
        if not stem or stem.lower() in seen:
            continue
        if note_map is not None and not resolve_wikilink(stem, note_map):
            continue
        seen.add(stem.lower())
        stems.append(stem)
    return stems


def _normalize_repaired_note(
    content: str,
    note_map: dict[str, list[Path]] | None,
    is_daily: bool,
) -> str | None:
    """Validate and normalize an AI-repaired note before it is written to disk.

    Guards against the small model emitting malformed frontmatter:

    * a missing closing ``---`` delimiter
    * leaked ``---BEGIN---`` / ``---END---`` / ``---yaml`` markers or code fences
    * a ``related`` value that is not a flat inline array of ``[[wikilinks]]``
    * fabricated ``[[wikilink]]`` targets that resolve to no vault note

    Returns the cleaned note (frontmatter + body), or ``None`` if the output
    cannot be made valid — the caller then treats this as a failed repair so
    nothing is written and the note is retried on the next run instead of being
    corrupted.
    """
    text = content.strip()
    # 1. Drop a leading/trailing code fence the model may have wrapped the note in.
    text = re.sub(r"^```[a-zA-Z]*\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    # 2. Drop leaked wrapper markers (but never a bare '---' delimiter).
    lines = [ln for ln in text.split("\n") if not _FM_LEAKED_MARKER_RE.match(ln)]
    if not lines:
        return None

    # 3. Locate the frontmatter block: first bare '---', next bare '---'.
    delim_idx = [i for i, ln in enumerate(lines) if _FM_DELIM_RE.match(ln)]
    if not delim_idx:
        return None  # no frontmatter at all — reject
    opener = delim_idx[0]
    if len(delim_idx) < 2:
        # Missing closer: insert one where the body begins (first line that is
        # neither a key:value field, a block-sequence entry, nor blank).
        closer = opener + 1
        while closer < len(lines) and (
            lines[closer].strip() == ""
            or _FM_KEY_RE.match(lines[closer])
            or lines[closer].startswith(" ")
            or lines[closer].startswith("-")
        ):
            closer += 1
        lines.insert(closer, "---")
    else:
        closer = delim_idx[1]

    fm_lines = lines[opener + 1 : closer]
    body_lines = lines[closer + 1 :]

    # Reject stacked/duplicate frontmatter blocks: if the body itself opens
    # with '---' or a key:value line, the model emitted two blocks — too risky
    # to disentangle, so retry rather than guess.
    for ln in body_lines:
        s = ln.strip()
        if s == "":
            continue
        if s == "---" or _FM_KEY_RE.match(s):
            return None
        break

    # 4. Rebuild the 'related:' line as a clean inline array of resolving links.
    new_fm: list[str] = []
    related_value = ""
    related_at: int | None = None
    for i, ln in enumerate(fm_lines):
        m = _FM_RELATED_RE.match(ln)
        if related_at is None and m:
            related_at = i
            related_value = m.group(1)
        else:
            new_fm.append(ln)

    stems = _frontmatter_stems(related_value, note_map)
    if not stems:
        if not is_daily:
            return None  # non-daily needs at least one real link
        new_related = "related: []"
    else:
        new_related = "related: [" + ", ".join(f'"[[{s}]]"' for s in stems) + "]"

    if related_at is not None:
        new_fm.insert(related_at, new_related)
    else:
        new_fm.append(new_related)

    return "\n".join(["---", *new_fm, "---", *body_lines])


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
        vault_path = _active_vault()
    key = _rel(note_path)
    rel = note_path.relative_to(vault_path)
    repairable = [i for i in note_issues if i.code in REPAIRABLE_CODES]
    broken = [i for i in repairable if i.code == "BROKEN_WIKILINK"]
    heading_issues = [i for i in repairable if i.code == "HEADING_MISMATCH"]
    self_ref_issues = [i for i in repairable if i.code == "SELF_REF"]
    other = [
        i
        for i in repairable
        if i.code not in ("BROKEN_WIKILINK", "HEADING_MISMATCH", "SELF_REF")
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

    # Step 0b: Python-based self-reference removal (no Claude needed)
    self_ref_fix_made = False
    if self_ref_issues:
        self_ref_fix_made = _auto_fix_self_refs(note_path)
        if self_ref_fix_made:
            with lock:
                print(f"  ✓ {rel}: removed self-referencing wikilink(s)", flush=True)

    # Step 1: Python-based broken-link repair (no Claude needed)
    link_fix_made = False
    became_orphan = False
    if broken and note_map is not None:
        fixed_content, became_orphan = _auto_repair_broken_wikilinks(
            note_path, broken, note_map
        )
        if fixed_content:
            _backup_note(vault_path, note_path)
            vault_fs.atomic_write_text(note_path, fixed_content + "\n")
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
            # Normalize the AI output before writing: defend against malformed
            # frontmatter (missing closing ---, leaked markers, fabricated or
            # badly-nested wikilinks). Reject (don't write) if it cannot be
            # made valid, so the note is retried instead of being corrupted.
            normalized = _normalize_repaired_note(
                fixed_content, note_map, _note_is_daily(rel, fixed_content)
            )
            if normalized is None:
                fixed_content = None
                repair_status = "failed"
            else:
                _backup_note(vault_path, note_path)
                vault_fs.atomic_write_text(note_path, normalized + "\n")
    elif broken or heading_issues or self_ref_issues:
        # Only broken wikilinks / heading / self-ref fixes — no Claude call needed
        repair_status = (
            "fixed"
            if (link_fix_made or heading_fix_made or self_ref_fix_made)
            else "failed"
        )

    if fixed_content:
        icon = "✓"
    elif (link_fix_made or heading_fix_made or self_ref_fix_made) and not other:
        # Fixed by Python, no Claude needed
        icon = "✓"
    else:
        if repair_status == "timeout" and prev_status == "timeout":
            repair_status = "needs_review"
        icon = "✗" if not (link_fix_made or self_ref_fix_made) else "~"

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

    return (
        fixed_content is not None
        or link_fix_made
        or heading_fix_made
        or self_ref_fix_made
    )


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
        fm_text = project_re.sub(
            lambda m: m.group(1) + m.group(2).replace("_", "-"),
            fm_text,
        )

        if fm_text != original_fm:
            new_content = (
                content[: fm_match.start(1)] + fm_text + content[fm_match.end(1) :]
            )
            _backup_note(vault_path, note)
            vault_fs.atomic_write_text(note, new_content)
            modified += 1

    if modified:
        print(f"  Normalized underscores → hyphens in {modified} note(s)")
    return modified


def _run_reindex(vault_path: Path | None = None) -> None:
    """Run update_index.py to rebuild the vault index."""
    if vault_path is None:
        vault_path = _active_vault()

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
        # QA-005: bound the index rebuild. vault_doctor --fix-all runs
        # unattended nightly; without a timeout a hung child stalls the cron
        # job indefinitely. 600 s is generous for a full re-index on a large
        # vault (the embedding-rebuild phase is itself bounded separately).
        subprocess.run(
            ["uv", "run", "--no-project", str(script), "--vault", str(vault_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
        )
        print("Index rebuilt successfully.")
    except subprocess.TimeoutExpired:
        print(
            "Warning: update_index.py timed out after 600s — index left stale.",
            file=sys.stderr,
        )
    except OSError as exc:
        print(f"Warning: update_index.py failed: {exc}", file=sys.stderr)


def run_fix_sessions(vault_path: Path | None = None) -> None:
    """Detect and report notes sharing the same session_id."""
    if vault_path is None:
        vault_path = _active_vault()

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
        vault_path = _active_vault()
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
        vault_path = _active_vault()
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

    # Rename files (skip failures gracefully — a mid-batch crash here would
    # leave already-renamed notes with unpatched, vault-wide broken wikilinks)
    renamed: list[tuple[Path, Path]] = []
    for old, new in pairs:
        try:
            _backup_note(vault_path, old)
            old.rename(new)
        except OSError as exc:
            rel = old.relative_to(vault_path)
            print(f"  ⚠ skipped (rename failed): {rel}: {exc}", file=sys.stderr)
            continue
        renamed.append((old, new))

    if not renamed:
        print("No files were renamed.")
        return

    # Build stem remapping for wikilink patching — only stems that actually renamed
    stem_map: dict[str, str] = {old.stem: new.stem for old, new in renamed}

    # Patch wikilinks vault-wide (including in the renamed files)
    patched_notes = 0
    current_notes = list(vault_common.all_vault_notes(vault_path))
    for note in current_notes:
        try:
            content = note.read_text(encoding="utf-8")
        except OSError:
            continue
        original = content
        content = vault_links.replace_wikilinks_outside_code(content, stem_map)
        if content != original:
            _backup_note(vault_path, note)
            vault_fs.atomic_write_text(note, content)
            patched_notes += 1

    vault_common.git_commit_vault(
        f"refactor(vault): strip redundant subfolder prefix from {len(renamed)} note(s)",
        vault=vault_path,
    )
    print(
        f"Renamed {len(renamed)} file(s), patched wikilinks in {patched_notes} note(s)."
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
        _backup_note(vault_root, old)
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

            # Match [[DD]] but not [[DD-something]] (avoid double-rename)
            stem_map = {
                old.stem: new.stem for old, new in moved if old.parent == month_dir
            }
            new_text = vault_links.replace_wikilinks_outside_code(text, stem_map)

            if new_text != text:
                _backup_note(vault_root, rollup)
                vault_fs.atomic_write_text(rollup, new_text)
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


def run_scan_and_repair(
    vault: Path,
    state: dict,
    *,
    notes: list[Path],
    dry_run: bool,
    fix_frontmatter: bool,
    fix_sessions: bool,
    errors_only: bool,
    no_state: bool,
    model: str | None,
    limit: int,
    jobs: int,
    timeout: int,
    fix_headings: bool,
) -> None:
    """Run the core scan-and-repair pipeline.

    Handles: legacy pending-path migration, session-consolidation check,
    related-link dedup, stale-file auto-commit, prefix-cluster detection,
    note scanning, issue reporting, and parallel AI-assisted repair.

    All parameters are passed explicitly — this function does not read the
    module-level ``_vault_path`` global.

    Args:
        vault: Resolved vault root path.
        state: Loaded doctor state dict (may be mutated and saved).
        notes: Explicit note paths to scan (empty list = all vault notes).
        dry_run: When True, report issues but skip all writes and AI calls.
        fix_frontmatter: When True, invoke the AI backend to repair issues.
        fix_sessions: When True, print session-duplicate report and exit.
        errors_only: When True, suppress warnings and only report/repair errors.
        no_state: When True, skip the stale-state filter.
        model: AI model override (None = backend default).
        limit: Max notes to repair per run (0 = unlimited).
        jobs: Parallel repair worker count.
        timeout: Per-repair AI call timeout in seconds.
        fix_headings: When True, auto-promote ## headings to #.
    """
    # Auto-fix legacy pending paths (silent when nothing to fix)
    fixed_paths = vault_common.migrate_pending_paths(dry_run=dry_run, vault=vault)
    if fixed_paths:
        action = "Would fix" if dry_run else "Fixed"
        print(
            f"{action} {fixed_paths} legacy transcript path(s) in pending_summaries.jsonl.\n"
        )

    # Session consolidation check
    if fix_sessions:
        run_fix_sessions(vault_path=vault)
        sys.exit(0)

    # Auto-deduplicate related wikilinks (silent when nothing to fix)
    deduped = dedup_related_links(dry_run=dry_run, vault_path=vault)
    if deduped:
        action = "Would deduplicate" if dry_run else "Deduplicated"
        print(f"{action} related links in {deduped} note(s).\n")

    # Auto-commit uncommitted vault files older than STALE_COMMIT_MINUTES
    stale = commit_stale_files(dry_run=dry_run, vault_path=vault)
    if stale:
        rel_stale = [str(p.relative_to(vault)) for p in stale]
        if dry_run:
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
    if notes:
        target_notes = [Path(n).resolve() for n in notes]
        explicit = True
    else:
        target_notes = list(vault_common.all_vault_notes(vault))
        explicit = False

    # Always skip auto-generated files (rebuilt by update_index.py, never doctor-repaired).
    vault_claude_md = vault / "CLAUDE.md"
    vault_tags_md = vault / "TAGS.md"
    target_notes = [
        p
        for p in target_notes
        if p != vault_claude_md and p != vault_tags_md and p.name != "MANIFEST.md"
    ]

    # Skip notes that have already been processed and are still fresh
    if not explicit and not no_state:
        before = len(target_notes)
        target_notes = [
            p for p in target_notes if not should_skip(_rel(p, vault), state)
        ]
        skipped_by_state = before - len(target_notes)
    else:
        skipped_by_state = 0

    # Build note map once for wikilink resolution
    all_notes = list(vault_common.all_vault_notes(vault))
    note_map = build_note_map(all_notes)

    # ── Prefix cluster detection and fixing ──────────────────────────────────
    clusters = find_prefix_clusters(all_notes, vault)
    if clusters and not dry_run:
        # Filter out generic-word false positives using the configured prompt AI backend
        clusters = _filter_clusters_with_claude(clusters, model=model, timeout=timeout)
    cluster_repaired = 0
    if clusters:
        total_cluster_notes = sum(len(n) for _, _, n, _ in clusters)
        print(
            f"\nFound {len(clusters)} prefix cluster(s) "
            f"({total_cluster_notes} note(s) to reorganize):\n"
        )
        for cluster_folder, prefix, cluster_notes, base_note in clusters:
            folder_rel = cluster_folder.relative_to(vault)
            kind = "exact-stem" if base_note is not None else "first-word"
            print(f"  {folder_rel}/{prefix}/  ({len(cluster_notes)} notes, {kind})")
            for note in sorted(cluster_notes):
                note_rel = note.relative_to(vault)
                if note is base_note:
                    new_name = note.name  # base note keeps its filename
                elif note.stem.startswith(f"{prefix}-"):
                    new_name = note.stem[len(prefix) + 1 :] + ".md"
                else:
                    new_name = note.name
                print(f"    {note_rel}  →  {folder_rel}/{prefix}/{new_name}")
        print()

        if not dry_run and fix_frontmatter:
            print("Reorganizing prefix clusters…\n")
            for cluster_folder, prefix, cluster_notes, base_note in clusters:
                moves = fix_prefix_cluster(
                    cluster_folder, prefix, cluster_notes, all_notes, base_note
                )
                for old_path, new_path in moves:
                    old_rel = old_path.relative_to(vault)
                    new_rel = new_path.relative_to(vault)
                    print(f"  {old_rel}  →  {new_rel}")
                    cluster_repaired += 1
            if cluster_repaired:
                vault_common.git_commit_vault(
                    f"refactor(vault): reorganize {cluster_repaired} note(s) into prefix subfolders",
                    vault=vault,
                )
                print()
                # Refresh after moves
                all_notes = list(vault_common.all_vault_notes(vault))
                note_map = build_note_map(all_notes)
                all_filtered = [
                    p
                    for p in all_notes
                    if p != vault_claude_md
                    and p != vault_tags_md
                    and p.name != "MANIFEST.md"
                ]
                if not explicit and not no_state:
                    target_notes = [
                        p
                        for p in all_filtered
                        if not should_skip(_rel(p, vault), state)
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
        note_issues = check_note(note, note_map, vault)
        if errors_only:
            note_issues = [i for i in note_issues if i.severity == "error"]
        key = _rel(note, vault)
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
        if not dry_run:
            save_state(state, vault)
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
        rel = note_path.relative_to(vault)
        print(f"  {rel}")
        for issue in note_issues:
            icon = "✗" if issue.severity == "error" else "⚠"
            print(f"    {icon} [{issue.code}] {issue.message}")
    print()

    if dry_run:
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
        key = _rel(p, vault)
        state.setdefault("notes", {})[key] = {
            "status": "skipped",
            "last_checked": today_str,
            "issues": [i.code for i in issues_by_note[p]],
        }

    if not repair_candidates:
        print("No repairable issues (flat daily notes require manual fixes).")
        save_state(state, vault)
        return

    if not fix_frontmatter:
        print(
            f"{len(repair_candidates)} note(s) have repairable issues.\n"
            "Run with --fix-frontmatter to repair them via the configured prompt AI backend."
        )
        save_state(state, vault)
        return

    # Apply repairs
    effective_limit = limit if limit > 0 else len(repair_candidates)
    effective_jobs = max(1, jobs)
    repaired = 0
    failed = 0
    lock = threading.Lock()

    print(
        f"Repairing up to {effective_limit} note(s) via prompt AI "
        f"({effective_jobs} parallel job(s), {timeout}s timeout)…\n"
    )
    batch = repair_candidates[:effective_limit]
    with concurrent.futures.ThreadPoolExecutor(max_workers=effective_jobs) as executor:
        futures = {
            executor.submit(
                _repair_one,
                note_path,
                note_issues,
                model,
                state,
                today_str,
                lock,
                timeout,
                note_map,
                fix_headings,
                vault,
            ): note_path
            for note_path, note_issues in batch
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                success = future.result()
            except Exception as exc:  # noqa: BLE001
                note_path = futures[future]
                print(f"  {_rel(note_path, vault)} … ✗ (exception: {exc})", flush=True)
                success = False
            if success:
                repaired += 1
            else:
                failed += 1

    save_state(state, vault)
    leftover = len(repair_candidates) - effective_limit
    print(
        f"\nDone: {repaired} repaired, {failed} failed, {leftover} not yet processed."
    )

    # Scan-and-repair is the LAST stage of the --fix-all pipeline; earlier
    # stages reindex only their own changes, so repairs must reindex here too.
    if repaired:
        _run_reindex(vault)


# ---------------------------------------------------------------------------
# SEC-109/110/112/114 migration: tighten permissions on sensitive vault files
# ---------------------------------------------------------------------------

# Files inside the vault that may carry secrets or session-derived PII.
# Chmod'd to 0600 (owner read/write only) by run_fix_permissions so a shared
# vault (rare but supported) cannot leak them to other accounts.
_SECRET_FILES: tuple[str, ...] = (
    "pending_summaries.jsonl",
    "dead_letters.jsonl",
    "config.yaml",
    "config.local.yaml",
)

# Glob patterns matching backup variants of the secret files (atomic-write
# leftovers, manual backups, the rotate-on-size copies vault_fs produces).
_SECRET_FILE_GLOBS: tuple[str, ...] = (
    "pending_summaries.jsonl.bak*",
    "pending_summaries.jsonl.tmp",
    "dead_letters.jsonl.bak*",
    "dead_letters.jsonl.tmp",
)

_FILE_MODE = 0o600
_DIR_MODE = 0o700


def _chmod_if_exists(path: Path, mode: int) -> bool:
    """Chmod *path* to *mode* when it exists. Best-effort; never raises.

    Returns True when the mode was applied, False otherwise (missing file,
    permission error, etc.). Errors are reported once via stderr so an
    unattended ``--fix-all`` run still surfaces them.
    """
    try:
        path.chmod(mode)
        return True
    except OSError as exc:
        # File-not-found is expected — many of the glob targets only exist
        # transiently. Anything else is a real environment problem worth a
        # stderr line.
        if exc.errno != errno.ENOENT:
            print(
                f"  permission repair: could not chmod {path}: {exc}",
                file=sys.stderr,
            )
        return False


def run_fix_permissions(
    vault_path: Path | None = None, *, dry_run: bool = False
) -> int:
    """Tighten permissions on sensitive vault files and key directories.

    Migrates older installs where the files below were created with the
    process umask default (typically 0644 for files / 0755 for dirs), making
    them readable to other accounts on a shared host. The current code paths
    create them at the tighter modes (SEC-109/110/112/114 closed the
    creation gaps); this function repairs pre-existing files to match.

    Targets:
      Files (chmod 0600): ``pending_summaries.jsonl``, ``dead_letters.jsonl``,
        their ``.bak*`` / ``.tmp`` variants, ``config.yaml`` and
        ``config.local.yaml`` (which may carry ANTHROPIC_API_KEY).
      Dirs (chmod 0700): the vault root and ``~/.claude/logs``.

    Args:
        vault_path: Vault root. Defaults to the active vault.
        dry_run: When True, report what would change without chmod'ing.

    Returns:
        Number of files/dirs repaired (0 in dry-run mode even if work exists).
    """
    if vault_path is None:
        vault_path = _active_vault()

    targets: list[tuple[Path, int]] = []

    # Vault secret files + glob variants
    for name in _SECRET_FILES:
        targets.append((vault_path / name, _FILE_MODE))
    for pattern in _SECRET_FILE_GLOBS:
        for match in vault_path.glob(pattern):
            targets.append((match, _FILE_MODE))

    # ~/.claude/logs is created by the hooks (parsidion-hook-errors.log,
    # parsidion-embed.log) and by the embedding-rebuild spawn. Pre-SEC-114
    # installs may have it at 0755.
    logs_dir = Path.home() / ".claude" / "logs"
    targets.append((logs_dir, _DIR_MODE))

    # The vault root itself — pre-SEC-109 installs created it at 0755.
    targets.append((vault_path, _DIR_MODE))

    repaired = 0
    print("\nPermission repair:")
    for target, mode in targets:
        if not target.exists():
            continue
        if dry_run:
            print(f"  would chmod {target} → {oct(mode)[2:]}")
            continue
        if _chmod_if_exists(target, mode):
            print(f"  chmod {target} → {oct(mode)[2:]}")
            repaired += 1
    if dry_run:
        print(f"  (dry-run: 0 of {len(targets)} targets chmod'd)")
    else:
        print(f"  Done: {repaired} path(s) repaired.")
    return repaired


def main() -> None:
    """Parse CLI arguments, acquire the singleton PID lock, and dispatch to the requested repair mode."""
    _backed_up_this_run.clear()  # defensive: fresh dedup set for this run
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
    parser.add_argument(
        "--fix-permissions",
        action="store_true",
        help=(
            "Tighten permissions on sensitive vault files: chmod 0600 "
            "pending_summaries.jsonl, dead_letters.jsonl, their .bak/.tmp "
            "variants, config.yaml, config.local.yaml; chmod 0700 the vault "
            "root and ~/.claude/logs. Closes SEC-109/110/112/114 for "
            "pre-existing files left at the umask default. Included in "
            "--fix-all."
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
    # ARC-001: clear caches so lru_cache-memoized load_config() and
    # resolve_vault() observe the new VAULT_ROOT instead of stale values.
    vault_common.load_config.cache_clear()  # type: ignore[attr-defined]
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]

    def _restore_vault_root() -> None:
        vault_common.VAULT_ROOT = original_vault_root
        # ARC-001: flush caches on restore so subsequent code sees the original vault.
        vault_common.load_config.cache_clear()  # type: ignore[attr-defined]
        vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]

    atexit.register(_restore_vault_root)

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
        args.fix_permissions = True
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
        run_migrate_subfolders(
            _vault_path, dry_run=dry, model=args.model, timeout=args.timeout
        )
        if not args.fix_all:
            return

    # ── --migrate-daily-notes mode ─────────────────────────────────────────
    if args.migrate_daily_notes:
        dry = not args.execute
        run_migrate_daily_notes(_vault_path, dry_run=dry, username=args.daily_username)
        if not args.fix_all:
            return

    # ── --fix-permissions mode ─────────────────────────────────────────────
    # SEC-109/110/112/114 migration: chmod sensitive files to 0600, vault
    # root and ~/.claude/logs to 0700. Runs as part of --fix-all (unattended
    # nightly) and standalone via --fix-permissions.
    if args.fix_permissions:
        dry = not args.execute
        run_fix_permissions(_vault_path, dry_run=dry)
        if not args.fix_all:
            return

    run_scan_and_repair(
        _vault_path,
        state,
        notes=list(args.notes),
        dry_run=args.dry_run,
        fix_frontmatter=args.fix_frontmatter,
        fix_sessions=args.fix_sessions,
        errors_only=args.errors_only,
        no_state=args.no_state,
        model=args.model,
        limit=args.limit,
        jobs=args.jobs,
        timeout=args.timeout,
        fix_headings=args.fix_headings,
    )


if __name__ == "__main__":
    main()
