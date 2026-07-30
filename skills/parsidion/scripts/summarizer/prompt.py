"""Prompt construction: build_prompt, tag/dedup renderers, template loader.

Extracted from ``summarize_sessions.py`` (ARC-009).

``build_prompt`` is test-patched on the entry shim
(``monkeypatch.setattr(summarize_sessions, "build_prompt", ...)``) and is called
from ``summarize_one`` in the shim.  Since the shim re-exports ``build_prompt``,
the shim's bare-name lookup at call time sees the patched version.  The prompt
helpers themselves (``_render_tags_instruction``, ``_render_dedup_block``,
``_load_prompt_template``) are not patched and call no patched functions, so
they extract cleanly.

``_PROMPT_TEMPLATE_CACHE`` is a mutable dict that tests ``.clear()`` on the
shim attribute — the re-export shares the same dict object so the in-place
mutation is visible to ``_load_prompt_template`` here.
"""

from __future__ import annotations

import string
from datetime import date

import vault_common

from summarizer._state_const import _VALID_NOTE_TYPES

# ARC-029: shared kebab-case / short-singular tag rule used by both branches
# of _render_tags_instruction so a single edit updates both.
_TAG_RULES_COMMON = (
    "  NEVER use underscores — always kebab-case (hyphens);\n"
    "  prefer short singular tags: 'voxel' not 'voxel-engine', 'hook' not 'hooks')"
)

# Cache loaded prompt templates so repeated calls in a summarizer run read
# each file once.  ``string.Template`` is immutable so caching the parsed
# object is safe.
_PROMPT_TEMPLATE_CACHE: dict[str, string.Template] = {}


def _render_tags_instruction(existing_tags: list[str]) -> str:
    """Render the frontmatter ``tags`` instruction line for the note prompt.

    When the vault has existing tags, instruct the model to STRONGLY prefer
    them (the canonical source for tag reuse). When no tags exist (fresh
    vault), instruct generic tag generation. Both branches share the same
    kebab-case / short-singular rule via :data:`_TAG_RULES_COMMON`.
    """
    if existing_tags:
        tags_str = ", ".join(existing_tags)
        return (
            f"  tags (2-4 tags — STRONGLY prefer existing tags: {tags_str};\n"
            "  only introduce a new tag if none of the existing ones fit;\n"
            + _TAG_RULES_COMMON
        )
    return "  tags (2-4 relevant tags;\n" + _TAG_RULES_COMMON


def _render_dedup_block(
    similar_notes: list[tuple[str, float, str]] | None,
) -> str:
    """Render the optional ``IMPORTANT: similar notes found`` dedup block.

    Empty string when no similar notes were found. The JSON example uses
    literal ``{ }`` because the block is substituted into a
    ``string.Template`` (not an f-string) and needs no escaping.
    """
    if not similar_notes:
        return ""
    note_lines: list[str] = []
    for stem, score, summary in similar_notes[:3]:
        note_lines.append(f"  - [[{stem}]] (similarity {score:.2f}): {summary or stem}")
    notes_str = "\n".join(note_lines)
    return (
        "\n"
        "IMPORTANT: The following existing vault notes are highly similar to this session\n"
        "(semantic similarity >= threshold). Prefer MERGING new insights into one of them\n"
        "rather than creating a duplicate note. Only create a new note if the new insights\n"
        "are genuinely distinct from all of these:\n"
        "\n"
        f"{notes_str}\n"
        "\n"
        "If you decide to merge, output ONLY this JSON (no other text):\n"
        '{{"decision": "merge", "target": "[[stem-of-note-to-update]]", "new_content": "<full updated note markdown>"}}\n'
    )


def _load_prompt_template(name: str) -> string.Template:
    """Load and cache ``templates/prompts/<name>`` as a string.Template.

    Resolution order mirrors resolve_templates_dir(): sibling ``templates/``
    dir next to this script (repo source layout) → installed
    ``~/.claude/skills/parsidion/templates``. Falls back to an empty
    Template on any read error so the caller's ``.substitute`` returns its
    placeholders verbatim — better than crashing a summarizer run because
    a prompt file is missing.
    """
    if name in _PROMPT_TEMPLATE_CACHE:
        return _PROMPT_TEMPLATE_CACHE[name]
    template_path = vault_common.resolve_templates_dir() / "prompts" / name
    try:
        content = template_path.read_text(encoding="utf-8")
    except OSError:
        content = ""
    template = string.Template(content)
    _PROMPT_TEMPLATE_CACHE[name] = template
    return template


def build_prompt(
    project: str,
    categories: list[str],
    cleaned_transcript: str,
    existing_tags: list[str],
    session_id: str,
    similar_notes: list[tuple[str, float, str]] | None = None,
) -> str:
    """Build the backend prompt for generating a vault note.

    Args:
        project: Project name.
        categories: Detected topic categories.
        cleaned_transcript: Pre-processed transcript text.
        existing_tags: All tags currently in the vault (for reuse preference).
        session_id: Runtime session ID to embed in frontmatter.
        similar_notes: Optional list of (stem, score, summary) tuples for
            near-duplicate notes found by semantic search.  When provided and
            non-empty, instructs the backend to merge rather than create a new note.

    Returns:
        Complete prompt string.
    """
    today = date.today().isoformat()
    cats_str = ", ".join(categories) if categories else "general"
    # ARC-029: tag-rules instruction and dedup-block construction are
    # extracted (the tag rule text was duplicated inline in the two branches
    # and had drifted in whitespace).
    tags_instruction = _render_tags_instruction(existing_tags)
    dedup_block = _render_dedup_block(similar_notes)
    valid_types = ", ".join(sorted(_VALID_NOTE_TYPES))
    template = _load_prompt_template("note_writing.txt")
    # SEC-004: the SYSTEM preamble (now in the template) instructs the model
    # to treat the transcript as passive data, not as instructions.
    return template.substitute(
        project=project,
        cats_str=cats_str,
        today=today,
        dedup_block=dedup_block,
        cleaned_transcript=cleaned_transcript,
        tags_instruction=tags_instruction,
        valid_types=valid_types,
        session_id=session_id,
    )
