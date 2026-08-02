#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "anyio>=4.0.0,<5.0",
#   # ENH-003: dedup now calls vault_search in-process (no subprocess), so the
#   # embeddings fallback needs fastembed + sqlite-vec in this script's own env
#   # when par-mem isn't serving. Previously vault_search.py ran as a subprocess
#   # that brought its own env; the in-process path instead shares one cached
#   # model across the run.
#   "fastembed>=0.6.0,<1.0",
#   "sqlite-vec>=0.1.6,<1.0",
# ]
# ///
"""On-demand AI-powered session summarizer for Parsidion vault.

Reads pending_summaries.jsonl, processes transcripts via the configured AI backend,
and writes structured vault notes to the appropriate vault folders.

Usage:
    uv run summarize_sessions.py [--sessions FILE] [--dry-run] [--model MODEL] [--persist]

ARC-015: Concurrency model rationale
This script uses ``anyio`` + ``anyio.create_task_group`` for async concurrency.
Structured concurrency guarantees from task groups (exception propagation,
automatic cancellation) are more robust than ``ThreadPoolExecutor`` futures.

vault_doctor.py uses ``concurrent.futures.ThreadPoolExecutor`` instead because
it is a stdlib-only script — adding anyio would violate that constraint.  Both
choices are intentional.  See ARC-015.

DONE(QA-018): Backlink helpers (find_related_by_tags, find_related_by_semantic,
inject_related_links, add_backlinks_to_existing) have been extracted into
vault_links.py.  This file now imports and delegates to that module.
"""

import argparse
import atexit
import contextlib
import json
import os
import subprocess  # noqa: F401 — re-exported for test monkeypatch (mod.subprocess.run)
import sys
import time
import traceback
from datetime import date  # noqa: F401 — re-exported for tests (summarize_sessions.date.today())
from pathlib import Path
from typing import NamedTuple, cast

import anyio  # type: ignore[import-untyped]

import ai_backend  # noqa: F401 — re-exported for tests (summarize_sessions.ai_backend)
import vault_common
import vault_links
from vault_path import is_path_inside_vault

# Constants, sentinels, enums, regexes, and default config values (ARC-009).
from summarizer._state_const import (  # noqa: F401 — re-exported for tests
    _ACTIVE_SESSION_GRACE_SECS,
    _DEAD,
    _DEAD_LETTER_RETENTION_DAYS,
    _DEFAULT_FOLDER,
    _DEFAULT_MAX_CLEANED_CHARS,
    _DEFAULT_MAX_PARALLEL,
    _DEFAULT_TRANSCRIPT_TAIL_BYTES,
    _DEFAULT_TRANSCRIPT_TAIL_LINES,
    _DEFERRED,
    _FAILURE_REASON_KEY,
    _FRONTMATTER_KEY_LINE_RE,
    _MAX_ATTEMPTS,
    _RELATED_LINE_RE,
    _RELATED_STEM_RE,
    _REQUIRED_FRONTMATTER_FIELDS,
    _SKIPPED,
    _STALE,
    _SUMMARIZER_STATE_FILENAME,
    _TYPE_FOLDERS,
    _VALID_NOTE_TYPES,
    _VALID_PROVENANCE_VALUES,
    FailureReason,
)

from summarizer.transcript import (  # noqa: E402,F401 — re-exported for tests
    _strip_code_fence,
    _summarize_chunk,
    preprocess_transcript,
    preprocess_transcript_hierarchical,
)


from summarizer.failure import (  # noqa: E402,F401 — re-exported for tests
    _failure_record_retryable,
    _format_failure_record,
    _mark_failure,
)

from summarizer.progress import (  # noqa: E402,F401 — re-exported for tests
    _PROGRESS_FILE,
    _clear_progress,
    _write_progress,
)


from summarizer.dedup import (  # noqa: E402,F401 — re-exported for tests
    _find_dedup_candidates,
    _resolve_note_stem,
    read_existing_tags,
    read_project_names,
)


