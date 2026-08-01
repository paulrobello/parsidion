"""index — focused submodules extracted from update_index.py (ARC-005).

The original 1,080-line ``update_index.py`` God module mixed PID-based
singleton guarding, per-note parsing, index assembly, Markdown rendering,
SQLite ``note_index`` writes, graph rebuild, and the argparse entry point
in one file. ARC-005 decomposes it into a package of focused submodules
behind the same proven ``doctor/`` layout, while ``update_index.py``
remains as a thin re-export shim and CLI entry point so every
``import update_index`` consumer and test attribute access
(``update_index.build_index``, ``update_index._extract_summary``,
``update_index.pid_file``, ``update_index._singleton_guard``,
``update_index.SUMMARY_MAX_CHARS``, …) keeps working byte-for-byte.

Submodule layout:
    _common    — FOLDER_ORDER, RECENT_DAYS, RECENT_MAX, SUMMARY_MAX_CHARS,
                 STALE_DAYS (shared tuning constants).
    models     — NoteEntry, NoteRecord NamedTuples.
    parse      — _WIKILINK_RE, _extract_summary, _folder_name, _wikilink,
                 _extract_wikilink_stems, _parse_note_record, _extract_title.
    build      — _compute_incoming_link_counts, _build_note_db_rows,
                 build_index (two-pass index assembly).
    render     — build_tags_md, build_manifests (Markdown emission).
    db         — _write_note_index_to_db (note_index table upsert).
    graph      — _find_build_graph_script, _rebuild_graph
                 (post-index ``build_graph.py`` invocation).
    cli        — _parse_args (argparse parser; ``main`` stays in the shim).

What stays in ``update_index.py`` and why:
    ``pid_file``, ``_write_pid``, ``_release_pid``, ``_singleton_guard``,
    and the ``_is_process_running`` alias remain defined in the entry shim.
    ``tests/test_index_enhancements.py`` patches
    ``update_index._is_process_running`` and ``update_index.os.getpid``
    (module-attribute patches), and ``_singleton_guard`` calls
    ``_is_process_running`` as a bare name that must resolve in the
    module the test patches — so the singleton cluster stays put.
    ``main`` stays too: it weaves the singleton guard, the inline
    ``__file__``-relative ``build_embeddings.py`` discovery, and the
    par-mem/embeddings spawn into one entry point that is simpler to keep
    at the scripts root than to relocate with adjusted path math.

Behaviour is identical to the original — this is a pure structural move.
"""

from __future__ import annotations
