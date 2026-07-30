"""summarizer — focused submodules extracted from summarize_sessions.py (ARC-009).

The original 2,724-line God module was decomposed into a package of focused
submodules.  ``summarize_sessions.py`` remains as the PEP-723 entry shim
(shebang + ``uv run --script`` block + ``main()``) and re-exports every public
and private symbol so existing ``import summarize_sessions`` consumers and
test ``monkeypatch`` calls keep working byte-for-byte.

Submodule layout:
    _state_const  — sentinels, enums, regexes, default config constants.
    failure       — _mark_failure / _format_failure_record / _failure_record_retryable.
    dead_letter   — dead-letter queue (dead_letters.jsonl) read / append / prune.
    progress      — progress-file writer for vault-stats --summarizer-progress.
    lock          — cross-process summarizer lock (claim / release).
    queue         — pending-queue read / remove-processed / index rebuild.
    notes         — frontmatter helpers, tag backfill, note writer.
    dedup         — stem resolution, dedup-candidate search, tag/project readers.
    transcript    — raw transcript preprocessing (tail extraction, code-fence strip).

The anyio core (``_run_summarizer_prompt``, ``_summarize_chunk``,
``preprocess_transcript_hierarchical``, ``build_prompt``, ``summarize_one``,
``run_all``, ``main``) stays in the entry shim because every test monkeypatches
those functions on the ``summarize_sessions`` module and Python resolves bare
names in the *caller's* module globals at call time — keeping the callers in the
shim is the only way the patches take effect without pervasive
``summarize_sessions.X()`` dispatch rewrites.

Stdlib-only is NOT required here (unlike the hook scripts): submodules run under
the PEP-723 entry script's env and MAY ``import anyio`` and ``import
vault_common``.
"""
