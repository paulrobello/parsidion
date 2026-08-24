#!/usr/bin/env python3
"""vault_doctor.py — Scan vault notes for issues; optionally repair via Claude haiku.

Thin re-export shim over the ``doctor`` package (ARC-008 / QA-003).  The
3,128-line God module was decomposed into focused submodules behind a
``Fixer``/``FixMode`` protocol; every public + private symbol the original
exposed remains importable from ``vault_doctor`` so existing scripts,
``import vault_doctor`` consumers, and test ``monkeypatch`` calls keep working
byte-for-byte.

Stdlib-only. Run with:
    uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py
    uv run --no-project ... --fix          # apply Claude-suggested repairs
    uv run --no-project ... --dry-run      # show issues only, no Claude calls
    uv run --no-project ... note.md ...    # scan specific notes only
    uv run --no-project ... --limit 10     # cap repairs at N notes
    uv run --no-project ... --fix --jobs 5 # repair with 5 parallel workers (default: 3)
    uv run --no-project ... --fix-all      # nightly-cron mode: every fix step + execute

When repairing BROKEN_WIKILINK issues, the doctor uses a Python-only two-stage
strategy — no Claude call needed:
  1. Exact case-insensitive stem match against the note map.
  2. Semantic fallback via ``vault-search --json --top=2 --min-score=0.5``.
  If a replacement is found the link is updated everywhere in the note; if not,
  the brackets are stripped (text kept in body, entry dropped from ``related``).
  If stripping empties the ``related`` field, the orphan-repair workflow kicks in
  (semantic candidates injected via ``_find_semantic_candidates``).

When repairing ORPHAN_NOTE issues (no [[wikilinks]] in 'related'), the doctor
queries ``vault-search`` semantically — using the note's H1 heading or stem as
the query — and injects the top-5 candidate stems into the Claude prompt.  This
ensures repairs pick real, existing notes rather than hallucinated links.
Degrades gracefully when ``vault-search`` is not installed or ``embeddings.db``
is absent.

Before the first execute-mode mutation of a note in a given run, a copy of the
original is saved to ``<vault>/.trash/backup/<YYYY-MM-DD>/<relative-path>``
(first version of the day wins). ``.trash`` is already excluded from indexing,
so backups never show up in search or the graph. Backups are best-effort and
never block a fix; prune ``.trash/backup/`` freely whenever you like.

# ARC-015: Concurrency model rationale
# vault_doctor.py uses ``concurrent.futures.ThreadPoolExecutor`` because it is
# a stdlib-only script.  ``ThreadPoolExecutor`` is sufficient here: the work is
# I/O-bound (prompt AI helper subprocesses + file reads/writes) and Python's
# GIL does not prevent I/O parallelism.  Adding ``anyio`` or ``asyncio`` would
# require a dependency change that violates the stdlib-only constraint.
#
# summarize_sessions.py uses ``anyio`` + ``anyio.create_task_group`` because it
# already depends on ``claude-agent-sdk`` (which is built on anyio) and benefits
# from structured concurrency guarantees (task groups propagate exceptions
# reliably, unlike ThreadPoolExecutor's ``Future`` cancellation model).
#
# Both approaches are intentional — the choice was driven by dependency
# constraints, not inconsistency.  See ARC-015.
"""

# Standard-library imports are re-exported so tests that monkeypatch the
# shared module objects (``vault_doctor.shutil.copy2``,
# ``vault_doctor.subprocess.run``, ``vault_doctor.ai_backend.run_ai_prompt``)
# keep working — every submodule that does ``import shutil`` etc. sees the
# patch because Python's module cache returns the same module object.
import argparse  # re-exported for tests
import atexit  # re-exported for tests
import concurrent.futures  # re-exported for tests
import errno  # re-exported for tests
import json  # re-exported for tests
import os  # re-exported for tests
import re  # re-exported for tests
import shutil  # re-exported for test monkeypatch (vault_doctor.shutil.copy2)
import subprocess  # re-exported for test monkeypatch (vault_doctor.subprocess.run)
import sys  # re-exported for tests
import threading  # re-exported for tests
from datetime import date, datetime  # re-exported for tests
from pathlib import Path  # re-exported for tests

