"""Summarize-one dispatch pipeline (QA-003).

The decision state machine — :func:`summarize_one` and its four stage helpers
(:func:`_early_gate`, :func:`_apply_merge_decision`,
:func:`_handle_write_gate_decision`, :func:`_apply_backlinks_and_strip_links`)
— extracted from the entry shim so the shim is a thin PEP-723 entrypoint
again. Their linear call sequence in :func:`summarize_one` IS the decision
dispatch's state machine.

Tests monkeypatch the stage helpers and the backend/preprocess dependencies on
THIS module (``summarizer.pipeline.X``): Python resolves bare names in the
caller's module globals at call time, so a patch takes effect only where the
name is looked up. The entry shim re-exports every name here so legacy
``summarize_sessions.X`` references (callers and the ``run_all`` driver) keep
resolving unchanged.

Stdlib-only is NOT required here (unlike the hook scripts): runs under the
PEP-723 entry script's env and MAY ``import anyio`` and ``import vault_common``.
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
import traceback
from pathlib import Path

import anyio  # type: ignore[import-untyped]  # annotation only (semaphore param)
import vault_common
import vault_links
from vault_path import is_path_inside_vault

from prompt_templates import load_prompt
from summarizer._state_const import (
    _ACTIVE_SESSION_GRACE_SECS,
    _DEAD,
    _DEFAULT_MAX_CLEANED_CHARS,
    _DEFAULT_TRANSCRIPT_TAIL_BYTES,
    _DEFAULT_TRANSCRIPT_TAIL_LINES,
    _DEFERRED,
    _SKIPPED,
    _STALE,
    FailureReason,
)
from summarizer.dead_letter import _dead_lettered_ids
from summarizer.dedup import _find_dedup_candidates, _resolve_note_stem
from summarizer.failure import _mark_failure
from summarizer.notes import (
    _backup_note,
    _normalize_related_field,
    _stamp_prompt_version,
    _validate_frontmatter,
    inject_project_tag,
    write_note,
)
from summarizer.prompt import _run_summarizer_prompt, build_prompt
from summarizer.transcript import _strip_code_fence, preprocess_transcript_hierarchical


def _early_gate(
    entry: dict[str, object],
    vault: Path,
    dead_lettered_ids: set[str] | None,
) -> tuple[dict[str, object], str] | None:
    """Stale-transcript, dead-letter, and active-session guards for ``summarize_one``.

    Pure reads/stats — no AI call, no queue mutation. Returns an
    ``(entry, sentinel)`` tuple when a guard trips (``_STALE`` / ``_DEAD`` /
    ``_DEFERRED``), or ``None`` to signal the caller should continue with
    normal summarization.

    QA-003: ``_ACTIVE_SESSION_GRACE_SECS`` is read via bare-name resolution
    through THIS module's globals, so test monkeypatches of
    ``summarizer.pipeline._ACTIVE_SESSION_GRACE_SECS`` take effect (and the
    shim's ``_fresh_summarize_sessions`` default-disables it here). The same
    applies to ``_resolve_note_stem`` inside ``_apply_merge_decision``.
    """
    transcript_path_str = str(entry.get("transcript_path", ""))
    session_id = str(entry.get("session_id") or Path(transcript_path_str).stem)

    # Check for missing transcript before expensive preprocessing.
    # Subagent transcripts are ephemeral — Claude Code may rename or
    # delete them between hook fire time and summarizer run.  Mark
    # these as stale so they get purged from the pending queue.
    if not Path(transcript_path_str).is_file():
        print(
            f"  Purging stale entry (transcript missing): {transcript_path_str}",
            file=sys.stderr,
        )
        return entry, _STALE

    # A session already recorded in dead_letters (prior failure or
    # write-gate skip) must not be re-processed even if a stop hook
    # re-queued it — that would re-bill an AI call for a session already
    # judged not worth a note. Purge on sight.
    # ARC-028: callers fan out many summarize_one calls in parallel; the
    # dead-letter set is read ONCE per run (see run_all) and passed in to
    # avoid re-reading the file once per entry. Tests and one-shot callers
    # that omit the parameter fall back to a single read here.
    dead_ids = (
        dead_lettered_ids
        if dead_lettered_ids is not None
        else _dead_lettered_ids(vault)
    )
    if session_id in dead_ids:
        print(
            f"  Purging re-queued dead-lettered session {session_id[:8]}",
            file=sys.stderr,
        )
        return entry, _DEAD

    # Active-session guard: a transcript still being written (this very
    # session, or one whose process is still flushing) is mutating under us.
    # Summarizing it mid-flight is racy and yields partial notes, so defer
    # it — leave it in the queue for a later run once it's genuinely idle.
    try:
        _transcript_age = time.time() - Path(transcript_path_str).stat().st_mtime
    except OSError:
        _transcript_age = float("inf")
    if _transcript_age < _ACTIVE_SESSION_GRACE_SECS:
        print(
            f"  Deferring active session {session_id[:8]} "
            f"(transcript modified {int(_transcript_age)}s ago)",
            file=sys.stderr,
        )
        return entry, _DEFERRED

    return None


def _apply_merge_decision(
    decision: dict[str, object],
    entry: dict[str, object],
    dry_run: bool,
    vault: Path,
) -> tuple[dict[str, object], Path | None]:
    """Apply a ``merge`` write-gate decision by overwriting an existing note.

    SEC-107/SEC-125 guards travel with this branch: frontmatter validation,
    vault containment re-check, and atomic write with a pre-overwrite backup.
    Returns ``(entry, resolved_target)`` on success, or ``(entry, None)`` with
    a failure reason recorded via ``_mark_failure`` on any guard failure.
    """
    # The backend chose to merge into an existing note.
    # A malformed or unresolvable merge must NOT fall
    # through to the generic write path — result_text is
    # still raw decision JSON there and always fails
    # frontmatter validation with a misleading error.
    # Fail with the real reason instead; the attempts cap
    # in remove_processed() bounds retries.
    target_wikilink = str(decision.get("target", ""))
    new_content = str(decision.get("new_content", ""))
    if not new_content or not target_wikilink:
        reason = "merge decision missing target or new_content"
        print(f"  {reason}", file=sys.stderr)
        _mark_failure(entry, FailureReason.MERGE_MALFORMED, reason)
        return entry, None
    # Extract stem from [[stem]] wikilink
    target_stem = target_wikilink.strip("[]")
    target_path = _resolve_note_stem(target_stem, vault)
    if dry_run:
        print(f"  [dry-run] Would merge into [[{target_stem}]]")
        return entry, None
    if target_path is None:
        reason = f"merge target [[{target_stem}]] could not be resolved"
        print(f"  {reason}", file=sys.stderr)
        _mark_failure(entry, FailureReason.MERGE_UNRESOLVABLE, reason)
        return entry, None
    new_content = _normalize_related_field(new_content)
    new_content, _stripped = vault_links.strip_unresolved_wikilinks(new_content, vault)
    # SEC-107: validate AI-generated merge content the same
    # way write_note validates a freshly created note. A
    # crafted transcript could otherwise steer the model
    # into emitting decision JSON whose ``new_content``
    # overwrites a trusted, frequently-retrieved note with
    # arbitrary/invalid frontmatter. Abort the merge (return
    # the failure sentinel) when validation fails so the
    # attempts cap in remove_processed bounds retries.
    merge_fm_error = _validate_frontmatter(new_content)
    if merge_fm_error:
        print(
            f"  Refusing to merge into [[{target_stem}]]: {merge_fm_error}",
            file=sys.stderr,
        )
        _mark_failure(entry, FailureReason.MERGE_VALIDATION, merge_fm_error)
        return entry, None
    # SEC-107 / SEC-125: containment re-check on the resolved
    # target so a symlinked or path-traversal target cannot
    # escape the vault at write time. ``_resolve_note_stem``
    # currently returns indexed paths so containment holds
    # today, but the model output (and therefore the target
    # wikilink) is attacker-influenced — check anyway.
    resolved_target = target_path.resolve()
    if not is_path_inside_vault(resolved_target, vault):
        reason = (
            f"merge target [[{target_stem}]] resolves outside vault: {resolved_target}"
        )
        print(f"  {reason}", file=sys.stderr)
        _mark_failure(entry, FailureReason.MERGE_CONTAINMENT, reason)
        return entry, None
    # SEC-107: back up the existing note before overwriting,
    # mirroring vault_doctor._backup_note. A failed merge
    # must never destroy the only copy of a trusted note.
    try:
        _backup_note(target_path, vault)
    except OSError as backup_err:
        print(
            f"  Warning: merge backup failed for [[{target_stem}]]: {backup_err}",
            file=sys.stderr,
        )
        _mark_failure(entry, FailureReason.BACKUP_FAILED, str(backup_err))
        return entry, None
    # SEC-127: atomic write preserves the existing mode and
    # is crash-safe (the create path uses the same primitive).
    vault_common.atomic_write_text(resolved_target, new_content)
    if _stripped:
        print(
            f"  [links] Stripped {_stripped} non-resolving wikilink(s)",
            file=sys.stderr,
        )
    print(f"  [dedup-merge] Updated [[{target_stem}]] instead of creating new note")
    return entry, resolved_target


def _handle_write_gate_decision(
    result_text: str,
    entry: dict[str, object],
    dry_run: bool,
    vault: Path,
    vault_notes: list[Path] | None,
) -> tuple[dict[str, object], Path | str | None] | None:
    """Apply the backend's write-gate ``skip`` / ``merge`` decision.

    Strips a wrapping markdown code fence, parses the decision JSON, and
    dispatches ``skip`` (returns ``_SKIPPED``) or ``merge`` (delegates to
    ``_apply_merge_decision``). Returns ``None`` to signal the caller should
    fall through to the normal note-write path (unstructured output, a
    non-skip/merge decision, or a fence that did not wrap JSON).
    """
    del vault_notes  # used only by the backlink step; accepted for call-site symmetry

    # Write-gate: check if the backend decided this session is not worth
    # saving or should merge. Strip a wrapping ```json code fence first —
    # otherwise a fenced skip/merge decision starts with a backtick, misses
    # this JSON branch, falls through to write_note, and fails frontmatter
    # validation (false "failed" result).
    candidate = _strip_code_fence(result_text)
    if candidate.startswith("{"):
        try:
            decision = json.loads(candidate)
            if isinstance(decision, dict):
                if decision.get("decision") == "skip":
                    reason = decision.get("reason", "no reason given")
                    short_id = str(entry.get("session_id", "?"))[:8]
                    print(f"  [write-gate] Skipping session {short_id}: {reason}")
                    return entry, _SKIPPED
                if decision.get("decision") == "merge":
                    return _apply_merge_decision(decision, entry, dry_run, vault)
        except (json.JSONDecodeError, ValueError):
            pass  # Not a structured decision — treat as normal note
    return None


def _apply_backlinks_and_strip_links(
    written: Path | None,
    vault: Path,
    vault_notes: list[Path] | None,
) -> None:
    """Strip unresolved wikilinks from the new note and inject backlinks.

    Best-effort: any ``OSError`` / ``UnicodeDecodeError`` is swallowed so the
    link-rewrite and backlink steps never fail the main summarization flow.
    """
    if written is None:
        return
    # Strip wikilinks the backend invented that resolve to no vault note
    # — the recurring [[<project>]] "hub" link that mirrors the project
    # field but points at nothing. Runs before backlinks so the note only
    # ever holds real, resolving links; write_note stays a pure writer.
    try:
        _written_text = written.read_text(encoding="utf-8")
        _written_text, _stripped = vault_links.strip_unresolved_wikilinks(
            _written_text, vault
        )
        if _stripped:
            # SEC-127: route through atomic_write_text so the link
            # rewrite is crash-atomic and preserves the note's mode.
            vault_common.atomic_write_text(written, _written_text)
            print(
                f"  [links] Stripped {_stripped} non-resolving wikilink(s)",
                file=sys.stderr,
            )
    except (OSError, UnicodeDecodeError):
        pass  # best-effort; never fail the main flow
    try:
        new_fm = vault_common.parse_frontmatter(written.read_text(encoding="utf-8"))
        note_tags = new_fm.get("tags") or []
        if not isinstance(note_tags, list):
            note_tags = []
        tag_strs = [str(t) for t in note_tags]
        related_links = vault_links.find_related_by_semantic(
            written, vault, max_links=5, tag_strs=tag_strs
        )
        if not related_links:
            related_links = vault_links.find_related_by_tags(
                written, tag_strs, vault_notes=vault_notes
            )
        if related_links:
            vault_links.inject_related_links(written, related_links)
            vault_links.add_backlinks_to_existing(
                written, related_links, vault_notes=vault_notes
            )
            print(
                f"  [backlinks] Added {len(related_links)} related links "
                f"to {written.name}"
            )
    except (OSError, UnicodeDecodeError):
        pass  # Backlink step is best-effort; never fail the main flow


async def summarize_one(
    entry: dict[str, object],
    model: str | None,
    dry_run: bool,
    semaphore: anyio.Semaphore | None,
    existing_tags: list[str],
    persist: bool,
    vault: Path,
    tail_lines: int = _DEFAULT_TRANSCRIPT_TAIL_LINES,
    tail_bytes: int | None = _DEFAULT_TRANSCRIPT_TAIL_BYTES,
    max_cleaned_chars: int = _DEFAULT_MAX_CLEANED_CHARS,
    cluster_model: str | None = None,
    vault_notes: list[Path] | None = None,
    dead_lettered_ids: set[str] | None = None,
) -> tuple[dict[str, object], Path | str | None]:
    """Summarize one pending session entry.

    Args:
        entry: Pending entry dict with transcript_path, project, categories.
        model: Model ID to use, or ``None`` for the backend large-model default.
        dry_run: If True, print without writing.
        semaphore: Concurrency limiter.
        existing_tags: All tags currently in the vault.
        persist: Backwards-compatible no-op accepted from legacy CLI usage.
        vault: Path to the vault directory.
        tail_lines: Number of transcript lines to read.
        max_cleaned_chars: Maximum characters after cleaning.
        tail_bytes: Byte ceiling on the raw tail, bounding huge-line transcripts.
        cluster_model: Model ID for hierarchical chunk summarization, or ``None``
            for the backend small-model default.
        vault_notes: Pre-collected list of all vault note paths.  Passed
            through to backlink helpers to avoid redundant vault walks.
            When ``None``, each helper calls ``all_vault_notes()`` on its
            own.  See ARC-010.

    Returns:
        Tuple of (entry, written_path). written_path is None on dry-run,
        skip decision, or error.  written_path is ``_STALE`` when the
        transcript file no longer exists (entry should be purged).
    """
    del persist

    # ARC-048(c): semaphore may be ``None`` when the caller has already
    # acquired it (run_all's _run_one wrapper does this so it can write the
    # progress ``current`` field AFTER acquisition — see _run_one). Use a
    # nullcontext so this function still works either way.
    semaphore_cm = semaphore if semaphore is not None else contextlib.nullcontext()
    async with semaphore_cm:
        transcript_path_str = str(entry.get("transcript_path", ""))
        project = str(entry.get("project", "unknown"))
        raw_cats = entry.get("categories") or []
        categories = [str(c) for c in (raw_cats if isinstance(raw_cats, list) else [])]
        session_id = str(entry.get("session_id") or Path(transcript_path_str).stem)

        gate = _early_gate(entry, vault, dead_lettered_ids)
        if gate is not None:
            return gate

        cleaned = await preprocess_transcript_hierarchical(
            transcript_path_str,
            tail_lines,
            max_cleaned_chars,
            cluster_model,
            vault,
            tail_bytes,
        )
        if not cleaned:
            print(
                f"  Skipping {transcript_path_str}: could not read transcript",
                file=sys.stderr,
            )
            _mark_failure(entry, FailureReason.TRANSCRIPT_READ, transcript_path_str)
            return entry, None

        # Semantic dedup: find near-duplicate notes before calling the backend
        dedup_threshold: float = vault_common.get_config(
            "summarizer", "dedup_threshold", 0.80
        )
        # Content-rich query: include a slice of the cleaned transcript so
        # semantic dedup can match the SPECIFIC existing note. The coarse
        # project+categories query was too generic and missed near-duplicates,
        # causing duplicate notes to be written.
        query_seed = (cleaned or "")[:400].replace("\n", " ").strip()
        topic_query = f"{project} {' '.join(categories)} {query_seed}".strip()
        similar_notes = _find_dedup_candidates(
            topic_query, vault, threshold=dedup_threshold
        )

        prompt = build_prompt(
            project, categories, cleaned, existing_tags, session_id, similar_notes
        )

        try:
            result_text = await _run_summarizer_prompt(
                prompt,
                model=model,
                model_tier="large",
                purpose="summarizer-note",
                timeout=vault_common.get_config("summarizer", "ai_timeout", None),
                vault=vault,
            )
        except Exception as e:  # noqa: BLE001
            print(
                f"  Error querying AI backend for {transcript_path_str}: {e}\n"
                + traceback.format_exc(),
                file=sys.stderr,
            )
            # QA-009: return None (not _STALE/_SKIPPED) so the queue entry is
            # preserved and retried on the next run. Only purge for known-stale
            # or write-gate-skipped cases.
            _mark_failure(entry, FailureReason.AI_BACKEND_ERROR, str(e))
            return entry, None

        if not result_text:
            print(
                f"  No result from AI backend for {transcript_path_str}",
                file=sys.stderr,
            )
            _mark_failure(entry, FailureReason.NO_RESULT, transcript_path_str)
            return entry, None

        gate_result = _handle_write_gate_decision(
            result_text, entry, dry_run, vault, vault_notes
        )
        if gate_result is not None:
            return gate_result

        result_text = inject_project_tag(result_text, project)
        # ENH-008 Step 3: stamp the prompt version into the note frontmatter
        # so evaluation can slice note quality by the prompt that produced it.
        result_text = _stamp_prompt_version(
            result_text, load_prompt("summarize-session").version_stamp
        )
        written = write_note(result_text, dry_run, vault, project, categories)
        if written is None and not dry_run:
            # write_note already printed the specific refusal (frontmatter
            # validation, daily-note skip, ...) to stderr.
            _mark_failure(
                entry, FailureReason.NOTE_VALIDATION, "write_note returned None"
            )

        # Automated backlink suggestion
        _apply_backlinks_and_strip_links(written, vault, vault_notes)

        return entry, written