from summarizer.prompt import (  # noqa: E402,F401 — re-exported for tests
    _PROMPT_TEMPLATE_CACHE,
    _TAG_RULES_COMMON,
    _load_prompt_template,
    _render_dedup_block,
    _render_tags_instruction,
    _run_summarizer_prompt,
    build_prompt,
)
from prompt_templates import load_prompt, render  # noqa: E402,F401 — re-exported for tests


from summarizer.notes import (  # noqa: E402,F401 — re-exported for tests
    _backfill_tags_if_empty,
    _backup_note,
    _clean_tag,
    _ensure_closing_frontmatter_delimiter,
    _note_body,
    _normalize_related_field,
    _stamp_prompt_version,
    _strip_leading_preamble,
    _validate_frontmatter,
    inject_project_tag,
    parse_note_title_slug,
    parse_note_type,
    write_note,
)


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

    QA-003: this stage reads ``_ACTIVE_SESSION_GRACE_SECS`` via bare-name
    resolution through this module's globals so test monkeypatches of
    ``summarize_sessions._ACTIVE_SESSION_GRACE_SECS`` take effect (the same
    constraint that keeps the anyio core in the entry shim — see
    ``summarizer/__init__.py``). The same applies to ``_resolve_note_stem``
    inside ``_apply_merge_decision``. The four stage helpers stay here as a
    result; their linear call sequence in ``summarize_one`` IS the decision
    dispatch's state machine.
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