import ai_backend  # re-exported for test monkeypatch (vault_doctor.ai_backend.run_ai_prompt)
import vault_common  # re-exported for tests
import vault_fs  # re-exported for test monkeypatch
import vault_links  # re-exported for test monkeypatch

# ---------------------------------------------------------------------------
# Constants, data model, and shared state live in doctor._state so the
# submodules and this shim share one ``_vault_path`` / ``_backed_up_this_run``
# object.  Re-exported here so existing ``vault_doctor.X`` attribute access
# (including test ``monkeypatch.setattr``) keeps working byte-for-byte.
# See doctor/_state.py for the test-patch compatibility contract.
# ---------------------------------------------------------------------------
from doctor._state import (  # re-exports
    AI_TIMEOUT,
    DEFAULT_MODEL,
    PREFIX_CLUSTER_MIN,
    REPAIRABLE_CODES,
    REQUIRED_FIELDS_ALL,
    REQUIRED_FIELDS_KNOWLEDGE,
    SESSION_ID_PATTERN,
    STATE_STALE_DAYS,
    STALE_COMMIT_MINUTES,
    VALID_TYPES,
    Issue,
    _active_vault,
    _backup_note,
    _backed_up_this_run,
    _get_state_file,
    _rel,
    _resolve_shim_vault_path,
    _vault_path,
    _write_json_atomic,
    is_process_running,
    load_state,
    save_state,
    should_skip,
)
from doctor.check import check_note  # re-export
from doctor.daily import run_migrate_daily_notes  # re-export
from doctor.frontmatter import (  # re-exports
    _FM_DELIM_RE,
    _FM_KEY_RE,
    _FM_LEAKED_MARKER_RE,
    _FM_RELATED_RE,
    _auto_fix_metadata_wrapper,
    _auto_fix_scalar_list_field,
    _frontmatter_stems,
    _normalize_repaired_note,
    _note_is_daily,
    repair_note,
)
from doctor.graph import _run_reindex, commit_stale_files  # re-exports
from doctor.headings import _auto_fix_headings, _auto_fix_self_refs  # re-exports
from doctor.links import (  # re-exports
    _auto_repair_broken_wikilinks,
    _find_link_replacement,
    _find_semantic_candidates,
    build_note_map,
    dedup_related_links,
    resolve_wikilink,
)
from doctor.orchestrator import run_scan_and_repair  # re-export
from doctor.protocol import DoctorOptions  # QA-005 re-export
from doctor.scan import (  # re-exports (ENH-007 read-only scan path)
    ScanSummary,
    _git_tracked_gitignored,
    scan_notes_readonly,
)
from doctor.permissions import (  # re-exports
    _DIR_MODE,
    _FILE_MODE,
    _SECRET_FILE_GLOBS,
    _SECRET_FILES,
    _chmod_if_exists,
    run_fix_permissions,
)
from doctor.prefixes import (  # re-exports
    _find_redundant_prefixes,
    run_strip_prefixes,
)
from doctor.protocol import FixMode, run_fix_modes  # re-exports
from doctor.subfolder import (  # re-exports
    _GENERIC_PREFIX_DENYLIST,
    _common_word_prefix,
    _filter_clusters_with_claude,
    _is_generic_prefix,
    find_prefix_clusters,
    find_subfolder_candidates,
    fix_prefix_cluster,
    run_migrate_subfolders,
)
from doctor.tags import (  # re-exports
    _TAGS_BLOCK_START_RE,
    _TAGS_INLINE_RE,
    _collect_all_tags,
    _find_session_duplicates,
    _find_tag_duplicates,
    _normalize_underscores_in_frontmatter,
    _replace_tag_in_note,
    _update_graph_json_tags,
    run_fix_sessions,
    run_fix_tags,
)
from doctor.worker import _repair_one  # re-export

# CLI entry point — imported last so the submodule graph is fully populated
# before main() can be invoked. ``if __name__ == "__main__": main()`` below
# keeps this file invocable as ``uv run --no-project vault_doctor.py …``.
from doctor.cli import main  # script entry point


if __name__ == "__main__":
    main()
