"""Argparse parser for the ``update_index`` CLI (ARC-005).

Extracted from ``update_index.py``. The ``main()`` entry point stays in
the shim (it weaves together the singleton guard, the inline
``__file__``-relative ``build_embeddings.py`` discovery, and the
parsight/embeddings spawn); only the parser extraction moves here.
Re-exported by the entry shim so ``update_index._parse_args`` keeps
resolving for tests and other callers.

Stdlib-only at module load.
"""

from __future__ import annotations

import argparse


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments for update_index."""
    parser = argparse.ArgumentParser(
        description="Rebuild the vault index (CLAUDE.md, MANIFEST.md, note_index DB).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--vault",
        "-V",
        type=str,
        help="Vault name or path (default: current project-local or default vault)",
    )
    parser.add_argument(
        "--rebuild-graph",
        action="store_true",
        default=False,
        help="Also rebuild visualizer graph.json after the index update",
    )
    parser.add_argument(
        "--graph-include-daily",
        action="store_true",
        default=False,
        help="Include Daily folder notes in the graph (only used with --rebuild-graph)",
    )
    # ENH-010: tri-state so the default (defer to config, which is on) can be
    # overridden either way from the CLI. --graph-incremental forces it on;
    # --no-graph-incremental forces a full rebuild.
    parser.add_argument(
        "--graph-incremental",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Rebuild graph.json incrementally (ENH-002): reuse the previous "
            "graph and recompute only changed notes. Honoured only with "
            "--rebuild-graph. Defaults to the summarizer.graph_incremental "
            "config value (on by default); pass --no-graph-incremental to "
            "force a full rebuild. build_graph.py also falls back to a full "
            "rebuild if the previous graph is missing or was built under "
            "different parameters, so incremental is always safe."
        ),
    )
    return parser.parse_args()