async def run_all(
    entries: list[dict[str, object]],
    model: str | None,
    dry_run: bool,
    persist: bool,
    vault: Path,
    max_parallel: int = _DEFAULT_MAX_PARALLEL,
    tail_lines: int = _DEFAULT_TRANSCRIPT_TAIL_LINES,
    tail_bytes: int | None = _DEFAULT_TRANSCRIPT_TAIL_BYTES,
    max_cleaned_chars: int = _DEFAULT_MAX_CLEANED_CHARS,
    cluster_model: str | None = None,
) -> list[tuple[dict[str, object], Path | str | None]]:
    """Run all summarization tasks in parallel.

    Args:
        entries: List of pending entries.
        model: Model ID, or ``None`` for the backend large-model default.
        dry_run: If True, print without writing.
        persist: Backwards-compatible no-op accepted from legacy CLI usage.
        vault: Path to the vault directory.
        max_parallel: Maximum concurrent summarization tasks.
        tail_lines: Transcript tail lines per entry.
        tail_bytes: Byte ceiling on the raw tail per entry.
        max_cleaned_chars: Max cleaned chars per entry.
        cluster_model: Model ID for hierarchical chunk summarization, or ``None``
            for the backend small-model default.

    Returns:
        List of (entry, written_path) tuples.
    """
    # ARC-010: collect vault notes once per run and pass to every per-entry
    # function so we don't call all_vault_notes() up to 3x per entry.
    vault_notes: list[Path] = vault_common.all_vault_notes(vault)
    existing_tags = read_existing_tags(vault)
    project_names = read_project_names(vault_notes=vault_notes, vault=vault)
    # Filter project names out -- they're injected post-generation, not chosen by the model
    semantic_tags = [t for t in existing_tags if t not in project_names]
    # ARC-028: read the dead-letter set ONCE per run instead of once per entry.
    # At max_parallel=5 with 50 entries the previous code re-parsed
    # dead_letters.jsonl 50 times; the file grows monotonically so the cost
    # compounded across a long run.
    dead_lettered = _dead_lettered_ids(vault)
    semaphore = anyio.Semaphore(max_parallel)
    results: list[tuple[dict[str, object], Path | str | None]] = []
    total = len(entries)

    # Initialize progress (#13)
    _write_progress(total=total, processed=0, written=0, skipped=0, errors=0)

    # Counters for progress tracking (shared across async tasks via list trick)
    _progress_counters: list[int] = [
        0,
        0,
        0,
        0,
    ]  # [processed, written, skipped, errors]

    async def _run_one(entry: dict[str, object]) -> None:
        """Wrapper that collects the result of summarize_one into *results*.

        ARC-012: catches every unhandled exception so one malformed session
        cannot cancel its siblings through ``anyio.create_task_group()``'s
        default cancellation semantics. Cancellation (Ctrl-C) is still
        propagated by re-raising ``anyio.get_cancelled_exc_class()``.
        """
        project = str(entry.get("project", "?"))
        session_id = str(entry.get("session_id", ""))[:8]
        current = f"{project} [{session_id}]"

        # ARC-048(c): acquire the semaphore HERE and write the progress
        # ``current`` field only after acquisition. Previously the progress
        # write happened before summarize_one awaited the semaphore, so
        # ``vault-stats --summarizer-progress`` named the last-*queued*
        # session rather than the one actually being processed — at
        # max_parallel=5 every queued entry showed as "current" until the
        # semaphore drained. summarize_one now accepts ``semaphore=None``
        # and uses a nullcontext when called this way.
        async with semaphore:
            _write_progress(
                total=total,
                processed=_progress_counters[0],
                written=_progress_counters[1],
                skipped=_progress_counters[2],
                errors=_progress_counters[3],
                current=current,
            )

            try:
                result = await summarize_one(
                    entry,
                    model,
                    dry_run,
                    None,  # semaphore already acquired above
                    semantic_tags,
                    persist,
                    vault,
                    tail_lines,
                    tail_bytes,
                    max_cleaned_chars,
                    cluster_model,
                    vault_notes=vault_notes,
                    dead_lettered_ids=dead_lettered,
                )
            except anyio.get_cancelled_exc_class():
                # Ctrl-C / task cancellation must propagate so the user can
                # abort a run. Do NOT swallow it.
                raise
            except Exception as exc:  # noqa: BLE001 — task-group boundary
                # Catch every unhandled exception: an unguarded write path
                # inside summarize_one would otherwise cancel all siblings
                # via anyio.create_task_group()'s cancel-on-raise semantics,
                # leaving the queue uncleaned and the index not rebuilt.
                print(
                    f" Unhandled failure for session {session_id} "
                    f"(project {project}): {exc}",
                    file=sys.stderr,
                )
                traceback.print_exc()
                _mark_failure(entry, FailureReason.UNHANDLED, str(exc))
                result = (entry, None)

            results.append(result)
            _progress_counters[0] += 1  # processed
            _, written_path = result
            if written_path in (_STALE, _SKIPPED, _DEAD):
                _progress_counters[2] += 1  # skipped/purged (stale, write-gate, dead)
            elif written_path == _DEFERRED:
                pass  # deferred active session — left in queue, not an error
            elif written_path is not None:
                _progress_counters[1] += 1  # written
            else:
                _progress_counters[3] += 1  # errors
            _write_progress(
                total=total,
                processed=_progress_counters[0],
                written=_progress_counters[1],
                skipped=_progress_counters[2],
                errors=_progress_counters[3],
            )

    async with anyio.create_task_group() as tg:
        for entry in entries:
            tg.start_soon(_run_one, entry)

    return results


from summarizer.dead_letter import (  # noqa: E402,F401 — re-exported for tests
    _append_dead_letter,
    _dead_lettered_ids,
    _prune_dead_letters,
)
from summarizer.queue import (  # noqa: E402,F401 — re-exported for tests
    _resolve,
    read_pending,
    rebuild_index,
    remove_processed,
)


from summarizer.lock import (  # noqa: E402,F401 — re-exported for tests
    _load_summarizer_state,
    _summarizer_claim_lock_file,
    _summarizer_state_file,
    _write_summarizer_state,
    claim_summarizer_lock,
    release_summarizer_lock,
)


