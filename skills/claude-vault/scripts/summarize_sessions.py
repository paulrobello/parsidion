#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["claude-agent-sdk>=0.0.10,<1.0", "anyio>=4.0.0,<5.0"]
# ///
"""On-demand AI-powered session summarizer for Claude Vault.

Reads pending_summaries.jsonl, processes transcripts via Claude Agent SDK,
and writes structured vault notes to the appropriate vault folders.

Usage:
    uv run summarize_sessions.py [--sessions FILE] [--dry-run] [--model MODEL] [--persist]
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import cast

import anyio  # type: ignore[import-untyped]
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query  # type: ignore[import-untyped]

# These scripts are not a proper package — sys.path.insert is intentional so
# each script can run standalone via ``uv run`` or ``python`` without requiring
# pip install or editable installs.  See ARC-009 in AUDIT.md.
sys.path.insert(0, str(Path(__file__).parent))
import vault_common  # noqa: E402

# File locking imported from vault_common (canonical implementation)
_flock_exclusive = vault_common.flock_exclusive
_flock_shared = vault_common.flock_shared
_funlock = vault_common.funlock


_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_CLUSTER_MODEL = "claude-haiku-4-5-20251001"
_DEFAULT_MAX_PARALLEL = 5
_DEFAULT_TRANSCRIPT_TAIL_LINES = 400
_DEFAULT_MAX_CLEANED_CHARS = 12_000

# Map note type values to vault folders
_TYPE_FOLDERS: dict[str, str] = {
    "debugging": "Debugging",
    "research": "Research",
    "pattern": "Patterns",
    "tool": "Tools",
    "framework": "Frameworks",
    "language": "Languages",
    "project": "Projects",
    "daily": "Daily",
}

# Fallback folder when type is unrecognized
_DEFAULT_FOLDER = "Research"


def read_pending(pending_path: Path) -> list[dict[str, object]]:
    """Read all entries from the pending summaries file.

    Args:
        pending_path: Path to the JSONL pending file.

    Returns:
        List of entry dicts.
    """
    if not pending_path.exists():
        return []
    entries: list[dict[str, object]] = []
    try:
        with open(pending_path, "r", encoding="utf-8") as f:
            _flock_shared(f)
            try:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except (json.JSONDecodeError, ValueError):
                        continue
            finally:
                _funlock(f)
    except OSError:
        pass
    return entries


def preprocess_transcript(
    transcript_path_str: str,
    tail_lines: int = _DEFAULT_TRANSCRIPT_TAIL_LINES,
    max_chars: int = _DEFAULT_MAX_CLEANED_CHARS,
) -> str:
    """Pre-process a transcript JSONL file into a cleaned human/assistant dialogue.

    Reads last N lines, keeps only human and assistant text blocks,
    strips tool calls and tool results, and truncates to a character limit.

    Args:
        transcript_path_str: String path to the transcript JSONL file.
        tail_lines: Number of trailing transcript lines to read.
        max_chars: Maximum output characters.

    Returns:
        Cleaned dialogue string, truncated to *max_chars*.
    """
    transcript_path = Path(transcript_path_str)
    if not transcript_path.is_file():
        return ""

    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except OSError:
        return ""

    tail = all_lines[-tail_lines:]
    pairs: list[str] = []

    for raw_line in tail:
        line = raw_line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        msg_type = entry.get("type", "")
        if msg_type not in ("user", "assistant"):
            continue

        message = entry.get("message", entry)
        content = message.get("content")
        if not content:
            continue

        # Extract text blocks only
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                # For user messages, skip tool_result blocks
                # For assistant messages, skip tool_use blocks
                block_type = block.get("type", "")
                if msg_type == "user" and block_type == "tool_result":
                    continue
                if msg_type == "assistant" and block_type == "tool_use":
                    continue
                if block_type == "text":
                    t = block.get("text", "")
                    if isinstance(t, str) and t.strip():
                        parts.append(t.strip())
            text = "\n".join(parts).strip()
        else:
            continue

        if not text:
            continue

        label = "Human" if msg_type == "user" else "Assistant"
        pairs.append(f"{label}: {text}")

    cleaned = "\n\n".join(pairs)
    return cleaned[:max_chars]


def read_project_names() -> set[str]:
    """Collect all project field values from vault note frontmatter.

    Used to filter project names out of the existing-tags list shown to the
    model, since project tags are injected deterministically post-generation.

    Returns:
        Set of project name strings found across all vault notes.
    """
    projects: set[str] = set()
    for note_path in vault_common.all_vault_notes():
        try:
            content = note_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm = vault_common.parse_frontmatter(content)
        proj = fm.get("project")
        if isinstance(proj, str) and proj:
            projects.add(proj)
    return projects


def read_existing_tags() -> list[str]:
    """Read existing tags from the vault index CLAUDE.md.

    Parses the '## Existing Tags' section which contains a comma-separated
    list of all tags currently in the vault.

    Returns:
        Sorted list of existing tag strings, or empty list if unavailable.
    """
    index_path = vault_common.VAULT_ROOT / "CLAUDE.md"
    if not index_path.exists():
        return []
    try:
        content = index_path.read_text(encoding="utf-8")
    except OSError:
        return []
    match = re.search(r"^## Existing Tags\n(.+)$", content, re.MULTILINE)
    if not match:
        return []
    tags_line = match.group(1).strip()
    return [t.strip() for t in tags_line.split(",") if t.strip()]


def build_prompt(
    project: str,
    categories: list[str],
    cleaned_transcript: str,
    existing_tags: list[str],
    session_id: str,
) -> str:
    """Build the Sonnet prompt for generating a vault note.

    Args:
        project: Project name.
        categories: Detected topic categories.
        cleaned_transcript: Pre-processed transcript text.
        existing_tags: All tags currently in the vault (for reuse preference).
        session_id: Claude session ID to embed in frontmatter.

    Returns:
        Complete prompt string.
    """
    today = date.today().isoformat()
    cats_str = ", ".join(categories) if categories else "general"
    tags_instruction: str
    if existing_tags:
        tags_str = ", ".join(existing_tags)
        tags_instruction = (
            f"  tags (2-4 tags — STRONGLY prefer existing tags: {tags_str};\n"
            "  only introduce a new tag if none of the existing ones fit;\n"
            "  when creating new tags prefer short single-word or minimal-hyphen tags\n"
            "  e.g. 'voxel' not 'voxel-engine', 'terminal' not 'terminal-emulator')"
        )
    else:
        tags_instruction = (
            "  tags (2-4 relevant tags; prefer short single-word or minimal-hyphen tags,\n"
            "  e.g. 'voxel' not 'voxel-engine', 'terminal' not 'terminal-emulator')"
        )
    return f"""You are writing a knowledge note for an Obsidian vault.
