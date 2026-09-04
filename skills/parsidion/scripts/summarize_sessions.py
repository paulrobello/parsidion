#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "anyio>=4.0.0,<5.0",
#   # ENH-003: dedup now calls vault_search in-process (no subprocess), so the
#   # embeddings fallback needs fastembed + sqlite-vec in this script's own env
#   # when parsight isn't serving. Previously vault_search.py ran as a subprocess
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
import math
import os
import subprocess  # re-exported for test monkeypatch (mod.subprocess.run)
import sys
import traceback
from datetime import date  # re-exported for tests (summarize_sessions.date.today())
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple, cast

import anyio  # type: ignore[import-untyped]

import ai_backend  # re-exported for tests (summarize_sessions.ai_backend)
import vault_common
import vault_links  # re-exported for tests (summarize_sessions.vault_links)
from core.subproc_util import run_with_pgkill

# Constants, sentinels, enums, regexes, and default config values (ARC-009).
from summarizer._state_const import (  # re-exported for tests
    _ACTIVE_SESSION_GRACE_SECS,
    _DEAD,
    _DEAD_LETTER_RETENTION_DAYS,
    _DEFAULT_FOLDER,
    _DEFAULT_MAX_CLEANED_CHARS,
    _DEFAULT_MAX_PARALLEL,
    _DEFAULT_TRANSCRIPT_TAIL_BYTES,
    _DEFAULT_SUMMARIZER_AI_TIMEOUT,
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

from summarizer.transcript import (  # re-exported for tests
    _strip_code_fence,
    _summarize_chunk,
    preprocess_transcript,
    preprocess_transcript_hierarchical,
)


from summarizer.failure import (  # re-exported for tests
    _failure_record_retryable,
    _format_failure_record,
    _mark_failure,
)

from summarizer.progress import (  # re-exported for tests
    _PROGRESS_FILE,
    _clear_progress,
    _write_progress,
)


from summarizer.dedup import (  # re-exported for tests
    _find_dedup_candidates,
    _resolve_note_stem,
    read_existing_tags,
    read_project_names,
)


from summarizer.prompt import (  # re-exported for tests
    _PROMPT_TEMPLATE_CACHE,
    _TAG_RULES_COMMON,
    _load_prompt_template,
    _render_dedup_block,
    _render_tags_instruction,
    _run_summarizer_prompt,
    build_prompt,
)
from prompt_templates import load_prompt, render  # re-exported for tests


from summarizer.notes import (  # re-exported for tests
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


from summarizer.pipeline import (  # re-exported for tests
    _apply_backlinks_and_strip_links,
    _apply_merge_decision,
    _early_gate,
    _handle_write_gate_decision,
    summarize_one,
)


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


from summarizer.dead_letter import (  # re-exported for tests
    _append_dead_letter,
    _dead_lettered_ids,
    _prune_dead_letters,
    _read_dead_letters,
    _remove_dead_letters_by_session_ids,
)
from summarizer.queue import (  # re-exported for tests
    _resolve,
    read_pending,
    rebuild_index,
    remove_processed,
)


from summarizer.lock import (  # re-exported for tests
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
        help="Run vault_doctor --fix-all (frontmatter, tags, subfolders) before summarizing.",
    )
    parser.add_argument(
        "--retry-dead-letters",
        action="store_true",
        default=False,
        help=(
            "Re-queue retryable dead-lettered sessions into the pending queue, "
            "then summarize. Most no_result dead-letters are transient and "
            "succeed on a later retry; the 3-strike graveyard otherwise loses "
            "them. Removes matches from dead_letters.jsonl so they are actually "
            "re-processed (the _dead_lettered_ids guard otherwise skips them). "
            "Pairs with --reason / --min-age-days / --max-count."
        ),
    )
    parser.add_argument(
        "--reason",
        default="no_result",
        help=(
            "With --retry-dead-letters: re-queue entries whose last_failure "
            "starts with this prefix (default 'no_result' covers the legacy "
            "opaque kind and no_result_timeout/empty/backend)."
        ),
    )
    parser.add_argument(
        "--min-age-days",
        type=float,
        default=0.0,
        help=(
            "With --retry-dead-letters: only re-queue entries dead-lettered at "
            "least this many days ago (cooldown; default 0 = all matching)."
        ),
    )
    parser.add_argument(
        "--max-count",
        type=int,
        default=0,
        help="With --retry-dead-letters: cap the number re-queued (0 = no cap).",
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


class _RequeueResult(NamedTuple):
    """Outcome of a --retry-dead-letters pass, split for operator reporting."""

    requeued: int
    unrecoverable: int


def _requeue_dead_letters(
    vault: Path,
    *,
    reason: str,
    min_age_days: float,
    max_count: int,
    dry_run: bool = False,
) -> _RequeueResult:
    """Pull retryable dead-lettered sessions back into the pending queue.

    Filters ``dead_letters.jsonl`` by ``last_failure`` prefix (e.g.
    ``"no_result"`` matches the legacy opaque kind and every granular
    ``no_result_*`` cause) and an optional minimum age, REMOVES the matches
    from ``dead_letters.jsonl`` (so ``_dead_lettered_ids`` no longer feeds
    them to ``_early_gate`` and the re-queued session is actually processed),
    then appends fresh copies to ``pending_summaries.jsonl``.

    Matches whose ``transcript_path`` no longer exists (typically a subagent
    transcript removed with its git worktree) can never succeed — the
    transcript is the pipeline's only input — so they are never re-queued;
    they are dropped from ``dead_letters.jsonl`` instead of sitting until
    ``dead_letter_retention_days`` expires them.

    Returns a ``_RequeueResult``: ``requeued`` is the number re-queued (or
    the count that WOULD be re-queued when ``dry_run``), ``unrecoverable``
    the number skipped for a missing transcript.

    Most ``no_result`` dead-letters are transient (rate-limit, momentary empty
    response, load) and succeed on a later retry; the permanent 3-strike
    graveyard was losing them. This gives them a second chance — successes
    write notes, repeat failures re-dead-letter.
    """
    records = _read_dead_letters(vault)
    now = datetime.now()
    matches: list[dict[str, object]] = []
    for record in records:
        if not str(record.get("last_failure", "")).startswith(reason):
            continue
        if min_age_days > 0:
            try:
                age = now - datetime.fromisoformat(str(record.get("dead_lettered_at")))
            except (ValueError, TypeError):
                age = timedelta(0)
            if age < timedelta(days=min_age_days):
                continue
        matches.append(record)
    recoverable: list[dict[str, object]] = []
    unrecoverable: list[dict[str, object]] = []
    for record in matches:
        transcript = str(record.get("transcript_path") or "")
        if transcript and Path(transcript).expanduser().is_file():
            recoverable.append(record)
        else:
            unrecoverable.append(record)
    if max_count > 0:
        recoverable = recoverable[:max_count]
    if dry_run:
        return _RequeueResult(len(recoverable), len(unrecoverable))
    if unrecoverable:
        _remove_dead_letters_by_session_ids(
            vault, {str(r.get("session_id", "")) for r in unrecoverable}
        )
    if not recoverable:
        return _RequeueResult(0, len(unrecoverable))
    session_ids = {str(m.get("session_id", "")) for m in recoverable}
    _remove_dead_letters_by_session_ids(vault, session_ids)
    requeued = 0
    for record in recoverable:
        raw_cats = record.get("categories") or []
        categories = (
            {str(c): [] for c in raw_cats} if isinstance(raw_cats, list) else {}
        )
        vault_common.append_to_pending(
            Path(str(record.get("transcript_path", ""))),
            str(record.get("project", "unknown")),
            categories,
            force=True,  # already passed the hook's significance gate once
            source=str(record.get("source", "session")),
            agent_type=(
                str(record["agent_type"]) if record.get("agent_type") else None
            ),
            session_id=(str(record.get("session_id", "")) or None),
            vault=vault,
        )
        requeued += 1
    return _RequeueResult(requeued, len(unrecoverable))


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


_MAX_PARALLEL_CEILING = 16
# QA-005: ceiling for the optional --run-doctor sub-run (see its call site).
_DOCTOR_TIMEOUT_SECS = 600


def _clamp_max_parallel(value: object) -> int:
    """Coerce ``summarizer.max_parallel`` into ``[1, 16]`` (ARC-106).

    Every unit of fan-out here is a full CLI agent process, so the config value
    reached ``anyio.Semaphore`` unbounded: ``max_parallel: 500`` launched 500 of
    them. Follows ``clamp_timeout``'s SEC-024 shape -- coerce first, because
    ``get_config`` can hand back a YAML string or a bool, and only then clamp --
    but ``0`` floors to 1 rather than falling back to the default, since "run
    one at a time" is the intent a zero expresses.

    Args:
        value: Raw configured value.

    Returns:
        A concurrency between 1 and 16.
    """
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        print(
            f"Note: summarizer.max_parallel {value!r} is not a valid concurrency; "
            f"using {_DEFAULT_MAX_PARALLEL}.",
            file=sys.stderr,
        )
        return _DEFAULT_MAX_PARALLEL
    clamped = max(1, min(_MAX_PARALLEL_CEILING, int(value)))
    if clamped != value:
        print(
            f"Note: summarizer.max_parallel {value!r} clamped to {clamped} "
            f"(valid range 1-{_MAX_PARALLEL_CEILING}).",
            file=sys.stderr,
        )
    return clamped


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
    max_parallel: int = _clamp_max_parallel(
        vault_common.get_config("summarizer", "max_parallel", _DEFAULT_MAX_PARALLEL)
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
    # Queue mode: write-gate skips get a bounded retry budget (the gate is
    # stochastic on borderline sessions — a session dead-lettered "skip" can
    # produce a high-quality note on re-evaluation), so they are NOT purged
    # here. Their keys go to remove_processed, which re-queues them (bumping a
    # "skips" counter) until _MAX_SKIPS, then sticky dead-letters.
    # --sessions mode is a one-shot explicit file: skips are simply purged
    # (no retry, no dead-letter side effect in an arbitrary source directory).
    if sessions_mode:
        removable = successful + stale + skipped
        skip_retry_keys: set[str] = set()
    else:
        removable = successful + stale
        skip_retry_keys = {
            str(e.get("session_id") or e.get("transcript_path", "")) for e in skipped
        }
    # ARC-048(d): always honor the dequeue lifecycle (queue OR --sessions
    # FILE). Previously --sessions skipped this block entirely, so a re-run
    # of the same FILE re-processed every entry, re-billed an AI call for
    # each, and (because write_note merges on slug collision) appended a
    # fresh ``## Session update`` block to each note — quietly compounding
    # duplicate content on every invocation. The sticky skip dead-lettering
    # now lives inside remove_processed's skip_retry path and is queue-only
    # (skip_retry_keys is empty in --sessions mode); --sessions mode still
    # dequeues via ``removable`` without that side effect.
    if failed_reasons or skip_retry_keys:
        remove_processed(
            source_path, removable, failed=failed_reasons, skip_retry=skip_retry_keys
        )
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
        _doctor = Path(__file__).parent / "vault_doctor.py"
        print("Running vault_doctor --fix-all before summarizing…")
        # QA-005: bound the run so a hung vault_doctor cannot stall the
        # summarizer indefinitely. vault_doctor --fix-all is bounded work
        # (a few seconds on a small vault, ~1 min on a large one); 10
        # minutes is a generous ceiling for the rare AI-driven repair.
        #
        # ARC-106: routed through run_with_pgkill like every other child in
        # this pipeline. The bare subprocess.run predated ARC-004 and killed
        # only the direct child on timeout, orphaning vault_doctor's own AI
        # worker processes -- exactly the zombie class the SessionStart
        # semantic-search comment documents. run_with_pgkill captures output,
        # so it is relayed here to preserve the previous console behaviour.
        _reason, _proc = run_with_pgkill(
            [sys.executable, str(_doctor), "--fix-all"],
            cwd=Path.cwd(),
            timeout=_DOCTOR_TIMEOUT_SECS,
        )
        if _reason == "timeout":
            print(
                f"Warning: vault_doctor --fix-all timed out after "
                f"{_DOCTOR_TIMEOUT_SECS}s; continuing with summarization.",
                file=sys.stderr,
            )
        elif _reason == "launch":
            print(
                "Warning: vault_doctor --fix-all could not be started; "
                "continuing with summarization.",
                file=sys.stderr,
            )
        elif _proc is not None:
            if _proc.stdout:
                print(_proc.stdout, end="")
            if _proc.stderr:
                print(_proc.stderr, end="", file=sys.stderr)

    # --retry-dead-letters: pull retryable failures out of the dead-letter
    # graveyard back into the live pending queue, then fall through to the
    # normal pending flow. Must run before read_pending so the re-queued
    # entries are picked up; removing them from dead_letters.jsonl is what lets
    # _early_gate process them instead of skipping as already-dead-lettered.
    if args.retry_dead_letters:
        result = _requeue_dead_letters(
            vault_path,
            reason=args.reason,
            min_age_days=args.min_age_days,
            max_count=args.max_count,
            dry_run=args.dry_run,
        )
        if args.dry_run:
            print(
                f"[dry-run] would re-queue {result.requeued} dead-lettered "
                f"session(s) matching --reason '{args.reason}'."
            )
            if result.unrecoverable:
                print(
                    f"[dry-run] would drop {result.unrecoverable} unrecoverable "
                    "record(s) whose transcript no longer exists."
                )
            return
        if result.unrecoverable:
            print(
                f"Skipped {result.unrecoverable} unrecoverable dead-letter "
                "record(s): transcript no longer exists (typically removed "
                "with its worktree); dropped them from dead_letters.jsonl."
            )
        if result.requeued == 0:
            print(
                f"No dead-lettered sessions matching --reason '{args.reason}'"
                + (
                    f" (min-age-days={args.min_age_days})"
                    if args.min_age_days > 0
                    else ""
                )
                + " to retry."
            )
            return
        print(
            f"Re-queued {result.requeued} dead-lettered session(s) into the "
            "pending queue."
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
