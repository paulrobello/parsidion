"""merge — focused submodules extracted from vault_merge.py (ARC-005).

The original 1,179-line ``vault_merge.py`` God module mixed AI-merge body
logic, dry-run preview caching, vault-wide wikilink rewriting, embedding
duplicate scanning, and the argparse entry point in one file. ARC-005
decomposes it into a package of focused submodules behind the same proven
``doctor/`` layout, while ``vault_merge.py`` remains as a thin re-export
shim and CLI entry point (``vault-merge = "vault_merge:main"`` in
``pyproject.toml``) so every ``import vault_merge`` consumer and test
attribute access (``vault_merge._ai_merge_bodies``,
``vault_merge.AIMergeOutputError``, ``vault_merge.ai_backend``,
``vault_merge._hash_content``, …) keeps working byte-for-byte.

Submodule layout:
    ai_helpers   — _is_valid_merge_body, _configured_merge_model,
                   _configured_merge_timeout (AI-output validation + config).
    lookup       — _find_note (path / stem resolution).
    frontmatter  — _WIKILINK_SPAN_RE, _parse_related_list, _parse_tags_list
                   (frontmatter field parsing).
    preview      — _PREVIEW_DIRNAME, _MERGE_LOCK_FILENAME, _preview_dir,
                   _preview_cache_path, _delete_preview, _merge_lock
                   (dry-run preview cache + execute-path locking).
    display      — _print_diff_summary (the pre-merge human-readable diff).
    scan         — _DEFAULT_SCAN_THRESHOLD, _DEFAULT_SCAN_TOP,
                   _is_excluded_from_scan, _scan_duplicates (embedding-based
                   near-duplicate detection across the whole vault).
    index        — _rebuild_index (post-merge ``update_index.py`` invocation).

What stays in ``vault_merge.py`` and why:
    ``AIMergeOutputError``, ``_ai_merge_bodies``, ``_merge_notes``,
    ``_hash_content``, ``_build_frontmatter``, ``_write_preview``,
    ``_load_fresh_preview``, ``_update_wikilinks_in_vault``, and ``main``
    remain defined in the entry shim. The test suite patches
    ``vault_merge.ai_backend.run_ai_prompt`` (a module-attribute patch that
    reaches the same singleton from any caller) and reads
    ``vault_merge.AIMergeOutputError`` / ``vault_merge.<helper>`` directly;
    ``_merge_notes`` weaves those helpers together via bare-name calls that
    must resolve in the shim's own globals, and ``main`` orchestrates the
    shim-resident helpers the same way. Matching the ``vault_search.py``
    precedent, those definitions stay put — the structural move is the bulk
    of the support code listed above.

Behaviour is identical to the original — this is a pure structural move.
``sqlite_vec`` and ``rich`` stay lazy-imported inside the functions that
need them so the package remains importable without the search/tools
extras installed.
"""

from __future__ import annotations
