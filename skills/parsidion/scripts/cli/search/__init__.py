"""search — focused submodules extracted from vault_search.py (ARC-005).

The original 1,228-line ``vault_search.py`` God module mixed the embeddings
backend (sqlite-vec + fastembed), the parsight routing layer, the metadata
SQL query path, the grep full-text filter, output formatting, env-var
helpers, and the curses interactive TUI. ARC-005 decomposes the movable
concerns into focused submodules behind the proven ``doctor/`` layout.

**What stays in ``vault_search.py``**: ``search_with_meta``, ``search``, and
``main`` remain in the entry shim as the CLI's routing layer. ARC-006
removed the test-driven exceptions that used to pin them there: the
deprecated ``LAST_BACKEND`` module global is gone (read
``SearchResultEnvelope.backend`` from ``search_with_meta`` instead), and the
embeddings leg is invoked through this package's ``embeddings`` module so
tests patch ``cli.search.embeddings._search_embeddings`` where it lives.

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
