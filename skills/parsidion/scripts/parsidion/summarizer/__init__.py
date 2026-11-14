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
    transcript    — raw + hierarchical transcript preprocessing (tail extraction,
                   code-fence strip, chunk summarization).
    prompt        — backend prompt construction (build_prompt, tag/dedup renderers)
                   and the prompt runner (_run_summarizer_prompt).
    pipeline      — the summarize_one decision state machine + its stage helpers
                   (_early_gate / _apply_merge_decision / _handle_write_gate_decision
                   / _apply_backlinks_and_strip_links). Added in QA-003.

The driver (``run_all``) and CLI (``main``, ``_build_parser``) stay in the entry
shim. QA-003 broke the bare-name monkeypatch contract that previously kept the
anyio core (``summarize_one``, its stage helpers, ``preprocess_*``,
``_summarize_chunk``, ``_run_summarizer_prompt``) pinned in the shim: those now
live in ``summarizer.{pipeline,transcript,prompt}``, and tests monkeypatch them
on the submodule where the bare-name lookup happens. The shim still re-exports
every symbol so legacy ``import summarize_sessions`` consumers and
``summarize_sessions.X`` references (including ``run_all``'s call sites) keep
working.

Stdlib-only is NOT required here (unlike the hook scripts): submodules run under
the PEP-723 entry script's env and MAY ``import anyio``.
"""
