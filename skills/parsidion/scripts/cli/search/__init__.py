"""search — focused submodules extracted from vault_search.py (ARC-005).

The original 1,228-line ``vault_search.py`` God module mixed the embeddings
backend (sqlite-vec + fastembed), the par-mem routing layer, the metadata
SQL query path, the grep full-text filter, output formatting, env-var
helpers, and the curses interactive TUI. ARC-005 decomposes the movable
concerns into focused submodules behind the proven ``doctor/`` layout.

**What stays in ``vault_search.py``** (same exception as the
``summarizer/`` split): ``search_with_meta``, ``search``, ``LAST_BACKEND``,
and ``main`` remain in the entry shim because ``tests/test_vault_search_backend.py``
monkeypatches ``vault_search._search_embeddings`` and Python resolves bare
names in the *caller's* module globals at call time — keeping
``search_with_meta`` in the shim is the only way the patch takes effect
without rewriting every test to patch ``cli.search.embeddings`` instead.
``main`` stays with it because it reads the ``LAST_BACKEND`` module global
that ``search`` mutates.

Submodule layout:
    _common      — shared constants + config helpers + SearchResultEnvelope.
    embeddings   — fastembed/sqlite-vec machinery + the embeddings backend
                   search (the ENH-003 service path lives here too).
    metadata     — SQL metadata query, all-notes fetcher, grep body filter.
    format       — text / rich output formatters, env-var float/int helpers.

``_interactive_search`` (the curses TUI delegate) stays in ``vault_search.py``
itself: its lazy ``vault_tui`` import is the ARC-023 contract that keeps the
vault_search <-> vault_tui edge cycle-free (``tests/test_vault_imports.py``).

Behaviour is identical to the original — this is a pure structural move.
``fastembed`` / ``sqlite_vec`` / ``rich`` stay lazy-imported inside the
functions that need them, so the submodules are importable without the
optional extras installed.
"""

from __future__ import annotations