# ---------------------------------------------------------------------------
# QA-003: ``main`` was the repo's #2 churn×complexity hotspot (complexity 27).
# The argparse definition, the defaults→config→CLI option resolution, the
# result categorisation, and the dequeue/rebuild/commit/summary finalisation
# are pure sub-operations; lifting them into named helpers collapses ``main``
# into a thin orchestrator and makes each piece independently testable.
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    """Build the summarizer CLI parser."""
    parser = argparse.ArgumentParser(
        description="AI-powered session summarizer for Parsidion vault",
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
        help="Override large model (default: backend large default)",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        default=None,
        help="Accepted for backwards compatibility; currently unused.",
    )
    parser.add_argument(
        "--run-doctor",
        action="store_true",
        default=False,
        help="Run vault_doctor before summarizing to fix legacy pending paths and stale files.",
    )
    parser.add_argument(
        "--rebuild-graph",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Rebuild visualizer graph.json after indexing (passed to update_index.py "
            "--rebuild-graph). Tri-state: --rebuild-graph forces on, --no-rebuild-graph "
            "forces off (overrides a config 'true'), unset reads "
            "summarizer.rebuild_graph from config (default false)."
        ),
    )
    parser.add_argument(
        "--graph-include-daily",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Include Daily folder notes in the graph (only used with --rebuild-graph). "
            "Same tri-state semantics as --rebuild-graph; reads "
            "summarizer.graph_include_daily from config when unset."
        ),
    )
    parser.add_argument(
        "--vault",
        "-V",
        metavar="PATH|NAME",
        default=None,
        help="Vault path or named vault (default: ~/ParsidionVault, or legacy ~/ClaudeVault if it exists)",
    )
    return parser


class _SummarizerOptions(NamedTuple):
    """Resolved summarizer options after defaults → config → CLI merge."""

    model: str | None
    persist: bool
    max_parallel: int
    tail_lines: int
    tail_bytes: int | None
    max_cleaned_chars: int
    cluster_model: str | None
    rebuild_graph: bool | None
    graph_include_daily: bool | None


def _resolve_options(args: argparse.Namespace) -> _SummarizerOptions:
    """Resolve defaults → config → CLI args (CLI wins) into a typed bundle."""
    configured_model = vault_common.get_config("summarizer", "model", None)
    if args.model is not None:
        model: str | None = args.model
    elif isinstance(configured_model, str) and configured_model.strip():
        model = configured_model
    else:
        model = None
    # ARC-042: route tri-state bools through _resolve so a config 'true' can be
    # overridden off via --no-<flag>; the previous `or` short-circuit treated
    # an absent CLI flag the same as `--flag False`, so once config was true
    # there was no way to disable from the CLI for a single run.
    persist: bool = _resolve(args.persist, "summarizer", "persist", False)
    max_parallel: int = vault_common.get_config(
        "summarizer", "max_parallel", _DEFAULT_MAX_PARALLEL
    )
    tail_lines: int = vault_common.get_config(
        "summarizer", "transcript_tail_lines", _DEFAULT_TRANSCRIPT_TAIL_LINES
    )
    tail_bytes: int | None = vault_common.get_config(
        "summarizer", "transcript_tail_bytes", _DEFAULT_TRANSCRIPT_TAIL_BYTES
    )
    max_cleaned_chars: int = vault_common.get_config(
        "summarizer", "max_cleaned_chars", _DEFAULT_MAX_CLEANED_CHARS
    )
    configured_cluster_model = vault_common.get_config(
        "summarizer", "cluster_model", None
    )
    cluster_model: str | None = (
        configured_cluster_model
        if isinstance(configured_cluster_model, str)
        and configured_cluster_model.strip()
        else None
    )
    rebuild_graph: bool | None = _resolve(
        args.rebuild_graph, "summarizer", "rebuild_graph", False
    )
    graph_include_daily: bool | None = _resolve(
        args.graph_include_daily, "summarizer", "graph_include_daily", False
    )
    return _SummarizerOptions(
        model=model,
        persist=persist,
        max_parallel=max_parallel,
        tail_lines=tail_lines,
        tail_bytes=tail_bytes,
        max_cleaned_chars=max_cleaned_chars,
        cluster_model=cluster_model,
        rebuild_graph=rebuild_graph,
        graph_include_daily=graph_include_daily,
    )


class _RunTotals(NamedTuple):
    """Categorised ``run_all`` results, bucketed by terminal sentinel."""

    successful: list[dict[str, object]]
    stale: list[dict[str, object]]  # includes _DEAD re-queue purges
    skipped: list[dict[str, object]]
    failed: list[dict[str, object]]
    deferred: list[dict[str, object]]


