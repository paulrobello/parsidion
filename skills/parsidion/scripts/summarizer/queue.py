"""Pending-queue operations: read, remove-processed, index rebuild.

Extracted from ``summarize_sessions.py`` (ARC-009).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import vault_common

from summarizer._state_const import _MAX_ATTEMPTS, _MAX_SKIPS
from summarizer.dead_letter import _append_dead_letter
from summarizer.failure import _failure_record_retryable, _format_failure_record

_flock_exclusive = vault_common.flock_exclusive
_flock_shared = vault_common.flock_shared
_funlock = vault_common.funlock


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
        with open(pending_path, encoding="utf-8") as f:
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


def remove_processed(
    pending_path: Path,
    processed_entries: list[dict[str, object]],
    failed: dict[str, object] | None = None,
    skip_retry: set[str] | None = None,
) -> None:
    """Remove successfully processed entries from the pending file.

    Entries keyed in *failed* get their optional ``attempts`` counter
    incremented (absent = 0); an entry reaching ``_MAX_ATTEMPTS`` is purged
    (dead-lettered) with a stderr warning so a deterministic failure cannot
    retry — and re-bill an AI call — on every run forever.

    ARC-030: when a failed entry's record carries ``retryable: False`` (a
    :class:`FailureReason` member marked non-retryable), the entry is
    dead-lettered on the FIRST failed attempt rather than after _MAX_ATTEMPTS
    retries — a deterministic model-output failure (MERGE_VALIDATION,
    NOTE_VALIDATION, MERGE_CONTAINMENT, ...) would re-bill an AI call and
    re-touch the same target note on every retry, so it should surface
    immediately as a dead-letter warning instead.

    Args:
        pending_path: Path to the pending JSONL file.
        processed_entries: Entries that were successfully processed.
        failed: Map of session_id/transcript_path key -> failure record. The
            record is the structured dict produced by :func:`_mark_failure`
            (``{"kind", "retryable", "detail"}``). A legacy plain-string value
            is still accepted for backward compatibility and treated as
            retryable.
        skip_retry: Set of session_id/transcript_path keys for write-gate-skipped
            entries. The write-gate decision is stochastic on borderline sessions,
            so a skipped entry is re-queued (its ``skips`` counter bumped) until
            it has skipped ``_MAX_SKIPS`` times, after which it is sticky
            dead-lettered with the ``write-gate skip (transient)`` label. Mirrors
            the ``failed``/``attempts`` retry path.
    """
    if not pending_path.exists():
        return

    failed = failed or {}
    # Prefer session_id for matching; fall back to transcript_path for entries
    # written by older versions of the hook that lack session_id.
    processed_ids = {
        str(e.get("session_id") or e.get("transcript_path", ""))
        for e in processed_entries
    }

    try:
        # Hold the exclusive lock on the REAL file for the whole read+swap so
        # concurrent append_to_pending() calls (vault_fs.py flocks the same
        # file) cannot interleave between the read and the replace.
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
                    except (json.JSONDecodeError, ValueError):
                        remaining.append(line)  # Keep malformed lines
                        continue
                    key = str(
                        entry.get("session_id") or entry.get("transcript_path", "")
                    )
                    if key in processed_ids:
                        continue
                    if key in failed:
                        record = failed[key]
                        retryable = _failure_record_retryable(record)
                        raw_attempts = entry.get("attempts")
                        attempts = (
                            raw_attempts if isinstance(raw_attempts, int) else 0
                        ) + 1
                        # ARC-030: non-retryable failures dead-letter on the
                        # first attempt; retryable ones wait for _MAX_ATTEMPTS.
                        dead_letter_now = (not retryable) or attempts >= _MAX_ATTEMPTS
                        if dead_letter_now:
                            label = _format_failure_record(record)
                            print(
                                f"Warning: dead-letter purge of session "
                                f"{entry.get('session_id') or entry.get('transcript_path', '?')} "
                                f"(project: {entry.get('project', 'unknown')}) "
                                f"{'(non-retryable) ' if not retryable else f'after {attempts} failed attempts '}"
                                f"last failure: {label}",
                                file=sys.stderr,
                            )
                            _append_dead_letter(pending_path, entry, attempts, label)
                            continue
                        entry["attempts"] = attempts
                        remaining.append(json.dumps(entry))
                        continue
                    if skip_retry and key in skip_retry:
                        # Write-gate skip: the decision is stochastic on
                        # borderline sessions, so re-evaluate up to _MAX_SKIPS
                        # times before sticky dead-lettering — one skip must
                        # not permanently shelve a recoverable session.
                        raw_skips = entry.get("skips")
                        skips = (raw_skips if isinstance(raw_skips, int) else 0) + 1
                        if skips >= _MAX_SKIPS:
                            print(
                                f"Warning: dead-letter purge of session "
                                f"{entry.get('session_id') or entry.get('transcript_path', '?')} "
                                f"(project: {entry.get('project', 'unknown')}) "
                                f"after {skips} write-gate skips "
                                f"last failure: write-gate skip (transient)",
                                file=sys.stderr,
                            )
                            _append_dead_letter(
                                pending_path,
                                entry,
                                skips,
                                "write-gate skip (transient)",
                            )
                            continue
                        entry["skips"] = skips
                        remaining.append(json.dumps(entry))
                        continue
                    remaining.append(line)
                # Crash-atomic rewrite: write survivors to a sibling .tmp and
                # swap it over the original (same pattern as
                # _write_summarizer_state / vault_fs.migrate_pending_paths).
                # SEC-109: create the tmp with mode 0o600 via os.open+os.fdopen
                # so the queue's owner-only protection survives the replace
                # (a plain ``tmp.write_text`` honours the process umask and
                # leaves the file world-readable, silently undoing the
                # 0o600 set on first creation by vault_fs.append_to_pending).
                tmp = pending_path.with_suffix(".jsonl.tmp")
                tmp_fd = os.open(
                    str(tmp),
                    os.O_CREAT | os.O_WRONLY | os.O_TRUNC,
                    0o600,
                )
                with open(tmp_fd, "w", encoding="utf-8") as out:
                    out.write("".join(line + "\n" for line in remaining))
                tmp.replace(pending_path)
            finally:
                _funlock(f)
    except OSError as e:
        print(f"Warning: could not update pending file: {e}", file=sys.stderr)


def _resolve(
    cli_value: bool | None,
    section: str,
    key: str,
    default: bool,
) -> bool:
    """Resolve a tri-state CLI bool against config and a default.

    ARC-042: centralises the "CLI overrides config overrides default" pattern
    that ``main`` previously inlined three times with subtly different shapes.
    Used for ``--rebuild-graph``, ``--graph-include-daily``, and ``--persist``
    so a YAML ``true`` can be overridden off from the CLI via ``--no-<flag>``
    (previously impossible: the ``or`` short-circuit meant CLI ``False`` was
    treated the same as "absent").

    Args:
        cli_value: The CLI-provided value, or ``None`` when the flag was not
            given (i.e. argparse ``default=None`` with ``BooleanOptionalAction``).
        section: Config section name (e.g. ``"summarizer"``).
        key: Key within the section (e.g. ``"rebuild_graph"``).
        default: Final fallback when neither CLI nor config provides a value.

    Returns:
        The resolved bool. Config values that are not bools fall back to
        *default* (defensive against a misconfigured YAML scalar).
    """
    if cli_value is not None:
        return cli_value
    configured = vault_common.get_config(section, key, default)
    if isinstance(configured, bool):
        return configured
    return default


def rebuild_index(
    vault: Path,
    rebuild_graph: bool | None = None,
    graph_include_daily: bool | None = None,
) -> None:
    """Run update_index.py to rebuild the vault index.

    ARC-027(a): the ``uv run`` invocation now passes ``--no-project`` so
    ``uv`` does not walk up from the inherited cwd (the user's project
    directory, for the auto-launch path) looking for a ``pyproject.toml``
    and syncing an unrelated project's dependencies. Without ``--no-project``
    the index rebuild fails when launched from inside a project whose own
    deps conflict; the failure was swallowed into a warning at the caller so
    the index silently went stale while the run reported success.

    Args:
        vault: Path to the vault directory.
        rebuild_graph: When True, pass ``--rebuild-graph`` to update_index.py
            so the visualizer graph.json is regenerated after indexing.
            ``None`` means "no flag" (leave update_index's own default).
        graph_include_daily: When True, also pass ``--graph-include-daily``
            (only meaningful when ``rebuild_graph`` is True). ``None`` means
            "no flag".
    """
    # scripts/ is the parent of this submodule's directory (summarizer/).
    index_script = Path(__file__).resolve().parent.parent / "update_index.py"
    if not index_script.exists():
        # Try installed location
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
            "Warning: update_index.py not found, skipping index rebuild",
            file=sys.stderr,
        )
        return
    # ARC-027(a): --no-project prevents uv from discovering a pyproject.toml
    # in the inherited cwd and syncing an unrelated project's dependencies.
    cmd = ["uv", "run", "--no-project", str(index_script), "--vault", str(vault)]
    if rebuild_graph:
        cmd.append("--rebuild-graph")
    if graph_include_daily:
        cmd.append("--graph-include-daily")
    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
            env=vault_common.env_without_claudecode(),
        )
        print("Vault index rebuilt.")
    except subprocess.CalledProcessError as e:
        print(f"Warning: index rebuild failed: {e.stderr}", file=sys.stderr)
    except subprocess.TimeoutExpired:
        # QA-005: a hung update_index/build_graph child would otherwise stall
        # the summarizer mid-run and leave the index stale with no error.
        # 300 s mirrors the bound the graph rebuild applies to its own child.
        print(
            "Warning: index rebuild timed out after 300 s; index may be stale.",
            file=sys.stderr,
        )
    except OSError as e:
        print(f"Warning: could not run update_index.py: {e}", file=sys.stderr)