Project: {project}
Detected topics: {cats_str}
Today's date: {today}

Session transcript (cleaned):
{cleaned_transcript}

Before writing the note, evaluate: Will the insights from this session change behavior
in future sessions? Is there something learnable, reusable, or architecturally significant?
Or is this session purely transient — a failed experiment with no generalizable insight,
a routine build/test run, a session that clarifies only session-specific context?

If transient (skip), respond with ONLY this JSON (no other text):
{{"decision": "skip", "reason": "<one sentence explaining why>"}}

If learnable (save), write the full vault note as specified below.

Write a complete markdown vault note. Requirements:
- YAML frontmatter: date ({today}), type (debugging|research|pattern|tool|framework|language|project),
{tags_instruction},
  project (if project-specific), confidence (high|medium|low),
  sources ([] or URLs mentioned),
  related (REQUIRED — must be a non-empty YAML list of quoted [[wikilinks]]; always provide at
  least one entry; if no specific note title is known, link to the project name or primary
  technology, e.g. ["[[{project}]]"]; an empty "related: []" is NEVER acceptable),
  session_id: {session_id}
- ## Title heading (3-5 descriptive words, not generic)
- ## Summary (2-3 sentences: what was learned and why it matters)
- ## Key Learnings (3-6 bullet points, concrete and reusable)
- ## Context (1-2 sentences: what triggered this, what project)