def _categorize_results(
    results: list[tuple[dict[str, object], Path | str | None]],
    dry_run: bool,
) -> _RunTotals:
    """Bucket ``run_all`` results by their terminal sentinel.

    Stale entries are purged from the queue since the transcript can never be
    recovered; write-gate skips are purged because the backend already decided
    they are transient and retrying would loop.
    """
    successful: list[dict[str, object]] = []
    stale: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    deferred: list[dict[str, object]] = []
    for entry, written_path in results:
        if written_path == _STALE:
            stale.append(entry)
        elif written_path == _SKIPPED:
            skipped.append(entry)
        elif written_path == _DEAD:
            # Re-queued dead-lettered session — purge like stale.
            stale.append(entry)
        elif written_path == _DEFERRED:
            # Active session — leave in queue for a later run.
            deferred.append(entry)
        elif written_path is not None:
            print(f"  Written: {written_path}")
            successful.append(entry)
        elif not dry_run:
            failed.append(entry)
    return _RunTotals(successful, stale, skipped, failed, deferred)


def _dequeue_and_finalize(
    totals: _RunTotals,
    *,
    source_path: Path,
    vault_path: Path,
    sessions_mode: bool,
    rebuild_graph: bool | None,
    graph_include_daily: bool | None,
) -> None:
    """Dequeue processed entries, persist sticky dead-letters, rebuild + commit.

    Runs only when ``not dry_run``. Failed entries get their attempts counter
    bumped (and are dead-lettered at ``_MAX_ATTEMPTS``, or on attempt 1 when
    the failure is classified non-retryable — see ARC-030).
    """
    successful = totals.successful
    stale = totals.stale
    skipped = totals.skipped
    failed = totals.failed

    removable = successful + stale + skipped
    # ARC-030: pass the structured failure record (dict) through to
    # remove_processed so it can honor the retryable flag. Fall back to
    # the legacy plain-string shape for entries queued by older code.
    failed_reasons: dict[str, object] = {
        str(e.get("session_id") or e.get("transcript_path", "")): (
            e[_FAILURE_REASON_KEY]
            if isinstance(e.get(_FAILURE_REASON_KEY), dict)
            else _format_failure_record(e.get(_FAILURE_REASON_KEY))
        )
        for e in failed
    }
    if not sessions_mode:
        # Make write-gate skips sticky: record them in dead_letters so a
        # future stop-hook re-queue is caught by the _DEAD guard instead of
        # re-billing an AI call to re-evaluate a session already judged
        # transient. (Skips are also dequeued below via `removable`.)
        for entry in skipped:
            _raw_attempts = entry.get("attempts")
            _attempts = _raw_attempts if isinstance(_raw_attempts, int) else 0
            _append_dead_letter(
                source_path,
                entry,
                _attempts,
                "write-gate skip (transient)",
            )
    # ARC-048(d): always honor the dequeue lifecycle (queue OR --sessions
    # FILE). Previously --sessions skipped this block entirely, so a re-run
    # of the same FILE re-processed every entry, re-billed an AI call for
    # each, and (because write_note merges on slug collision) appended a
    # fresh ``## Session update`` block to each note — quietly compounding
    # duplicate content on every invocation. The sticky dead-letter write
    # above remains queue-only (it writes a sibling dead_letters.jsonl and
    # would litter an arbitrary source directory); --sessions mode still
    # dequeues via ``removable`` without that side effect.
    if failed_reasons:
        remove_processed(source_path, removable, failed=failed_reasons)
    elif removable:
        remove_processed(source_path, removable)

    # Rebuild vault index and commit all new notes + updated index
    if successful:
        rebuild_index(
            vault_path,
            rebuild_graph=rebuild_graph,
            graph_include_daily=graph_include_daily,
        )
        # SEC-002: sanitize project names to prevent embedded newlines in commit messages
        projects = {
            str(e.get("project", "unknown"))
            .replace("\n", " ")
            .replace("\r", "")
            .strip()
            for e in successful
        }
        project_str = ", ".join(sorted(projects))
        vault_common.git_commit_vault(
            f"chore(vault): add session notes [{project_str}]",
            vault=vault_path,
        )


