"""session_start — focused submodules extracted from session_start_hook.py (ARC-006).

The original 1,253-line God module was decomposed into a package of focused
submodules.  ``session_start_hook.py`` remains as the entry shim (shebang +
``main()``) and re-exports every public and private symbol so existing
``import session_start_hook`` consumers and test ``monkeypatch`` calls keep
working byte-for-byte.

Submodule layout:
    graph_retrieval — Tier 1 neighbour expansion + Tier 2 tag/hubness rerank
                      plus the AI-mode candidate enrichment that splices
                      1-hop wikilink neighbours into the selector pool.
    seed_selection  — candidate building (project + recent notes, SQLite-first
                      with filesystem fallback) and adaptive usefulness rerank.
    ai_selector     — per-vault single-flight lock + cooldown stamp mechanics
                      for the optional AI note-selection path.
    context         — string assembly (untrusted-content framing, pending /
                      dead-letter notices, cross-session delta) and the debug
                      log writer.

The orchestration core (``_run_semantic_search``, ``_select_seed_notes``,
``_select_context_with_ai``, ``build_session_context``,
``main``) stays in the entry shim because tests monkeypatch these functions
and their patched callees (``find_notes_by_project``, ``find_recent_notes``,
``_release_ai_lock``, ``_try_acquire_ai_lock``, ``read_note_summary``,
``ai_backend.run_ai_prompt``, ``_select_context_with_ai`` itself) on the
``session_start_hook`` module and Python resolves bare names in the *caller's*
module globals at call time — keeping the callers in the shim is the only way
the patches take effect without pervasive ``session_start_hook.X()`` dispatch
rewrites.  This mirrors the ``summarizer/`` decomposition (ARC-009).

Stdlib-only — same constraint as the entry shim and the rest of the hook
scripts.  ``ai_backend`` / ``parsight_backend`` / ``vault_*`` imports are
permitted because they are themselves stdlib-only.
"""
