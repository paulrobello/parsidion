"""Prompt construction: build_prompt, tag/dedup renderers, template loader.

Extracted from ``summarize_sessions.py`` (ARC-009).

``build_prompt`` is test-patched on the entry shim
(``monkeypatch.setattr(summarize_sessions, "build_prompt", ...)``) and is called
from ``summarize_one`` in the shim.  Since the shim re-exports ``build_prompt``,
the shim's bare-name lookup at call time sees the patched version.  The prompt
helpers themselves (``_render_tags_instruction``, ``_render_dedup_block``) are
not patched and call no patched functions, so they extract cleanly.

ENH-008: the template files now live under ``templates/prompts/*.md`` with YAML
frontmatter and are loaded through ``prompt_templates.render`` (strict
variable contract, lru_cache-cached).  ``_load_prompt_template`` remains as a
thin backward-compat shim so the pre-ENH-008 caching test and any external
callers keep working; it delegates to the new loader.
"""

from __future__ import annotations

import string
from datetime import date
from functools import partial
from pathlib import Path
from typing import cast

import ai_backend
from anyio import to_thread  # type: ignore[import-untyped]

from prompt_templates import load_prompt, render
from summarizer._state_const import _VALID_NOTE_TYPES

# ARC-029 / ENH-008: shared kebab-case / short-singular tag rule, sourced from
# note_schema so the rule is stated exactly once across the whole codebase.
import note_schema as _note_schema

_TAG_RULES_COMMON = _note_schema.TAG_RULES

# Backward-compat cache handle. The new loader uses functools.lru_cache, so
# this dict is kept only so ``_PROMPT_TEMPLATE_CACHE.clear()`` in existing
# tests remains a no-op rather than an AttributeError. Tests that need to
# force a re-read call ``prompt_templates.reset_cache()`` instead.
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
    """Backward-compat shim over the ENH-008 loader.

    Historical callers (and the caching regression test) passed a literal
    filename like ``note_writing.txt``. The new loader keys by canonical
    template id, so map the two legacy filenames to their ids and return a
    ``string.Template`` wrapping the loaded body. The constructed Template is
    cached per name so repeated calls return the same object (preserves the
    pre-ENH-008 identity contract). Clearing ``_PROMPT_TEMPLATE_CACHE``
    drops this wrapper cache; the underlying ``load_prompt`` lru_cache is
    cleared via ``prompt_templates.reset_cache()``.
    """
    cached = _PROMPT_TEMPLATE_CACHE.get(name)
    if cached is not None:
        return cached
    _LEGACY = {
        "note_writing.txt": "summarize-session",
        "chunk_summary.txt": "summarize-chunk",
    }
    prompt_id = _LEGACY.get(name, name.removesuffix(".txt"))
    tpl = load_prompt(prompt_id)
    wrapper = string.Template(tpl.body)
    _PROMPT_TEMPLATE_CACHE[name] = wrapper
    return wrapper


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
    # SEC-004: the SYSTEM preamble (in the template) instructs the model to
    # treat the transcript as passive data, not as instructions.
    return render(
        "summarize-session",
        project=project,
        cats_str=cats_str,
        today=today,
        dedup_block=dedup_block,
        cleaned_transcript=cleaned_transcript,
        tags_instruction=tags_instruction,
        valid_types=valid_types,
        session_id=session_id,
    )


async def _run_summarizer_prompt(
    prompt: str,
    *,
    model: str | None,
    model_tier: ai_backend.ModelTier,
    purpose: str,
    timeout: int | float | None,
    vault: Path,
) -> str | None:
    """Run a summarizer prompt through the configured AI backend.

    QA-003: moved here from the entry shim so :mod:`summarizer.pipeline` can
    import it without reaching back into the PEP-723 entry script. Tests swap
    ``sys.modules["anyio"]`` per test (real anyio is not installed in the dev
    env); this module is imported once and cached, so the stub-swap test
    (``test_run_summarizer_prompt_delegates_to_ai_backend_in_thread``) pops
    ``summarizer.prompt`` to force a re-import against its own stub.
    """
    result = await to_thread.run_sync(
        partial(
            ai_backend.run_ai_prompt,
            prompt,
            model=model,
            model_tier=model_tier,
            purpose=purpose,
            timeout=timeout,
            vault=vault,
        )
    )
    return cast(str | None, result)


async def _run_summarizer_prompt_with_cause(
    prompt: str,
    *,
    model: str | None,
    model_tier: ai_backend.ModelTier,
    purpose: str,
    timeout: int | float | None,
    vault: Path,
) -> tuple[str | None, str | None]:
    """Like ``_run_summarizer_prompt`` but also returns the backend failure cause.

    The top-level note-generation call uses this so an empty result can be
    classified (timeout vs empty vs backend error) for the dead-letter record;
    chunk/write-gate callers keep the text-only variant since they don't
    dead-letter on ``no_result``.
    """
    return cast(
        tuple[str | None, str | None],
        await to_thread.run_sync(
            partial(
                ai_backend.run_ai_prompt_with_cause,
                prompt,
                model=model,
                model_tier=model_tier,
                purpose=purpose,
                timeout=timeout,
                vault=vault,
            )
        ),
    )
