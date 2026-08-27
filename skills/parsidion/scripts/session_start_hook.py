#!/usr/bin/env python3
"""Claude Code SessionStart hook that loads relevant vault context.

Reads JSON from stdin with session info, searches the vault for project-specific
and recent notes, and outputs additionalContext as JSON to stdout.

Optional --ai flag uses the configured AI backend to intelligently select the
most relevant notes rather than relying on recency and project tags alone.
The installer registers a 60 s SessionStart timeout for every runtime, so no
manual settings.json timeout edit is needed for the AI call.

ARC-006: the implementation is decomposed into focused submodules under
``session_start/`` (``graph_retrieval``, ``seed_selection``, ``ai_selector``,
``context``).  This file is the entry shim — it re-exports every moved symbol
so existing ``import session_start_hook`` consumers and test ``monkeypatch``
calls keep working byte-for-byte, and it keeps the orchestration core
(``_run_semantic_search``, ``_select_seed_notes``, ``_select_context_with_ai``,
``build_session_context``, ``main``) inline because tests
monkeypatch these functions and their patched callees on the
``session_start_hook`` module, and Python resolves bare names in the caller's
own module globals at call time.  See ``session_start/__init__.py`` for the
full rationale.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import traceback
from datetime import date, datetime
from io import TextIOWrapper
from pathlib import Path

from core import ai_backend, parsight_backend
from core.vault_adaptive import (
    load_last_seen,
    save_injected_notes,
    save_last_seen,
)
from core.vault_config import load_typed_config, validate_config
from core.vault_fs import ensure_vault_dirs, today_daily_path
from core.vault_hooks import (
    env_without_claudecode,
    get_project_name,
    log_hook_error,
    write_hook_event,
)
from core.vault_index import (
    SessionIndexSnapshot,
    build_compact_index,
    build_context_block,
    find_notes_by_project,
    find_recent_notes,
    load_graph_metadata,
    load_session_index_snapshot,
    read_note_summary,
)
from core.vault_path import (
    get_embeddings_db_path,
    resolve_vault,
)
from prompt_templates import render

# ARC-006: focused submodules.  These from-imports load the subpackage AND
# re-export every moved symbol on this module's namespace — that is what lets
# test monkeypatching of ``session_start_hook._release_ai_lock`` /
# ``._select_context_with_ai`` / etc. keep working after the extraction, and
# what lets the codex/gemini adapters keep doing
# ``from session_start_hook import build_session_context``.
# re-exports are intentional.
from session_start.ai_selector import (
    _AI_LOCK_FILENAME,
    _AI_STAMP_FILENAME,
    _ai_lock_path,
    _ai_stamp_path,
    _release_ai_lock,
    _try_acquire_ai_lock,
    _write_ai_cooldown_stamp,
)
from session_start.context import (
    _assemble_context,
    _build_dead_letter_notice,
    _build_delta_section,
    _build_pending_notice,
    _debug_file,
    _write_debug_log,
)
from session_start.graph_retrieval import (
    _DEFAULT_GRAPH_EXPAND,
    _DEFAULT_GRAPH_EXPAND_MAX,
    _DEFAULT_GRAPH_RERANK,
    _apply_graph_retrieval,
    _enrich_with_graph,
    _graph_neighbors,
    _rank_by_graph,
)
from session_start.seed_selection import (
    _build_candidates,
    _rank_by_usefulness,
)

# ARC-101: no module-level config read here. The previous import-time
# ``_DEFAULT_AI_MODEL = load_typed_config()...`` primed the LRU-frozen
# ``_resolve_vault_cached(None, None)`` with the process cwd before the hook
# payload's cwd was parsed, silently pinning every later vault-less lookup in
# the process to the wrong vault. Config is read after the payload cwd is
# resolved to a vault (see main()/build_session_context).
_BACKEND_DEFAULT_AI_MODEL = "__parsidion_backend_default__"
_DEFAULT_MAX_CHARS = 4000
# PRF-104: cap on the recent-notes fetch in the standard context path. The
# injected context is char-budgeted (_DEFAULT_MAX_CHARS), so the tail of a
# thousands-row fetch could never reach the agent.
_CONTEXT_RECENT_LIMIT = 200
_VAULT_SEARCH_SCRIPT_NAME: str = "vault_search.py"
_SEMANTIC_TOP_N: int = 5
_SEMANTIC_TIMEOUT: int = 10  # seconds
# PRF-101: how the semantic leg of the last _run_semantic_search call was
# served — "parsight-inproc" (in-process parsight_search call), "subprocess"
# (``uv run vault_search.py``), or "timeout" (subprocess killed at
# _SEMANTIC_TIMEOUT). None when semantic search never ran or failed before
# choosing a path. Read by main() for the SessionStart hook event so
# degradation is visible in ``vault-stats --hooks``.
_SEMANTIC_SOURCE: str | None = None
# Characters reserved for the vault-context header injected before the AI-selected
# note content.  Ensures the final output never slightly exceeds max_chars.
_AI_CONTEXT_HEADER_RESERVE: int = 500

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


def _is_ai_cooldown_active(vault_path: Path) -> bool:
    """Return True when AI SessionStart ran too recently for this vault.

    Lives in the orchestrator (not ``session_start/ai_selector.py``) because
    tests monkeypatch ``session_start_hook.get_config`` and then call this
    helper directly — bare-name ``get_config`` lookup must therefore resolve
    in this module's namespace.
    """
    cooldown_seconds = load_typed_config(
        vault=vault_path
    ).session_start_hook.ai_cooldown_seconds
    if cooldown_seconds <= 0:
        return False
    stamp_path = _ai_stamp_path(vault_path)
    try:
        age_seconds = datetime.now().timestamp() - stamp_path.stat().st_mtime
    except OSError:
        return False
    return age_seconds < cooldown_seconds


def _run_semantic_search(
    query: str,
    top: int,
    vault_search_script: Path,
    vault_path: Path,
) -> list[Path]:
    """Run the semantic search and return matching note paths.

    PRF-101: when parsight serves retrieval (backend ``auto``/``parsight``
    and the daemon is available), the query runs in-process via
    ``parsight_backend.parsight_search`` — a stdlib-only call already
    imported here. The ``uv run vault_search.py`` subprocess is kept
    exclusively for the embeddings fallback (parsight unavailable or
    backend forced to ``embeddings``): the subprocess only ever existed to
    isolate fastembed, which the parsight path never touches.

    Returns an empty list if the script doesn't exist, the DB is missing,
    the subprocess times out, or any other error occurs.

    Args:
        query: Search query string.
        top: Number of results to request.
        vault_search_script: Path to vault_search.py.
        vault_path: The vault root path.

    Returns:
        List of note Paths from the semantic search results.
    """
    global _SEMANTIC_SOURCE
    import json as _json

    _SEMANTIC_SOURCE = None
    backend = (
        (load_typed_config(vault=vault_path).search.backend or "auto").strip().lower()
    )
    parsight_available = parsight_backend.resolve_parsight_backend(vault_path)

    if backend in ("auto", "parsight") and parsight_available:
        # In-process parsight path. parsight_search never raises and returns
        # None on any failure so the caller can fall back.
        results = parsight_backend.parsight_search(query, top_k=top, vault=vault_path)
        if results is not None:
            _SEMANTIC_SOURCE = "parsight-inproc"
            return [Path(str(item["path"])) for item in results]
        if backend == "parsight":
            # Forced parsight failed in-process; the subprocess routes to the
            # same backend, so spawning it would only re-pay the tower to
            # fail again.
            return []
        # auto: the parsight query itself failed — fall through to the
        # embeddings subprocess fallback below.

    if not vault_search_script.exists():
        return []

    # parsight serves retrieval without a local embeddings DB. Only require
    # embeddings.db for the local-embeddings path: an explicit ``embeddings``
    # backend, or ``auto`` falling back when parsight is unavailable. Matches
    # vault_search.py's own backend routing.
    if backend == "embeddings" or (backend == "auto" and not parsight_available):
        db_path = get_embeddings_db_path(vault=vault_path)
        if not db_path.exists():
            return []

    try:
        # Use Popen + start_new_session so the entire process group (uv + its
        # Python child) can be killed together on timeout.  subprocess.run with
        # timeout only kills the direct child (uv), leaving the grandchild
        # (vault_search.py Python) holding the stdout pipe open, which causes
        # communicate() to block indefinitely and turns session_start_hook.py
        # into a zombie process.
        proc = subprocess.Popen(
            [
                "uv",
                "run",
                "--no-project",
                str(vault_search_script),
                "--top",
                str(top),
                "--json",
                # SEC-128: ``--`` separates flags from the note-derived
                # positional so a vault note named "[[--help]]" or
                # "--top" cannot parse as a vault-search flag.
                "--",
                query,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # new process group — enables killpg
            env=env_without_claudecode(),
        )
        _SEMANTIC_SOURCE = "subprocess"
        try:
            stdout, _ = proc.communicate(timeout=_SEMANTIC_TIMEOUT)
        except subprocess.TimeoutExpired:
            # Kill the entire process group (uv + vault_search.py Python child)
            # so the stdout pipe is closed and communicate() returns immediately.
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                proc.kill()
            proc.wait()
            _SEMANTIC_SOURCE = "timeout"
            return []
        if proc.returncode != 0:
            return []
        items: list[dict[str, object]] = _json.loads(stdout)
        return [Path(str(item["path"])) for item in items]
    except (
        FileNotFoundError,
        OSError,
        _json.JSONDecodeError,
        KeyError,
        ValueError,
    ):
        return []


def _select_context_with_ai(
    project_name: str,
    cwd: str,
    candidate_notes: list[Path],
    model: str | None,
    max_chars: int = _DEFAULT_MAX_CHARS,
    vault_path: Path | None = None,
) -> str:
    """Use the configured AI backend to select relevant notes for session context.

    Backend execution is delegated to ai_backend.run_ai_prompt so Claude and
    Codex model defaults are resolved consistently.

    Args:
        project_name: The current project name.
        cwd: The current working directory.
        candidate_notes: Ordered list of candidate note paths (project-first).
        model: Explicit model ID to use, or None for the backend default.
        max_chars: Maximum characters for the output context block.
        vault_path: The vault root path.

    Returns:
        Formatted context string chosen by the AI, or empty string on failure.
    """
    if vault_path is None:
        vault_path = resolve_vault(cwd=cwd)

    lock_handle: TextIOWrapper | None = None
    if load_typed_config(vault=vault_path).session_start_hook.ai_single_flight:
        lock_handle = _try_acquire_ai_lock(vault_path)
        if lock_handle is None:
            return ""

    if _is_ai_cooldown_active(vault_path):
        _release_ai_lock(lock_handle)
        return ""

    try:
        # Build the candidate block, capped so the prompt stays manageable.
        candidate_parts: list[str] = []
        char_budget = 8000

        for note_path in candidate_notes:
            try:
                rel = note_path.relative_to(vault_path)
            except ValueError:
                rel = Path(note_path.parent.name) / note_path.name

            summary = read_note_summary(note_path, max_lines=6)
            if not summary:
                continue

            entry = f"### {rel}\n{summary}\n\n"
            if sum(len(p) for p in candidate_parts) + len(entry) > char_budget:
                break
            candidate_parts.append(entry)

        if not candidate_parts:
            return ""

        candidates_text = "".join(candidate_parts)
        output_limit = (
            max_chars - _AI_CONTEXT_HEADER_RESERVE
        )  # reserve headroom for the header

        prompt = render(
            "select-notes",
            project_name=project_name,
            cwd=str(cwd),
            output_limit=output_limit,
            candidates_text=candidates_text,
        )

        output = ai_backend.run_ai_prompt(
            prompt,
            model=model,
            model_tier="small",
            timeout=load_typed_config(vault=vault_path).session_start_hook.ai_timeout,
            cwd=cwd,
            purpose="session-start-selection",
            vault=vault_path,
        )
        if output:
            _write_ai_cooldown_stamp(vault_path)
            return output.strip()
        # Attempt completed without output (timeout, backend error, or empty
        # response) — the full ai_timeout budget was already spent. Stamp the
        # cooldown so back-to-back session starts don't re-pay it while the
        # backend is slow or down; falls through to the standard path below.
        _write_ai_cooldown_stamp(vault_path)
    except (FileNotFoundError, OSError):
        pass
    finally:
        _release_ai_lock(lock_handle)

    return ""


def _select_seed_notes(
    project_name: str,
    vault_path: Path,
    daily_path: Path,
    snapshot: SessionIndexSnapshot | None = None,
) -> tuple[list[Path], set[Path]]:
    """Collect and de-duplicate the seed note set for the standard context path.

    Merges project notes, recent notes, and semantic-search blends (served by
    the configured backend — parsight, or local ``embeddings.db`` as fallback),
    then ensures today's daily note is included.
    Order is preserved. The returned ``seen`` set carries resolved paths so
    graph-neighbour expansion (:func:`_apply_graph_retrieval`) dedups against
    the same index.
    """
    project_notes: list[Path] = find_notes_by_project(
        project_name, vault=vault_path, snapshot=snapshot
    )
    recent_days: int = load_typed_config(
        vault=vault_path
    ).session_start_hook.recent_days
    # PRF-104: cap the recent-notes fetch. The general-purpose finder is still
    # unbounded for other callers; here the injected-context budget is 4,000
    # chars, so pulling thousands of rows only to truncate them serves nothing.
    recent_notes: list[Path] = find_recent_notes(
        days=recent_days,
        vault=vault_path,
        limit=_CONTEXT_RECENT_LIMIT,
        snapshot=snapshot,
    )

    seen: set[Path] = set()
    all_notes: list[Path] = []

    for note in (*project_notes, *recent_notes):
        resolved = note.resolve()
        if resolved not in seen:
            seen.add(resolved)
            all_notes.append(note)

    use_embeddings: bool = load_typed_config(
        vault=vault_path
    ).session_start_hook.use_embeddings
    if use_embeddings:
        # Backend-aware gating lives inside _run_semantic_search (parsight needs
        # no local embeddings.db), so just delegate; it returns [] when there is
        # nothing to search rather than spawning vault_search pointlessly.
        vault_search_script = Path(__file__).parent / _VAULT_SEARCH_SCRIPT_NAME
        semantic_notes = _run_semantic_search(
            project_name, _SEMANTIC_TOP_N, vault_search_script, vault_path
        )
        for note in semantic_notes:
            resolved = note.resolve()
            if resolved not in seen:
                seen.add(resolved)
                all_notes.append(note)

    daily_resolved = daily_path.resolve()
    if daily_resolved not in seen:
        all_notes.append(daily_path)

    return all_notes, seen


def build_session_context(
    cwd: str,
    ai_model: str | None = None,
    max_chars: int = _DEFAULT_MAX_CHARS,
    verbose_mode: bool = False,
    ai_enabled: bool = False,
) -> tuple[str, int]:
    """Build a context string from vault notes relevant to the current session.

    Args:
        cwd: The current working directory from the session info.
        ai_model: Explicit model override for AI note selection. When None and
            ai_enabled is true, the configured backend resolves its default.
            Falls back to standard behaviour on failure.
        max_chars: Maximum total characters for the context output (default: 4000).
        verbose_mode: When True, inject full note summaries instead of the default
            compact one-line-per-note index. Ignored when AI mode is enabled (AI
            mode always uses full summaries). Defaults to False.
        ai_enabled: Enables AI selection even when ai_model is None, allowing the
            backend to resolve its tier default model.

    Returns:
        Tuple of (formatted context string, number of notes injected).
    """
    project_name: str = get_project_name(cwd)
    today_str: str = date.today().isoformat()

    # Resolve vault path from cwd (supports multi-vault)
    vault_path: Path = resolve_vault(cwd=cwd)

    # Ensure vault directories exist and create today's daily note
    ensure_vault_dirs(vault=vault_path)

    # PRF-104: one note_index read for the whole invocation. Every DB-backed
    # consumer below (seed queries, graph metadata, the compact index, the
    # delta block) takes its data off this snapshot instead of opening its own
    # connection. None means no embeddings.db -- each consumer then falls back
    # exactly as it did before.
    snapshot = load_session_index_snapshot(vault=vault_path)

    header: str = f"# Vault Context for {project_name}\n**Date:** {today_str}\n\n"

    # --- Pending queue warning (#3) ---
    pending_notice = _build_pending_notice(vault_path)
    if load_typed_config(vault=vault_path).session_start_hook.show_dead_letter_notice:
        dead_letter_notice = _build_dead_letter_notice(vault_path)
        if dead_letter_notice:
            pending_notice = (
                f"{pending_notice}\n{dead_letter_notice}"
                if pending_notice
                else dead_letter_notice
            )

    # --- Cross-session delta (#10) ---
    delta_section = ""
    if load_typed_config(vault=vault_path).session_start_hook.track_delta:
        last_seen_map = load_last_seen(vault=vault_path)
        last_seen_ts = last_seen_map.get(project_name)
        delta_section = _build_delta_section(
            project_name, last_seen_ts, vault_path, snapshot=snapshot
        )
    # Update last-seen timestamp for this project
    save_last_seen(project_name, vault=vault_path)

    notes_injected = 0

    if ai_enabled or ai_model is not None:
        # Phase 3: widen the AI's candidate pool with 1-hop graph neighbours of
        # the project notes so the selector sees related prior art.  The pool
        # is ranked and pruned Python-side (project match > graph adjacency >
        # adaptive usefulness > recency > hubness) so the selector's prompt
        # carries the best subset, not an arbitrary 8000-char prefix.
        ai_graph_meta = (
            snapshot.graph_metadata()
            if snapshot is not None
            else load_graph_metadata(vault=vault_path)
        )
        ai_max_add = 0
        _cfg = load_typed_config(vault=vault_path)
        if _cfg.session_start_hook.graph_expand and ai_graph_meta is not None:
            ai_max_add = _cfg.session_start_hook.graph_expand_max
        candidates = _build_candidates(
            project_name,
            vault_path,
            graph_meta=ai_graph_meta,
            graph_expand_max=ai_max_add,
            max_candidates=load_typed_config(
                vault=vault_path
            ).session_start_hook.ai_candidates_max,
            snapshot=snapshot,
        )
        ai_context = _select_context_with_ai(
            project_name, cwd, candidates, ai_model, max_chars, vault_path=vault_path
        )
        if ai_context:
            notes_injected = ai_context.count("\n### ") + (
                1 if ai_context.startswith("### ") else 0
            )
            context = _assemble_context(
                header, ai_context, pending_notice, delta_section
            )
            return context, notes_injected
        # AI failed — fall through to standard behaviour

    # Standard behaviour: project notes + recent notes + today's daily note
    daily_path: Path = today_daily_path(vault=vault_path)
    if not daily_path.exists():
        # Create daily note if missing
        ensure_vault_dirs(vault=vault_path)
        from datetime import date as _date

        _month = f"{_date.today().year:04d}-{_date.today().month:02d}"
        daily_dir = vault_path / "Daily" / _month
        daily_dir.mkdir(parents=True, exist_ok=True)
        daily_path.touch()

    all_notes, seen = _select_seed_notes(
        project_name, vault_path, daily_path, snapshot=snapshot
    )

    # Graph retrieval (Tier 1 neighbour expansion + Tier 2 tag/hubness rerank)
    # plus the adaptive usefulness rerank. The seed snapshot is captured inside
    # the helper BEFORE expansion, so Tier 2 reflects the intentional selection.
    adaptive_enabled: bool = load_typed_config(
        vault=vault_path
    ).adaptive_context.enabled
    graph_meta = (
        snapshot.graph_metadata()
        if snapshot is not None
        else load_graph_metadata(vault=vault_path)
    )
    all_notes = _apply_graph_retrieval(
        all_notes, seen, graph_meta, vault_path, adaptive_enabled, snapshot=snapshot
    )

    notes_injected = len(all_notes)

    if not all_notes:
        context = _assemble_context(
            header, "_No relevant vault notes found._", pending_notice, delta_section
        )
        return context, 0

    # Build context block from collected notes, reserving space for the header
    max_body_chars: int = max_chars - len(header)
    if not verbose_mode:
        context_body: str = build_compact_index(
            all_notes, max_chars=max_body_chars, vault=vault_path, snapshot=snapshot
        )
    else:
        context_body = build_context_block(all_notes, max_chars=max_body_chars)

    if not context_body:
        context = _assemble_context(
            header, "_No relevant vault notes found._", pending_notice, delta_section
        )
        return context, 0

    # Save injected stems for usefulness tracking
    if adaptive_enabled:
        injected_stems = [p.stem for p in all_notes]
        save_injected_notes(project_name, injected_stems)

    context = _assemble_context(header, context_body, pending_notice, delta_section)
    return context, notes_injected


def main() -> None:
    """Entry point: read session JSON from stdin, output context JSON to stdout."""
    if os.environ.get("PARSIDION_INTERNAL"):
        sys.stdout.write("{}")
        return

    parser = argparse.ArgumentParser(
        description="Claude Code SessionStart hook — loads relevant vault context.",
    )
    parser.add_argument(
        "--ai",
        metavar="MODEL",
        nargs="?",
        const=_BACKEND_DEFAULT_AI_MODEL,
        default=None,
        help=(
            "Use the specified model to intelligently select the most relevant "
            "vault notes (no MODEL = configured backend default). "
            "The installer's 60 s SessionStart timeout covers the AI call."
        ),
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=None,
        metavar="N",
        help=f"Maximum characters for injected context (default: {_DEFAULT_MAX_CHARS})",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help=(
            "Inject full note summaries instead of the default compact one-line-per-note "
            "index. Uses significantly more tokens."
        ),
    )
    parser.add_argument(
        "--debug",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            f"Append injected context and metadata to {_debug_file()} "
            "for quality evaluation. Use --no-debug to force off even if "
            "config.yaml enables it."
        ),
    )
    args = parser.parse_args()

    try:
        input_data: dict = json.loads(sys.stdin.read())
        cwd: str = input_data.get("cwd", "")

        if not cwd:
            cwd = str(Path.cwd())

        # Resolve vault path from cwd (supports multi-vault)
        vault_path: Path = resolve_vault(cwd=cwd)

        # Resolve options: defaults → config → CLI args
        ai_model: str | None
        ai_enabled: bool
        if args.ai == _BACKEND_DEFAULT_AI_MODEL:
            ai_model = None
            ai_enabled = True
        elif args.ai is not None:
            ai_model = args.ai
            ai_enabled = True
        else:
            ai_model = load_typed_config(vault=vault_path).session_start_hook.ai_model
            ai_enabled = ai_model is not None
        max_chars: int = (
            args.max_chars
            if args.max_chars is not None
            else load_typed_config(vault=vault_path).session_start_hook.max_chars
        )
        verbose_mode: bool = (
            args.verbose
            or load_typed_config(vault=vault_path).session_start_hook.verbose_mode
        )
        # args.debug is always a bool (BooleanOptionalAction); OR with config so
        # either --debug CLI flag or config.yaml debug:true enables it, while
        # --no-debug explicitly overrides config.
        debug: bool = (
            args.debug or load_typed_config(vault=vault_path).session_start_hook.debug
        )

        # Config validation (#5) — warn on startup for typos
        config_warnings = validate_config(vault=vault_path)
        for warning in config_warnings:
            print(f"[session_start_hook] {warning}", file=sys.stderr)

        start_time = datetime.now()
        context, notes_injected = build_session_context(
            cwd,
            ai_model=ai_model,
            max_chars=max_chars,
            verbose_mode=verbose_mode,
            ai_enabled=ai_enabled,
        )
        elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000

        project_name = get_project_name(cwd)

        # Hook event log (#1). PRF-101: record how the semantic leg was served
        # (or that it timed out) so degradation shows up in vault-stats --hooks.
        extra_event: dict[str, object] = {}
        if _SEMANTIC_SOURCE:
            extra_event["semantic"] = _SEMANTIC_SOURCE
        write_hook_event(
            hook="SessionStart",
            project=project_name,
            duration_ms=elapsed_ms,
            notes_injected=notes_injected,
            chars=len(context),
            vault=vault_path,
            **extra_event,
        )

        # parsight watch hold: fire-and-forget so live vault edits reindex in
        # parsight while this session is active. Released in session_stop_hook;
        # server-side TTL covers crashed sessions. No-op when the backend is
        # unavailable — must never block or fail the hook.
        session_id = str(input_data.get("session_id", "") or "")
        if session_id:
            parsight_backend.spawn_watch(vault_path, session_id)

        if debug:
            _write_debug_log(
                context=context,
                cwd=cwd,
                project_name=project_name,
                ai_model=ai_model,
                max_chars=max_chars,
                elapsed_ms=elapsed_ms,
                verbose_mode=verbose_mode,
            )

        output: dict = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        }

        sys.stdout.write(json.dumps(output))

    except Exception:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        # Log unexpected programming errors to a persistent file so regressions
        # are visible without requiring manual stderr inspection.
        log_hook_error("session_start_hook")
        # On any error, output valid JSON with empty context so the hook doesn't crash
        fallback: dict = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "",
            }
        }
        sys.stdout.write(json.dumps(fallback))


if __name__ == "__main__":
    main()