def _print_run_summary(total: int, totals: _RunTotals) -> None:
    """Print the human-readable run summary and clear the progress file."""
    successful, stale, skipped, failed, deferred = totals
    summary_parts = [f"{len(successful)} written"]
    if stale:
        summary_parts.append(f"{len(stale)} purged (stale/dead-lettered)")
    if skipped:
        summary_parts.append(f"{len(skipped)} skipped by write-gate")
    if deferred:
        summary_parts.append(f"{len(deferred)} deferred (active)")
    if failed:
        summary_parts.append(f"{len(failed)} failed")
    print(f"Done. {total} session(s) processed: {', '.join(summary_parts)}.")
    _clear_progress()  # Remove progress file when done (#13)


def main() -> None:
    """Parse arguments and run the summarizer."""
    parser = _build_parser()
    args = parser.parse_args()
    options = _resolve_options(args)

    # Resolve vault
    vault_path = vault_common.resolve_vault(explicit=args.vault, cwd=os.getcwd())
    vault_common.apply_configured_env_defaults(vault=vault_path)

    # Singleton guard — only one summarizer may run at a time per vault.
    if not claim_summarizer_lock(vault_path):
        sys.exit(1)
    atexit.register(release_summarizer_lock, vault_path)

    # Retention: prune dead-letter records older than the configured window so
    # dead_letters.jsonl (which accumulates every sticky write-gate skip) stays
    # bounded. Runs every invocation regardless of pending work.
    _retention_days = int(
        vault_common.get_config(
            "summarizer",
            "dead_letter_retention_days",
            _DEAD_LETTER_RETENTION_DAYS,
        )
    )
    _pruned_dl = _prune_dead_letters(vault_path, _retention_days)
    if _pruned_dl:
        print(
            f"Pruned {_pruned_dl} dead-letter record(s) older than "
            f"{_retention_days} day(s)"
        )

    # Optionally run vault_doctor first (--fix-all: frontmatter, tags, subfolders)
    if args.run_doctor:
        import subprocess as _sp
        import sys as _sys

        _doctor = Path(__file__).parent / "vault_doctor.py"
        print("Running vault_doctor --fix-all before summarizing…")
        # QA-005: bound the run so a hung vault_doctor cannot stall the
        # summarizer indefinitely. vault_doctor --fix-all is bounded work
        # (a few seconds on a small vault, ~1 min on a large one); 10
        # minutes is a generous ceiling for the rare AI-driven repair.
        try:
            _sp.run(
                [_sys.executable, str(_doctor), "--fix-all"],
                check=False,
                timeout=600,
            )
        except _sp.TimeoutExpired:
            print(
                "Warning: vault_doctor --fix-all timed out after 600s; "
                "continuing with summarization.",
                file=sys.stderr,
            )

    # Determine source file
    if args.sessions:
        source_path = Path(args.sessions).expanduser()
    else:
        # Default: pending file in resolved vault
        source_path = vault_path / "pending_summaries.jsonl"

    entries = read_pending(source_path)
    if not entries:
        print(f"No pending sessions in {source_path}")
        return

    model_label = options.model or "backend large default"
    print(f"Processing {len(entries)} session(s) with model {model_label}...")
    if args.dry_run:
        print("[dry-run mode — nothing will be written]")

    results: list[tuple[dict[str, object], Path | str | None]] = cast(
        list[tuple[dict[str, object], Path | str | None]],
        anyio.run(
            run_all,
            entries,
            options.model,
            args.dry_run,
            options.persist,
            vault_path,
            options.max_parallel,
            options.tail_lines,
            options.tail_bytes,
            options.max_cleaned_chars,
            options.cluster_model,
        ),
    )

    totals = _categorize_results(results, args.dry_run)

    if not args.dry_run:
        _dequeue_and_finalize(
            totals,
            source_path=source_path,
            vault_path=vault_path,
            sessions_mode=bool(args.sessions),
            rebuild_graph=options.rebuild_graph,
            graph_include_daily=options.graph_include_daily,
        )

    _print_run_summary(len(entries), totals)


if __name__ == "__main__":
    main()