Respond with ONLY the raw markdown note. No preamble, no explanation, no code fences."""


def parse_note_type(note_content: str) -> str:
    """Extract the type field from note YAML frontmatter.

    Args:
        note_content: Full markdown note content.

    Returns:
        The type value, or 'research' as fallback.
    """
    match = re.search(r"^type:\s*(\S+)", note_content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return "research"


def parse_note_title_slug(note_content: str) -> str:
    """Extract the first ## heading from note content and slugify it.

    Args:
        note_content: Full markdown note content.

    Returns:
        Kebab-case slug, or 'session-note' as fallback.
    """
    match = re.search(r"^##\s+(.+)$", note_content, re.MULTILINE)
    if match:
        heading = match.group(1).strip()
        slug = vault_common.slugify(heading)
        if slug:
            return slug
    return "session-note"


def inject_project_tag(note_content: str, project: str) -> str:
    """Ensure the project name appears in the tags frontmatter field.

    Parses the YAML tags block (list or inline) and appends the project tag
    if not already present. Leaves the note unchanged if no tags field exists
    or the project tag is already there.

    Args:
        note_content: Full markdown note content.
        project: Project name to inject as a tag.

    Returns:
        Updated note content with project tag present.
    """
    if not project or project == "unknown":
        return note_content

    # Match YAML list tags block:  tags:\n  - a\n  - b
    list_match = re.search(r"^(tags:\n(?:  - .+\n)+)", note_content, re.MULTILINE)
    if list_match:
        block = list_match.group(1)
        if f"  - {project}\n" not in block:
            new_block = block.rstrip("\n") + f"\n  - {project}\n"
            return note_content.replace(block, new_block, 1)
        return note_content

    # Match inline tags:  tags: [a, b, c]
    inline_match = re.search(r"^(tags:\s*\[)([^\]]*?)(\])", note_content, re.MULTILINE)
    if inline_match:
        existing = inline_match.group(2)
        existing_tags = [t.strip() for t in existing.split(",") if t.strip()]
        if project not in existing_tags:
            existing_tags.append(project)
            new_tags = ", ".join(existing_tags)
            new_line = f"{inline_match.group(1)}{new_tags}{inline_match.group(3)}"
            return (
                note_content[: inline_match.start()]
                + new_line
                + note_content[inline_match.end() :]
            )
        return note_content

    return note_content


def write_note(note_content: str, dry_run: bool) -> Path | None:
    """Write a generated vault note to the appropriate folder.

    Args:
        note_content: Full markdown note content.
        dry_run: If True, print without writing.

    Returns:
        Path where the note was written, or None on dry-run/error.
    """
    # Strip outer code fence if the model wrapped the entire note.
    # Only strip when the content after the opening fence starts with "---"
    # (YAML frontmatter), so inner ```python fences are left untouched.
    stripped = note_content.strip()
    if stripped.startswith("```"):
        first_newline = stripped.index("\n")
        inner = stripped[first_newline + 1 :]
        if inner.lstrip().startswith("---"):
            if inner.rstrip().endswith("```"):
                inner = inner.rstrip()[:-3].rstrip()
            note_content = inner

    note_type = parse_note_type(note_content)
    folder_name = _TYPE_FOLDERS.get(note_type, _DEFAULT_FOLDER)
    slug = parse_note_title_slug(note_content)

    # Never write to Daily/ for today — the stop hook manages today's daily note
    if folder_name == "Daily":
        today = date.today().isoformat()
        fm = re.search(r"^date:\s*(\S+)", note_content, re.MULTILINE)
        note_date = fm.group(1).strip() if fm else ""
        if note_date == today:
            print(
                f"  Skipping Daily note for today ({today}) — still being built.",
                file=sys.stderr,
            )
            return None

    target_dir = vault_common.VAULT_ROOT / folder_name
    target_path = target_dir / f"{slug}.md"

    if dry_run:
        print(f"[dry-run] Would write: {target_path}")
        print("---")
        print(note_content[:500])
        print("...")
        return None

    target_dir.mkdir(parents=True, exist_ok=True)

    if target_path.exists():
        suffix = datetime.now().strftime("%H%M")
        target_path = target_dir / f"{slug}-{suffix}.md"

    try:
        target_path.write_text(note_content, encoding="utf-8")
        return target_path
    except OSError as e:
        print(f"Error writing {target_path}: {e}", file=sys.stderr)
        return None


async def _summarize_chunk(
    chunk_text: str,
    chunk_num: int,
    total_chunks: int,
    model: str,
    extra: dict[str, str | None],
) -> str:
    """Summarize one chunk of a long transcript using a cheaper model.

    Args:
        chunk_text: The transcript chunk to summarize.
        chunk_num: 1-based index of this chunk.
        total_chunks: Total number of chunks.
        model: Model ID to use for summarization.
        extra: Extra args to pass to ClaudeAgentOptions (e.g. no-session-persistence).

    Returns:
        A summary string (3-5 sentences). Falls back to a truncated version of
        chunk_text on failure.
    """
    prompt = (
        f"Summarize this portion ({chunk_num}/{total_chunks}) of a coding session "
        "transcript in 3-5 sentences, capturing key decisions, errors encountered, "
        "and solutions found. Focus on what would be useful to remember in future "
        f"sessions.\n\nTranscript:\n{chunk_text}"
    )
    result_text = ""
    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                allowed_tools=[],
                permission_mode="default",
                model=model,
                extra_args=extra,
            ),
        ):
            if isinstance(message, ResultMessage):
                result_text = message.result
    except Exception:
        pass

    if result_text:
        return result_text
    # Fallback: return truncated raw chunk
    return chunk_text[:500]


async def preprocess_transcript_hierarchical(
    transcript_path_str: str,
    tail_lines: int,
    max_cleaned_chars: int,
    cluster_model: str,
    extra: dict[str, str | None],
) -> str:
    """Pre-process a transcript, using hierarchical summarization for long ones.

    For transcripts within the character limit, returns the cleaned text
    unchanged. For transcripts exceeding the limit, splits into chunks,
    summarizes each chunk with a cheaper model, and returns the combined
    chunk summaries.

    Args:
        transcript_path_str: String path to the transcript JSONL file.
        tail_lines: Number of trailing transcript lines to read.
        max_cleaned_chars: Maximum characters threshold.
        cluster_model: Model ID to use for chunk summarization.
        extra: Extra args forwarded to the Agent SDK.

    Returns:
        Cleaned dialogue string, or hierarchical summary string for long sessions.
    """
    cleaned = preprocess_transcript(transcript_path_str, tail_lines, max_cleaned_chars)
    if len(cleaned) <= max_cleaned_chars:
        return cleaned

    # Split into chunks at newline boundaries
    chunk_size = max_cleaned_chars // 3
    chunks: list[str] = []
    remaining = cleaned
    while remaining:
        if len(remaining) <= chunk_size:
            chunks.append(remaining)
            break
        # Find a newline near the chunk boundary to avoid mid-sentence cuts
        split_pos = remaining.rfind("\n", 0, chunk_size)
        if split_pos == -1:
            split_pos = chunk_size
        chunks.append(remaining[:split_pos])
        remaining = remaining[split_pos:].lstrip("\n")

    total = len(chunks)
    print(
        f"  [hierarchical] Session too long ({len(cleaned)} chars), "
        f"summarizing {total} chunks..."
    )

    summaries: list[str] = []
    for i, chunk in enumerate(chunks):
        summary = await _summarize_chunk(chunk, i + 1, total, cluster_model, extra)
        summaries.append(summary)

    header = f"[Hierarchical summary from {total} transcript segments]"
    body = "\n\n".join(f"Segment {i + 1}:\n{s}" for i, s in enumerate(summaries))
    return f"{header}\n\n{body}"


def _find_related_by_tags(
    new_note_path: Path,
    new_tags: list[str],
    max_links: int = 5,
) -> list[str]:
    """Find existing vault notes that share tags with a new note.

    Args:
        new_note_path: Path to the newly written note (excluded from results).
        new_tags: Tags from the new note's frontmatter.
        max_links: Maximum number of related note wikilinks to return.

    Returns:
        List of ``"[[stem]]"`` wikilink strings for the top matching notes,
        sorted by tag-overlap score descending.
    """
    if not new_tags:
        return []

    new_tag_set = set(new_tags)
    candidates: list[tuple[int, Path]] = []

    for note_path in vault_common.all_vault_notes():
        # Skip the note itself and daily notes
        if note_path == new_note_path:
            continue
        if note_path.parts and "Daily" in note_path.parts:
            continue

        try:
            content = note_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        fm = vault_common.parse_frontmatter(content)
        existing_tags = fm.get("tags")
        if not isinstance(existing_tags, list):
            continue

        overlap = len(new_tag_set & {str(t) for t in existing_tags})
        if overlap >= 1:
            candidates.append((overlap, note_path))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [f"[[{p.stem}]]" for _, p in candidates[:max_links]]


def _find_related_by_semantic(
    new_note_path: Path,
    vault_root: Path,
    max_links: int = 5,
) -> list[str]:
    """Find related vault notes using semantic search via vault_search.py subprocess.

    Returns an empty list when vault_search.py or embeddings.db is missing,
    or when the subprocess fails for any reason.

    Args:
        new_note_path: Path to the newly written note (excluded from results).
        vault_root: Vault root directory.
        max_links: Maximum number of related note wikilinks to return.

    Returns:
        List of ``"[[stem]]"`` wikilink strings, sorted by semantic similarity.
    """
    import json as _json

    vault_search_script = Path(__file__).parent / "vault_search.py"
    if not vault_search_script.exists():
        return []

    db_path = vault_common.VAULT_ROOT / vault_common.EMBEDDINGS_DB_FILENAME
    if not db_path.exists():
        return []

    # Build query from stem and tags of the new note
    try:
        content = new_note_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    fm = vault_common.parse_frontmatter(content)
    note_tags = fm.get("tags") or []
    if not isinstance(note_tags, list):
        note_tags = []
    tag_part = " ".join(str(t) for t in note_tags)
    query = f"{new_note_path.stem.replace('-', ' ')} {tag_part}".strip()

    try:
        result = subprocess.run(
            [
                "uv",
                "run",
                "--no-project",
                str(vault_search_script),
                query,
                "--top",
                str(max_links + 1),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            env=vault_common.env_without_claudecode(),
        )
        if result.returncode != 0:
            return []
        items: list[dict[str, object]] = _json.loads(result.stdout)
    except (
        subprocess.TimeoutExpired,
        FileNotFoundError,
        OSError,
        _json.JSONDecodeError,
    ):
        return []

    links: list[str] = []
    for item in items:
        stem = str(item.get("stem", ""))
        if not stem or stem == new_note_path.stem:
            continue
        links.append(f"[[{stem}]]")
        if len(links) >= max_links:
            break

    return links


def _inject_related_links(note_path: Path, new_links: list[str]) -> None:
    """Merge new wikilinks into the ``related`` frontmatter field of a note.

    Only modifies the ``related:`` line in frontmatter. Uses inline quoted
    array format: ``related: ["[[a]]", "[[b]]"]``.

    Args:
        note_path: Path to the note to update.
        new_links: Wikilinks to add (e.g. ``["[[note-a]]", "[[note-b]]"]``).
    """
    try:
        content = note_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return

    fm = vault_common.parse_frontmatter(content)
    existing_related = fm.get("related") or []
    if not isinstance(existing_related, list):
        existing_related = []
    # Normalise existing entries to strings
    existing_strs: list[str] = [str(r) for r in existing_related]

    merged = existing_strs + [lnk for lnk in new_links if lnk not in existing_strs]
    if merged == existing_strs:
        # Nothing new to add
        return

    # Build the replacement line using inline quoted array format
    quoted_items = ", ".join(f'"{lnk}"' for lnk in merged)
    new_related_line = f"related: [{quoted_items}]"

    # Replace existing related: line, or insert before closing --- if absent
    if re.search(r"^related:.*$", content, re.MULTILINE):
        updated = re.sub(
            r"^related:.*$", new_related_line, content, count=1, flags=re.MULTILINE
        )
    else:
        # Insert before the closing --- of frontmatter
        updated = content.replace("\n---\n", f"\n{new_related_line}\n---\n", 1)

    try:
        note_path.write_text(updated, encoding="utf-8")
    except OSError:
        pass


def _add_backlinks_to_existing(
    new_note_path: Path,
    related_notes: list[str],
) -> list[Path]:
    """Add a backlink to ``new_note_path`` in each of the ``related_notes``.

    For each wikilink in ``related_notes``, locates the corresponding note
    file in the vault and calls ``_inject_related_links()`` to add a
    back-reference to ``new_note_path``.

    Args:
        new_note_path: Path to the newly written note.
        related_notes: List of ``"[[stem]]"`` wikilinks for existing notes.

    Returns:
        List of Paths that were modified.
    """
    new_link = f"[[{new_note_path.stem}]]"
    modified: list[Path] = []

    # Build a stem -> path index from all vault notes once
    stem_index: dict[str, Path] = {}
    for note_path in vault_common.all_vault_notes():
        stem_index[note_path.stem] = note_path

    for wikilink in related_notes:
        # Extract stem from [[stem]]
        stem_match = re.match(r"^\[\[(.+)\]\]$", wikilink)
        if not stem_match:
            continue
        stem = stem_match.group(1)
        target_path = stem_index.get(stem)
        if target_path is None or target_path == new_note_path:
            continue
        _inject_related_links(target_path, [new_link])
        modified.append(target_path)

    return modified


async def summarize_one(
    entry: dict[str, object],
    model: str,
    dry_run: bool,
    semaphore: anyio.Semaphore,
    existing_tags: list[str],
    persist: bool,
    tail_lines: int = _DEFAULT_TRANSCRIPT_TAIL_LINES,
    max_cleaned_chars: int = _DEFAULT_MAX_CLEANED_CHARS,
    cluster_model: str = _DEFAULT_CLUSTER_MODEL,
) -> tuple[dict[str, object], Path | None]:
    """Summarize one pending session entry.

    Args:
        entry: Pending entry dict with transcript_path, project, categories.
        model: Model ID to use.
        dry_run: If True, print without writing.
        semaphore: Concurrency limiter.
        existing_tags: All tags currently in the vault.
        persist: If True, allow the SDK to persist the session to disk.
        tail_lines: Number of transcript lines to read.
        max_cleaned_chars: Maximum characters after cleaning.
        cluster_model: Model ID for hierarchical chunk summarization.

    Returns:
        Tuple of (entry, written_path). written_path is None on dry-run,
        skip decision, or error.
    """
    async with semaphore:
        transcript_path_str = str(entry.get("transcript_path", ""))
        project = str(entry.get("project", "unknown"))
        raw_cats = entry.get("categories") or []
        categories = [str(c) for c in (raw_cats if isinstance(raw_cats, list) else [])]
        session_id = str(entry.get("session_id") or Path(transcript_path_str).stem)

        extra: dict[str, str | None] = (
            {} if persist else {"no-session-persistence": None}
        )

        cleaned = await preprocess_transcript_hierarchical(
            transcript_path_str, tail_lines, max_cleaned_chars, cluster_model, extra
        )
        if not cleaned:
            print(
                f"  Skipping {transcript_path_str}: could not read transcript",
                file=sys.stderr,
            )
            return entry, None

        prompt = build_prompt(project, categories, cleaned, existing_tags, session_id)

        result_text = ""
        try:
            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(
                    allowed_tools=[],
                    permission_mode="default",
                    model=model,
                    extra_args=extra,
                ),
            ):
                if isinstance(message, ResultMessage):
                    result_text = message.result
        except Exception as e:
            print(
                f"  Error querying Claude for {transcript_path_str}: {e}",
                file=sys.stderr,
            )
            return entry, None

        if not result_text:
            print(f"  No result from Claude for {transcript_path_str}", file=sys.stderr)
            return entry, None

        # Write-gate: check if Claude decided this session is not worth saving
        stripped_result = result_text.strip()
        if stripped_result.startswith("{"):
            try:
                decision = json.loads(stripped_result)
                if isinstance(decision, dict) and decision.get("decision") == "skip":
                    reason = decision.get("reason", "no reason given")
                    short_id = str(entry.get("session_id", "?"))[:8]
                    print(f"  [write-gate] Skipping session {short_id}: {reason}")
                    return entry, None
            except (json.JSONDecodeError, ValueError):
                pass  # Not a skip decision — treat as normal note

        result_text = inject_project_tag(result_text, project)
        written = write_note(result_text, dry_run)

        # Automated backlink suggestion
        if written is not None:
            try:
                new_fm = vault_common.parse_frontmatter(
                    written.read_text(encoding="utf-8")
                )
                note_tags = new_fm.get("tags") or []
                if not isinstance(note_tags, list):
                    note_tags = []
                tag_strs = [str(t) for t in note_tags]
                related_links = _find_related_by_semantic(
                    written, vault_common.VAULT_ROOT, max_links=5
                )
                if not related_links:
                    related_links = _find_related_by_tags(written, tag_strs)
                if related_links:
                    _inject_related_links(written, related_links)
                    _add_backlinks_to_existing(written, related_links)
                    print(
                        f"  [backlinks] Added {len(related_links)} related links "
                        f"to {written.name}"
                    )
            except (OSError, UnicodeDecodeError):
                pass  # Backlink step is best-effort; never fail the main flow

        return entry, written


async def run_all(
    entries: list[dict[str, object]],
    model: str,
    dry_run: bool,
    persist: bool,
    max_parallel: int = _DEFAULT_MAX_PARALLEL,
    tail_lines: int = _DEFAULT_TRANSCRIPT_TAIL_LINES,
    max_cleaned_chars: int = _DEFAULT_MAX_CLEANED_CHARS,
    cluster_model: str = _DEFAULT_CLUSTER_MODEL,
) -> list[tuple[dict[str, object], Path | None]]:
    """Run all summarization tasks in parallel.

    Args:
        entries: List of pending entries.
        model: Model ID.
        dry_run: If True, print without writing.
        persist: If True, allow SDK session persistence.
        max_parallel: Maximum concurrent summarization tasks.
        tail_lines: Transcript tail lines per entry.
        max_cleaned_chars: Max cleaned chars per entry.
        cluster_model: Model ID for hierarchical chunk summarization.

    Returns:
        List of (entry, written_path) tuples.
    """
    existing_tags = read_existing_tags()
    project_names = read_project_names()
    # Filter project names out — they're injected post-generation, not chosen by the model
    semantic_tags = [t for t in existing_tags if t not in project_names]
    semaphore = anyio.Semaphore(max_parallel)
    results: list[tuple[dict[str, object], Path | None]] = []

    async def _run_one(entry: dict[str, object]) -> None:
        """Wrapper that collects the result of summarize_one into *results*."""
        result = await summarize_one(
            entry,
            model,
            dry_run,
            semaphore,
            semantic_tags,
            persist,
            tail_lines,
            max_cleaned_chars,
            cluster_model,
        )
        results.append(result)

    async with anyio.create_task_group() as tg:
        for entry in entries:
            tg.start_soon(_run_one, entry)

    return results


def remove_processed(
    pending_path: Path,
    processed_entries: list[dict[str, object]],
) -> None:
    """Remove successfully processed entries from the pending file.

    Args:
        pending_path: Path to the pending JSONL file.
        processed_entries: Entries that were successfully processed.
    """
    if not pending_path.exists():
        return

    # Prefer session_id for matching; fall back to transcript_path for entries
    # written by older versions of the hook that lack session_id.
    processed_ids = {
        str(e.get("session_id") or e.get("transcript_path", ""))
        for e in processed_entries
    }

    try:
        # Open r+ so read and truncate+rewrite happen under a single
        # exclusive lock — stop hooks appending concurrently will block
        # until the rewrite completes, preventing lost entries.
        with open(pending_path, "r+", encoding="utf-8") as f:
            _flock_exclusive(f)
            try:
                remaining: list[str] = []
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        key = str(
                            entry.get("session_id") or entry.get("transcript_path", "")
                        )
                        if key not in processed_ids:
                            remaining.append(line)
                    except (json.JSONDecodeError, ValueError):
                        remaining.append(line)  # Keep malformed lines
                f.seek(0)
                f.truncate()
                for line in remaining:
                    f.write(line + "\n")
            finally:
                _funlock(f)
    except OSError as e:
        print(f"Warning: could not update pending file: {e}", file=sys.stderr)


def rebuild_index() -> None:
    """Run update_index.py to rebuild the vault index."""
    index_script = Path(__file__).parent / "update_index.py"
    if not index_script.exists():
        # Try installed location
        index_script = (
            Path.home()
            / ".claude"
            / "skills"
            / "claude-vault"
            / "scripts"
            / "update_index.py"
        )
    if not index_script.exists():
        print(
            "Warning: update_index.py not found, skipping index rebuild",
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


def main() -> None:
    """Parse arguments and run the summarizer."""
    parser = argparse.ArgumentParser(
        description="AI-powered session summarizer for Claude Vault",
    )
    parser.add_argument(
        "--sessions",
        metavar="FILE",
        help="Process an explicit JSONL file (same format as pending file)",
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        default=False,
        help="Preview what would be created without writing",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"Override model (default: {_DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        default=None,
        help="Enable SDK session persistence (default: off). Use when debugging to inspect saved sessions.",
    )
    args = parser.parse_args()

    # Resolve options: defaults → config → CLI args
    model: str = (
        args.model
        if args.model is not None
        else vault_common.get_config("summarizer", "model", _DEFAULT_MODEL)
    )
    persist: bool = (
        args.persist
        if args.persist is not None
        else vault_common.get_config("summarizer", "persist", False)
    )
    max_parallel: int = vault_common.get_config(
        "summarizer",
        "max_parallel",
        _DEFAULT_MAX_PARALLEL,
    )
    tail_lines: int = vault_common.get_config(
        "summarizer",
        "transcript_tail_lines",
        _DEFAULT_TRANSCRIPT_TAIL_LINES,
    )
    max_cleaned_chars: int = vault_common.get_config(
        "summarizer",
        "max_cleaned_chars",
        _DEFAULT_MAX_CLEANED_CHARS,
    )
    cluster_model: str = vault_common.get_config(
        "summarizer",
        "cluster_model",
        _DEFAULT_CLUSTER_MODEL,
    )

    # Determine source file
    if args.sessions:
        source_path = Path(args.sessions).expanduser()
    else:
        # Default: pending file
        source_path = vault_common.VAULT_ROOT / "pending_summaries.jsonl"

    entries = read_pending(source_path)
    if not entries:
        print(f"No pending sessions in {source_path}")
        return

    print(f"Processing {len(entries)} session(s) with model {model}...")
    if args.dry_run:
        print("[dry-run mode — nothing will be written]")

    results: list[tuple[dict[str, object], Path | None]] = cast(
        list[tuple[dict[str, object], Path | None]],
        anyio.run(
            run_all,
            entries,
            model,
            args.dry_run,
            persist,
            max_parallel,
            tail_lines,
            max_cleaned_chars,
            cluster_model,
        ),
    )

    # Categorise results: written notes, write-gate skips, and hard failures.
    # A None written_path covers both "skipped by write-gate" and "failed";
    # the write-gate already prints its own message, so we just distinguish
    # from any other None (no transcript, query error) to avoid double-printing.
    successful_entries: list[dict[str, object]] = []
    failed_count = 0
    for entry, written_path in results:
        if written_path is not None:
            print(f"  Written: {written_path}")
            successful_entries.append(entry)
        elif not args.dry_run:
            # write-gate skips already printed their own "[write-gate]" line;
            # count everything else as a failure for the summary line.
            failed_count += 1

    skipped_count = len(entries) - len(successful_entries) - failed_count

    if not args.dry_run:
        # Remove processed entries from pending file (only when using the default pending path)
        if not args.sessions and successful_entries:
            remove_processed(source_path, successful_entries)

        # Rebuild vault index and commit all new notes + updated index
        if successful_entries:
            rebuild_index()
            projects = {str(e.get("project", "unknown")) for e in successful_entries}
            project_str = ", ".join(sorted(projects))
            vault_common.git_commit_vault(
                f"chore(vault): add session notes [{project_str}]"
            )

    summary_parts = [f"{len(successful_entries)} written"]
    if skipped_count:
        summary_parts.append(f"{skipped_count} skipped by write-gate")
    if failed_count:
        summary_parts.append(f"{failed_count} failed")
    print(
        f"Done. {len(entries)} session(s) processed: {', '.join(summary_parts)}."
    )


if __name__ == "__main__":
    main()
