#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "fastembed>=0.6.0,<1.0",
#   "sqlite-vec>=0.1.6,<1.0",
#   "rich>=13.0",
# ]
# ///
"""Unified vault search: semantic (positional query) or metadata (filter flags).

Partial re-export shim over the ``cli.search`` package (ARC-005). The
embeddings backend, metadata SQL query, grep body filter, output
formatters, env-var helpers, and the interactive TUI delegate have moved to
focused submodules under ``cli.search``; they are re-exported below so every
existing ``import vault_search`` consumer (vault_tui, vault_links,
parsidion-mcp, the summarizer's dedup pass, and the test suite) keeps
working byte-for-byte.

What stays here and why:
    ``search_with_meta``, ``search``, ``LAST_BACKEND``, and ``main`` remain
    in this entry shim because ``tests/test_vault_search_backend.py``
    monkeypatches ``vault_search._search_embeddings`` and Python resolves
    bare names in the *caller's* module globals at call time. Keeping
    ``search_with_meta`` (which calls ``_search_embeddings``) in the same
    module the test patches is the only way the patch takes effect without
    rewriting every test to patch ``cli.search.embeddings`` instead — the
    same exception the ``summarizer/`` split took for its anyio core.

Semantic mode — provide a natural language query:
    vault_search.py "sqlite vector search" --top 5
    vault_search.py "hook patterns" --json
    vault_search.py "qdrant embeddings" --min-score 0.4

Metadata mode — provide one or more filter flags (no positional query):
    vault_search.py --tag python --limit 10
    vault_search.py --folder Patterns
    vault_search.py --type debugging
    vault_search.py --project parsidion
    vault_search.py --recent-days 7
    vault_search.py --tag rust --folder Patterns --text

Both modes output the same JSON structure. Semantic results include a ``score``
field (cosine similarity); metadata results set ``score`` to ``null``.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import parmem_backend
import vault_common

# ---------------------------------------------------------------------------
# Re-exports from cli.search.* — every symbol the original vault_search.py
# exposed remains importable from ``vault_search`` (function objects are
# immutable, so ``from cli.search.X import f`` + ``vault_search.f(...)`` is a
# stable binding for external callers; only module-global *assignments* need
# the ``__getattr__`` live-attribute pattern, and the only mutable global is
# ``LAST_BACKEND`` which stays defined below).
# ---------------------------------------------------------------------------
from cli.search._common import (  # noqa: F401 — re-exports
    _DEFAULT_MODEL,
    _EMBED_MODEL_LOCK,
    _ENV_PREFIX,
    _VALID_BACKENDS,
    SearchResultEnvelope,
    _configured_search_backend,
)
from cli.search.embeddings import (  # noqa: F401 — re-exports
    _SERVICE_SPAWN_DEBOUNCE_S,
    _apply_decay,
    _embed_query,
    _embeddings_service_active,
    _get_embedding_model,
    _last_service_spawn_attempt,
    _open_db_semantic,
    _pack_vector,
    _search_embeddings,
    _service_embed,
    _spawn_service,
)
from cli.search.format import (  # noqa: F401 — re-exports
    _env_float,
    _env_int,
    _format_rich,
    _format_text,
)
from cli.search.metadata import (  # noqa: F401 — re-exports
    _apply_grep_filter,
    _get_all_notes_as_results,
    query,
)


def _interactive_search(vault: Path | None = None, backend: str | None = None) -> None:
    """Launch the interactive vault search TUI.

    ``vault_tui`` is imported lazily inside this function body so importing
    ``vault_search`` for metadata/grep modes does not pull in ``curses`` or
    ``fastembed`` eagerly. This deferred cross-import is the ARC-023 contract
    that keeps the vault_search <-> vault_tui edge cycle-free; it must stay in
    this entry shim (see ``tests/test_vault_imports.py``).
    """
    from vault_tui import interactive_search  # noqa: PLC0415 — lazy, avoids import cycle

    interactive_search(vault, backend=backend)


# ---------------------------------------------------------------------------
# Backend routing + public search API.
# These three (search_with_meta / search / LAST_BACKEND) stay in this entry
# shim so the ``tests/test_vault_search_backend.py`` monkeypatch of
# ``vault_search._search_embeddings`` resolves at call time — see the module
# docstring. ``main`` stays with them because it reads ``LAST_BACKEND``.
# ---------------------------------------------------------------------------


def search_with_meta(
    query: str,
    top: int = 10,
    min_score: float = 0.45,
    model_name: str = _DEFAULT_MODEL,
    vault: Path | None = None,
    backend: str | None = None,
) -> SearchResultEnvelope:
    """Search the vault and return ``(results, backend, score_kind)``.

    Same routing as :func:`search` (par-mem when available + selected, falling
    back to embeddings); additionally reports which backend served the call
    and what its ``score`` field means. Use this when you need to render a
    backend label, gate on score scale (``min_score`` is meaningful only for
    ``score_kind == "cosine"``), or call search concurrently.

    Args:
        query: Natural language query string.
        top: Maximum number of results to return.
        min_score: Minimum cosine similarity (embeddings backend only).
        model_name: fastembed model ID used when the index was built.
        vault: Optional vault path. Defaults to resolve_vault().
        backend: ``auto | par-mem | embeddings | none`` override; None reads
            the ``search.backend`` config key (default ``auto``).

    Returns:
        A :class:`SearchResultEnvelope` whose ``results`` is the list of dicts
        with keys: score, stem, title, folder, tags, path, summary, note_type,
        project, confidence, mtime, related, is_stale, incoming_links (sorted
        by score descending); ``backend`` names the serving path; and
        ``score_kind`` discriminates cosine vs RRF.
    """
    selected = (backend or "").strip().lower() or _configured_search_backend()
    if selected not in _VALID_BACKENDS:
        selected = "auto"

    if selected == "none":
        return SearchResultEnvelope([], "none", None)

    if selected in ("auto", "par-mem"):
        available = parmem_backend.resolve_parmem_backend(vault)
        if available and parmem_backend.ensure_vault_indexed(vault):
            parmem_results = parmem_backend.parmem_search(query, top_k=top, vault=vault)
            if parmem_results is not None:
                return SearchResultEnvelope(parmem_results, "par-mem", "rrf")
        if selected == "par-mem":
            # Explicit par-mem: no embeddings fallback (testing/debug affordance).
            return SearchResultEnvelope([], "par-mem", "rrf")

    embeddings_results = _search_embeddings(
        query=query, top=top, min_score=min_score, model_name=model_name, vault=vault
    )
    return SearchResultEnvelope(embeddings_results, "embeddings", "cosine")


# ARC-031 back-compat shim: tests and a couple of callers still read this name
# to render the --rich backend label. It is set as a side-effect of search()
# for that narrow purpose; new code should call search_with_meta() instead and
# read .backend off the envelope. The shim will go away once the remaining
# readers migrate.
LAST_BACKEND: str | None = None


def search(
    query: str,
    top: int = 10,
    min_score: float = 0.45,
    model_name: str = _DEFAULT_MODEL,
    vault: Path | None = None,
    backend: str | None = None,
) -> list[dict[str, object]]:
    """Search the vault for notes semantically similar to *query*.

    Routes to the optional par-mem backend when selected and available,
    silently falling back to the local embeddings pipeline. Both backends
    return identically shaped result dicts. ``min_score`` applies only to
    the embeddings backend — par-mem RRF scores are rank-fusion values, not
    cosines, and gate by rank/``top`` instead.

    ARC-031: this thin wrapper preserves the list-returning public API by
    delegating to :func:`search_with_meta` and stamping the deprecated
    ``LAST_BACKEND`` module attribute. Callers that need the backend or
    score-kind should call ``search_with_meta()`` directly.

    Args:
        query: Natural language query string.
        top: Maximum number of results to return.
        min_score: Minimum cosine similarity (embeddings backend only).
        model_name: fastembed model ID used when the index was built.
        vault: Optional vault path. Defaults to resolve_vault().
        backend: ``auto | par-mem | embeddings | none`` override; None reads
            the ``search.backend`` config key (default ``auto``).

    Returns:
        List of result dicts with keys: score, stem, title, folder, tags,
        path, summary, note_type, project, confidence, mtime, related,
        is_stale, incoming_links. Sorted by score descending.
    """
    global LAST_BACKEND
    envelope = search_with_meta(
        query=query,
        top=top,
        min_score=min_score,
        model_name=model_name,
        vault=vault,
        backend=backend,
    )
    LAST_BACKEND = envelope.backend
    return envelope.results


def main() -> None:
    """CLI entry point: semantic search or metadata filter depending on args."""
    parser = argparse.ArgumentParser(
        prog="vault-search",
        description=(
            "Search Parsidion vault notes by meaning (semantic) or by metadata filters.\n\n"
            "Semantic mode: provide a QUERY string.\n"
            "Metadata mode: provide one or more filter flags (--tag, --folder, etc.).\n\n"
            "Environment variables (VAULT_SEARCH_*):\n"
            "  FORMAT=json|text|rich   default output format\n"
            "  MIN_SCORE=0.0–1.0       minimum cosine similarity threshold\n"
            "  TOP=N                   max semantic results\n"
            "  LIMIT=N                 max metadata results\n"
            "  MODEL=<id>              fastembed model ID\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Vault selection flag
    parser.add_argument(
        "--vault",
        "-V",
        metavar="PATH|NAME",
        default=None,
        help="Vault path or named vault (default: ~/ParsidionVault, or legacy ~/ClaudeVault if it exists)",
    )

    # Positional — optional; triggers semantic mode when present
    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="Natural language query for semantic search. Omit to use metadata filters.",
    )

    # Semantic-only flags
    _cfg_top_k: int = vault_common.get_config("embeddings", "top_k", 10)
    _cfg_min_score: float = vault_common.get_config("embeddings", "min_score", 0.45)
    _eff_top_k = _env_int("TOP", _cfg_top_k)
    _eff_min_score = _env_float("MIN_SCORE", _cfg_min_score)
    _eff_model = os.environ.get(_ENV_PREFIX + "MODEL", _DEFAULT_MODEL)
    parser.add_argument(
        "--top",
        "-n",
        type=int,
        default=_eff_top_k,
        metavar="N",
        help=f"Semantic: max results (default {_eff_top_k}, env: VAULT_SEARCH_TOP).",
    )
    parser.add_argument(
        "--min-score",
        "-s",
        type=float,
        default=_eff_min_score,
        metavar="FLOAT",
        help=(
            f"Semantic: minimum cosine similarity 0.0–1.0 "
            f"(default {_eff_min_score}, env: VAULT_SEARCH_MIN_SCORE)."
        ),
    )
    parser.add_argument(
        "--model",
        "-m",
        default=_eff_model,
        metavar="MODEL",
        help=f"Semantic: fastembed model ID (default: {_eff_model}, env: VAULT_SEARCH_MODEL).",
    )
    parser.add_argument(
        "--backend",
        "-B",
        choices=["auto", "par-mem", "embeddings", "none"],
        default=None,
        help="Semantic: backend override (default: search.backend config, auto).",
    )

    # Metadata filter flags
    parser.add_argument(
        "--tag", "-T", metavar="TAG", help="Metadata: filter by exact tag token."
    )
    parser.add_argument(
        "--folder",
        "-f",
        metavar="FOLDER",
        help="Metadata: filter by exact folder name.",
    )
    parser.add_argument(
        "--type",
        "-k",
        metavar="TYPE",
        dest="note_type",
        help="Metadata: filter by note type.",
    )
    parser.add_argument(
        "--project", "-p", metavar="PROJECT", help="Metadata: filter by project name."
    )
    parser.add_argument(
        "--recent-days",
        "-d",
        metavar="N",
        type=int,
        help="Metadata: notes modified within the last N days.",
    )
    parser.add_argument(
        "--changed-since",
        "-c",
        metavar="DATE",
        help="Metadata: notes modified on/after DATE (YYYY-MM-DD). Uses file mtime.",
    )
    parser.add_argument(
        "--as-of",
        "-A",
        metavar="DATE",
        help="Metadata: point-in-time view — notes whose frontmatter date <= DATE (YYYY-MM-DD).",
    )

    # Grep / full-text body search flags
    parser.add_argument(
        "--grep",
        "-G",
        metavar="PATTERN",
        default=None,
        help=(
            "Full-text: filter notes whose body matches PATTERN (re.search). "
            "Case-insensitive by default; use --grep-case to make it case-sensitive. "
            "Can be combined with metadata filters or used standalone."
        ),
    )
    parser.add_argument(
        "--grep-case",
        action="store_true",
        default=False,
        help="Full-text: disable case-insensitive matching for --grep.",
    )

    _eff_limit = _env_int("LIMIT", 50)
    parser.add_argument(
        "--limit",
        "-l",
        metavar="N",
        type=int,
        default=_eff_limit,
        help=f"Metadata: maximum number of results (default: {_eff_limit}, env: VAULT_SEARCH_LIMIT).",
    )

    # Output format — VAULT_SEARCH_FORMAT=json|text|rich sets the default
    _eff_format = os.environ.get(_ENV_PREFIX + "FORMAT", "json").lower()
    if _eff_format not in {"json", "text", "rich"}:
        _eff_format = "json"
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--json",
        "-j",
        dest="output_format",
        action="store_const",
        const="json",
        help="JSON array output.",
    )
    output_group.add_argument(
        "--text",
        "-t",
        dest="output_format",
        action="store_const",
        const="text",
        help="Human-readable one-line-per-note output.",
    )
    output_group.add_argument(
        "--rich",
        "-r",
        dest="output_format",
        action="store_const",
        const="rich",
        help="Rich colorized one-line-per-note output.",
    )
    parser.set_defaults(output_format=_eff_format)

    # Interactive mode flag
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        default=False,
        help="Launch interactive curses TUI for real-time search.",
    )

    args = parser.parse_args()

    # Resolve vault path
    vault_path = vault_common.resolve_vault(explicit=args.vault, cwd=os.getcwd())

    # Interactive mode — runs before the normal search logic
    if args.interactive:
        _interactive_search(vault_path, backend=args.backend)
        return

    _filter_flags = (
        args.tag,
        args.folder,
        args.note_type,
        args.project,
        args.recent_days,
        args.changed_since,
        args.as_of,
    )
    has_query = args.query is not None
    has_filters = any(f is not None for f in _filter_flags)
    has_grep = args.grep is not None

    if not has_query and not has_filters and not has_grep:
        parser.error(
            "Provide a search QUERY for semantic search, or at least one filter flag "
            "(--tag, --folder, --type, --project, --recent-days, --changed-since, --as-of, --grep) for metadata/grep search."
        )

    if has_query and has_filters:
        parser.error(
            "Semantic search (QUERY) and metadata filters are mutually exclusive. "
            "Use one mode at a time."
        )

    if has_query:
        selected_backend = args.backend or _configured_search_backend()
        parmem_may_serve = selected_backend in (
            "auto",
            "par-mem",
        ) and parmem_backend.resolve_parmem_backend(vault_path)
        db_path = vault_common.get_embeddings_db_path(vault_path)
        if not db_path.exists() and not parmem_may_serve and selected_backend != "none":
            print(
                "embeddings.db not found — run build_embeddings.py first",
                file=sys.stderr,
            )
            sys.exit(0)
        results = search(
            query=args.query,
            top=args.top,
            min_score=args.min_score,
            model_name=args.model,
            vault=vault_path,
            backend=args.backend,
        )
    else:
        results = query(
            tag=args.tag,
            folder=args.folder,
            note_type=args.note_type,
            project=args.project,
            recent_days=args.recent_days,
            changed_since=args.changed_since,
            as_of=args.as_of,
            limit=args.limit,
            vault=vault_path,
        )

    # --grep post-filter: applied after semantic or metadata results, or standalone
    if has_grep:
        results = _apply_grep_filter(
            results=results,
            pattern=args.grep,
            case_sensitive=args.grep_case,
            has_filters=has_filters,
            has_query=has_query,
            limit=args.limit,
            vault=vault_path,
        )

    if args.output_format == "text":
        print(_format_text(results))
    elif args.output_format == "rich":
        if has_query and LAST_BACKEND is not None:
            from rich.console import Console

            Console(stderr=True).print(f"[dim]backend: {LAST_BACKEND}[/dim]")
        _format_rich(results)
    else:
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
